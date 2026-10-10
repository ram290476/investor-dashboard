"""D5: government awards for SpaceX and Tesla.

USAspending needs no key and does the backfill. SAM.gov is capped at about 3 calls
a run and 10 a day. A 401 or 403 is a partial run, not a failed job.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
# Contract award fields from the spending_by_award contract (base fields plus contract fields).
USASPENDING_CONTRACT_FIELDS = frozenset(
    {
        "Award ID",
        "Recipient Name",
        "Recipient DUNS Number",
        "recipient_id",
        "Awarding Agency",
        "Awarding Agency Code",
        "Awarding Sub Agency",
        "Awarding Sub Agency Code",
        "Funding Agency",
        "Funding Agency Code",
        "Funding Sub Agency",
        "Funding Sub Agency Code",
        "Place of Performance City Code",
        "Place of Performance State Code",
        "Place of Performance Country Code",
        "Place of Performance Zip5",
        "Description",
        "Last Modified Date",
        "Base Obligation Date",
        "prime_award_recipient_id",
        "generated_internal_id",
        "def_codes",
        "COVID-19 Obligations",
        "COVID-19 Outlays",
        "Infrastructure Obligations",
        "Infrastructure Outlays",
        "Recipient UEI",
        "Recipient Location",
        "Primary Place of Performance",
        "Start Date",
        "End Date",
        "Award Amount",
        "Total Outlays",
        "Contract Award Type",
        "NAICS",
        "PSC",
    }
)
DOD_FEED_URL = "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max=10"
NASA_FEED_URL = "https://www.nasa.gov/news-release/feed/"
SAM_OPPORTUNITIES_URL = "https://api.sam.gov/opportunities/v2/search"
NAME_TO_TICKER = (
    ("space exploration technologies", "SPCX"),
    ("spacex", "SPCX"),
    ("tesla", "TSLA"),
)
# UEIs are filled as awards identify them. A listed UEI wins over the name match.
UEI_TO_TICKER: dict[str, str] = {}


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


def ticker_for(name: str | None, uei: str | None = None) -> str | None:
    if uei and uei in UEI_TO_TICKER:
        return UEI_TO_TICKER[uei]
    lowered = (name or "").lower()
    for needle, ticker in NAME_TO_TICKER:
        if needle in lowered:
            return ticker
    return None


def sam_budget(calls_today: int, wanted: int = SAM_CALLS_PER_RUN) -> int:
    """Never more than 3 calls this run, and never more than 10 in the day."""
    remaining = SAM_CALLS_PER_DAY - max(0, calls_today)
    return max(0, min(SAM_CALLS_PER_RUN, wanted, remaining))


def sam_status(status_code: int) -> str:
    """An expired or rejected SAM key is partial. Other HTTP errors still surface."""
    if status_code in {401, 403}:
        return "partial"
    if status_code >= 400:
        raise RuntimeError(f"SAM.gov returned HTTP {status_code}")
    return "ok"


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
    """spending_by_award request. award_type_codes is required; fields stay on the contract allowlist."""
    fields = [
        "Award ID",
        "Recipient Name",
        "Recipient UEI",
        "Award Amount",
        "Awarding Agency",
        "Awarding Sub Agency",
        "Start Date",
        "Description",
    ]
    unknown = [name for name in fields if name not in USASPENDING_CONTRACT_FIELDS]
    if unknown:
        raise ValueError("USAspending fields are not in the contract allowlist: " + ", ".join(unknown))
    if not USASPENDING_CONTRACT_CODES:
        raise ValueError("award_type_codes is required")
    return {
        "subawards": False,
        "filters": {
            "recipient_search_text": [recipient],
            "award_type_codes": list(USASPENDING_CONTRACT_CODES),
            "time_period": [{"start_date": start, "end_date": end}],
        },
        "fields": fields,
        "limit": USASPENDING_PAGE_SIZE,
        "page": page,
    }


def sam_opportunity_params(today: date, api_key: str, title: str = "SpaceX") -> dict:
    """One opportunities search. postedFrom and postedTo are required and span at most a year."""
    start = today - timedelta(days=364)
    return {
        "api_key": api_key,
        "postedFrom": start.strftime("%m/%d/%Y"),
        "postedTo": today.strftime("%m/%d/%Y"),
        "title": title,
        "limit": 10,
        "offset": 0,
    }


def provider_failure(status: int) -> str | None:
    """Name a non-2xx response. 429 is throttling, not a parser failure."""
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "access_restricted"
    if status in (400, 404, 422):
        return "request_rejected"
    if status >= 400:
        return "upstream"
    return None


class ProviderStatus(RuntimeError):
    """Safe source failure. The message has a status, not a URL or key."""


def raise_for_provider(status: int, source: str) -> None:
    kind = provider_failure(status)
    if kind is None:
        return
    if kind == "rate_limited":
        raise ProviderStatus(f"{source} HTTP 429; throttled, not a parser failure")
    if kind == "access_restricted":
        raise ProviderStatus(f"{source} HTTP {status}; access restricted, no rows synthesized")
    raise ProviderStatus(f"{source} HTTP {status}")


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


def _item_text(item, tag: str) -> str:
    found = item.find(tag)
    if found is None or not found.text:
        return ""
    return " ".join(found.text.split())


def rss_items(text: str) -> list[dict]:
    """Title and description from an RSS document. Item links are not requested."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ValueError("response was not an RSS feed") from None
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    if root_name not in {"rss", "rdf"}:
        raise ValueError("response was not an RSS feed")
    return [
        {
            "title": _item_text(item, "title"),
            "description": _item_text(item, "description"),
            "link": _item_text(item, "link"),
        }
        for item in root.iter("item")
    ]


def feed_coverage(text: str, ingested_at: str, day: date) -> tuple[list[dict], dict]:
    """Awards found in feed text, plus coverage. Article URLs are never fetched."""
    items = rss_items(text)
    awards: list[dict] = []
    for item in items:
        body = "\n\n".join(part for part in (item["title"], item["description"]) if part)
        awards.extend(parse_dod(body, ingested_at, day))
    return awards, {
        "feed_items": len(items),
        "award_rows": len(awards),
        "article_urls_fetched": 0,
    }


def parse_dod(text: str, ingested_at: str, day: date) -> list[dict]:
    """DoD paragraphs that name SpaceX or Tesla. A PIID is reconciled; otherwise the row stays provisional."""
    rows = []
    for paragraph in (text or "").split("\n\n"):
        folded = paragraph.lower()
        if "contract" not in folded and "award" not in folded:
            continue
        ticker = ticker_for(paragraph)
        if not ticker:
            continue
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
                "recipient_name": "SpaceX" if ticker == "SPCX" else "Tesla",
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


def rollup_contracts(rows: list[dict], as_of: date) -> list[dict]:
    """Trailing-12-month obligations, agency mix, and the last 30 days of new awards."""
    ttm_start = (as_of - timedelta(days=365)).isoformat()
    recent_start = (as_of - timedelta(days=30)).isoformat()
    by_ticker: dict[str, dict] = {}
    for row in rows:
        ticker = row.get("ticker")
        if ticker not in {"TSLA", "SPCX"}:
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
    from api_keys import api_key
    from http_client import get_client, request_with_retry
    from lake import read_json, read_parquet_prefix, write_json, write_parquet
    from observability import job_handler, logger, source_run

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

        usaspending_ok = True
        with get_client() as http:
            for index, name in enumerate(("Space Exploration Technologies", "Tesla")):
                with source_run("usaspending") as record:
                    for page in range(1, USASPENDING_MAX_PAGES + 1):
                        try:
                            response = request_with_retry(
                                http,
                                "POST",
                                "https://api.usaspending.gov/api/v2/search/spending_by_award/",
                                json=usaspending_body(name, start, now.date().isoformat(), page),
                            )
                        except Exception as exc:
                            status = getattr(getattr(exc, "response", None), "status_code", None)
                            if isinstance(status, int):
                                raise_for_provider(status, "USAspending")
                            raise
                        raise_for_provider(response.status_code, "USAspending")
                        payload = response.json()
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
            if budget:
                with source_run("sam-awards") as record:
                    response = http.get(
                        "https://api.sam.gov/contract-awards/v1/search",
                        params={"api_key": api_key("sam-gov"), "q": "SpaceX", "limit": 10},
                    )
                    used += 1
                    outcome = sam_status(response.status_code)
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
            if budget >= 2:
                with source_run("sam-opportunities") as record:
                    response = http.get(
                        SAM_OPPORTUNITIES_URL,
                        params=sam_opportunity_params(now.date(), api_key("sam-gov")),
                    )
                    used += 1
                    if response.status_code == 429:
                        raise_for_provider(429, "SAM opportunities")
                    if sam_status(response.status_code) == "partial":
                        partial = True
                        logger.warning("sam_key_rejected", extra={"hint": "rotate sam-gov"})
                        raise RuntimeError("SAM.gov rejected the key")
                    raise_for_provider(response.status_code, "SAM opportunities")
                    payload = response.json()
                    found = parse_sam(payload, ingested_at, kind="sam-opportunities")
                    opportunities.extend(found)
                    record["rows"] = len(found)
                    record["coverage"] = f"opportunity_rows={len(found)}"
            with source_run("dod") as record:
                response = request_with_retry(
                    http,
                    "GET",
                    DOD_FEED_URL,
                    return_statuses=frozenset({403, 404}),
                )
                raise_for_provider(response.status_code, "DoD contracts feed")
                found, coverage = feed_coverage(response.text, ingested_at, now.date())
                awards.extend(found)
                record["rows"] = coverage["award_rows"]
                record["coverage"] = (
                    f"feed_items={coverage['feed_items']};award_rows={coverage['award_rows']};"
                    "article_urls_fetched=0"
                )
            with source_run("nasa") as record:
                try:
                    response = request_with_retry(http, "GET", NASA_FEED_URL)
                except Exception as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status == 429:
                        raise_for_provider(429, "NASA")
                    raise
                raise_for_provider(response.status_code, "NASA")
                found, coverage = feed_coverage(response.text, ingested_at, now.date())
                awards.extend(found)
                record["rows"] = coverage["award_rows"]
                record["coverage"] = (
                    f"feed_items={coverage['feed_items']};award_rows={coverage['award_rows']};"
                    "article_urls_fetched=0"
                )

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
        rolled = rollup_contracts(all_rows.to_dicts() if not all_rows.is_empty() else merged, now.date())
        if rolled:
            write_json(rolled, "curated/contracts_rollup/latest.json")
        state.setdefault("sam_calls", {})[now.date().isoformat()] = calls_today + used
        # USAspending is the backfill source; keep the old watermark so a failed run retries the window.
        if usaspending_ok:
            state["watermark"] = now.date().isoformat()
        write_json(state, STATE_KEY)
        return {"status": "partial" if partial else "success", "awards": len(merged), "sam_calls": used}

    return run(event, context)
