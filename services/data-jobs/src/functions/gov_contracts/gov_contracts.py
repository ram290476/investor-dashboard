"""D5: government awards for every watchlist ticker (base tickers plus prefs, capped at 25).

USAspending needs no key and does the backfill. SAM.gov is capped at about 3 calls
a run and 10 a day. A 401 or 403 is a partial run, not a failed job.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

JOB_ID = "D5"
ET = ZoneInfo("America/New_York")
SAM_CALLS_PER_RUN = 3
SAM_CALLS_PER_DAY = 10
BACKFILL_DAYS = 365 * 5
STATE_KEY = "curated/contracts/_state.json"
USASPENDING_CONTRACT_CODES = ("A", "B", "C", "D")
USASPENDING_PAGE_SIZE = 100
USASPENDING_MAX_PAGES = 20
# UEIs are filled as awards identify them. A listed UEI wins over the name match.
UEI_TO_TICKER: dict[str, str] = {}


def catalog_dir() -> Path:
    """Ticker catalogs. Tests keep this file beside company_ir/; the Lambda image flattens both."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "catalog", here.parent / "company_ir" / "catalog"):
        if candidate.is_dir():
            return candidate
    return here / "catalog"


def catalog_contract_blocks() -> dict[str, dict]:
    """`gov_contracts` blocks from every packaged catalog. Names are not hard-coded here."""
    found: dict[str, dict] = {}
    root = catalog_dir()
    if not root.is_dir():
        return found
    for path in sorted(root.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        block = document.get("gov_contracts")
        if not isinstance(block, dict):
            continue
        ticker = str(document.get("ticker") or path.stem).upper()
        if ticker:
            found[ticker] = block
    return found


def _alias_entries(blocks: dict[str, dict], tickers: list[str] | None = None) -> list[tuple[str, str]]:
    chosen = list(blocks) if tickers is None else [str(ticker or "").upper() for ticker in tickers]
    entries: list[tuple[str, str]] = []
    for ticker in chosen:
        block = blocks.get(ticker) or {}
        for alias in block.get("name_aliases") or []:
            needle = " ".join(re.sub(r"[^a-z0-9]+", " ", str(alias).lower()).split())
            if needle and len(needle) >= 3 and all(needle != known for known, _owner in entries):
                entries.append((needle, ticker))
    return entries


NAME_TO_TICKER: list[tuple[str, str]] = _alias_entries(catalog_contract_blocks())


def is_business_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    import holidays

    return day not in holidays.US(years=[day.year])


def fiscal_year(day: date) -> int:
    return day.year + 1 if day.month >= 10 else day.year


def fiscal_quarter(day: date) -> str:
    year = fiscal_year(day)
    if day.month in (10, 11, 12):
        quarter = 1
    elif day.month in (1, 2, 3):
        quarter = 2
    elif day.month in (4, 5, 6):
        quarter = 3
    else:
        quarter = 4
    return f"FY{year}Q{quarter}"


def match_ticker(text: str | None) -> tuple[str, str] | None:
    """(ticker, matched spelling) using word boundaries. The spelling keeps the source's case."""
    raw = text or ""
    for needle, ticker in NAME_TO_TICKER:
        found = re.search(rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])", raw, re.IGNORECASE)
        if found:
            return ticker, found.group(0)
    return None


def ticker_for(name: str | None, uei: str | None = None) -> str | None:
    if uei and uei in UEI_TO_TICKER:
        return UEI_TO_TICKER[uei]
    matched = match_ticker(name)
    return None if matched is None else matched[0]


def name_map_from_watchlist(names: dict[str, str | None]) -> list[tuple[str, str]]:
    """Catalog aliases for these tickers, then each cleaned SEC entity name."""
    from universe import short_company_name

    entries = _alias_entries(catalog_contract_blocks(), list(names))
    for ticker, name in names.items():
        needle = " ".join(short_company_name(name).lower().replace("&", " ").split())
        needle = " ".join(re.sub(r"[^a-z0-9]+", " ", needle).split())
        if needle and len(needle) >= 3 and all(needle != known for known, _owner in entries):
            entries.append((needle, str(ticker).upper()))
    return entries


def use_watchlist_names(names: dict[str, str | None]) -> list[tuple[str, str]]:
    """Point ticker_for at this run's watchlist."""
    NAME_TO_TICKER[:] = name_map_from_watchlist(names)
    return NAME_TO_TICKER


def recipient_searches(tickers: list[str], names: dict[str, str | None]) -> list[tuple[str, str]]:
    """(ticker, USAspending recipient search text) for each watchlist ticker with a usable name."""
    from universe import short_company_name

    blocks = catalog_contract_blocks()
    searches = []
    for ticker in tickers:
        symbol = str(ticker or "").upper()
        text = str((blocks.get(symbol) or {}).get("recipient") or "").strip()
        if not text:
            text = short_company_name(names.get(ticker))
        if text:
            searches.append((symbol, text))
    return searches


def sam_query_for(searches: list[tuple[str, str]], day: date) -> str:
    """SAM.gov allows ~10 calls a day, so one watchlist name per day, in rotation."""
    if not searches:
        return ""
    return searches[day.toordinal() % len(searches)][1]


def sam_budget(calls_today: int, wanted: int = SAM_CALLS_PER_RUN) -> int:
    """Never more than 3 calls this run, and never more than 10 in the day."""
    remaining = SAM_CALLS_PER_DAY - max(0, calls_today)
    return max(0, min(SAM_CALLS_PER_RUN, wanted, remaining))


SAM_OPPORTUNITIES_WINDOW_DAYS = 30
# The Department of Defense site moved to war.gov. The HTML Contracts pages sit behind an
# Akamai bot rule (403 for non-browser clients); the RSS feed is served to collectors.
NASA_FEED = "https://www.nasa.gov/news/releases/latest/feed/"
# NASA's feed rate-limits occasionally (429 at 2:45 PM PT Oct 15). Retry a few times honoring
# Retry-After; if it is still limited, log a warning and move on rather than failing D5 (#235).
NASA_RATE_LIMIT_ATTEMPTS = 4
NASA_BASE_DELAY_S = 5.0
NASA_MAX_DELAY_S = 60.0


def retry_after_delay(header: str | None, attempt: int, base_s: float = NASA_BASE_DELAY_S,
                      cap_s: float = NASA_MAX_DELAY_S) -> float:
    """Seconds to wait before the next try: Retry-After (seconds or HTTP date) if given, else
    exponential backoff, never above cap_s."""
    backoff = base_s * (2**attempt)
    wait = None
    if header:
        try:
            wait = float(header)
        except ValueError:
            try:
                wait = (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError):
                wait = None
    return max(0.0, min(cap_s, wait if wait is not None else backoff))


def fetch_with_rate_limit(send, sleep, attempts: int = NASA_RATE_LIMIT_ATTEMPTS):
    """Call send() until it returns a non-429 response. Returns None if every try was rate limited.

    send() must return an httpx-like response and must not raise on 429.
    """
    for attempt in range(attempts):
        response = send()
        if response.status_code != 429:
            return response
        if attempt + 1 < attempts:
            sleep(retry_after_delay(response.headers.get("Retry-After"), attempt))
    return None


DOD_CONTRACTS_FEED = "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max=10"


def redact(text: str | None) -> str:
    """Strip api_key values from URLs or bodies before they reach logs or errors."""
    return re.sub(r"(api_key=)[^&\s\"']+", r"\1REDACTED", text or "")


def sam_status(status_code: int, body: str | None = None) -> str:
    """An expired or rejected SAM key is partial. Other HTTP errors still surface, with the body."""
    if status_code in {401, 403}:
        return "partial"
    if status_code >= 400:
        detail = " ".join(redact(body).split())[:300]
        raise RuntimeError(f"SAM.gov returned HTTP {status_code}" + (f": {detail}" if detail else ""))
    return "ok"


def sam_opportunities_params(query: str, day: date, key: str) -> dict:
    """Get Opportunities v2: postedFrom and postedTo are mandatory (MM/dd/yyyy, at most a year apart).

    Without them SAM.gov answers HTTP 400.
    """
    start = day - timedelta(days=SAM_OPPORTUNITIES_WINDOW_DAYS)
    return {
        "api_key": key,
        "title": query,
        "postedFrom": start.strftime("%m/%d/%Y"),
        "postedTo": day.strftime("%m/%d/%Y"),
        "limit": 10,
    }


def parse_dod_feed(xml_text: str) -> list[dict]:
    """Items of the war.gov Contracts RSS feed: title, link, pubDate."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        items.append(
            {
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "published": (item.findtext("pubDate") or "").strip(),
            }
        )
    return items


def catch_up_start(watermark: str | None, today: date) -> str:
    if not watermark:
        return (today - timedelta(days=BACKFILL_DAYS)).isoformat()
    return watermark[:10]


def _amount(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        return float(cleaned or 0)
    return 0.0


def usaspending_body(recipient: str, start: str, end: str, page: int = 1) -> dict:
    """spending_by_award request. award_type_codes is required (422 without it); A-D are contracts."""
    return {
        "filters": {
            "recipient_search_text": [recipient],
            "award_type_codes": list(USASPENDING_CONTRACT_CODES),
            "time_period": [{"start_date": start, "end_date": end}],
        },
        "fields": [
            "Award ID",
            "Recipient Name",
            "Recipient UEI",
            "Award Amount",
            "Awarding Agency",
            "Awarding Sub Agency",
            "Start Date",
            "Description",
        ],
        "limit": USASPENDING_PAGE_SIZE,
        "page": page,
    }


def parse_usaspending(payload: dict, ingested_at: str) -> list[dict]:
    rows = []
    for item in payload.get("results") or []:
        name = item.get("Recipient Name") or item.get("recipient_name") or ""
        uei = item.get("Recipient UEI") or item.get("recipient_uei")
        award_id = item.get("Award ID") or item.get("generated_internal_id")
        if not award_id:
            continue
        action = item.get("Start Date") or item.get("action_date") or ""
        rows.append(
            {
                "award_id": str(award_id),
                "parent_award_id": item.get("parent_award_id"),
                "recipient_name": name,
                "recipient_uei": uei,
                "ticker": ticker_for(name, uei),
                "agency": item.get("Awarding Agency") or item.get("agency") or "",
                "sub_agency": item.get("Awarding Sub Agency") or item.get("sub_agency") or "",
                "action_date": str(action)[:10],
                "obligated_amount": _amount(item.get("Award Amount") or item.get("obligated_amount")),
                "total_value": _amount(item.get("Award Amount") or item.get("total_value")),
                "description": item.get("Description") or item.get("description") or "",
                "naics": item.get("NAICS Code") or item.get("naics"),
                "psc": item.get("PSC") or item.get("psc"),
                "source": "usaspending",
                "source_url": item.get("source_url") or "https://www.usaspending.gov/",
                "ingested_at": ingested_at,
            }
        )
    return rows


def parse_sam(payload: dict, ingested_at: str, kind: str = "sam-awards") -> list[dict]:
    rows = []
    for item in payload.get("awards") or payload.get("opportunitiesData") or payload.get("results") or []:
        name = item.get("recipientName") or item.get("title") or ""
        uei = item.get("uei") or item.get("recipientUei")
        award_id = item.get("piid") or item.get("noticeId") or item.get("awardId")
        if not award_id:
            continue
        rows.append(
            {
                "award_id": str(award_id),
                "parent_award_id": item.get("parentAwardId"),
                "recipient_name": name,
                "recipient_uei": uei,
                "ticker": ticker_for(name, uei),
                "agency": item.get("contractingAgency") or item.get("department") or "",
                "sub_agency": item.get("contractingOffice") or "",
                "action_date": str(item.get("awardDate") or item.get("postedDate") or "")[:10],
                "obligated_amount": _amount(item.get("obligatedAmount") or item.get("awardAmount")),
                "total_value": _amount(item.get("totalContractValue") or item.get("awardAmount")),
                "description": item.get("description") or item.get("title") or "",
                "naics": item.get("naics"),
                "psc": item.get("psc"),
                "source": kind,
                "source_url": item.get("uiLink") or item.get("url") or "https://sam.gov/",
                "ingested_at": ingested_at,
            }
        )
    return rows


def parse_dod(text: str, ingested_at: str, day: date) -> list[dict]:
    """DoD paragraphs that name a watchlist company. A PIID is reconciled; otherwise the row stays provisional."""
    rows = []
    for paragraph in (text or "").split("\n\n"):
        if "contract" not in paragraph.lower():
            continue
        matched = match_ticker(paragraph)
        if not matched:
            continue
        ticker, recipient = matched
        piid = None
        for token in paragraph.replace(",", " ").split():
            if len(token) >= 8 and any(char.isdigit() for char in token) and "-" in token:
                piid = token.strip("().")
                break
        amount = 0.0
        if "$" in paragraph:
            raw = paragraph.split("$", 1)[1].split()[0]
            amount = _amount(raw)
        rows.append(
            {
                "award_id": piid or f"DoD-{ticker}-{day.isoformat()}-{len(rows)}",
                "parent_award_id": None,
                "recipient_name": recipient,
                "recipient_uei": None,
                "ticker": ticker,
                "agency": "Department of Defense",
                "sub_agency": "",
                "action_date": day.isoformat(),
                "obligated_amount": amount,
                "total_value": amount,
                "description": " ".join(paragraph.split()),
                "naics": None,
                "psc": None,
                "source": "DoD",
                "source_url": "https://www.defense.gov/News/Contracts/",
                "ingested_at": ingested_at,
            }
        )
    return rows


def merge_awards(rows: list[dict]) -> list[dict]:
    """Latest action wins the descriptive fields. Obligated dollars sum across distinct mods."""
    grouped: dict[str, dict] = {}
    seen: set[tuple] = set()
    ordered = sorted(rows, key=lambda row: str(row.get("action_date") or ""))
    for row in ordered:
        award_id = row.get("award_id")
        if not award_id:
            continue
        mod = (award_id, row.get("action_date"), row.get("obligated_amount"), row.get("source"))
        if mod in seen:
            continue
        seen.add(mod)
        amount = _amount(row.get("obligated_amount"))
        current = grouped.get(award_id)
        if current is None:
            grouped[award_id] = {**row, "obligated_amount": amount}
            continue
        total = current["obligated_amount"] + amount
        current.update(row)
        current["obligated_amount"] = total
    return list(grouped.values())


def reconcile(rows: list[dict]) -> list[dict]:
    """A DoD line with a PIID that already exists on an award is the same award, not a second one."""
    official = {row.get("award_id") for row in rows if row.get("source") != "DoD"}
    kept = [row for row in rows if not (row.get("source") == "DoD" and row.get("award_id") in official)]
    return merge_awards(kept)


def rollup_contracts(rows: list[dict], as_of: date, tickers: list[str] | None = None) -> list[dict]:
    """Trailing-12-month obligations, agency mix, and the last 30 days of new awards.

    Every watchlist ticker with matched awards is rolled up; None means any matched ticker.
    """
    wanted = None if tickers is None else {str(t).upper() for t in tickers}
    ttm_start = (as_of - timedelta(days=365)).isoformat()
    recent_start = (as_of - timedelta(days=30)).isoformat()
    by_ticker: dict[str, dict] = {}
    for row in rows:
        ticker = row.get("ticker")
        if not ticker or (wanted is not None and ticker not in wanted):
            continue
        bucket = by_ticker.setdefault(
            ticker,
            {"ttm_obligated": 0.0, "ttm_rows": [], "by_agency": {}, "recent": [], "largest": None},
        )
        action = str(row.get("action_date") or "")
        amount = _amount(row.get("obligated_amount"))
        if action >= ttm_start:
            bucket["ttm_rows"].append(row)
            bucket["ttm_obligated"] += amount
            agency = row.get("agency") or "Unknown"
            quarter = fiscal_quarter(date.fromisoformat(action)) if len(action) == 10 else ""
            key = f"{agency}|{quarter}"
            bucket["by_agency"][key] = bucket["by_agency"].get(key, 0.0) + amount
        if action >= recent_start:
            bucket["recent"].append(row)
            if bucket["largest"] is None or amount > _amount(bucket["largest"].get("obligated_amount")):
                bucket["largest"] = row
    rolled = []
    for ticker, bucket in sorted(by_ticker.items()):
        agencies = [
            {"agency": key.split("|", 1)[0], "quarter": key.split("|", 1)[1], "obligated": value}
            for key, value in sorted(bucket["by_agency"].items())
        ]
        recent = sorted(bucket["recent"], key=lambda row: str(row.get("action_date") or ""), reverse=True)[:10]
        largest = bucket["largest"]
        ttm_rows = bucket["ttm_rows"]
        observations = [str(row.get("ingested_at")) for row in ttm_rows if row.get("ingested_at")]
        rolled.append(
            {
                "ticker": ticker,
                "as_of": as_of.isoformat(),
                "ttm_obligated": bucket["ttm_obligated"] if ttm_rows else None,
                "ttm_awards_count": len(ttm_rows),
                "source_ids": sorted({str(row["source"]) for row in ttm_rows if row.get("source")}),
                "observed_at": max(observations) if observations else None,
                "by_agency": agencies,
                "new_awards_30d": len(bucket["recent"]),
                "largest_award_30d": None if largest is None else largest.get("award_id"),
                "recent": [
                    {
                        "award_id": row.get("award_id"),
                        "agency": row.get("agency"),
                        "amount": row.get("obligated_amount"),
                        "date": row.get("action_date"),
                        "source_id": row.get("source"),
                        "observed_at": row.get("ingested_at"),
                        "url": row.get("source_url"),
                    }
                    for row in recent
                ],
            }
        )
    return rolled


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    import time

    from api_keys import api_key
    from http_client import get_client, request_with_retry
    from lake import read_json, read_parquet_prefix, write_json, write_parquet
    from observability import job_handler, logger, source_run
    from universe import watchlist_names, watchlist_tickers

    @job_handler(JOB_ID)
    def run(event, context):
        now = datetime.now(ET)
        if not is_business_day(now.date()):
            logger.info("market_calendar_skip", extra={"job": JOB_ID, "reason": "federal holiday or weekend"})
            return {"status": "skipped", "reason": "federal holiday or weekend"}
        ingested_at = datetime.now(UTC).isoformat(timespec="seconds")
        run_id = ingested_at.replace(":", "")
        state = read_json(STATE_KEY) or {}
        start = catch_up_start(state.get("watermark"), now.date())
        calls_today = int((state.get("sam_calls") or {}).get(now.date().isoformat(), 0))
        budget = sam_budget(calls_today)
        partial = False
        awards: list[dict] = []
        opportunities: list[dict] = []
        tickers = watchlist_tickers()
        names = watchlist_names(tickers, read_json)
        use_watchlist_names(names)
        searches = recipient_searches(tickers, names)
        sam_query = sam_query_for(searches, now.date())

        def time_left() -> bool:
            remaining = getattr(context, "get_remaining_time_in_millis", None)
            return remaining is None or remaining() > 60_000

        usaspending_ok = True
        with get_client() as http:
            for index, (_ticker, name) in enumerate(searches):
                if not time_left():
                    logger.warning("usaspending_time_budget_skip", extra={"job": JOB_ID, "recipient": name})
                    usaspending_ok = False
                    continue
                with source_run("usaspending") as record:
                    for page in range(1, USASPENDING_MAX_PAGES + 1):
                        payload = request_with_retry(
                            http,
                            "POST",
                            "https://api.usaspending.gov/api/v2/search/spending_by_award/",
                            json=usaspending_body(name, start, now.date().isoformat(), page),
                        ).json()
                        found = parse_usaspending(payload, ingested_at)
                        day = now.date().isoformat()
                        write_json(payload, f"raw/contracts/usaspending/date={day}/{run_id}-{index}-{page}.json")
                        awards.extend(found)
                        record["rows"] += len(found)
                        if not (payload.get("page_metadata") or {}).get("hasNext"):
                            break
                if record["outcome"] == "failure":
                    usaspending_ok = False
            used = 0
            if budget and sam_query:
                with source_run("sam-awards") as record:
                    response = http.get(
                        "https://api.sam.gov/contract-awards/v1/search",
                        params={"api_key": api_key("sam-gov"), "q": sam_query, "limit": 10},
                    )
                    used += 1
                    outcome = sam_status(response.status_code, response.text)
                    if outcome == "partial":
                        partial = True
                        logger.warning("sam_key_rejected", extra={"hint": "rotate sam-gov"})
                        record["rows"] = 0
                        raise RuntimeError("SAM.gov rejected the key")
                    payload = response.json()
                    write_json(payload, f"raw/contracts/sam/date={now.date().isoformat()}/{run_id}.json")
                    found = parse_sam(payload, ingested_at)
                    awards.extend(found)
                    record["rows"] = len(found)
            if budget >= 2 and sam_query:
                with source_run("sam-opportunities") as record:
                    response = http.get(
                        "https://api.sam.gov/opportunities/v2/search",
                        params=sam_opportunities_params(sam_query, now.date(), api_key("sam-gov")),
                    )
                    used += 1
                    if sam_status(response.status_code, response.text) == "partial":
                        partial = True
                        logger.warning("sam_key_rejected", extra={"hint": "rotate sam-gov"})
                        raise RuntimeError("SAM.gov rejected the key")
                    payload = response.json()
                    found = parse_sam(payload, ingested_at, kind="sam-opportunities")
                    opportunities.extend(found)
                    record["rows"] = len(found)
            with source_run("dod") as record:
                feed = parse_dod_feed(request_with_retry(http, "GET", DOD_CONTRACTS_FEED).text)
                latest = feed[0] if feed else None
                if latest and latest["link"]:
                    article = http.get(latest["link"])
                    if article.status_code == 403:
                        # Article HTML is bot-blocked; USAspending still carries these awards.
                        logger.warning(
                            "dod_article_blocked",
                            extra={"job": JOB_ID, "url": latest["link"], "status": 403},
                        )
                    else:
                        article.raise_for_status()
                        found = parse_dod(article.text, ingested_at, now.date())
                        awards.extend(found)
                        record["rows"] = len(found)
            with source_run("nasa") as record:
                response = fetch_with_rate_limit(
                    lambda: request_with_retry(http, "GET", NASA_FEED, return_statuses=frozenset({429})),
                    time.sleep,
                )
                if response is None:
                    logger.warning(
                        "nasa_feed_rate_limited",
                        extra={"job": JOB_ID, "url": NASA_FEED, "status": 429, "attempts": NASA_RATE_LIMIT_ATTEMPTS},
                    )
                    text = ""
                else:
                    response.raise_for_status()
                    text = response.text
                found = parse_dod(text, ingested_at, now.date()) if "contract" in text.lower() else []
                awards.extend(found)
                record["rows"] = len(found)

        merged = reconcile(awards)
        if merged:
            by_year: dict[int, list[dict]] = {}
            for row in merged:
                action = row.get("action_date") or now.date().isoformat()
                if len(str(action)) >= 10:
                    year = fiscal_year(date.fromisoformat(str(action)[:10]))
                else:
                    year = fiscal_year(now.date())
                by_year.setdefault(year, []).append(row)
            for year, group in by_year.items():
                key = f"curated/contracts/fiscal_year={year}/contracts.parquet"
                existing = read_parquet_prefix(f"curated/contracts/fiscal_year={year}/")
                prior = existing.to_dicts() if not existing.is_empty() else []
                write_parquet(pl.DataFrame(merge_awards([*prior, *group])), key)
        if opportunities:
            write_parquet(
                pl.DataFrame(opportunities),
                f"curated/contract_opportunities/date={now.date().isoformat()}/{run_id}.parquet",
            )
        all_rows = read_parquet_prefix("curated/contracts/")
        rolled = rollup_contracts(all_rows.to_dicts() if not all_rows.is_empty() else merged, now.date(), tickers)
        if rolled:
            write_json(rolled, "curated/contracts_rollup/latest.json")
        state.setdefault("sam_calls", {})[now.date().isoformat()] = calls_today + used
        # USAspending is the backfill source; keep the old watermark so a failed run retries the window.
        if usaspending_ok:
            state["watermark"] = now.date().isoformat()
        write_json(state, STATE_KEY)
        return {"status": "partial" if partial else "success", "awards": len(merged), "sam_calls": used}

    return run(event, context)
