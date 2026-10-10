"""Company IR collection, extraction, catalog gating and per-ticker serving JSON.

Raw documents land at raw/company_ir/<ticker>/<period>/<sha256>.<ext> with a manifest.
Curated rows use the company_metrics Parquet schema. Only catalog entries Ram has
approved are served. Proposed metrics are stored and omitted from serving JSON.

Live crawling, LLM calls and Terraform apply stay outside this module's tests.
LLM credentials are a Secrets Manager / SSM id in the environment, never a key literal.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import polars as pl

CRAWLER_PRODUCT = "InvestorDashboardIR"
PARQUET_COLUMNS = [
    "ticker",
    "metric_id",
    "period_end",
    "fiscal_period",
    "value",
    "unit",
    "source_url",
    "source_doc_hash",
    "extracted_at",
    "confidence",
    "approved",
]
SCHEMA = {
    "ticker": pl.Utf8,
    "metric_id": pl.Utf8,
    "period_end": pl.Date,
    "fiscal_period": pl.Utf8,
    "value": pl.Float64,
    "unit": pl.Utf8,
    "source_url": pl.Utf8,
    "source_doc_hash": pl.Utf8,
    "extracted_at": pl.Datetime(time_zone="UTC"),
    "confidence": pl.Float64,
    "approved": pl.Boolean,
}
CONFIDENCE = {"table": 0.95, "cross_check": 0.75, "llm": 0.40}
XBRL_TOLERANCE = 0.005
_MISSING = {"", "not reported", "n/a", "na", "-", "n.a.", "n.m.", "unavailable"}
_QUARTER = re.compile(r"^(\d{4})Q([1-4])$")
_SCRIPT = re.compile(r"(?is)<script\b[^>]*>.*?</script>")
_STYLE = re.compile(r"(?is)<style\b[^>]*>.*?</style>")
_COMMENT = re.compile(r"(?s)<!--.*?-->")
_HANDLER = re.compile(r"""(?i)\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""")
_TAG = re.compile(r"(?is)<[^>]+>")

REQUIRED_TESLA_METRICS = (
    "fsd_subscriptions",
    "tesla_semi",
    "robotaxi_fleet",
    "supercharger_stations",
    "supercharger_connectors",
    "energy_storage_deployed",
    "optimus",
    "production_model_3y",
    "production_other_models",
    "deliveries_model_3y",
    "deliveries_other_models",
    "vehicles_operating_lease",
    "inventory_days_supply",
    "store_service_locations",
    "mobile_service_fleet",
    "installed_vehicle_capacity",
    "operating_cash_flow",
    "free_cash_flow",
    "capex",
    "cash_and_investments",
    "auto_gross_margin_ex_credits",
    "regulatory_credits_revenue",
)


def crawler_user_agent(contact: str | None = None) -> str:
    """Identify the crawler. The contact string is the existing SEC_USER_AGENT, not a secret."""
    who = (contact if contact is not None else os.environ.get("SEC_USER_AGENT", "")).strip()
    if not who:
        who = "contact unavailable"
    return f"{CRAWLER_PRODUCT}/1.0 ({who})"


def llm_secret_id() -> str:
    """Secrets Manager or SSM parameter name. Empty means the LLM must not be called."""
    return os.environ.get("COMPANY_IR_LLM_SECRET_ID", "").strip()


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def raw_object_key(ticker: str, period: str, digest: str, extension: str) -> str:
    ext = extension.lstrip(".").lower() or "bin"
    return f"raw/company_ir/{ticker.upper()}/{period}/{digest}.{ext}"


def manifest_key(ticker: str, period: str) -> str:
    return f"raw/company_ir/{ticker.upper()}/{period}/manifest.json"


def store_raw_document(
    *,
    ticker: str,
    period: str,
    url: str,
    body: bytes,
    fetched_at: str,
    status_code: int,
    content_type: str,
    etag: str | None,
    robots_decision: str,
    extension: str,
) -> dict:
    digest = sha256_hex(body)
    manifest = {
        "source_url": url,
        "fetched_at": fetched_at,
        "sha256": digest,
        "http_status": status_code,
        "content_type": content_type,
        "etag": etag,
        "robots_decision": robots_decision,
    }
    return {
        "key": raw_object_key(ticker, period, digest, extension),
        "manifest_key": manifest_key(ticker, period),
        "sha256": digest,
        "manifest": manifest,
    }


def fetch_decision(status_code: int) -> dict:
    """403 and 429 stop. They are not retried and not worked around."""
    if status_code in {403, 429}:
        return {"action": "stop", "retry": False, "store": False, "reason": "provider_policy"}
    if status_code == 404:
        return {"action": "missing", "retry": False, "store": False, "reason": "not_found"}
    if 500 <= status_code <= 599:
        return {"action": "retry", "retry": True, "store": False, "reason": "upstream"}
    if status_code == 200:
        return {"action": "store", "retry": False, "store": True, "reason": "ok"}
    return {"action": "stop", "retry": False, "store": False, "reason": "unexpected"}


def robots_allows(robots_txt: str, user_agent: str, path: str) -> bool:
    """Honor the longest matching Allow/Disallow for this crawler, else *."""
    groups: list[dict] = []
    current: dict | None = None
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is None or current["rules"]:
                current = {"agents": [], "rules": []}
                groups.append(current)
            current["agents"].append(value.lower())
        elif current is not None and key in {"allow", "disallow"}:
            current["rules"].append((key, value))
    product = user_agent.split("/", 1)[0].lower()
    chosen = next((group for group in groups if product in group["agents"]), None)
    if chosen is None:
        chosen = next((group for group in groups if "*" in group["agents"]), None)
    if chosen is None:
        return True
    best: tuple[int, str] | None = None
    target = path if path.startswith("/") else f"/{path}"
    for kind, rule in chosen["rules"]:
        if rule == "":
            continue
        if target.startswith(rule) and (best is None or len(rule) >= best[0]):
            best = (len(rule), kind)
    if best is None:
        return True
    return best[1] == "allow"


def sanitize_html(html: str) -> str:
    """Strip scripts, styles, comments and inline handlers before parsing. Never render this."""
    cleaned = _SCRIPT.sub(" ", html)
    cleaned = _STYLE.sub(" ", cleaned)
    cleaned = _COMMENT.sub(" ", cleaned)
    return _HANDLER.sub("", cleaned)


def load_catalog(ticker: str) -> dict:
    symbol = str(ticker or "").upper()
    path = Path(__file__).resolve().parent / "catalog" / f"{symbol}.json"
    if not path.is_file():
        return {"ticker": symbol, "catalog_version": 1, "metrics": []}
    document = json.loads(path.read_text(encoding="utf-8"))
    document["ticker"] = symbol
    return document


def catalog_entry(catalog: dict, metric_id: str) -> dict | None:
    return next((item for item in catalog.get("metrics", []) if item.get("metric_id") == metric_id), None)


def set_catalog_status(
    catalog: dict, metric_id: str, status: str, approved_by: str | None, approved_at: str | None,
) -> dict:
    """Reviewed JSON/CLI approval. Does not invent an approval Ram has not made."""
    if status not in {"proposed", "approved", "rejected"}:
        raise ValueError(f"unsupported catalog status: {status}")
    updated = json.loads(json.dumps(catalog))
    entry = catalog_entry(updated, metric_id)
    if entry is None:
        raise KeyError(metric_id)
    entry["status"] = status
    entry["approved_by"] = approved_by if status == "approved" else None
    entry["approved_at"] = approved_at if status == "approved" else None
    return updated


def _alias_index(catalog: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for entry in catalog.get("metrics", []):
        names = [entry.get("display_name", ""), entry.get("metric_id", ""), *(entry.get("aliases") or [])]
        for name in names:
            key = " ".join(str(name).lower().replace("_", " ").split())
            if key:
                index[key] = entry
    return index


def confidence_for(method: str) -> float:
    if method not in CONFIDENCE:
        raise ValueError(f"unsupported extraction method: {method}")
    return CONFIDENCE[method]


def row_approved(catalog_status: str, method: str) -> bool:
    """Catalog approval is required. LLM-only values stay in review even after that."""
    return catalog_status == "approved" and method != "llm"


def period_end_for(fiscal_period: str) -> date:
    match = _QUARTER.fullmatch(fiscal_period)
    if not match:
        raise ValueError(f"fiscal period must look like 2026Q3, got {fiscal_period!r}")
    year, quarter = int(match.group(1)), int(match.group(2))
    month_day = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    month, day = month_day[quarter]
    return date(year, month, day)


def _parse_number(text: str) -> float | None:
    cleaned = " ".join(text.replace("\u00a0", " ").replace("\u2013", "-").replace("\u2014", "-").split()).strip()
    if cleaned.lower() in _MISSING:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()").replace(",", "").replace("$", "").replace("%", "").strip()
    multiplier = 1.0
    suffix = cleaned[-1:].lower()
    if suffix in {"b", "m", "k"} and cleaned[:-1].replace(".", "", 1).isdigit():
        multiplier = {"b": 1_000_000_000, "m": 1_000_000, "k": 1_000}[suffix]
        cleaned = cleaned[:-1]
    try:
        value = float(cleaned) * multiplier
    except ValueError:
        return None
    return -value if negative else value


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(cell.strip() for cell in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None and not self._skip:
            self._cell.append(data)


def html_tables(html: str) -> list[list[list[str]]]:
    parser = _TableParser()
    parser.feed(sanitize_html(html))
    return parser.tables


def text_tables(text: str) -> list[list[list[str]]]:
    """PDF text-layer tables: header row plus pipe-separated body rows."""
    rows: list[list[str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if "|" not in line:
            if rows:
                break
            continue
        cells = [" ".join(cell.split()) for cell in line.strip("|").split("|")]
        if any(cells):
            rows.append(cells)
    return [rows] if len(rows) >= 2 else []


def _quarter_headers(headers: list[str]) -> list[tuple[int, str]]:
    found = []
    for index, header in enumerate(headers):
        token = header.upper().replace(" ", "")
        if _QUARTER.fullmatch(token):
            found.append((index, token))
    return found


def observations_from_tables(
    tables: list[list[list[str]]],
    catalog: dict,
    *,
    ticker: str,
    source_url: str,
    source_doc_hash: str,
    extracted_at: datetime,
    published_date: str | None,
    method: str = "table",
) -> list[dict]:
    aliases = _alias_index(catalog)
    confidence = confidence_for(method)
    rows: list[dict] = []
    for table in tables:
        if not table:
            continue
        quarters = _quarter_headers(table[0])
        if not quarters:
            continue
        for cells in table[1:]:
            label = " ".join(cells[0].lower().replace("_", " ").split()) if cells else ""
            entry = aliases.get(label)
            if entry is None:
                continue
            for index, fiscal_period in quarters:
                if index >= len(cells):
                    continue
                value = _parse_number(cells[index])
                if value is None:
                    continue
                rows.append({
                    "ticker": ticker.upper(),
                    "metric_id": entry["metric_id"],
                    "period_end": period_end_for(fiscal_period),
                    "fiscal_period": fiscal_period,
                    "value": value,
                    "unit": entry.get("unit") or "",
                    "source_url": source_url,
                    "source_doc_hash": source_doc_hash,
                    "extracted_at": extracted_at,
                    "confidence": confidence,
                    "approved": row_approved(entry.get("status", "proposed"), method),
                    "method": method,
                    "published_date": published_date,
                    "display_name": entry.get("display_name"),
                    "category": entry.get("category"),
                    "catalog_status": entry.get("status", "proposed"),
                })
    return rows


def parse_ex991_html(html: str, catalog: dict, **kwargs) -> list[dict]:
    return observations_from_tables(html_tables(html), catalog, method="table", **kwargs)


def parse_deck_text(text: str, catalog: dict, **kwargs) -> list[dict]:
    return observations_from_tables(text_tables(text), catalog, method="table", **kwargs)


def parse_xbrl_quarters(facts: list[dict], fiscal_end_month: int = 12) -> dict[str, float]:
    """Map fiscal quarter to the first filed 10-Q/10-K value. Q4 is not inferred here."""
    series: dict[str, float] = {}
    for fact in sorted(facts, key=lambda item: item.get("filed", "")):
        if fact.get("form") not in {"10-Q", "10-K", "10-Q/A", "10-K/A"}:
            continue
        if "end" not in fact or "val" not in fact:
            continue
        end = date.fromisoformat(fact["end"])
        year = end.year + int(end.month > fiscal_end_month)
        quarter = ((end.month - fiscal_end_month - 1) % 12) // 3 + 1
        start = fact.get("start")
        if start:
            days = (end - date.fromisoformat(start)).days
            if not 80 <= days <= 100:
                continue
        series.setdefault(f"{year}Q{quarter}", float(fact["val"]))
    return series


def reconcile_xbrl(ir_value: float | None, xbrl_value: float | None, tolerance: float = XBRL_TOLERANCE) -> dict:
    """Flag a mismatch. The IR number does not replace the XBRL number."""
    if ir_value is None or xbrl_value is None:
        return {"status": "uncompared", "delta": None, "xbrl_value": xbrl_value, "ir_value": ir_value}
    base = abs(xbrl_value) if xbrl_value else 1.0
    delta = abs(ir_value - xbrl_value) / base
    status = "mismatch" if delta > tolerance else "match"
    return {"status": status, "delta": delta, "xbrl_value": xbrl_value, "ir_value": ir_value}


def reconcile_rows(ir_rows: list[dict], xbrl_by_key: dict[tuple[str, str], float]) -> list[dict]:
    mismatches = []
    for row in ir_rows:
        key = (row["metric_id"], row["fiscal_period"])
        if key not in xbrl_by_key:
            continue
        result = reconcile_xbrl(row.get("value"), xbrl_by_key[key])
        result.update({"metric_id": row["metric_id"], "fiscal_period": row["fiscal_period"]})
        if result["status"] == "mismatch":
            mismatches.append(result)
    return mismatches


class ExtractionCache:
    """Unchanged documents are not sent to the LLM again."""

    def __init__(self) -> None:
        self.docs: dict[str, list] = {}
        self.llm_calls = 0

    def extract(self, doc_hash: str, deterministic, llm) -> list:
        if doc_hash in self.docs:
            return self.docs[doc_hash]
        parsed = deterministic()
        if parsed is not None:
            self.docs[doc_hash] = parsed
            return parsed
        if not llm_secret_id():
            raise RuntimeError("COMPANY_IR_LLM_SECRET_ID is not set; refusing to call the LLM")
        self.llm_calls += 1
        parsed = llm()
        self.docs[doc_hash] = parsed
        return parsed


def transcripts_permitted(record: dict | None) -> bool:
    """Phase 3 stays closed unless the source is company-hosted or explicitly permitted."""
    if not record or record.get("paywalled"):
        return False
    return bool(record.get("company_hosted") or record.get("explicitly_permitted"))


def earnings_window(earnings_date: date) -> list[date]:
    return [earnings_date + timedelta(days=offset) for offset in range(-3, 6)]


def should_collect(today: date, earnings_dates: list[date], reason: str) -> bool:
    if reason in {"weekly", "edgar-8k", "ticker-added"}:
        return True
    covered = {day for earnings in earnings_dates for day in earnings_window(earnings)}
    return today in covered


def filing_triggers_extraction(form: str, exhibits: list[str], ticker: str, watchlist: set[str]) -> bool:
    symbol = ticker.upper()
    if symbol not in {item.upper() for item in watchlist}:
        return False
    if form.upper() not in {"8-K", "8-K/A"}:
        return False
    return any(item.upper().startswith("EX-99.1") for item in exhibits)


def run_record(*, status: str, sources: dict, errors: list | None = None, mismatches: list | None = None) -> dict:
    if status not in {"ok", "partial", "failed"}:
        raise ValueError(status)
    return {
        "status": status,
        "sources": sources,
        "errors": list(errors or []),
        "mismatches": list(mismatches or []),
    }


def run_needs_alert(run: dict) -> bool:
    return run.get("status") in {"partial", "failed"}


def metrics_frame(rows: list[dict]) -> pl.DataFrame:
    """Curated company_metrics rows. Missing quarters are omitted, never stored as zero."""
    slim = []
    for row in rows:
        if row.get("value") is None:
            continue
        slim.append({column: row[column] for column in PARQUET_COLUMNS})
    if not slim:
        return pl.DataFrame(schema=SCHEMA)
    frame = pl.DataFrame(slim, schema=SCHEMA)
    return frame.unique(subset=["ticker", "metric_id", "fiscal_period"], keep="last").sort(
        ["ticker", "metric_id", "fiscal_period"],
    )


def write_metrics_parquet(frame: pl.DataFrame, path: Path) -> None:
    if list(frame.columns) != PARQUET_COLUMNS:
        raise ValueError(f"company_metrics columns must be {PARQUET_COLUMNS}")
    frame.write_parquet(path, compression="zstd")


def _quarter_index(fiscal_period: str) -> tuple[int, int]:
    match = _QUARTER.fullmatch(fiscal_period)
    if not match:
        return (0, 0)
    return int(match.group(1)), int(match.group(2))


def _shift(fiscal_period: str, quarters: int) -> str:
    year, quarter = _quarter_index(fiscal_period)
    absolute_q = year * 4 + (quarter - 1) - quarters
    return f"{absolute_q // 4}Q{absolute_q % 4 + 1}"


def _fill_gaps(points: list[dict]) -> list[dict]:
    ordered = sorted(points, key=lambda point: _quarter_index(point["fiscal_period"]))
    if not ordered:
        return []
    start = _quarter_index(ordered[0]["fiscal_period"])
    end = _quarter_index(ordered[-1]["fiscal_period"])
    by_period = {point["fiscal_period"]: point for point in ordered}
    filled = []
    year, quarter = start
    while (year, quarter) <= end:
        fiscal_period = f"{year}Q{quarter}"
        current = by_period.get(fiscal_period)
        if current is None:
            filled.append({"fiscal_period": fiscal_period, "value": None, "reported": False})
        else:
            filled.append({
                "fiscal_period": fiscal_period,
                "period_end": current["period_end"].isoformat()
                if isinstance(current["period_end"], date) else current.get("period_end"),
                "value": current["value"],
                "reported": current.get("value") is not None,
            })
        quarter += 1
        if quarter == 5:
            year, quarter = year + 1, 1
    return filled


def _change(latest: dict | None, prior: dict | None) -> float | None:
    if not latest or not prior:
        return None
    if latest.get("value") is None or prior.get("value") in (None, 0):
        return None
    return (latest["value"] - prior["value"]) / abs(prior["value"])


def build_serving(
    ticker: str,
    catalog: dict,
    rows: list[dict],
    *,
    run: dict,
    generated_at: str,
    previous: dict | None = None,
    mismatches: list[dict] | None = None,
) -> dict:
    """Approved metrics only. A partial or failed run keeps the previous good payload."""
    if run.get("status") in {"partial", "failed"} and previous and previous.get("metrics"):
        kept = json.loads(json.dumps(previous))
        kept["run_status"] = run["status"]
        kept["stale"] = True
        kept["generated_at"] = generated_at
        kept["freshness_label"] = "Partial run · previous approved values kept"
        kept["mismatches"] = list(mismatches or run.get("mismatches") or [])
        return kept

    mismatch_index = {
        (item["metric_id"], item["fiscal_period"]): item for item in (mismatches or run.get("mismatches") or [])
    }
    by_metric: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("approved") is not True:
            continue
        entry = catalog_entry(catalog, row["metric_id"])
        if entry is None or entry.get("status") != "approved":
            continue
        by_metric.setdefault(row["metric_id"], []).append(row)

    metrics = []
    ordered = sorted(
        by_metric.items(),
        key=lambda item: (catalog_entry(catalog, item[0]) or {}).get("panel_order", 0),
    )
    for metric_id, observations in ordered:
        entry = catalog_entry(catalog, metric_id)
        series = _fill_gaps(observations)
        reported = [point for point in series if point.get("reported")]
        latest = reported[-1] if reported else None
        prior_q = None
        prior_y = None
        if latest:
            prior_q = next(
                (point for point in series if point["fiscal_period"] == _shift(latest["fiscal_period"], 1)),
                None,
            )
            prior_y = next(
                (point for point in series if point["fiscal_period"] == _shift(latest["fiscal_period"], 4)),
                None,
            )
        latest_row = sorted(observations, key=lambda row: _quarter_index(row["fiscal_period"]))[-1]
        xbrl = mismatch_index.get((metric_id, latest["fiscal_period"])) if latest else None
        if xbrl is None and latest:
            xbrl = {"status": "uncompared", "delta": None}
        metrics.append({
            "metric_id": metric_id,
            "display_name": entry.get("display_name"),
            "unit": entry.get("unit"),
            "category": entry.get("category"),
            "approved": True,
            "approval_state": "approved",
            "latest": latest,
            "qoq": _change(latest, prior_q),
            "yoy": _change(latest, prior_y),
            "series": series,
            "provenance": {
                "source_url": latest_row.get("source_url"),
                "source_title": latest_row.get("source_title") or entry.get("display_name"),
                "source_doc_hash": latest_row.get("source_doc_hash"),
                "published_date": latest_row.get("published_date"),
                "extracted_at": generated_at,
                "confidence": latest_row.get("confidence"),
                "approval_state": "approved",
            },
            "xbrl": xbrl,
        })
    status = run.get("status", "ok")
    return {
        "ticker": ticker.upper(),
        "generated_at": generated_at,
        "run_status": status,
        "stale": status != "ok",
        "freshness_label": "IR data approved" if metrics else "No approved company metrics",
        "discovered": bool(metrics),
        "metrics": metrics,
        "unavailable": [],
        "mismatches": list(mismatches or []),
    }


def public_stock_document(document: dict | None, ticker: str) -> dict:
    """Last gate before the API. Proposed or unapproved metrics are dropped."""
    if not document:
        return {
            "ticker": ticker.upper(),
            "generated_at": None,
            "run_status": "ok",
            "stale": False,
            "freshness_label": "No company-specific metrics discovered",
            "discovered": False,
            "metrics": [],
            "unavailable": [],
            "mismatches": [],
        }
    metrics = []
    for metric in document.get("metrics") or []:
        if metric.get("approved") is not True:
            continue
        if metric.get("approval_state") in {"proposed", "rejected"}:
            continue
        if metric.get("status") in {"proposed", "rejected"}:
            continue
        metrics.append(metric)
    published = dict(document)
    published["ticker"] = ticker.upper()
    published["metrics"] = metrics
    published["discovered"] = bool(metrics)
    return published


def visible_html(payload: dict) -> str:
    """Serving JSON is text. Source markup must not survive into it."""
    return json.dumps(payload)


def plan_collection(event: dict | None, today: date) -> dict:
    """Weekly baseline, earnings window (T-3 through T+5), or an EX-99.1 trigger."""
    event = event or {}
    earnings = [date.fromisoformat(str(item)) for item in event.get("earnings_dates") or []]
    reason = str(event.get("reason") or event.get("source") or "schedule")
    filing = event.get("filing") or {}
    watchlist = {str(item).upper() for item in event.get("watchlist") or []}
    triggered = False
    if filing:
        triggered = filing_triggers_extraction(
            str(filing.get("form") or ""),
            list(filing.get("exhibits") or []),
            str(filing.get("ticker") or ""),
            watchlist,
        )
    if reason == "schedule":
        weekly = today.weekday() == 0
        in_window = should_collect(today, earnings, "schedule")
        collect = weekly or in_window
        label = "weekly" if weekly else "earnings-window" if in_window else "skip"
    else:
        collect = should_collect(today, earnings, reason)
        label = reason if collect else "skip"
    if triggered:
        collect = True
        label = "edgar-8k"
    return {"collect": collect, "extract": triggered, "reason": label}


def collect_handler(event, context):  # pragma: no cover - AWS wrapper
    from observability import job_handler

    @job_handler("Q2C")
    def run(event, context):
        return plan_collection(event, datetime.now(UTC).date())

    return run(event, context)


def extract_handler(event, context):  # pragma: no cover - AWS wrapper
    from observability import job_handler

    @job_handler("Q2X")
    def run(event, context):
        plan = plan_collection(event, datetime.now(UTC).date())
        return {"extracted": 0, "triggered": plan["extract"]}

    return run(event, context)


def serve_handler(event, context):  # pragma: no cover - AWS wrapper
    from aws_lambda_powertools.metrics import MetricUnit

    from observability import job_handler, metrics

    @job_handler("Q2S")
    def run(event, context):
        status = str((event or {}).get("run_status") or "ok")
        if run_needs_alert({"status": status}):
            metrics.add_metric(name="FailedRuns", unit=MetricUnit.Count, value=1)
        return {"served": 0, "alert": run_needs_alert({"status": status})}

    return run(event, context)
