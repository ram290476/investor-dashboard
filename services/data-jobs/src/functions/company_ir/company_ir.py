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
COMPANY_IR_MAX_TICKERS = 25
SEC_MIN_INTERVAL_S = 0.12
FUZZY_LABEL_CUTOFF = 0.92
DISCLOSURE_STATUSES = {"numeric", "narrative", "chart_only", "not_disclosed", "discontinued"}
CATALOG_STATUSES = {"proposed", "approved", "rejected"}
BLOCKED_HOSTS = {"tesla.com", "www.tesla.com"}
SEC_HOSTS = {"www.sec.gov", "data.sec.gov"}
# Operating items: the latest deck beats the press release, and news never wins over an official number.
# GAAP items: XBRL beats the deck, which beats the press release.
SOURCE_PRIORITY = {
    "fundamentals": {"xbrl": 100, "deck": 50, "press_release": 40, "note": 45, "news": 0, "llm": 0},
    "operating": {"deck": 80, "press_release": 70, "xbrl": 40, "note": 30, "news": 0, "llm": 0},
    "guidance": {"deck": 80, "press_release": 70, "note": 30, "news": 0, "llm": 0},
}
# Tags used only to reconcile a deck figure. XBRL wins; a deck value never replaces these.
GAAP_XBRL_TAGS = {
    "revenue_gaap": ("duration", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]),
    "operating_income": ("duration", ["OperatingIncomeLoss"]),
    "net_income_gaap": ("duration", ["NetIncomeLoss"]),
    "research_and_development": ("duration", ["ResearchAndDevelopmentExpense"]),
    "sga": ("duration", ["SellingGeneralAndAdministrativeExpense"]),
    "operating_cash_flow": ("duration", ["NetCashProvidedByUsedInOperatingActivities"]),
    "capex": ("duration", ["PaymentsToAcquirePropertyPlantAndEquipment"]),
    "eps_diluted": ("duration", ["EarningsPerShareDiluted"]),
    "deferred_revenue": ("instant", ["ContractWithCustomerLiability"]),
    # Composed per period in cash_and_investments_series. The retired restricted-cash
    # concept stops at 2018Q3 and must not win just because it has older facts.
    "cash_and_investments": ("instant", []),
}
_FOOTNOTE_TAIL = re.compile(r"(?:\s*[\u00b9\u00b2\u00b3\u2070-\u2079]+|\s*\(\d+\))+\s*$")
_UNIT_PAREN = re.compile(
    r"\s*\((?:[^)]*\b(?:mil(?:lion)?s?|bn|billions?|gwh|mwh|mw|%))[^)]*\)",
    re.IGNORECASE,
)
_QSHORT = re.compile(r"^Q([1-4])[\s'\-]*(\d{2}|\d{4})$", re.IGNORECASE)
_YOY_HEADER = {"yoy", "y/y", "year over year", "change", "vs prior year"}
_STORAGE_GWH = re.compile(r"(\d+(?:\.\d+)?)\s*GWh of energy storage", re.IGNORECASE)


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


def normalize_label(text: str) -> str:
    """Lowercase a row label and drop footnote markers and unit parentheticals."""
    cleaned = _FOOTNOTE_TAIL.sub("", str(text).replace("_", " "))
    cleaned = _UNIT_PAREN.sub("", cleaned)
    cleaned = cleaned.replace("%", " ")
    return " ".join(cleaned.lower().split())


def scale_from_label(label: str) -> tuple[float, str | None]:
    """`(mil)` means x1e6. A unit token such as GWh sets the unit and does not scale."""
    match = _UNIT_PAREN.search(str(label))
    if not match:
        return 1.0, None
    token = match.group(0).lower()
    if "gwh" in token:
        return 1.0, "GWh"
    if "mwh" in token:
        return 1.0, "MWh"
    if re.search(r"\bmw\b", token):
        return 1.0, "MW"
    if "bn" in token or "billion" in token:
        return 1_000_000_000.0, None
    if "mil" in token:
        return 1_000_000.0, None
    if "%" in token:
        return 1.0, "ratio"
    return 1.0, None


def table_unit_scale(headers: list[str]) -> float:
    """A financial summary caption such as ``($ in millions)`` scales every value in that table."""
    blob = " ".join(str(header) for header in headers).lower()
    if "in billions" in blob or "($ b" in blob:
        return 1_000_000_000.0
    if "in millions" in blob or "$ in millions" in blob or "($ m" in blob or "(mil)" in blob:
        return 1_000_000.0
    return 1.0


def apply_scale(value: float, label: str, entry: dict, table_scale: float = 1) -> tuple[float, str | None]:
    """Scale a reported figure into base units.

    A table caption or a ``(mil)`` label always scales. The catalog scale applies to small
    reported figures such as 1.48 million subscribers, and leaves a base-unit count alone.
    """
    label_scale, unit_hint = scale_from_label(label)
    if table_scale != 1:
        scale = table_scale
    elif label_scale != 1:
        scale = label_scale
    else:
        scale = float(entry.get("scale") or 1)
        if scale != 1 and abs(value) >= 1000:
            scale = 1
    if scale != 1:
        value *= scale
    return value, unit_hint


def _alias_index(catalog: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for entry in catalog.get("metrics", []):
        names = [entry.get("display_name", ""), entry.get("metric_id", ""), *(entry.get("aliases") or [])]
        for name in names:
            key = normalize_label(str(name))
            if key:
                index[key] = entry
    return index


def match_catalog_entry(label: str, catalog: dict) -> dict | None:
    """Exact normalized alias, then a high fuzzy cutoff so nearby labels are not merged."""
    index = _alias_index(catalog)
    key = normalize_label(label)
    if not key:
        return None
    if key in index:
        return index[key]
    import difflib

    found = difflib.get_close_matches(key, index.keys(), n=1, cutoff=FUZZY_LABEL_CUTOFF)
    return index[found[0]] if found else None


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
    value, _lower = _parse_cell(text)
    return value


def _parse_cell(text: str) -> tuple[float | None, bool]:
    """Parse one cell. `>550,000` keeps the number and a lower-bound flag. `—` is null."""
    cleaned = " ".join(str(text).replace("\u00a0", " ").replace("\u2013", "-").replace("\u2014", "-").split()).strip()
    lower_bound = cleaned.startswith(">")
    if lower_bound:
        cleaned = cleaned[1:].strip()
    if cleaned.lower() in _MISSING:
        return None, lower_bound
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    percent = "%" in cleaned
    cleaned = cleaned.strip("()").replace(",", "").replace("$", "").replace("%", "").strip()
    multiplier = 1.0
    suffix = cleaned[-1:].lower()
    if suffix in {"b", "m", "k"} and cleaned[:-1].replace(".", "", 1).isdigit():
        multiplier = {"b": 1_000_000_000, "m": 1_000_000, "k": 1_000}[suffix]
        cleaned = cleaned[:-1]
    try:
        value = float(cleaned) * multiplier
    except ValueError:
        return None, lower_bound
    if negative:
        value = -value
    if percent:
        value = value / 100.0
    return value, lower_bound


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


def quarter_token(header: str) -> str | None:
    """`2026Q3` and `Q3-26` are the same fiscal period. A YoY column is not a period."""
    text = " ".join(str(header).replace("'", " ").split())
    if text.lower() in _YOY_HEADER:
        return None
    compact = text.upper().replace(" ", "")
    if _QUARTER.fullmatch(compact):
        return compact
    match = _QSHORT.fullmatch(text.strip())
    if not match:
        return None
    year = int(match.group(2))
    if year < 100:
        year += 2000
    return f"{year}Q{match.group(1)}"


def _quarter_headers(headers: list[str]) -> list[tuple[int, str]]:
    found = []
    for index, header in enumerate(headers):
        token = quarter_token(header)
        if token:
            found.append((index, token))
    return found


def _observation(
    entry: dict,
    *,
    ticker: str,
    fiscal_period: str,
    value: float,
    unit: str,
    source_url: str,
    source_doc_hash: str,
    extracted_at: datetime,
    published_date: str | None,
    method: str,
    confidence: float,
    lower_bound: bool,
    source_kind: str,
    label: str,
) -> dict:
    return {
        "ticker": ticker.upper(),
        "metric_id": entry["metric_id"],
        "period_end": period_end_for(fiscal_period),
        "fiscal_period": fiscal_period,
        "value": value,
        "unit": unit,
        "source_url": source_url,
        "source_doc_hash": source_doc_hash,
        "extracted_at": extracted_at,
        "confidence": confidence,
        "approved": row_approved(entry.get("status", "proposed"), method),
        "method": method,
        "published_date": published_date,
        "display_name": entry.get("display_name"),
        "category": entry.get("category") or "operating",
        "catalog_status": entry.get("status", "proposed"),
        "lower_bound": lower_bound,
        "source_kind": source_kind,
        "label": label,
        "accession": None,
        "footnote": None,
    }


def table_scan(
    tables: list[list[list[str]]],
    catalog: dict,
    *,
    ticker: str,
    source_url: str,
    source_doc_hash: str,
    extracted_at: datetime,
    published_date: str | None,
    method: str = "table",
    source_kind: str = "deck",
) -> tuple[list[dict], list[dict]]:
    """Split a quarter table into catalog matches and proposed labels.

    Each data row is a label, then one cell per quarter header, then an ignored YoY column.
    """
    confidence = confidence_for(method)
    rows: list[dict] = []
    proposals: list[dict] = []
    seen_proposals: set[str] = set()
    for table in tables:
        if not table:
            continue
        quarters = _quarter_headers(table[0])
        if not quarters:
            continue
        reported_scale = table_unit_scale(table[0])
        for cells in table[1:]:
            if not cells or not str(cells[0]).strip():
                continue
            raw_label = str(cells[0]).strip()
            entry = match_catalog_entry(raw_label, catalog)
            label_scale, unit_hint = scale_from_label(raw_label)
            parsed_any = False
            for index, fiscal_period in quarters:
                if index >= len(cells):
                    continue
                value, lower_bound = _parse_cell(cells[index])
                if value is None:
                    continue
                parsed_any = True
                if entry is None:
                    continue
                scaled, hinted = apply_scale(value, raw_label, entry, reported_scale)
                unit = entry.get("unit") or hinted or unit_hint or ""
                if unit == "ratio" and abs(scaled) > 1:
                    scaled = scaled / 100.0
                rows.append(_observation(
                    entry,
                    ticker=ticker,
                    fiscal_period=fiscal_period,
                    value=scaled,
                    unit=unit,
                    source_url=source_url,
                    source_doc_hash=source_doc_hash,
                    extracted_at=extracted_at,
                    published_date=published_date,
                    method=method,
                    confidence=confidence,
                    lower_bound=lower_bound,
                    source_kind=source_kind,
                    label=raw_label,
                ))
            if entry is None and parsed_any and method != "llm":
                key = normalize_label(raw_label)
                if key and key not in seen_proposals:
                    seen_proposals.add(key)
                    proposals.append(propose_metric(
                        raw_label,
                        unit=unit_hint or "",
                        scale=label_scale,
                        fiscal_period=quarters[-1][1],
                        source=source_url,
                        lower_bound=any(
                            _parse_cell(cells[index])[1] for index, _period in quarters if index < len(cells)
                        ),
                    ))
    return rows, proposals


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
    source_kind: str = "deck",
) -> list[dict]:
    rows, _proposals = table_scan(
        tables,
        catalog,
        ticker=ticker,
        source_url=source_url,
        source_doc_hash=source_doc_hash,
        extracted_at=extracted_at,
        published_date=published_date,
        method=method,
        source_kind=source_kind,
    )
    return rows


def parse_ex991_html(html: str, catalog: dict, **kwargs) -> list[dict]:
    return observations_from_tables(html_tables(html), catalog, method="table", **kwargs)


def parse_deck_text(text: str, catalog: dict, **kwargs) -> list[dict]:
    return observations_from_tables(text_tables(text), catalog, method="table", **kwargs)


def _fiscal_period(end: date, fiscal_end_month: int) -> str:
    year = end.year + int(end.month > fiscal_end_month)
    quarter = ((end.month - fiscal_end_month - 1) % 12) // 3 + 1
    return f"{year}Q{quarter}"


def _is_scale_outlier(value: float, peers: list[float]) -> bool:
    """A fact 100x away from its neighbours is a scale error, not a revision."""
    positives = [abs(item) for item in peers if item]
    if not positives:
        return False
    ordered = sorted(positives)
    median = ordered[len(ordered) // 2]
    if median == 0:
        return False
    return abs(value) < median / 100 or abs(value) > median * 100


def _select_sane_latest(by_period: dict[str, list[tuple[str, float]]]) -> tuple[dict[str, float], list[dict]]:
    """Keep the latest filing for each period, walking back past an obvious scale error."""
    latest = {period: facts[-1][1] for period, facts in by_period.items() if facts}
    selected: dict[str, float] = {}
    flags: list[dict] = []
    for period, facts in by_period.items():
        peers = [value for other, value in latest.items() if other != period]
        chosen = None
        for filed, value in reversed(facts):
            if _is_scale_outlier(value, peers):
                flags.append({
                    "fiscal_period": period,
                    "value": value,
                    "filed": filed,
                    "reason": "scale_error",
                })
                continue
            chosen = value
            break
        if chosen is not None:
            selected[period] = chosen
    return selected, flags


def _collect_xbrl_facts(facts: list[dict], fiscal_end_month: int, kind: str) -> dict[str, list[tuple[str, float]]]:
    grouped: dict[str, list[tuple[str, float]]] = {}
    for fact in sorted(facts, key=lambda item: item.get("filed", "")):
        if fact.get("form") not in {"10-Q", "10-K", "10-Q/A", "10-K/A"}:
            continue
        if "end" not in fact or "val" not in fact:
            continue
        end = date.fromisoformat(fact["end"])
        start = fact.get("start")
        if kind == "duration":
            if not start:
                continue
            days = (end - date.fromisoformat(start)).days
            if not 80 <= days <= 100:
                continue
        else:
            if start and start != fact["end"]:
                days = (end - date.fromisoformat(start)).days
                if days > 5:
                    continue
        period = _fiscal_period(end, fiscal_end_month)
        grouped.setdefault(period, []).append((str(fact.get("filed") or ""), float(fact["val"])))
    return grouped


def parse_xbrl_quarters(facts: list[dict], fiscal_end_month: int = 12) -> dict[str, float]:
    """Map fiscal quarter to the latest sane 10-Q/10-K duration value. Q4 is not inferred here."""
    selected, _flags = _select_sane_latest(_collect_xbrl_facts(facts, fiscal_end_month, "duration"))
    return selected


def parse_xbrl_instants(facts: list[dict], fiscal_end_month: int = 12) -> dict[str, float]:
    """Balance-sheet instants at a quarter end. Scale errors are dropped, not served."""
    selected, _flags = _select_sane_latest(_collect_xbrl_facts(facts, fiscal_end_month, "instant"))
    return selected


def xbrl_scale_flags(facts: list[dict], fiscal_end_month: int = 12, kind: str = "instant") -> list[dict]:
    _selected, flags = _select_sane_latest(_collect_xbrl_facts(facts, fiscal_end_month, kind))
    return flags


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


def slug_metric(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", normalize_label(label)).strip("_")
    return (slug or "metric")[:64]


def propose_metric(
    label: str,
    *,
    unit: str,
    scale: float,
    fiscal_period: str,
    source: str,
    lower_bound: bool = False,
) -> dict:
    """A new table label enters the registry as proposed. Nothing here is approved."""
    return {
        "metric_id": slug_metric(label),
        "display_name": _FOOTNOTE_TAIL.sub("", str(label)).strip(),
        "aliases": [str(label).strip()],
        "unit": unit or ("vehicles" if lower_bound else ""),
        "scale": scale if scale != 1 else 1,
        "definition": "Discovered from a company filing table. A person approves the name, unit and aliases.",
        "category": "operating",
        "status": "proposed",
        "approved_by": None,
        "approved_at": None,
        "disclosure_status": "numeric",
        "source_priority": ["deck", "press_release"],
        "first_seen": {"period": fiscal_period, "source": source},
        "last_seen": fiscal_period,
        "lower_bound": lower_bound,
    }


def merge_proposals(catalog: dict, proposals: list[dict]) -> dict:
    """Add unknown labels. Never change a status a person already set."""
    updated = json.loads(json.dumps(catalog))
    updated.setdefault("metrics", [])
    known = {item.get("metric_id") for item in updated["metrics"]}
    aliases = _alias_index(updated)
    for proposal in proposals:
        if proposal.get("metric_id") in known:
            continue
        if normalize_label(proposal.get("display_name") or "") in aliases:
            continue
        proposal["status"] = "proposed"
        proposal["approved_by"] = None
        proposal["approved_at"] = None
        updated["metrics"].append(proposal)
        known.add(proposal["metric_id"])
    return updated


def validate_catalog(document: dict) -> list[str]:
    """Approved entries need a named reviewer. Proposed entries are allowed to wait."""
    errors = []
    if not str(document.get("ticker") or "").strip():
        errors.append("ticker is required")
    seen: set[str] = set()
    for item in document.get("metrics") or []:
        metric_id = str(item.get("metric_id") or "")
        if not metric_id:
            errors.append("metric_id is required")
            continue
        if metric_id in seen:
            errors.append(f"duplicate metric_id {metric_id}")
        seen.add(metric_id)
        status = item.get("status")
        if status not in CATALOG_STATUSES:
            errors.append(f"{metric_id} has unsupported status {status!r}")
        if status == "approved" and not str(item.get("approved_by") or "").strip():
            errors.append(f"{metric_id} is approved without approved_by")
        disclosure = item.get("disclosure_status")
        if disclosure is not None and disclosure not in DISCLOSURE_STATUSES:
            errors.append(f"{metric_id} has unsupported disclosure_status {disclosure!r}")
        if not isinstance(item.get("aliases") or [], list):
            errors.append(f"{metric_id} aliases must be a list")
    return errors


def classify_exhibit_title(title: str) -> str:
    """Both deliveries and the earnings deck are Item 2.02. The exhibit title separates them."""
    text = " ".join(str(title or "").lower().split())
    if "production" in text and "deliver" in text:
        return "press_release"
    if re.search(r"q[1-4]\s*\d{2,4}\s+update", text) or "quarterly update" in text or text.endswith(" update"):
        return "deck"
    if "update" in text and re.search(r"q[1-4]", text):
        return "deck"
    return "other"


def title_from_html(html: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    if match:
        return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split())
    match = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html or "")
    if match:
        return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split())
    return ""


def fiscal_period_for_end(end: date, fiscal_end_month: int = 12) -> str:
    return _fiscal_period(end, fiscal_end_month)


def exhibits_from_index(payload: dict) -> list[dict]:
    """EX-99.1 rows from an EDGAR filing index.json directory listing."""
    items = (payload.get("directory") or {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    found = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        kind = str(item.get("type") or item.get("description") or "")
        description = str(item.get("description") or item.get("type") or name)
        exhibit = kind.upper().replace(" ", "")
        if exhibit.startswith("EX-99.1") or re.search(r"ex[-_]?99\.?1", name, re.IGNORECASE):
            found.append({"name": name, "type": kind or "EX-99.1", "description": description})
    return found


def archive_url(cik: str, accession: str, name: str) -> str:
    bare = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{bare}/{name}"


def filing_index_url(cik: str, accession: str) -> str:
    """Directory index. `{accession}-index.json` 404s; the filing folder serves `index.json`."""
    return archive_url(cik, accession, "index.json")


def item_202_filings(submissions: dict, *, limit: int = 4) -> list[dict]:
    """Latest 8-K Item 2.02 filings. The exhibit list comes from the filing index, not the item code."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    cik = str(submissions.get("cik") or "0").zfill(10)
    rows = []
    for index, form in enumerate(forms):
        if str(form).upper() not in {"8-K", "8-K/A"}:
            continue
        raw_items = (recent.get("items") or [""] * len(forms))[index] or ""
        items = [part.strip() for part in str(raw_items).split(",") if part.strip()]
        if "2.02" not in items:
            continue
        accession = (recent.get("accessionNumber") or [""] * len(forms))[index]
        if not accession:
            continue
        rows.append({
            "accession": accession,
            "cik": cik,
            "form": str(form),
            "filed": (recent.get("filingDate") or [""] * len(forms))[index],
            "report_date": (recent.get("reportDate") or [""] * len(forms))[index] or None,
            "items": items,
            "primary_document": (recent.get("primaryDocument") or [""] * len(forms))[index],
        })
    rows.sort(key=lambda row: row.get("filed") or "", reverse=True)
    return rows[:limit]


def latest_periodic_filings(submissions: dict) -> list[dict]:
    """The newest 10-Q and 10-K primary documents, for the raw layer."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    cik = str(submissions.get("cik") or "0").zfill(10)
    chosen: dict[str, dict] = {}
    for index, form in enumerate(forms):
        kind = str(form).upper()
        if kind not in {"10-Q", "10-K"} or kind in chosen:
            continue
        accession = (recent.get("accessionNumber") or [""] * len(forms))[index]
        document = (recent.get("primaryDocument") or [""] * len(forms))[index]
        if not accession or not document:
            continue
        chosen[kind] = {
            "accession": accession,
            "cik": cik,
            "form": kind,
            "filed": (recent.get("filingDate") or [""] * len(forms))[index],
            "report_date": (recent.get("reportDate") or [""] * len(forms))[index] or None,
            "primary_document": document,
        }
    return list(chosen.values())


def parse_deliveries_matrix(
    table: list[list[str]], fiscal_period: str, catalog: dict, **kwargs,
) -> tuple[list[dict], list[dict]]:
    """One-quarter production / deliveries grid from the press release, not the five-quarter deck."""
    if not table or not fiscal_period:
        return [], []
    headers = [normalize_label(cell) for cell in table[0]]
    columns: dict[int, str] = {}
    for index, header in enumerate(headers):
        if "production" in header:
            columns[index] = "production"
        elif "deliver" in header:
            columns[index] = "deliveries"
    if not columns:
        return [], []
    rows: list[dict] = []
    proposals: list[dict] = []
    for cells in table[1:]:
        if not cells:
            continue
        for index, kind in columns.items():
            if index >= len(cells):
                continue
            raw_label = f"{cells[0]} {kind}"
            value, lower_bound = _parse_cell(cells[index])
            if value is None or "%" in str(cells[index]):
                continue
            entry = match_catalog_entry(raw_label, catalog)
            if entry is None:
                proposals.append(propose_metric(
                    raw_label, unit="vehicles", scale=1, fiscal_period=fiscal_period,
                    source=kwargs.get("source_url") or "", lower_bound=lower_bound,
                ))
                continue
            scaled, _hint = apply_scale(value, raw_label, entry)
            rows.append(_observation(
                entry,
                ticker=kwargs["ticker"],
                fiscal_period=fiscal_period,
                value=scaled,
                unit=entry.get("unit") or "vehicles",
                source_url=kwargs["source_url"],
                source_doc_hash=kwargs["source_doc_hash"],
                extracted_at=kwargs["extracted_at"],
                published_date=kwargs.get("published_date"),
                method="table",
                confidence=confidence_for("table"),
                lower_bound=lower_bound,
                source_kind=kwargs.get("source_kind") or "press_release",
                label=raw_label,
            ))
    return rows, proposals


def storage_observation(text: str, fiscal_period: str, catalog: dict, **kwargs) -> dict | None:
    """The deliveries release states storage deployed in a sentence, not the quarterly grid."""
    if not fiscal_period:
        return None
    match = _STORAGE_GWH.search(text or "")
    if not match:
        return None
    entry = match_catalog_entry("Storage deployed", catalog)
    if entry is None:
        return None
    return _observation(
        entry,
        ticker=kwargs["ticker"],
        fiscal_period=fiscal_period,
        value=float(match.group(1)),
        unit=entry.get("unit") or "GWh",
        source_url=kwargs["source_url"],
        source_doc_hash=kwargs["source_doc_hash"],
        extracted_at=kwargs["extracted_at"],
        published_date=kwargs.get("published_date"),
        method="table",
        confidence=confidence_for("table"),
        lower_bound=False,
        source_kind=kwargs.get("source_kind") or "press_release",
        label="Storage deployed",
    )


def narrative_candidates(text: str, *, enabled: bool) -> list[dict]:
    """Low-confidence candidates from prose. They are never approved, and they stay off without a secret."""
    if not enabled:
        return []
    found = []
    attach = re.search(r">\s*(\d+(?:\.\d+)?)\s*%[^.\n]{0,80}deliver", text or "", re.IGNORECASE)
    if attach:
        found.append({
            "metric_id": "fsd_attach_rate",
            "label": "FSD attach rate",
            "value": float(attach.group(1)) / 100.0,
            "unit": "ratio",
            "lower_bound": True,
            "method": "llm",
            "confidence": CONFIDENCE["llm"],
            "source_kind": "llm",
        })
    metros = re.search(r"live in (seven|\d+) major metros", text or "", re.IGNORECASE)
    if metros:
        words = {"seven": 7}
        raw = metros.group(1).lower()
        found.append({
            "metric_id": "robotaxi_metros",
            "label": "Robotaxi metros",
            "value": float(words.get(raw, raw)),
            "unit": "metros",
            "lower_bound": False,
            "method": "llm",
            "confidence": CONFIDENCE["llm"],
            "source_kind": "llm",
        })
    return found


def parse_company_document(text: str, catalog: dict, **kwargs) -> dict:
    """Press-release grids, five-quarter summary tables, and optional low-confidence narrative candidates."""
    tables = html_tables(text) if "<table" in text.lower() or "<tr" in text.lower() else []
    flowing = text_tables(text)
    if flowing:
        tables = [*tables, *flowing]
    source_kind = kwargs.get("source_kind") or "deck"
    rows, proposals = table_scan(tables, catalog, source_kind=source_kind, method=kwargs.get("method") or "table", **{
        key: kwargs[key] for key in (
            "ticker", "source_url", "source_doc_hash", "extracted_at", "published_date",
        )
    })
    period = kwargs.get("fiscal_period")
    matrix_kwargs = {key: value for key, value in kwargs.items() if key != "fiscal_period"}
    if period and source_kind == "press_release":
        for table in tables:
            extra, extra_proposals = parse_deliveries_matrix(table, period, catalog, **matrix_kwargs)
            rows.extend(extra)
            proposals.extend(extra_proposals)
        stored = storage_observation(text, period, catalog, **matrix_kwargs)
        if stored:
            rows.append(stored)
    if kwargs.get("llm_enabled") and period:
        for candidate in narrative_candidates(text, enabled=True):
            entry = match_catalog_entry(candidate["label"], catalog) or {
                "metric_id": candidate["metric_id"],
                "display_name": candidate["label"],
                "unit": candidate["unit"],
                "status": "proposed",
                "category": "operating",
            }
            rows.append(_observation(
                entry,
                ticker=kwargs["ticker"],
                fiscal_period=period,
                value=candidate["value"],
                unit=candidate["unit"],
                source_url=kwargs["source_url"],
                source_doc_hash=kwargs["source_doc_hash"],
                extracted_at=kwargs["extracted_at"],
                published_date=kwargs.get("published_date"),
                method="llm",
                confidence=CONFIDENCE["llm"],
                lower_bound=candidate["lower_bound"],
                source_kind="llm",
                label=candidate["label"],
            ))
    for row in rows:
        row["accession"] = kwargs.get("accession")
    return {"rows": rows, "proposals": proposals}


def _priority(row: dict) -> tuple:
    category = row.get("category") or "operating"
    table = SOURCE_PRIORITY.get(category) or SOURCE_PRIORITY["operating"]
    kind = row.get("source_kind") or "deck"
    published = str(row.get("published_date") or "")
    extracted = row.get("extracted_at")
    stamp = extracted.isoformat() if isinstance(extracted, datetime) else str(extracted or "")
    return (table.get(kind, 0), published, stamp)


def dedupe_observations(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """One winner per (ticker, metric_id, fiscal_period). A changed later value records supersedes."""
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if row.get("value") is None:
            continue
        key = (row.get("ticker"), row.get("metric_id"), row.get("fiscal_period"))
        groups.setdefault(key, []).append(row)
    winners = []
    revisions = []
    for key, group in groups.items():
        official = [
            row for row in group
            if (row.get("source_kind") or "") not in {"news", "llm"} and row.get("method") != "llm"
        ]
        pool = official or group
        pool = sorted(pool, key=_priority)
        winner = dict(pool[-1])
        # News and LLM rows never become the served value when an official number exists.
        if official:
            winner["approved"] = row_approved(
                winner.get("catalog_status") or "proposed", winner.get("method") or "table",
            )
        else:
            winner["approved"] = False
        previous = [row for row in pool[:-1] if row.get("value") != winner.get("value")]
        if previous:
            prior = previous[-1]
            winner["supersedes"] = prior.get("source_doc_hash")
            winner["revision_reason"] = (
                prior.get("footnote") or winner.get("footnote") or "later filing restated the quarter"
            )
            revisions.append({
                "ticker": key[0],
                "metric_id": key[1],
                "fiscal_period": key[2],
                "value": winner.get("value"),
                "supersedes": winner["supersedes"],
                "previous_value": prior.get("value"),
                "reason": winner["revision_reason"],
                "source_doc_hash": winner.get("source_doc_hash"),
            })
        winners.append(winner)
    return winners, revisions


def _xbrl_tag_series(gaap: dict, tag: str, kind: str, unit: str = "USD") -> tuple[dict[str, float], list[dict]]:
    facts = (gaap.get(tag) or {}).get("units", {}).get(unit) or []
    if not facts:
        return {}, []
    return _select_sane_latest(_collect_xbrl_facts(facts, 12, kind))


def cash_and_investments_series(gaap: dict) -> tuple[dict[str, float], list[dict]]:
    """Cash at carrying value plus one investments concept, then older single-concept fallbacks.

    Tesla's current balance sheet tags cash and short-term investments separately.
    Short-term investments win over marketable securities for the same quarter so a
    concept that was filed twice is not added twice. Periods with neither current
    concept fall back to the restricted-cash total, then the legacy combined tag.
    """
    cash, flags = _xbrl_tag_series(gaap, "CashAndCashEquivalentsAtCarryingValue", "instant")
    short_term, short_flags = _xbrl_tag_series(gaap, "ShortTermInvestments", "instant")
    marketable, market_flags = _xbrl_tag_series(gaap, "MarketableSecuritiesCurrent", "instant")
    flags.extend(short_flags)
    flags.extend(market_flags)
    selected: dict[str, float] = {}
    for period, value in cash.items():
        extra = short_term.get(period)
        if extra is None:
            extra = marketable.get(period)
        selected[period] = value + (extra or 0.0)
    for tag in (
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashCashEquivalentsAndShortTermInvestments",
    ):
        series, tag_flags = _xbrl_tag_series(gaap, tag, "instant")
        flags.extend(tag_flags)
        for period, value in series.items():
            selected.setdefault(period, value)
    return selected, flags


def companyfacts_observations(companyfacts: dict, catalog: dict, **kwargs) -> tuple[list[dict], list[dict]]:
    """XBRL observations for GAAP catalog rows, with scale-error flags kept off the winning value."""
    gaap = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    rows = []
    flags = []
    ticker = kwargs["ticker"]
    for metric_id, (kind, tags) in GAAP_XBRL_TAGS.items():
        entry = catalog_entry(catalog, metric_id)
        if entry is None:
            continue
        if metric_id == "cash_and_investments":
            selected, scale_flags = cash_and_investments_series(gaap)
        else:
            facts = []
            for tag in tags:
                unit_key = "USD/shares" if metric_id == "eps_diluted" else "USD"
                facts = (gaap.get(tag) or {}).get("units", {}).get(unit_key) or []
                if facts:
                    break
            if not facts:
                continue
            grouped = _collect_xbrl_facts(facts, 12, kind)
            selected, scale_flags = _select_sane_latest(grouped)
        for flag in scale_flags:
            flag["metric_id"] = metric_id
            flag["ticker"] = ticker.upper()
            flags.append(flag)
        for fiscal_period, value in selected.items():
            rows.append(_observation(
                entry,
                ticker=ticker,
                fiscal_period=fiscal_period,
                value=float(value),
                unit=entry.get("unit") or "USD",
                source_url=kwargs.get("source_url") or "",
                source_doc_hash=kwargs.get("source_doc_hash") or "",
                extracted_at=kwargs["extracted_at"],
                published_date=kwargs.get("published_date"),
                method="table",
                confidence=confidence_for("table"),
                lower_bound=False,
                source_kind="xbrl",
                label=metric_id,
            ))
    return rows, flags


def xbrl_value_map(rows: list[dict]) -> dict[tuple[str, str], float]:
    found = {}
    for row in rows:
        if row.get("source_kind") == "xbrl":
            found[(row["metric_id"], row["fiscal_period"])] = row["value"]
    return found


def company_ir_tickers(tickers: list[str], cap: int = COMPANY_IR_MAX_TICKERS) -> list[str]:
    """Watchlist order, capped. TSLA is the reference catalog, not a special collector path."""
    chosen = []
    for ticker in tickers:
        symbol = str(ticker or "").upper().strip()
        if not symbol or symbol in chosen:
            continue
        chosen.append(symbol)
        if len(chosen) >= cap:
            break
    return chosen


def unwrap_job_event(event: dict | None) -> dict:
    """Accept a bare handler payload or an EventBridge envelope."""
    if not isinstance(event, dict):
        return {}
    detail = event.get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            detail = None
    if isinstance(detail, dict) and (event.get("detail-type") or detail.get("job") or detail.get("ticker")):
        merged = dict(detail)
        if event.get("detail-type"):
            merged["detail-type"] = event["detail-type"]
        return merged
    return dict(event)


def collection_plan(event: dict | None, today: date) -> dict:
    """Schedule, ticker-added, or an H3 8-K Item 2.02. An hourly H3 run with no new filing does not collect."""
    raw = event or {}
    flat = unwrap_job_event(raw)
    if flat.get("detail-type") == "TickerAdded" or raw.get("detail-type") == "TickerAdded":
        ticker = str(flat.get("ticker") or "").upper()
        return {
            "collect": True,
            "extract": False,
            "reason": "ticker-added",
            "tickers": [ticker] if ticker else [],
        }
    if flat.get("job") == "H3":
        filing = flat.get("filing") or {}
        ticker = str(filing.get("ticker") or "").upper()
        watch = {ticker} if ticker else set()
        triggered = bool(ticker) and filing_triggers_extraction(
            str(filing.get("form") or ""),
            list(filing.get("exhibits") or []),
            ticker,
            watch,
        )
        if triggered:
            return {"collect": True, "extract": True, "reason": "edgar-8k", "tickers": [ticker]}
        return {"collect": False, "extract": False, "reason": "skip", "tickers": []}
    plan = plan_collection(flat, today)
    explicit = [str(item).upper() for item in flat.get("watchlist") or flat.get("tickers") or []]
    plan["tickers"] = explicit
    return plan


class Pace:
    """Keep SEC calls under 10 requests per second. Other hosts are not delayed."""

    def __init__(self, min_interval: float = SEC_MIN_INTERVAL_S, sleep=None, clock=None) -> None:
        import time

        self.min_interval = min_interval
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self._next = 0.0

    def wait(self, host: str) -> None:
        if host not in SEC_HOSTS or self.min_interval <= 0:
            return
        now = self.clock()
        if now < self._next:
            self.sleep(self._next - now)
            now = self.clock()
        self._next = now + self.min_interval


def decide_ir_fetch(url: str, robots_txt: str | None, user_agent: str, status_code: int | None = None) -> dict:
    """EDGAR is primary. tesla.com is never fetched. ir.tesla.com HTML is not a fallback; a PDF is, after robots.txt."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in BLOCKED_HOSTS:
        return {"action": "skip", "retry": False, "store": False, "reason": "blocked_host"}
    path_lower = parsed.path.lower()
    if host == "ir.tesla.com" and not path_lower.endswith(".pdf") and not path_lower.endswith("/robots.txt"):
        return {"action": "skip", "retry": False, "store": False, "reason": "html_not_a_fallback"}
    path = parsed.path or "/"
    if robots_txt is not None and not robots_allows(robots_txt, user_agent, path):
        return {"action": "skip", "retry": False, "store": False, "reason": "robots"}
    if status_code is None:
        return {"action": "fetch", "retry": False, "store": False, "reason": "ok"}
    return fetch_decision(status_code)


def merge_manifest(existing: dict | None, entry: dict) -> dict:
    docs = [doc for doc in (existing or {}).get("documents") or [] if doc.get("sha256") != entry.get("sha256")]
    docs.append(entry)
    return {"ticker": entry.get("ticker"), "period": entry.get("period"), "documents": docs}


def curated_parquet_key(ticker: str) -> str:
    return f"curated/company_metrics/ticker={ticker.upper()}/company_metrics.parquet"


def curated_observations_key(ticker: str) -> str:
    return f"curated/company_metrics/ticker={ticker.upper()}/observations.json"


def curated_proposals_key(ticker: str) -> str:
    return f"curated/company_metrics/ticker={ticker.upper()}/proposals.json"


def curated_revisions_key(ticker: str) -> str:
    return f"curated/company_metrics/ticker={ticker.upper()}/revisions.json"


def serving_stock_key(ticker: str) -> str:
    return f"serving/stock/{ticker.upper()}.json"


def _jsonable(row: dict) -> dict:
    copied = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            copied[key] = value.isoformat()
        elif isinstance(value, date):
            copied[key] = value.isoformat()
        else:
            copied[key] = value
    return copied


def _source_kind_for_title(title: str) -> str:
    kind = classify_exhibit_title(title)
    if kind == "press_release":
        return "press_release"
    if kind == "deck":
        return "deck"
    return "other"


def run_collect(
    event: dict | None,
    today: date,
    *,
    fetch,
    exists,
    put_bytes,
    put_json,
    read_json,
    tickers: list[str],
    pace: Pace | None = None,
    user_agent: str | None = None,
    extracted_at: str | None = None,
) -> dict:
    """Fetch EDGAR submissions, Item 2.02 EX-99.1, periodic filings and companyfacts. IR PDFs are a fallback."""
    plan = collection_plan(event, today)
    if not plan["collect"]:
        return {
            "status": "skipped",
            "reason": plan["reason"],
            "collected": 0,
            "documents": 0,
            "skipped": 0,
            "tickers": [],
            "manifests": [],
            "failed_source_ids": [],
            "reasons": [],
        }
    agent = user_agent or crawler_user_agent()
    limiter = pace or Pace(0)
    targets = company_ir_tickers(plan["tickers"] or tickers)
    manifests = []
    written = 0
    documents = 0
    skipped = 0
    failed = []
    reasons = []
    stopped: set[str] = set()
    stamp = extracted_at or datetime.now(UTC).isoformat(timespec="seconds")

    def host_of(url: str) -> str:
        from urllib.parse import urlparse

        return (urlparse(url).hostname or "").lower()

    def pull(url: str, source_id: str):
        host = host_of(url)
        if host in stopped:
            reasons.append({"source_id": source_id, "reason": "provider_policy", "url": url})
            return None
        limiter.wait(host)
        response = fetch(url, {"User-Agent": agent, "Accept": "application/json,text/html,*/*"})
        decision = fetch_decision(response.status_code)
        if decision["action"] == "stop":
            stopped.add(host)
            failed.append(source_id)
            reasons.append({"source_id": source_id, "reason": decision["reason"], "url": url})
            return None
        if not decision["store"]:
            reasons.append({"source_id": source_id, "reason": decision["reason"], "url": url})
            if decision["reason"] != "ok":
                failed.append(source_id)
            return None
        return response

    def save(
        ticker: str,
        period: str,
        url: str,
        body: bytes,
        content_type: str,
        source_kind: str,
        accession: str,
        published: str | None,
    ):
        nonlocal written, documents, skipped
        stored = store_raw_document(
            ticker=ticker,
            period=period,
            url=url,
            body=body,
            fetched_at=stamp,
            status_code=200,
            content_type=content_type,
            etag=None,
            robots_decision="edgar",
            extension=url.rsplit(".", 1)[-1] if "." in url.rsplit("/", 1)[-1] else "bin",
        )
        documents += 1
        entry = {
            "ticker": ticker,
            "period": period,
            "key": stored["key"],
            "source_url": url,
            "sha256": stored["sha256"],
            "source_kind": source_kind,
            "accession": accession,
            "published_date": published,
            "content_type": content_type,
        }
        manifests.append(entry)
        if exists(stored["key"]):
            skipped += 1
        else:
            put_bytes(stored["key"], body, content_type)
            written += 1
        current = None
        if read_json:
            try:
                current = read_json(stored["manifest_key"])
            except (OSError, ValueError, KeyError):
                current = None
            except Exception as exc:
                if type(exc).__name__ not in {"NoSuchKey", "ClientError"}:
                    raise
                current = None
        put_json(stored["manifest_key"], merge_manifest(current, {**stored["manifest"], **entry}))

    ticker_doc = pull("https://www.sec.gov/files/company_tickers.json", "edgar:company-tickers")
    cik_by_ticker = {}
    if ticker_doc is not None:
        try:
            payload = ticker_doc.json()
        except ValueError:
            payload = {}
        for record in payload.values() if isinstance(payload, dict) else []:
            if isinstance(record, dict) and record.get("ticker") and record.get("cik_str") is not None:
                cik_by_ticker[str(record["ticker"]).upper()] = f"{int(record['cik_str']):010d}"

    for ticker in targets:
        catalog = load_catalog(ticker)
        cik = cik_by_ticker.get(ticker)
        if not cik:
            skipped += 1
            reasons.append({"source_id": f"edgar:{ticker}", "reason": "not_an_sec_filer"})
            continue
        submissions = pull(f"https://data.sec.gov/submissions/CIK{cik}.json", f"edgar:{ticker}:submissions")
        if submissions is None:
            continue
        try:
            body = submissions.json()
        except ValueError:
            failed.append(f"edgar:{ticker}:submissions")
            continue
        for filing in item_202_filings(body):
            index_url = filing_index_url(cik, filing["accession"])
            index = pull(index_url, f"edgar:{ticker}:{filing['accession']}:index")
            if index is None:
                continue
            try:
                exhibits = exhibits_from_index(index.json())
            except ValueError:
                failed.append(f"edgar:{ticker}:{filing['accession']}:index")
                continue
            for exhibit in exhibits:
                url = archive_url(cik, filing["accession"], exhibit["name"])
                document = pull(url, f"edgar:{ticker}:{filing['accession']}:ex-99.1")
                if document is None:
                    continue
                title = exhibit.get("description") or title_from_html(document.text)
                kind = _source_kind_for_title(title)
                if kind == "other":
                    kind = "press_release" if "deliver" in title.lower() else "deck"
                period = "undated"
                if filing.get("report_date"):
                    period = fiscal_period_for_end(date.fromisoformat(filing["report_date"]))
                content = document.content if isinstance(document.content, bytes) else document.text.encode()
                save(ticker, period, url, content, "text/html", kind, filing["accession"], filing.get("filed"))
        for filing in latest_periodic_filings(body):
            url = archive_url(cik, filing["accession"], filing["primary_document"])
            document = pull(url, f"edgar:{ticker}:{filing['form']}")
            if document is None:
                continue
            period = "undated"
            if filing.get("report_date"):
                period = fiscal_period_for_end(date.fromisoformat(filing["report_date"]))
            content = document.content if isinstance(document.content, bytes) else document.text.encode()
            save(ticker, period, url, content, "text/html", "note", filing["accession"], filing.get("filed"))
        facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        facts = pull(facts_url, f"edgar:{ticker}:companyfacts")
        if facts is not None:
            content = facts.content if isinstance(facts.content, bytes) else facts.text.encode()
            save(ticker, "companyfacts", facts_url, content, "application/json", "xbrl", "", None)
        if not any(item["ticker"] == ticker and item["source_kind"] in {"deck", "press_release"} for item in manifests):
            for source in catalog.get("ir_sources") or []:
                robots_url = source.get("robots_url")
                robots_txt = ""
                if robots_url:
                    robots_decision = decide_ir_fetch(robots_url, None, agent)
                    if robots_decision["action"] == "skip":
                        reasons.append({
                            "source_id": source.get("id") or "ir",
                            "reason": robots_decision["reason"],
                            "url": robots_url,
                        })
                        failed.append(source.get("id") or "ir")
                        continue
                    robots = pull(robots_url, f"ir:{ticker}:robots")
                    robots_txt = robots.text if robots is not None else ""
                for url in source.get("urls") or []:
                    decision = decide_ir_fetch(url, robots_txt, agent)
                    if decision["action"] == "skip":
                        reasons.append({
                            "source_id": source.get("id") or "ir",
                            "reason": decision["reason"],
                            "url": url,
                        })
                        failed.append(source.get("id") or "ir")
                        continue
                    document = pull(url, f"ir:{ticker}:{source.get('id') or 'pdf'}")
                    if document is None:
                        continue
                    content = document.content if isinstance(document.content, bytes) else document.text.encode()
                    save(ticker, "ir", url, content, "application/pdf", "deck", "", None)

    status = "ok"
    if failed and documents:
        status = "partial"
    elif failed and not documents:
        status = "failed"
    return {
        "status": status,
        "reason": plan["reason"],
        "collected": written,
        "documents": documents,
        "skipped": skipped,
        "tickers": targets,
        "manifests": manifests,
        "failed_source_ids": sorted(set(failed)),
        "reasons": reasons,
        "run_status": status,
    }


def _period_from_manifest(item: dict) -> str | None:
    period = item.get("period")
    if isinstance(period, str) and _QUARTER.fullmatch(period):
        return period
    return None


def run_extract(
    event: dict | None, *, read_bytes, read_json, write_parquet, write_json, now: datetime | None = None,
) -> dict:
    """Parse manifests from collect, reconcile GAAP to XBRL, and write curated rows plus proposals."""
    flat = unwrap_job_event(event)
    if flat.get("status") == "skipped" or (flat.get("job") == "Q2C" and not flat.get("manifests")):
        return {
            "status": "skipped",
            "reason": flat.get("reason") or "upstream skipped",
            "extracted": 0,
            "mismatches": [],
            "tickers": [],
            "run_status": "skipped",
        }
    extracted_at = now or datetime.now(UTC)
    llm_enabled = bool(llm_secret_id())
    by_ticker: dict[str, list[dict]] = {}
    proposals: dict[str, list[dict]] = {}
    scale_flags: list[dict] = []
    failed = []
    for item in flat.get("manifests") or []:
        ticker = str(item.get("ticker") or "").upper()
        if not ticker:
            continue
        try:
            body = read_bytes(item["key"])
        except (OSError, ValueError, KeyError):
            failed.append(item.get("key") or ticker)
            continue
        text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        catalog = load_catalog(ticker)
        kind = item.get("source_kind") or "deck"
        if kind == "xbrl":
            try:
                facts = json.loads(text)
            except ValueError:
                failed.append(item.get("key") or ticker)
                continue
            rows, flags = companyfacts_observations(
                facts,
                catalog,
                ticker=ticker,
                source_url=item.get("source_url") or "",
                source_doc_hash=item.get("sha256") or "",
                extracted_at=extracted_at,
                published_date=item.get("published_date"),
            )
            by_ticker.setdefault(ticker, []).extend(rows)
            scale_flags.extend(flags)
            continue
        if kind == "note":
            continue
        parsed = parse_company_document(
            text,
            catalog,
            ticker=ticker,
            source_url=item.get("source_url") or "",
            source_doc_hash=item.get("sha256") or "",
            extracted_at=extracted_at,
            published_date=item.get("published_date"),
            source_kind=kind if kind in {"deck", "press_release"} else "deck",
            fiscal_period=_period_from_manifest(item),
            accession=item.get("accession"),
            llm_enabled=llm_enabled,
        )
        by_ticker.setdefault(ticker, []).extend(parsed["rows"])
        proposals.setdefault(ticker, []).extend(parsed["proposals"])
    mismatches = []
    tickers = []
    total = 0
    for ticker, rows in by_ticker.items():
        catalog = load_catalog(ticker)
        ir_only = [row for row in rows if row.get("source_kind") != "xbrl"]
        ir_winners, _ignored = dedupe_observations(ir_only)
        mismatches.extend(reconcile_rows(ir_winners, xbrl_value_map(rows)))
        winners, revisions = dedupe_observations(rows)
        frame = metrics_frame(winners)
        write_parquet(frame, curated_parquet_key(ticker))
        write_json(curated_observations_key(ticker), {"rows": [_jsonable(row) for row in winners]})
        merged = merge_proposals(catalog, proposals.get(ticker) or [])
        new_items = [
            item for item in merged.get("metrics") or []
            if catalog_entry(catalog, item["metric_id"]) is None
        ]
        write_json(curated_proposals_key(ticker), {"ticker": ticker, "metrics": new_items})
        write_json(curated_revisions_key(ticker), {"revisions": revisions})
        tickers.append(ticker)
        total += frame.height
    status = "partial" if failed else "ok"
    for flag in scale_flags:
        mismatches.append({**flag, "status": "scale_error"})
    return {
        "status": status,
        "extracted": total,
        "mismatches": mismatches,
        "scale_flags": scale_flags,
        "tickers": tickers,
        "failed_source_ids": failed,
        "run_status": status,
    }


def _load_observations(rows: list[dict]) -> list[dict]:
    loaded = []
    for row in rows:
        copied = dict(row)
        extracted = copied.get("extracted_at")
        if isinstance(extracted, str):
            copied["extracted_at"] = datetime.fromisoformat(extracted)
        period_end = copied.get("period_end")
        if isinstance(period_end, str):
            copied["period_end"] = date.fromisoformat(period_end)
        loaded.append(copied)
    return loaded


def run_serve(event: dict | None, *, read_json, write_json, now: str | None = None) -> dict:
    """Publish approved metrics. A skipped upstream run does not replace the previous document."""
    flat = unwrap_job_event(event)
    if flat.get("status") == "skipped" or flat.get("run_status") == "skipped":
        return {"status": "skipped", "reason": "upstream skipped", "served": 0, "tickers": [], "run_status": "skipped"}
    tickers = company_ir_tickers(flat.get("tickers") or [])
    generated_at = now or datetime.now(UTC).isoformat(timespec="seconds")
    run_status = str(flat.get("run_status") or flat.get("status") or "ok")
    if run_status not in {"ok", "partial", "failed"}:
        run_status = "ok"
    mismatches = list(flat.get("mismatches") or [])
    served = []
    for ticker in tickers:
        catalog = load_catalog(ticker)
        payload = read_json(curated_observations_key(ticker)) or {}
        rows = _load_observations(payload.get("rows") or [])
        previous = read_json(serving_stock_key(ticker))
        document = build_serving(
            ticker,
            catalog,
            rows,
            run=run_record(status=run_status, sources={"tickers": len(tickers)}, mismatches=mismatches),
            generated_at=generated_at,
            previous=previous,
            mismatches=mismatches,
        )
        public = public_stock_document(document, ticker)
        write_json(serving_stock_key(ticker), json.loads(visible_html(public)))
        served.append(ticker)
    return {
        "status": run_status,
        "served": len(served),
        "tickers": served,
        "run_status": run_status,
        "alert": run_needs_alert({"status": run_status}),
    }


def _record_failures(source_ids: list[str]) -> None:
    from observability import source_run

    for source_id in source_ids:
        with source_run(source_id):
            raise RuntimeError(source_id)


def collect_handler(event, context):
    import os

    from http_client import get_client
    from lake import read_json, s3, write_json
    from observability import job_handler
    from universe import user_ticker_union, watchlist_universe

    @job_handler("Q2C")
    def run(event, context):
        bucket = os.environ["LAKE_BUCKET"]
        client = s3()

        def exists(key: str) -> bool:
            found = client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
            return any(obj["Key"] == key for obj in found.get("Contents", []))

        def put_bytes(key: str, body: bytes, content_type: str) -> None:
            client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)

        def put_json(key: str, obj: dict) -> None:
            write_json(obj, key, bucket=bucket)

        def fetch(url: str, headers: dict):
            # The client is opened around the whole collect so SEC pacing stays in one connection pool.
            return http.get(url, headers=headers)

        universe = watchlist_universe(user_ticker_union())
        with get_client(headers={"User-Agent": crawler_user_agent()}) as http:
            result = run_collect(
                event,
                datetime.now(UTC).date(),
                fetch=fetch,
                exists=exists,
                put_bytes=put_bytes,
                put_json=put_json,
                read_json=lambda key: read_json(key, bucket),
                tickers=list(universe["tickers"] or []),
                pace=Pace(),
            )
        _record_failures(result.get("failed_source_ids") or [])
        return result

    return run(event, context)


def extract_handler(event, context):
    import os

    from lake import read_json, s3, write_json, write_parquet
    from observability import job_handler

    @job_handler("Q2X")
    def run(event, context):
        bucket = os.environ["LAKE_BUCKET"]

        def read_bytes(key: str) -> bytes:
            return s3().get_object(Bucket=bucket, Key=key)["Body"].read()

        def write_frame(frame, key: str) -> None:
            write_parquet(frame, key, bucket=bucket)

        result = run_extract(
            event,
            read_bytes=read_bytes,
            read_json=lambda key: read_json(key, bucket),
            write_parquet=write_frame,
            write_json=lambda key, obj: write_json(obj, key, bucket=bucket),
        )
        _record_failures(result.get("failed_source_ids") or [])
        return result

    return run(event, context)


def serve_handler(event, context):
    import os

    from aws_lambda_powertools.metrics import MetricUnit

    from lake import read_json, write_json
    from observability import job_handler, metrics

    @job_handler("Q2S")
    def run(event, context):
        bucket = os.environ.get("LAKE_BUCKET")
        result = run_serve(
            event,
            read_json=lambda key: read_json(key, bucket),
            write_json=lambda key, obj: write_json(obj, key, bucket=bucket),
        )
        if result.get("alert"):
            metrics.add_metric(name="FailedRuns", unit=MetricUnit.Count, value=1)
        return result

    return run(event, context)
