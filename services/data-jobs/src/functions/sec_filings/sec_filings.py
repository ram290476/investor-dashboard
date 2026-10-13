"""SEC filing log for every watchlist ticker.

Metadata comes from the EDGAR submissions API. CIK lookup, fiscal-period labels,
exhibit discovery, and the 10-request-per-second pace are the company_ir helpers.
Primary documents are stored as URLs only. Tickers with no CIK are ``no_sec_filer``.
A failed fetch is ``failed`` with a reason. Nothing here is specific to one issuer.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from company_ir import (
    Pace,
    archive_url,
    company_ir_tickers,
    crawler_user_agent,
    exhibits_from_index,
    exhibits_from_index_page,
    fiscal_end_month_from_submissions,
    fiscal_period_for_end,
    unwrap_job_event,
)
from regulatory_feeds import assert_prod_user_agent, classify_8k, is_business_day

JOB_ID = "SEC"
ET = ZoneInfo("America/New_York")
SOURCE = "edgar-submissions"
BACKFILL_YEARS = 5
INCREMENTAL_DAYS = 21
CIK_CACHE_DAYS = 7
SERVING_RECENT = 40
MAX_SCHEDULED_BACKFILLS = 2
EXHIBIT_FETCH_CAP = 40
RAW_INDEX_CAP = 80
RAW_INDEX_CAP_BACKFILL = 400
SEC_MAX_ATTEMPTS = 4
STATE_KEY = "raw/sec_filings/_state.json"
CIK_CACHE_KEY = "raw/sec_filings/company_tickers.json"
LEASE_KEY = "raw/sec_filings/_lease.json"
EXHIBIT_FORMS = frozenset({"8-K", "8-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A"})
RETRYABLE_STATUS = frozenset({403, 429, 500, 502, 503, 504})
_OLDER_FILE = re.compile(r"^CIK\d+-submissions-\d+\.json$")
PARQUET_SCHEMA = {
    "ticker": pl.Utf8,
    "cik": pl.Utf8,
    "entity_name": pl.Utf8,
    "accession_number": pl.Utf8,
    "form": pl.Utf8,
    "items": pl.Utf8,
    "filed_at": pl.Utf8,
    "accepted_at": pl.Utf8,
    "period_of_report": pl.Utf8,
    "fiscal_year": pl.Int32,
    "fiscal_period": pl.Utf8,
    "primary_doc_url": pl.Utf8,
    "exhibits_json": pl.Utf8,
    "title": pl.Utf8,
    "size_bytes": pl.Int64,
    "xbrl": pl.Boolean,
    "first_seen": pl.Utf8,
    "last_checked": pl.Utf8,
    "source": pl.Utf8,
    "status": pl.Utf8,
    "_rank": pl.Int64,
}


def curated_filings_prefix(ticker: str) -> str:
    return f"curated/sec_filings/ticker={ticker.upper()}/"


def curated_filings_key(ticker: str) -> str:
    return f"{curated_filings_prefix(ticker)}filings.parquet"


def serving_filings_key(ticker: str) -> str:
    return f"serving/filings/{ticker.upper()}.json"


def description_key(ticker: str) -> str:
    return f"raw/sec_filings/ticker={ticker.upper()}/description.json"


def ticker_status_key(ticker: str) -> str:
    return f"raw/sec_filings/ticker={ticker.upper()}/status.json"


def raw_accession_key(ticker: str, accession: str) -> str:
    safe = str(accession or "").replace("/", "")
    return f"raw/sec_filings/ticker={ticker.upper()}/accession={safe}/index.json"


def run_record_key(day: date, stamp: str) -> str:
    token = re.sub(r"[^0-9T]", "", stamp)[:16] or "run"
    return f"raw/sec_filings/runs/date={day.isoformat()}/{token}.json"


def _log_event(ticker: str, status: str, **fields) -> dict:
    """One structured line per ticker. Counts and reasons only; no document bodies."""
    from observability import logger

    event = {
        "event": "sec_filing_ticker",
        "ticker": str(ticker or "").upper(),
        "status": status,
        "reason": fields.get("reason"),
        "cik": fields.get("cik"),
        "filings": int(fields.get("filings") or 0),
        "new_filings": int(fields.get("new_filings") or 0),
    }
    logger.info("sec_filing_ticker", extra=event)
    return event


def cik_map_from_company_tickers(payload: dict) -> dict[str, dict]:
    """SEC ``company_tickers.json``: ticker -> zero-padded CIK and EDGAR title."""
    found: dict[str, dict] = {}
    if not isinstance(payload, dict):
        return found
    for record in payload.values():
        if not isinstance(record, dict):
            continue
        symbol = str(record.get("ticker") or "").upper().strip()
        raw_cik = record.get("cik_str")
        if not symbol or raw_cik is None:
            continue
        try:
            cik = f"{int(raw_cik):010d}"
        except (TypeError, ValueError):
            continue
        found[symbol] = {"cik": cik, "title": str(record.get("title") or "").strip()}
    return found


def lookup_cik(cik_map: dict, ticker: str) -> dict | None:
    """Match the watchlist symbol to EDGAR. Class shares use either dot or dash."""
    symbol = str(ticker or "").upper().strip()
    if not isinstance(cik_map, dict) or not symbol:
        return None
    for candidate in (symbol, symbol.replace(".", "-"), symbol.replace("-", ".")):
        entry = cik_map.get(candidate)
        if isinstance(entry, dict) and entry.get("cik"):
            return entry
    return None


def cik_cache_is_fresh(document: dict | None, now: datetime, max_age_days: int = CIK_CACHE_DAYS) -> bool:
    if not isinstance(document, dict) or not isinstance(document.get("map"), dict) or not document["map"]:
        return False
    stamp = str(document.get("fetched_at") or "")
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    try:
        fetched = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    return now - fetched <= timedelta(days=max_age_days)


def sec_backoff_seconds(attempt: int, retry_after: str | None = None, *, base: float = 1.0, cap: float = 30.0) -> float:
    """Wait longer after each 403 or 429. A numeric Retry-After raises the floor."""
    delay = base * (2 ** max(0, attempt))
    if retry_after:
        try:
            parsed = float(retry_after)
        except ValueError:
            parsed = 0.0
        if parsed > 0:
            delay = max(delay, parsed)
    return min(delay, cap)


def sec_status_action(status_code: int, attempt: int, max_attempts: int = SEC_MAX_ATTEMPTS) -> str:
    """``ok`` stores the body. ``backoff`` tries again. Anything else fails the request."""
    if status_code in {200, 304}:
        return "ok"
    if status_code in RETRYABLE_STATUS and attempt + 1 < max_attempts:
        return "backoff"
    return "fail"


def fetch_sec(url: str, fetch, pace, sleep, max_attempts: int = SEC_MAX_ATTEMPTS):
    """GET one SEC URL under the shared pace. 403 and 429 back off, then fail closed."""
    host = (urlparse(url).hostname or "").lower()
    reason = "request_failed"
    for attempt in range(max_attempts):
        pace.wait(host)
        try:
            response = fetch(url)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            reason = type(exc).__name__
            if attempt + 1 >= max_attempts:
                return None, reason
            sleep(sec_backoff_seconds(attempt))
            continue
        status_code = int(getattr(response, "status_code", 0) or 0)
        action = sec_status_action(status_code, attempt, max_attempts)
        if action == "ok":
            return response, None
        reason = f"http_{status_code}"
        if action != "backoff":
            return None, reason
        headers = getattr(response, "headers", {}) or {}
        retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
        sleep(sec_backoff_seconds(attempt, None if retry_after is None else str(retry_after)))
    return None, reason


def _years_ago(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def fiscal_labels(period_of_report: str | None, fiscal_end_month: int) -> dict:
    """Fiscal year and quarter from the period of report and the filer's year-end month.

    The month comes from EDGAR ``fiscalYearEnd``. NVDA's ``0131`` and MRVL's early-February
    year end both go through this helper; the ticker symbol is not an input.
    """
    text = str(period_of_report or "")[:10]
    if len(text) != 10:
        return {"fiscal_year": None, "fiscal_period": None}
    try:
        end = date.fromisoformat(text)
    except ValueError:
        return {"fiscal_year": None, "fiscal_period": None}
    period = fiscal_period_for_end(end, fiscal_end_month)
    return {"fiscal_year": int(period[:4]), "fiscal_period": period}


def entity_name_from_submissions(payload: dict, fallback: str = "") -> str:
    if not isinstance(payload, dict):
        return fallback
    for key in ("entityName", "name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _column(block: dict, key: str, index: int):
    values = block.get(key) or []
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) == 1
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _size(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _items(value) -> list[str]:
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def filing_block(payload: dict) -> dict | None:
    """Recent filings, or an older submissions file (columns at the top or under ``filings.recent``)."""
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("accessionNumber"), list):
        return payload
    recent = (payload.get("filings") or {}).get("recent") if isinstance(payload.get("filings"), dict) else None
    if isinstance(recent, dict) and isinstance(recent.get("accessionNumber"), list):
        return recent
    return None


def older_submission_names(submissions: dict, cutoff: date) -> list[str]:
    files = (submissions.get("filings") or {}).get("files") if isinstance(submissions, dict) else None
    names = []
    for item in files or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not _OLDER_FILE.match(name):
            continue
        end = str(item.get("filingTo") or "")[:10]
        if end and end < cutoff.isoformat():
            continue
        names.append(name)
    return names


def filing_class(form: str, items: list[str]) -> str:
    kind = str(form or "").upper()
    if kind in {"8-K", "8-K/A"}:
        return classify_8k(items)
    if kind == "4":
        return "insider"
    if kind in {"10-K", "10-Q", "10-K/A", "10-Q/A"}:
        return "periodic"
    return "other"


def filings_from_block(
    block: dict,
    *,
    ticker: str,
    cik: str,
    entity_name: str,
    fiscal_end_month: int,
    checked_at: str,
) -> list[dict]:
    """One submissions column block -> filing rows. The accession number is the key."""
    forms = block.get("form") or []
    if not isinstance(forms, list):
        return []
    rows = []
    symbol = ticker.upper()
    for index, form in enumerate(forms):
        accession = str(_column(block, "accessionNumber", index) or "").strip()
        filed = str(_column(block, "filingDate", index) or "")[:10]
        if not accession or len(filed) != 10:
            continue
        items = _items(_column(block, "items", index))
        period = str(_column(block, "reportDate", index) or "")[:10]
        labels = fiscal_labels(period, fiscal_end_month)
        document = str(_column(block, "primaryDocument", index) or "").strip()
        form_name = str(form or "").strip()
        title = str(_column(block, "primaryDocDescription", index) or "").strip() or form_name
        xbrl = _flag(_column(block, "isXBRL", index)) or _flag(_column(block, "isInlineXBRL", index))
        rows.append(
            {
                "ticker": symbol,
                "cik": cik,
                "entity_name": entity_name,
                "accession_number": accession,
                "form": form_name,
                "items": items,
                "filed_at": filed,
                "accepted_at": str(_column(block, "acceptanceDateTime", index) or ""),
                "period_of_report": period,
                "fiscal_year": labels["fiscal_year"],
                "fiscal_period": labels["fiscal_period"] or "",
                "primary_doc_url": archive_url(cik, accession, document) if document else "",
                "exhibits": [],
                "title": title,
                "size_bytes": _size(_column(block, "size", index)),
                "xbrl": xbrl,
                "first_seen": checked_at,
                "last_checked": checked_at,
                "source": SOURCE,
                "status": "ok",
                "filing_class": filing_class(form_name, items),
            }
        )
    return rows


def filings_to_store(rows: list[dict], known: set[str], *, recent_cutoff: date, history_cutoff: date) -> list[dict]:
    """Keep a new accession inside the history window, and refresh rows filed inside the recent window."""
    chosen = []
    seen: set[str] = set()
    for row in rows:
        accession = row.get("accession_number") or ""
        filed = str(row.get("filed_at") or "")
        if not accession or accession in seen or filed < history_cutoff.isoformat():
            continue
        if accession not in known or filed >= recent_cutoff.isoformat():
            chosen.append(row)
            seen.add(accession)
    return chosen


def exhibits_for_filing(cik: str, accession: str, index_html: str | None, index_json: dict | None) -> list[dict]:
    """Exhibit type, description, and URL. Document type comes from the company_ir parsers."""
    found = exhibits_from_index_page(index_html or "") if index_html else []
    if not found and isinstance(index_json, dict):
        found = exhibits_from_index(index_json)
    exhibits = []
    for item in found:
        name = str(item.get("name") or "")
        kind = str(item.get("type") or "")
        if not kind:
            continue
        exhibits.append(
            {
                "type": kind,
                "description": str(item.get("description") or kind),
                "url": archive_url(cik, accession, name) if name else "",
            }
        )
    return exhibits


def _exhibits_list(row: dict) -> list:
    exhibits = row.get("exhibits")
    if isinstance(exhibits, list):
        return exhibits
    raw = row.get("exhibits_json")
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def normalize_stored_row(row: dict) -> dict:
    items = _items(row.get("items"))
    fiscal_year = row.get("fiscal_year")
    try:
        fiscal_year = int(fiscal_year) if fiscal_year not in (None, "") else None
    except (TypeError, ValueError):
        fiscal_year = None
    return {
        "ticker": str(row.get("ticker") or "").upper(),
        "cik": str(row.get("cik") or ""),
        "entity_name": str(row.get("entity_name") or ""),
        "accession_number": str(row.get("accession_number") or row.get("accession_no") or "").strip(),
        "form": str(row.get("form") or ""),
        "items": items,
        "filed_at": str(row.get("filed_at") or "")[:10],
        "accepted_at": str(row.get("accepted_at") or ""),
        "period_of_report": str(row.get("period_of_report") or row.get("report_date") or "")[:10],
        "fiscal_year": fiscal_year,
        "fiscal_period": str(row.get("fiscal_period") or ""),
        "primary_doc_url": str(row.get("primary_doc_url") or row.get("url") or ""),
        "exhibits": _exhibits_list(row),
        "title": str(row.get("title") or row.get("form") or ""),
        "size_bytes": _size(row.get("size_bytes") if "size_bytes" in row else row.get("size")),
        "xbrl": bool(row.get("xbrl")),
        "first_seen": str(row.get("first_seen") or ""),
        "last_checked": str(row.get("last_checked") or ""),
        "source": str(row.get("source") or SOURCE),
        "status": str(row.get("status") or "ok"),
        "filing_class": str(row.get("filing_class") or row.get("class") or ""),
    }


def merge_filings(existing: list[dict], incoming: list[dict], checked_at: str) -> tuple[list[dict], int]:
    """Upsert on ``(ticker, accession_number)``. The first time an accession is seen is kept."""
    by_key: dict[tuple[str, str], dict] = {}
    for row in existing:
        normalized = normalize_stored_row(row)
        key = (normalized["ticker"], normalized["accession_number"])
        if key[0] and key[1]:
            by_key[key] = normalized
    new_count = 0
    for row in incoming:
        normalized = normalize_stored_row(row)
        key = (normalized["ticker"], normalized["accession_number"])
        if not key[0] or not key[1]:
            continue
        prior = by_key.get(key)
        if prior is None:
            new_count += 1
            merged = {**normalized, "first_seen": normalized["first_seen"] or checked_at}
        else:
            exhibits = normalized["exhibits"] or prior["exhibits"]
            merged = {
                **prior,
                **normalized,
                "exhibits": exhibits,
                "first_seen": prior["first_seen"] or normalized["first_seen"] or checked_at,
            }
        merged["last_checked"] = checked_at
        by_key[key] = merged
    ordered = [by_key[key] for key in sorted(by_key)]
    return ordered, new_count


def checked_rank(stamp: str) -> int:
    text = str(stamp or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def parquet_row(row: dict) -> dict:
    normalized = normalize_stored_row(row)
    return {
        "ticker": normalized["ticker"],
        "cik": normalized["cik"],
        "entity_name": normalized["entity_name"],
        "accession_number": normalized["accession_number"],
        "form": normalized["form"],
        "items": ",".join(normalized["items"]),
        "filed_at": normalized["filed_at"],
        "accepted_at": normalized["accepted_at"],
        "period_of_report": normalized["period_of_report"],
        "fiscal_year": normalized["fiscal_year"],
        "fiscal_period": normalized["fiscal_period"],
        "primary_doc_url": normalized["primary_doc_url"],
        "exhibits_json": json.dumps(normalized["exhibits"], separators=(",", ":")),
        "title": normalized["title"],
        "size_bytes": normalized["size_bytes"],
        "xbrl": normalized["xbrl"],
        "first_seen": normalized["first_seen"],
        "last_checked": normalized["last_checked"],
        "source": normalized["source"],
        "status": normalized["status"],
        "_rank": checked_rank(normalized["last_checked"]),
    }


def filings_frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame([parquet_row(row) for row in rows], schema=PARQUET_SCHEMA)


def recent_filings(rows: list[dict], limit: int = SERVING_RECENT) -> list[dict]:
    ordered = sorted(
        rows,
        key=lambda row: (str(row.get("filed_at") or ""), str(row.get("accession_number") or "")),
        reverse=True,
    )
    return ordered[:limit]


def public_filing(row: dict) -> dict:
    normalized = normalize_stored_row(row)
    return {
        "accession_number": normalized["accession_number"],
        "form": normalized["form"],
        "items": normalized["items"],
        "filed_at": normalized["filed_at"],
        "accepted_at": normalized["accepted_at"],
        "period_of_report": normalized["period_of_report"],
        "report_date": normalized["period_of_report"],
        "fiscal_year": normalized["fiscal_year"],
        "fiscal_period": normalized["fiscal_period"],
        "title": normalized["title"],
        "primary_doc_url": normalized["primary_doc_url"],
        "url": normalized["primary_doc_url"],
        "filing_class": normalized["filing_class"],
        "class": normalized["filing_class"],
        "size_bytes": normalized["size_bytes"],
        "xbrl": normalized["xbrl"],
        "exhibits": normalized["exhibits"],
    }


ANNUAL_FORMS = ("10-K", "20-F", "40-F", "10-KT", "10-K405")
DESCRIPTION_MAX_CHARS = 600
_BLOCK_TAG = re.compile(r"(?is)<\s*/?\s*(p|div|br|tr|li|h[1-6]|table|section)\b[^>]*>")
_DROP_TAG = re.compile(r"(?is)<(script|style|head|title|ix:header)\b.*?</\1\s*>")
_ANY_TAG = re.compile(r"(?s)<[^>]+>")
_SECTION_START = {
    "10-K": r"item\s*1\s*[.:\-\u2014\u2013]?\s*business\b",
    "20-F": r"item\s*4\s*[.:\-\u2014\u2013]?\s*information\s+on\s+the\s+company\b",
}
_SECTION_END = {
    "10-K": r"item\s*1\s*a\s*[.:\-\u2014\u2013]?\s*risk\s+factors|item\s*2\s*[.:\-\u2014\u2013]?\s*properties",
    "20-F": r"item\s*4\s*a\b|item\s*5\s*[.:\-\u2014\u2013]?\s*operating",
}
_SKIP_PARAGRAPH = re.compile(
    r"(?i)^(item\s*\d|part\s+[iv]+\b|table of contents|overview$|general$|our company$|business$)"
    r"|forward-looking|incorporated (herein )?by reference|unless the context|as used in this"
)


def latest_annual_filing(rows: list[dict]) -> dict | None:
    """Most recent original annual report (10-K, 20-F, 40-F) with a primary document URL."""
    candidates = [
        row
        for row in rows or []
        if str(row.get("form") or "").upper() in ANNUAL_FORMS
        and str(row.get("primary_doc_url") or "").startswith("http")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: (str(row.get("filed_at") or ""), str(row.get("accession_number") or "")))


def html_paragraphs(html: str) -> list[str]:
    import html as html_lib

    text = _DROP_TAG.sub(" ", str(html or ""))
    text = _BLOCK_TAG.sub("\n", text)
    text = html_lib.unescape(_ANY_TAG.sub(" ", text)).replace("\xa0", " ")
    return [" ".join(line.split()) for line in text.split("\n") if line.strip()]


def _trim_sentences(text: str, limit: int = DESCRIPTION_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind(".\u201d "))
    if stop >= 80:
        return cut[: stop + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "\u2026"


def description_from_annual_report(html: str, form: str = "10-K") -> str:
    """First prose of Item 1 (10-K/40-F) or Item 4 (20-F), trimmed to whole sentences.

    The table-of-contents entry is skipped by choosing the heading whose section holds the
    most prose before the next item. Empty when no business section is found.
    """
    kind = "20-F" if str(form or "").upper().startswith("20-F") else "10-K"
    start = re.compile(_SECTION_START[kind], re.I)
    end = re.compile(_SECTION_END[kind], re.I)
    paragraphs = html_paragraphs(html)
    best: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        if len(paragraph) > 200 or not start.search(paragraph):
            continue
        section: list[str] = []
        for later in paragraphs[index + 1 :]:
            if end.search(later) and len(later) < 200:
                break
            section.append(later)
        if sum(len(item) for item in section) > sum(len(item) for item in best):
            best = section
    chosen: list[str] = []
    for paragraph in best:
        if len(paragraph) < 80 or "." not in paragraph or _SKIP_PARAGRAPH.search(paragraph):
            continue
        chosen.append(paragraph)
        if sum(len(item) + 1 for item in chosen) >= DESCRIPTION_MAX_CHARS or len(chosen) >= 2:
            break
    return _trim_sentences(" ".join(chosen)) if chosen else ""


def resolve_description(annual: dict | None, cached: dict | None, fetch_document) -> dict | None:
    """Reuse the cached description while the latest annual report is unchanged; else parse it."""
    if annual is None:
        return cached if isinstance(cached, dict) and cached.get("description") else None
    accession = str(annual.get("accession_number") or "")
    if isinstance(cached, dict) and cached.get("accession_number") == accession and "description" in cached:
        return cached
    html = fetch_document(str(annual.get("primary_doc_url") or ""))
    if html is None:
        return cached if isinstance(cached, dict) and cached.get("description") else None
    return {
        "description": description_from_annual_report(html, str(annual.get("form") or "10-K")),
        "accession_number": accession,
        "form": str(annual.get("form") or ""),
        "filed_at": str(annual.get("filed_at") or ""),
        "source_url": str(annual.get("primary_doc_url") or ""),
    }


def serving_document(
    ticker: str,
    *,
    status: str,
    reason: str | None,
    cik: str | None,
    entity_name: str | None,
    filings: list[dict],
    as_of: str,
    description: dict | None = None,
) -> dict:
    described = description if isinstance(description, dict) and description.get("description") else None
    return {
        "ticker": ticker.upper(),
        "cik": cik,
        "entity_name": entity_name,
        "description": described["description"] if described else None,
        "description_source": None
        if described is None
        else {key: described.get(key) for key in ("form", "filed_at", "accession_number", "source_url")},
        "status": status,
        "reason": reason,
        "as_of": as_of,
        "filings": [public_filing(row) for row in recent_filings(filings)],
    }


def raw_index_document(row: dict) -> dict:
    """Accession index. The primary document stays a URL; the bytes are not copied."""
    public = public_filing(row)
    normalized = normalize_stored_row(row)
    return {
        "ticker": normalized["ticker"],
        "cik": normalized["cik"],
        "entity_name": normalized["entity_name"],
        "accession_number": public["accession_number"],
        "form": public["form"],
        "items": public["items"],
        "filed_at": public["filed_at"],
        "accepted_at": public["accepted_at"],
        "period_of_report": public["period_of_report"],
        "fiscal_year": public["fiscal_year"],
        "fiscal_period": public["fiscal_period"],
        "primary_doc_url": public["primary_doc_url"],
        "exhibits": public["exhibits"],
        "title": public["title"],
        "size_bytes": public["size_bytes"],
        "xbrl": public["xbrl"],
        "source": normalized["source"],
        "first_seen": normalized["first_seen"],
        "last_checked": normalized["last_checked"],
        "status": normalized["status"],
    }


def dashboard_rows_from_serving(document: dict | None) -> tuple[list[dict], str | None]:
    """Compact rows for ``dashboard.json``. Exhibits stay on ``serving/filings``."""
    if not isinstance(document, dict):
        return [], None
    rows = []
    for row in document.get("filings") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "accession_no": row.get("accession_number") or row.get("accession_no"),
                "form": row.get("form"),
                "filed_at": row.get("filed_at"),
                "title": row.get("title") or row.get("form"),
                "primary_doc_url": row.get("primary_doc_url") or row.get("url"),
                "filing_class": row.get("filing_class") or row.get("class"),
                "report_date": row.get("period_of_report") or row.get("report_date"),
                "items": _items(row.get("items")),
                "fiscal_period": row.get("fiscal_period"),
            }
        )
    status = document.get("status")
    return rows, status if isinstance(status, str) else None


def target_tickers(tickers: list[str], event: dict | None) -> dict:
    """Schedule covers the watchlist, capped at 25. A ticker-added event collects that symbol only."""
    flat = unwrap_job_event(event)
    detail_type = flat.get("detail-type") or (event or {}).get("detail-type")
    if detail_type == "TickerAdded":
        ticker = str(flat.get("ticker") or "").upper().strip()
        return {
            "reason": "ticker-added",
            "tickers": [ticker] if ticker else [],
            "backfill": {ticker} if ticker else set(),
            "dropped": [],
        }
    symbols = [str(item or "").upper().strip() for item in tickers if str(item or "").strip()]
    chosen = company_ir_tickers(symbols)
    return {
        "reason": "schedule",
        "tickers": chosen,
        "backfill": set(),
        "dropped": [item for item in symbols if item not in chosen],
    }


def run_summary(results: list[dict]) -> dict:
    return {
        "tickers_processed": len(results),
        "new_filings": sum(int(row.get("new_filings") or 0) for row in results),
        "failures": sum(1 for row in results if row.get("status") == "failed"),
        "no_sec_filer": sum(1 for row in results if row.get("status") == "no_sec_filer"),
    }


def _json_body(response) -> dict | None:
    try:
        payload = response.json()
    except (ValueError, AttributeError):
        return None
    return payload if isinstance(payload, dict) else None


def _index_urls(cik: str, accession: str) -> list[str]:
    return [
        archive_url(cik, accession, f"{accession}-index.html"),
        archive_url(cik, accession, f"{accession}-index.htm"),
    ]


def collect_ticker(
    ticker: str,
    *,
    cik_entry: dict | None,
    submissions: dict | None,
    extra_blocks: list[dict],
    existing: list[dict],
    backfill: bool,
    today: date,
    checked_at: str,
    fetch_error: str | None = None,
    fetch_index=None,
    map_error: str | None = None,
) -> dict:
    """Build one ticker's filing set. ``fetch_index`` is optional and only used for new exhibit forms."""
    symbol = ticker.upper()
    if map_error and not cik_entry:
        return {
            "ticker": symbol,
            "status": "failed",
            "reason": map_error,
            "rows": list(existing),
            "new_filings": 0,
            "cik": None,
        }
    if not cik_entry:
        return {
            "ticker": symbol,
            "status": "no_sec_filer",
            "reason": "no_sec_cik",
            "rows": [],
            "new_filings": 0,
            "cik": None,
            "entity_name": None,
        }
    cik = str(cik_entry.get("cik") or "")
    if fetch_error or not isinstance(submissions, dict):
        return {
            "ticker": symbol,
            "status": "failed",
            "reason": fetch_error or "submissions_unavailable",
            "rows": [normalize_stored_row(row) for row in existing],
            "new_filings": 0,
            "cik": cik,
            "entity_name": cik_entry.get("title"),
        }
    fiscal_end = fiscal_end_month_from_submissions(submissions)
    entity = entity_name_from_submissions(submissions, str(cik_entry.get("title") or ""))
    parsed: list[dict] = []
    for block in [filing_block(submissions), *extra_blocks]:
        if not isinstance(block, dict):
            continue
        parsed.extend(
            filings_from_block(
                block,
                ticker=symbol,
                cik=cik,
                entity_name=entity,
                fiscal_end_month=fiscal_end,
                checked_at=checked_at,
            )
        )
    history_cutoff = _years_ago(today, BACKFILL_YEARS)
    recent_cutoff = history_cutoff if backfill else today - timedelta(days=INCREMENTAL_DAYS)
    known = {str(row.get("accession_number") or row.get("accession_no") or "") for row in existing}
    known_exhibits = {
        str(row.get("accession_number") or row.get("accession_no") or "")
        for row in existing
        if _exhibits_list(row)
    }
    chosen = filings_to_store(parsed, known, recent_cutoff=recent_cutoff, history_cutoff=history_cutoff)
    if fetch_index is not None:
        candidates = [
            row
            for row in sorted(parsed, key=lambda item: str(item.get("filed_at") or ""), reverse=True)
            if str(row.get("filed_at") or "") >= history_cutoff.isoformat()
            and str(row.get("form") or "").upper() in EXHIBIT_FORMS
            and row.get("accession_number") not in known_exhibits
        ]
        for row in candidates[:EXHIBIT_FETCH_CAP]:
            html = fetch_index(cik, row["accession_number"])
            if html:
                row["exhibits"] = exhibits_for_filing(cik, row["accession_number"], html, None)
            if row not in chosen:
                chosen.append(row)
    merged, new_count = merge_filings(existing, chosen, checked_at)
    return {
        "ticker": symbol,
        "status": "ok",
        "reason": None,
        "rows": merged,
        "new_filings": new_count,
        "cik": cik,
        "entity_name": entity,
        "touched": chosen,
        "annual_report": latest_annual_filing([*parsed, *merged]),
    }


def scheduled_backfill_tickers(tickers: list[str], history_complete: set[str], explicit: set[str]) -> set[str]:
    """Ticker-added symbols are always a 5-year backfill. The daily run finishes a few incomplete ones."""
    chosen = set(explicit)
    for ticker in tickers:
        if len(chosen) >= len(explicit) + MAX_SCHEDULED_BACKFILLS:
            break
        if ticker not in history_complete and ticker not in chosen:
            chosen.add(ticker)
    return chosen


def _response_json(fetch, pace, sleep, url: str) -> tuple[dict | None, str | None]:
    response, reason = fetch_sec(url, fetch, pace, sleep)
    if response is None:
        return None, reason
    payload = _json_body(response)
    if payload is None:
        return None, "invalid_json"
    return payload, None


def run_collection(
    tickers: list[str],
    *,
    backfill: set[str],
    cached_ciks: dict | None,
    existing_for,
    fetch,
    pace,
    sleep,
    today: date,
    checked_at: str,
    now: datetime,
    cached_description_for=None,
) -> dict:
    """Fetch CIKs and submissions, then return per-ticker results. Callers write the lake."""
    document = cached_ciks if isinstance(cached_ciks, dict) else None
    refreshed = False
    map_error = None
    if not cik_cache_is_fresh(document, now):
        payload, error = _response_json(fetch, pace, sleep, "https://www.sec.gov/files/company_tickers.json")
        if payload is None:
            map_error = error or "cik_map_unavailable"
            if not (isinstance(document, dict) and isinstance(document.get("map"), dict) and document["map"]):
                document = {"fetched_at": checked_at, "map": {}}
        else:
            document = {"fetched_at": checked_at, "map": cik_map_from_company_tickers(payload)}
            refreshed = True
            map_error = None
    cik_map = document.get("map") if isinstance(document, dict) else {}
    if not isinstance(cik_map, dict):
        cik_map = {}
    results = []
    for ticker in tickers:
        entry = lookup_cik(cik_map, ticker)
        existing = existing_for(ticker) or []
        if entry is None and not cik_map and map_error:
            result = collect_ticker(
                ticker,
                cik_entry=None,
                submissions=None,
                extra_blocks=[],
                existing=existing,
                backfill=ticker in backfill,
                today=today,
                checked_at=checked_at,
                map_error=map_error,
            )
            result["history_complete"] = False
            results.append(result)
            continue
        submissions = None
        extra_blocks: list[dict] = []
        fetch_error = None
        older_complete = ticker not in backfill
        if entry is not None:
            cik = entry["cik"]
            submissions, fetch_error = _response_json(
                fetch, pace, sleep, f"https://data.sec.gov/submissions/CIK{cik}.json",
            )
            older_complete = False
            if submissions is not None and ticker in backfill:
                older_complete = True
                for name in older_submission_names(submissions, _years_ago(today, BACKFILL_YEARS)):
                    extra, _extra_error = _response_json(fetch, pace, sleep, f"https://data.sec.gov/submissions/{name}")
                    if extra is None:
                        older_complete = False
                        break
                    block = filing_block(extra)
                    if block is not None:
                        extra_blocks.append(block)

        def fetch_index(cik: str, accession: str, _fetch=fetch, _pace=pace, _sleep=sleep) -> str | None:
            for url in _index_urls(cik, accession):
                response, reason = fetch_sec(url, _fetch, _pace, _sleep)
                if response is not None and int(getattr(response, "status_code", 0) or 0) == 200:
                    return str(getattr(response, "text", "") or "")
                if reason and not str(reason).startswith("http_404"):
                    return None
            return None

        result = collect_ticker(
            ticker,
            cik_entry=entry,
            submissions=submissions,
            extra_blocks=extra_blocks,
            existing=existing,
            backfill=ticker in backfill,
            today=today,
            checked_at=checked_at,
            fetch_error=fetch_error,
            fetch_index=fetch_index if entry is not None and fetch_error is None else None,
            map_error=None if cik_map else map_error,
        )
        if result["status"] == "ok":

            def fetch_document(url: str, _fetch=fetch, _pace=pace, _sleep=sleep) -> str | None:
                response, _reason = fetch_sec(url, _fetch, _pace, _sleep)
                return None if response is None else str(getattr(response, "text", "") or "")

            cached = cached_description_for(ticker) if cached_description_for is not None else None
            result["description"] = resolve_description(result.get("annual_report"), cached, fetch_document)
        if result["status"] == "no_sec_filer":
            result["history_complete"] = True
        elif ticker in backfill and result["status"] == "ok" and older_complete:
            result["history_complete"] = True
        else:
            result["history_complete"] = False
        results.append(result)
    return {"cik_document": document, "cik_refreshed": refreshed, "results": results, "map_error": map_error}


def publish_ticker(result: dict, *, write_json, write_frame, checked_at: str, backfill: bool) -> None:
    ticker = result["ticker"]
    rows = result.get("rows") or []
    write_json(
        {
            "ticker": ticker,
            "status": result["status"],
            "reason": result.get("reason"),
            "cik": result.get("cik"),
            "entity_name": result.get("entity_name"),
            "filings": len(rows),
            "new_filings": int(result.get("new_filings") or 0),
            "checked_at": checked_at,
        },
        ticker_status_key(ticker),
    )
    write_json(
        serving_document(
            ticker,
            status=result["status"],
            reason=result.get("reason"),
            cik=result.get("cik"),
            entity_name=result.get("entity_name"),
            filings=rows,
            as_of=checked_at,
            description=result.get("description"),
        ),
        serving_filings_key(ticker),
    )
    if isinstance(result.get("description"), dict):
        write_json(result["description"], description_key(ticker))
    if result["status"] == "ok" and rows:
        write_frame(ticker, rows)
        cap = RAW_INDEX_CAP_BACKFILL if backfill else RAW_INDEX_CAP
        for row in recent_filings(result.get("touched") or rows, cap):
            write_json(raw_index_document(row), raw_accession_key(ticker, row["accession_number"]))


def outcome_for(results: list[dict]) -> str:
    if any(row.get("status") == "failed" for row in results) and any(row.get("status") == "ok" for row in results):
        return "partial"
    if results and all(row.get("status") == "failed" for row in results):
        return "failed"
    if any(row.get("status") == "failed" for row in results):
        return "partial"
    return "ok"


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    import os

    from lake import read_json, read_parquet_prefix, upsert_ranked, write_json
    from observability import job_handler
    from universe import user_ticker_union, watchlist_universe

    @job_handler(JOB_ID, lease_key=LEASE_KEY)
    def run(event, context):
        now = datetime.now(UTC)
        today = now.astimezone(ET).date()
        planned = target_tickers([], event)
        if planned["reason"] == "schedule" and not is_business_day(today):
            return {"status": "skipped", "reason": "federal holiday or weekend"}
        if os.getenv("INVDASH_ENV") == "prod":
            assert_prod_user_agent(os.getenv("SEC_USER_AGENT"))
        if planned["reason"] != "ticker-added":
            universe = watchlist_universe(user_ticker_union())
            planned = target_tickers(list(universe.get("tickers") or []), event)
            planned["dropped"] = list(dict.fromkeys([*(universe.get("dropped") or []), *planned["dropped"]]))
        state = read_json(STATE_KEY) or {}
        cached = read_json(CIK_CACHE_KEY)
        complete = {str(item).upper() for item in (state.get("history_complete") or [])}
        backfill = scheduled_backfill_tickers(planned["tickers"], complete, set(planned["backfill"]))

        def existing_for(ticker: str) -> list[dict]:
            frame = read_parquet_prefix(curated_filings_prefix(ticker))
            return frame.to_dicts() if not frame.is_empty() else []

        def write_frame(ticker: str, rows: list[dict]) -> None:
            if rows:
                key = f"curated/sec_filings/ticker={ticker.upper()}/filings.parquet"
                upsert_ranked(filings_frame(rows), key, ["ticker", "accession_number"], "_rank")

        from http_client import get_client

        pace = Pace()
        headers = {"User-Agent": crawler_user_agent(), "Accept": "application/json,text/html,*/*"}
        with get_client(headers=headers) as http:
            collected = run_collection(
                planned["tickers"],
                backfill=backfill,
                cached_ciks=cached if isinstance(cached, dict) else None,
                existing_for=existing_for,
                fetch=lambda url: http.get(url),
                pace=pace,
                sleep=pace.sleep,
                today=today,
                checked_at=now.isoformat(timespec="seconds"),
                now=now,
                cached_description_for=lambda ticker: read_json(description_key(ticker)),
            )
        for result in collected["results"]:
            publish_ticker(
                result,
                write_json=write_json,
                write_frame=write_frame,
                checked_at=now.isoformat(timespec="seconds"),
                backfill=result["ticker"] in backfill,
            )
            _log_event(
                result["ticker"],
                result["status"],
                reason=result.get("reason"),
                cik=result.get("cik"),
                filings=len(result.get("rows") or []),
                new_filings=result.get("new_filings") or 0,
            )
        finished = {row["ticker"] for row in collected["results"] if row.get("history_complete")}
        if collected["cik_refreshed"] and isinstance(collected["cik_document"], dict):
            write_json(collected["cik_document"], CIK_CACHE_KEY)
        write_json(
            {
                "history_complete": sorted(complete | finished),
                "updated_at": now.isoformat(timespec="seconds"),
            },
            STATE_KEY,
        )
        summary = run_summary(collected["results"])
        write_json(
            {
                "job": JOB_ID,
                "checked_at": now.isoformat(timespec="seconds"),
                "reason": planned["reason"],
                "summary": summary,
                "tickers": [
                    {
                        "ticker": row["ticker"],
                        "status": row["status"],
                        "reason": row.get("reason"),
                        "new_filings": row.get("new_filings") or 0,
                    }
                    for row in collected["results"]
                ],
            },
            run_record_key(today, now.isoformat(timespec="seconds")),
        )
        status = outcome_for(collected["results"])
        body = {
            "status": "success" if status == "ok" else status,
            "summary": summary,
            "dropped": planned["dropped"],
            "reason": planned["reason"],
        }
        if status == "partial":
            body["run_status"] = "partial"
        elif status == "failed":
            body["run_status"] = "failed"
        return body

    return run(event, context)
