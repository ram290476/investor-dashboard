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
                     Neither is in XBRL. Approved company-metrics rows
                     (total_deliveries, or the Model 3/Y plus other-models split, and
                     fsd_subscriptions) replace the manual file for that quarter.
                     Until those catalog entries are approved, both still come from
                     s3://<lake>/manual/fundamentals/fundamentals_manual.csv
                     (see scripts/add_fundamental.py). A missing file is logged.

Fiscal quarters are inferred from annual duration facts' end month. Fiscal-year labels use
the year the fiscal year ends. Companies changing their fiscal calendar and 52/53-week
years crossing month boundaries need a reviewed fiscal-calendar mapping.
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
    "measurement_date": pl.Date,
    "value": pl.Float64,
    "unit": pl.Utf8,
    "source_id": pl.Utf8,
}


def _quarter(end: date, fiscal_end_month: int = 12) -> str:
    year = end.year + int(end.month > fiscal_end_month)
    quarter = ((end.month - fiscal_end_month - 1) % 12) // 3 + 1
    return f"{year}Q{quarter}"


def _duration_days(f: dict) -> int:
    return (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days


def _usd_facts(companyfacts: dict, tags: list[str]) -> list[dict]:
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    for tag in tags:  # first tag the company actually reports wins
        facts = gaap.get(tag, {}).get("units", {}).get("USD", [])
        if facts:
            return [f for f in facts if f.get("form") in {"10-Q", "10-K", "10-Q/A", "10-K/A"} and "start" in f]
    return []


def quarterly_series(facts: list[dict], fiscal_end_month: int = 12) -> dict[str, tuple[float, date]]:
    """{fiscal_quarter: (value, filed)} using the first filing that reported each period.

    Quarters come from ~3-month facts; Q4 = full-year fact minus that year's Q1-Q3.
    """
    quarters: dict[str, tuple[float, date]] = {}
    annual: dict[int, tuple[float, date]] = {}
    for f in sorted(facts, key=lambda x: x["filed"]):
        end, filed, days = date.fromisoformat(f["end"]), date.fromisoformat(f["filed"]), _duration_days(f)
        if 80 <= days <= 100:
            quarters.setdefault(_quarter(end, fiscal_end_month), (float(f["val"]), filed))
        elif 350 <= days <= 380:
            annual.setdefault(end.year, (float(f["val"]), filed))
    for year, (fy_val, filed) in annual.items():
        q4 = f"{year}Q4"
        parts = [quarters.get(f"{year}Q{i}") for i in (1, 2, 3)]
        if q4 not in quarters and all(parts):
            quarters[q4] = (fy_val - sum(p[0] for p in parts), filed)
    return quarters


def xbrl_rows(ticker: str, companyfacts: dict, source_id: str = "DS-11") -> list[dict]:
    revenue_facts = _usd_facts(companyfacts, XBRL_TAGS["revenue_gaap"])
    annual = sorted(
        (fact for fact in revenue_facts if 350 <= _duration_days(fact) <= 380),
        key=lambda fact: fact["filed"],
    )
    fiscal_end_month = date.fromisoformat(annual[0]["end"]).month if annual else 12
    series = {
        m: quarterly_series(_usd_facts(companyfacts, tags), fiscal_end_month)
        for m, tags in XBRL_TAGS.items()
    }
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
    dei_facts = (
        companyfacts.get("facts", {})
        .get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {})
        .get("units", {})
        .get("shares", [])
    )
    shares_by_quarter = {}
    for fact in sorted(dei_facts, key=lambda item: item.get("filed", "")):
        if fact.get("form") not in {"10-Q", "10-K", "10-Q/A", "10-K/A"} or not fact.get("end"):
            continue
        quarter = _quarter(date.fromisoformat(fact["end"]), fiscal_end_month)
        shares_by_quarter.setdefault(quarter, (float(fact["val"]), date.fromisoformat(fact["filed"])))
    rows.extend(
        dict(
            ticker=ticker,
            metric="shares_outstanding",
            fiscal_quarter=quarter,
            release_date=filed,
            value=value,
            unit="shares",
            source_id=source_id,
        )
        for quarter, (value, filed) in shares_by_quarter.items()
    )
    public_float_facts = (
        companyfacts.get("facts", {})
        .get("dei", {})
        .get("EntityPublicFloat", {})
        .get("units", {})
        .get("USD", [])
    )
    public_float_by_date = {}
    for fact in sorted(public_float_facts, key=lambda item: item.get("filed", "")):
        if fact.get("form") not in {"10-Q", "10-K", "10-Q/A", "10-K/A"} or not fact.get("end"):
            continue
        measured = date.fromisoformat(fact["end"])
        public_float_by_date.setdefault(measured, (float(fact["val"]), date.fromisoformat(fact["filed"])))
    rows.extend(
        dict(
            ticker=ticker,
            metric="public_float_usd",
            fiscal_quarter=_quarter(measured, fiscal_end_month),
            release_date=filed,
            measurement_date=measured,
            value=value,
            unit="USD",
            source_id=source_id,
        )
        for measured, (value, filed) in public_float_by_date.items()
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


_COMPONENT_DELIVERIES = ("deliveries_model_3y", "deliveries_other_models")


def _whole_count(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or abs(number - round(number)) > 1e-6:
        return None
    return float(round(number))


def rows_from_company_metrics(observations: list[dict], approved: set[tuple[str, str]]) -> list[dict]:
    """Approved curated deliveries and FSD subscriptions, in the manual-row shape Q1 already publishes.

    `approved` is a set of (TICKER, metric_id). A proposed catalog entry contributes nothing.
    total_deliveries wins. Otherwise the two delivery components are summed when both are approved.
    """
    chosen: dict[tuple[str, str, str], dict] = {}
    parts: dict[tuple[str, str], dict[str, tuple[float, date]]] = {}
    for row in observations:
        metric_id = str(row.get("metric_id") or "")
        ticker = str(row.get("ticker") or "").upper()
        if row.get("approved") is not True or (ticker, metric_id) not in approved:
            continue
        quarter = str(row.get("fiscal_period") or row.get("fiscal_quarter") or "")
        if not QUARTER_RE.match(quarter):
            continue
        published = str(row.get("published_date") or "")[:10]
        try:
            release = date.fromisoformat(published)
        except ValueError:
            continue
        number = _whole_count(row.get("value"))
        if number is None:
            continue
        if metric_id in {"total_deliveries", "fsd_subscriptions"}:
            target = "deliveries" if metric_id == "total_deliveries" else "fsd_subscribers"
            chosen[(ticker, target, quarter)] = dict(
                ticker=ticker,
                metric=target,
                fiscal_quarter=quarter,
                release_date=release,
                value=number,
                unit=MANUAL_METRICS[target],
                source_id="DS-12",
            )
        elif metric_id in _COMPONENT_DELIVERIES:
            parts.setdefault((ticker, quarter), {})[metric_id] = (number, release)
    for (ticker, quarter), found in parts.items():
        if (ticker, "deliveries", quarter) in chosen:
            continue
        if not set(_COMPONENT_DELIVERIES) <= set(found) or not set(_COMPONENT_DELIVERIES) <= {
            metric_id for symbol, metric_id in approved if symbol == ticker
        }:
            continue
        release = max(item[1] for item in found.values())
        chosen[(ticker, "deliveries", quarter)] = dict(
            ticker=ticker,
            metric="deliveries",
            fiscal_quarter=quarter,
            release_date=release,
            value=sum(item[0] for item in found.values()),
            unit=MANUAL_METRICS["deliveries"],
            source_id="DS-12",
        )
    return list(chosen.values())


def manual_csv_warning(text: str | None) -> str | None:
    """Q1 logs this instead of treating a missing reviewed file as a silent empty success."""
    if text is None:
        return (
            "manual/fundamentals/fundamentals_manual.csv is missing; "
            "operating metrics stay empty until curated rows are approved"
        )
    return None


def build_table(xbrl: list[dict], manual: list[dict]) -> pl.DataFrame:
    df = pl.DataFrame(xbrl + manual, schema=SCHEMA)
    # One row per key; a later manual correction for the same key wins.
    return df.unique(subset=["ticker", "metric", "fiscal_quarter"], keep="last").sort(
        ["ticker", "metric", "fiscal_quarter"]
    )


def resolve_ciks(tickers: list[str], records: dict) -> dict[str, str]:
    by_ticker = {str(rec["ticker"]).upper(): f"{int(rec['cik_str']):010d}" for rec in records.values()}
    return {ticker: by_ticker[ticker.replace(".", "-")] for ticker in tickers if ticker.replace(".", "-") in by_ticker}


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    import os

    from company_ir import load_catalog
    from http_client import get_client
    from lake import job_lease, read_json, read_parquet_prefix, s3, write_json, write_parquet
    from observability import job_handler, logger, source_run
    from universe import BASE_TICKERS, user_ticker_union

    @job_handler("Q1")
    def run(event, context):
        with job_lease("curated/fundamentals_quarterly/_lease.json"):
            return collect()

    def collect():
        bucket = os.environ["LAKE_BUCKET"]
        xbrl: list[dict] = []
        ticker_status = {}
        tickers = list(dict.fromkeys([*BASE_TICKERS, *user_ticker_union()]))
        with get_client() as http:
            response = http.get("https://www.sec.gov/files/company_tickers.json")
            response.raise_for_status()
            ciks = resolve_ciks(tickers, response.json())
            for ticker in tickers:
                if ticker not in ciks:
                    ticker_status[ticker] = "not_an_sec_filer"
            for ticker, cik in ciks.items():
                with source_run("DS-75" if ticker == "SPCX" else "DS-11") as run_rec:
                    response = http.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
                    if response.status_code == 404:
                        ticker_status[ticker] = "no_xbrl"
                    else:
                        response.raise_for_status()
                        rows = xbrl_rows(ticker, response.json(), "DS-75" if ticker == "SPCX" else "DS-11")
                        xbrl.extend(rows)
                        run_rec["rows"] = len(rows)
                        ticker_status[ticker] = "available" if rows else "no_supported_facts"
                if run_rec["outcome"] == "failure":
                    raise RuntimeError(f"Fundamentals failed for {ticker}: {run_rec['error']}")
        manual: list[dict] = []
        with source_run("DS-12") as run_rec:
            try:
                body = s3().get_object(Bucket=bucket, Key="manual/fundamentals/fundamentals_manual.csv")["Body"]
                manual = parse_manual_csv(body.read().decode())
            except s3().exceptions.NoSuchKey:
                manual = []
                logger.warning("fundamentals_manual_csv_missing", extra={"warning": manual_csv_warning(None)})
            run_rec["rows"] = len(manual)
        if run_rec["outcome"] == "failure":
            raise RuntimeError(f"Manual fundamentals failed: {run_rec['error']}")
        approved: set[tuple[str, str]] = set()
        for symbol in tickers:
            for item in load_catalog(symbol).get("metrics") or []:
                if item.get("status") == "approved" and item.get("metric_id"):
                    approved.add((symbol, item["metric_id"]))
        curated_frame = read_parquet_prefix("curated/company_metrics/")
        curated_observations = curated_frame.to_dicts() if not curated_frame.is_empty() else []
        # Parquet keeps the canonical columns. published_date lives on the observations sidecar.
        for symbol in tickers:
            sidecar = read_json(f"curated/company_metrics/ticker={symbol}/observations.json") or {}
            if sidecar.get("rows"):
                curated_observations = [
                    row for row in curated_observations if str(row.get("ticker") or "").upper() != symbol
                ]
                curated_observations.extend(sidecar["rows"])
        curated = rows_from_company_metrics(curated_observations, approved)
        # Curated approved rows are appended last so they replace the manual CSV for the same key.
        table = build_table(xbrl, manual + curated)
        write_parquet(table, "curated/fundamentals_quarterly/fundamentals_quarterly.parquet")
        write_json(
            {"rows": table.to_dicts(), "ticker_status": ticker_status},
            "serving/fundamentals_quarterly.json", cache_seconds=3600,
        )
        return {"rows": table.height, "ticker_status": ticker_status}

    return run(event, context)
