"""Q1 job: fundamentals_quarterly, one row per (ticker, metric, fiscal_quarter).

Columns: ticker, metric, fiscal_quarter (e.g. 2026Q2), release_date, value, unit, source_id.
The UI draws each metric as a step line that changes on release_date.

Metrics
- gross_margin_gaap  total GAAP gross margin = GrossProfit / Revenues, from SEC XBRL company facts (DS-11).
                     release_date = the SEC filing date of the 10-Q/10-K carrying the number. Q4 is not
                     reported on its own, so Q4 = full year (10-K) minus Q1-Q3.
                     The inputs are kept as rows too: revenue_gaap, gross_profit_gaap.
- deliveries         Tesla quarterly deliveries (DS-12 press release).
- fsd_subscribers    FSD active subscribers (DS-12 shareholder deck, where disclosed).
                     Neither is in XBRL, so both come from a reviewed manual file:
                     s3://<lake>/manual/fundamentals/fundamentals_manual.csv (see scripts/add_fundamental.py).

Fiscal quarters assume a calendar fiscal year (true for Tesla). SPCX's fiscal year must be
confirmed from its 10-K before its rows are trusted.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date

import polars as pl

XBRL_TAGS = {
    "revenue_gaap": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "gross_profit_gaap": ["GrossProfit"],
}
MANUAL_METRICS = {"deliveries": "vehicles", "fsd_subscribers": "subscribers"}
ALLOWED_METRICS = {"gross_margin_gaap", "revenue_gaap", "gross_profit_gaap", *MANUAL_METRICS}
QUARTER_RE = re.compile(r"^\d{4}Q[1-4]$")
SCHEMA = {
    "ticker": pl.Utf8,
    "metric": pl.Utf8,
    "fiscal_quarter": pl.Utf8,
    "release_date": pl.Date,
    "value": pl.Float64,
    "unit": pl.Utf8,
    "source_id": pl.Utf8,
}


def _quarter(end: date) -> str:
    return f"{end.year}Q{(end.month - 1) // 3 + 1}"


def _duration_days(f: dict) -> int:
    return (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days


def _usd_facts(companyfacts: dict, tags: list[str]) -> list[dict]:
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    for tag in tags:  # first tag the company actually reports wins
        facts = gaap.get(tag, {}).get("units", {}).get("USD", [])
        if facts:
            return [f for f in facts if f.get("form") in {"10-Q", "10-K", "10-Q/A", "10-K/A"} and "start" in f]
    return []


def quarterly_series(facts: list[dict]) -> dict[str, tuple[float, date]]:
    """{fiscal_quarter: (value, filed)} using the first filing that reported each period.

    Quarters come from ~3-month facts; Q4 = full-year fact minus that year's Q1-Q3.
    """
    quarters: dict[str, tuple[float, date]] = {}
    annual: dict[int, tuple[float, date]] = {}
    for f in sorted(facts, key=lambda x: x["filed"]):
        end, filed, days = date.fromisoformat(f["end"]), date.fromisoformat(f["filed"]), _duration_days(f)
        if 80 <= days <= 100:
            quarters.setdefault(_quarter(end), (float(f["val"]), filed))
        elif 350 <= days <= 380:
            annual.setdefault(end.year, (float(f["val"]), filed))
    for year, (fy_val, filed) in annual.items():
        q4 = f"{year}Q4"
        parts = [quarters.get(f"{year}Q{i}") for i in (1, 2, 3)]
        if q4 not in quarters and all(parts):
            quarters[q4] = (fy_val - sum(p[0] for p in parts), filed)
    return quarters


def xbrl_rows(ticker: str, companyfacts: dict, source_id: str = "DS-11") -> list[dict]:
    series = {m: quarterly_series(_usd_facts(companyfacts, tags)) for m, tags in XBRL_TAGS.items()}
    rows = []
    for metric, by_q in series.items():
        for q, (val, filed) in by_q.items():
            rows.append(dict(ticker=ticker, metric=metric, fiscal_quarter=q, release_date=filed, value=val, unit="USD"))
    rev, gp = series["revenue_gaap"], series["gross_profit_gaap"]
    for q in sorted(set(rev) & set(gp)):
        if rev[q][0]:
            rows.append(
                dict(
                    ticker=ticker,
                    metric="gross_margin_gaap",
                    fiscal_quarter=q,
                    release_date=max(rev[q][1], gp[q][1]),
                    value=gp[q][0] / rev[q][0],
                    unit="ratio",
                )
            )
    for r in rows:
        r["source_id"] = source_id
    return rows


class ManualRowError(ValueError):
    pass


def parse_manual_csv(text: str) -> list[dict]:
    """Validate the reviewed manual file. Raises ManualRowError naming the bad line."""
    rows = []
    for n, rec in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        try:
            metric = rec["metric"].strip()
            if metric not in MANUAL_METRICS:
                raise ManualRowError(f"metric must be one of {sorted(MANUAL_METRICS)}")
            q = rec["fiscal_quarter"].strip()
            if not QUARTER_RE.match(q):
                raise ManualRowError("fiscal_quarter must look like 2026Q2")
            value = float(rec["value"].replace(",", ""))
            if value < 0 or not value.is_integer():
                raise ManualRowError("value must be a whole, non-negative count")
            rows.append(
                dict(
                    ticker=rec["ticker"].strip().upper(),
                    metric=metric,
                    fiscal_quarter=q,
                    release_date=date.fromisoformat(rec["release_date"].strip()),
                    value=value,
                    unit=MANUAL_METRICS[metric],
                    source_id=rec.get("source_id", "").strip() or "DS-12",
                )
            )
        except (KeyError, ValueError) as exc:
            raise ManualRowError(f"fundamentals_manual.csv line {n}: {exc}") from exc
    return rows


def build_table(xbrl: list[dict], manual: list[dict]) -> pl.DataFrame:
    df = pl.DataFrame(xbrl + manual, schema=SCHEMA)
    # One row per key; a later manual correction for the same key wins.
    return df.unique(subset=["ticker", "metric", "fiscal_quarter"], keep="last").sort(
        ["ticker", "metric", "fiscal_quarter"]
    )


CIKS = {"TSLA": "0001318605"}  # SPCX: resolved from company_tickers.json at run time


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    import os

    from http_client import get_client
    from lake import s3, write_json, write_parquet
    from observability import job_handler, source_run

    @job_handler("Q1")
    def run(event, context):
        bucket = os.environ["LAKE_BUCKET"]
        xbrl: list[dict] = []
        with get_client() as http:
            ciks = dict(CIKS)
            tickers_map = http.get("https://www.sec.gov/files/company_tickers.json").json()
            for rec in tickers_map.values():
                if rec["ticker"] == "SPCX":
                    ciks["SPCX"] = f"{int(rec['cik_str']):010d}"
            for ticker, cik in ciks.items():
                with source_run("DS-11" if ticker == "TSLA" else "DS-75") as run_rec:
                    facts = http.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json").json()
                    rows = xbrl_rows(ticker, facts, "DS-11" if ticker == "TSLA" else "DS-75")
                    xbrl.extend(rows)
                    run_rec["rows"] = len(rows)
        manual: list[dict] = []
        with source_run("DS-12") as run_rec:
            try:
                body = s3().get_object(Bucket=bucket, Key="manual/fundamentals/fundamentals_manual.csv")["Body"]
                manual = parse_manual_csv(body.read().decode())
            except s3().exceptions.NoSuchKey:
                manual = []
            run_rec["rows"] = len(manual)
        table = build_table(xbrl, manual)
        write_parquet(table, "curated/fundamentals_quarterly/fundamentals_quarterly.parquet")
        write_json({"rows": table.to_dicts()}, "serving/fundamentals_quarterly.json", cache_seconds=3600)
        return {"rows": table.height}

    return run(event, context)
