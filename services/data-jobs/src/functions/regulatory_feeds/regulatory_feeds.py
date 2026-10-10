"""H3: SEC filings and public regulatory events for TSLA and SPCX.

EDGAR needs no key. The contact User-Agent is the existing SEC_USER_AGENT env var.
In production the job refuses to run while that value is missing or still the placeholder.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import polars as pl

JOB_ID = "H3"
ET = ZoneInfo("America/New_York")
TSLA_CIK = "0001318605"
BACKFILL_DAYS = 90
PLACEHOLDER_USER_AGENT = "invdash-collector (contact: set SEC_USER_AGENT)"
ITEM_CLASS = {"2.02": "earnings", "5.02": "officer", "1.01": "agreement", "8.01": "other"}
FORM4_CLASS = {"P": "buy", "S": "sell", "A": "grant"}
STATE_KEY = "curated/filings/_state.json"


def is_business_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    import holidays

    return day not in holidays.US(years=[day.year])


def assert_prod_user_agent(value: str | None) -> None:
    """Refuse to call EDGAR in production with a missing or placeholder contact string."""
    if not value or value.strip() == PLACEHOLDER_USER_AGENT or "set SEC_USER_AGENT" in value:
        raise RuntimeError("SEC_USER_AGENT must identify a contact and must not be the placeholder")


def earnings_filing_detail(rows: list[dict], today: date) -> dict | None:
    """A new 8-K Item 2.02 filed today. Item 9.01 means the EX-99.1 exhibit is in the filing index."""
    fresh = [
        row for row in rows
        if row.get("form") == "8-K"
        and "2.02" in (row.get("items") or [])
        and str(row.get("filed_at") or "")[:10] == today.isoformat()
    ]
    if not fresh:
        return None
    first = fresh[0]
    exhibits = ["EX-99.1"] if "9.01" in (first.get("items") or []) else []
    return {
        "form": "8-K",
        "exhibits": exhibits,
        "ticker": first.get("ticker"),
        "accession": first.get("accession_no"),
        "items": list(first.get("items") or []),
    }


def classify_8k(items: list[str]) -> str:
    found = {ITEM_CLASS[item] for item in items if item in ITEM_CLASS}
    for name in ("earnings", "officer", "agreement", "other"):
        if name in found:
            return name
    return "other"


def classify_form4(code: str) -> str:
    return FORM4_CLASS.get((code or "").upper(), "other")


def classify_event(title: str, agencies: list[str]) -> str:
    text = f"{title} {' '.join(agencies)}".lower()
    if "sanction" in text or "ofac" in text:
        return "sanctions"
    if "tariff" in text or any(name in text for name in ("ustr", "international trade", "customs", "bis")):
        return "tariff"
    if "fomc" in text or "federal reserve" in text or "monetary" in text:
        return "monetary"
    if "launch" in text or "spacex" in text:
        return "launch"
    if "spectrum" in text or "starlink" in text or "fcc" in text:
        return "spectrum"
    return "other"


def event_id(source: str, source_url: str) -> str:
    return hashlib.sha256(f"{source}{source_url}".encode()).hexdigest()


def dedupe_filings(rows: list[dict]) -> list[dict]:
    """Accession numbers are the primary key. A later copy of the same filing replaces the earlier one."""
    by_key: dict[str, dict] = {}
    for row in rows:
        accession = row.get("accession_no")
        if accession:
            by_key[accession] = row
    return list(by_key.values())


def dedupe_events(rows: list[dict]) -> list[dict]:
    by_key: dict[str, dict] = {}
    for row in rows:
        key = row.get("event_id")
        if key:
            by_key[key] = row
    return list(by_key.values())


def _cik(value: str) -> str:
    return str(value).zfill(10)


def resolve_cik(company_tickers: dict, symbol: str) -> str | None:
    for row in company_tickers.values():
        if isinstance(row, dict) and str(row.get("ticker", "")).upper() == symbol.upper():
            return _cik(row.get("cik_str"))
    return None


def filing_url(cik: str, accession: str, document: str) -> str:
    bare = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{bare}/{document}"


def parse_submissions(payload: dict, ticker: str, today: date, ingested_at: str) -> list[dict]:
    """Turn the submissions `recent` arrays into filing rows from the last 90 days."""
    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    cutoff = (today - timedelta(days=BACKFILL_DAYS)).isoformat()
    rows = []
    cik = _cik(payload.get("cik") or "0")
    count = len(forms)
    for index in range(count):
        form = forms[index]
        if form not in {"8-K", "10-Q", "10-K", "4"}:
            continue
        filed = (recent.get("filingDate") or [""] * count)[index]
        if not filed or filed < cutoff:
            continue
        accession = (recent.get("accessionNumber") or [""] * count)[index]
        document = (recent.get("primaryDocument") or [""] * count)[index]
        raw_items = (recent.get("items") or [""] * count)[index] or ""
        items = [part.strip() for part in str(raw_items).split(",") if part.strip()]
        title = (recent.get("primaryDocDescription") or [""] * count)[index] or form
        kind = classify_8k(items) if form == "8-K" else ("insider" if form == "4" else "periodic")
        rows.append(
            {
                "accession_no": accession,
                "cik": cik,
                "ticker": ticker,
                "form": form,
                "filed_at": filed,
                "report_date": (recent.get("reportDate") or [""] * count)[index] or None,
                "primary_doc_url": filing_url(cik, accession, document),
                "items": items,
                "insider_name": None,
                "insider_role": None,
                "shares": None,
                "price": None,
                "txn_code": None,
                "filing_class": kind,
                "title": title,
                "ingested_at": ingested_at,
            }
        )
    return rows


def _text(node, name: str) -> str:
    if node is None:
        return ""
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1] == name and child.text:
            return child.text.strip()
    return ""


def parse_form4(xml_text: str) -> dict:
    """Pull the reporting owner and the first coded transaction out of a Form 4 XML document."""
    root = ElementTree.fromstring(xml_text)
    name = ""
    role = ""
    code = ""
    shares = None
    price = None
    for node in root.iter():
        local = node.tag.rsplit("}", 1)[-1]
        if local == "rptOwnerName" and node.text:
            name = node.text.strip()
        elif local == "officerTitle" and node.text:
            role = node.text.strip()
        elif local == "transactionCode" and node.text and not code:
            code = node.text.strip().upper()
        elif local == "transactionShares" and shares is None:
            shares = float(_text(node, "value") or 0)
        elif local == "transactionPricePerShare" and price is None:
            raw = _text(node, "value")
            price = float(raw) if raw else 0.0
    return {
        "insider_name": name or None,
        "insider_role": role or None,
        "txn_code": code or None,
        "shares": shares,
        "price": price,
        "filing_class": classify_form4(code),
    }


def insider_flows(filings: list[dict], as_of: date) -> dict:
    """Net open-market insider shares and value over the last 30 days. Grants move shares only."""
    start = (as_of - timedelta(days=30)).isoformat()
    net_shares = 0.0
    net_value = 0.0
    for row in filings:
        if row.get("form") != "4" or str(row.get("filed_at") or "") < start:
            continue
        shares = row.get("shares")
        if not isinstance(shares, (int, float)):
            continue
        code = classify_form4(row.get("txn_code") or "")
        price = float(row.get("price") or 0)
        sign = 1 if code in {"buy", "grant"} else -1 if code == "sell" else 0
        net_shares += sign * float(shares)
        if code in {"buy", "sell"}:
            net_value += sign * float(shares) * price
    return {"net_shares": net_shares, "net_value": net_value}


def parse_federal_register(payload: dict, ingested_at: str) -> list[dict]:
    events = []
    for doc in payload.get("results") or []:
        url = doc.get("html_url") or doc.get("url") or ""
        if not url:
            continue
        agencies = [agency.get("name") or "" for agency in doc.get("agencies") or []]
        title = doc.get("title") or ""
        kind = classify_event(title, agencies)
        events.append(
            {
                "event_id": event_id("federal-register", url),
                "event_ts": doc.get("publication_date") or ingested_at[:10],
                "type": kind,
                "source": "federal-register",
                "source_url": url,
                "title": title,
                "tickers": [],
                "agencies": [name for name in agencies if name],
                "severity": "high" if kind in {"sanctions", "earnings"} else "medium",
                "ingested_at": ingested_at,
            }
        )
    return events


def parse_rss_events(xml_text: str, source: str, ingested_at: str) -> list[dict]:
    if not xml_text:
        return []
    root = ElementTree.fromstring(xml_text)
    events = []
    for item in root.iter("item"):
        link = item.find("link")
        url = ((link.text if link is not None else "") or "").strip()
        title = (item.findtext("title") or "").strip()
        if not url or not title:
            continue
        pub = item.findtext("pubDate") or ""
        try:
            stamp = parsedate_to_datetime(pub).astimezone(UTC).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            stamp = ingested_at
        kind = classify_event(title, [source])
        events.append(
            {
                "event_id": event_id(source, url),
                "event_ts": stamp,
                "type": kind,
                "source": source,
                "source_url": url,
                "title": title,
                "tickers": ["SPCX"] if kind == "launch" else [],
                "agencies": [source],
                "severity": "medium",
                "ingested_at": ingested_at,
            }
        )
    return events


def parse_launches(payload: dict, ingested_at: str) -> list[dict]:
    events = []
    for launch in payload.get("results") or []:
        url = launch.get("url") or ""
        title = launch.get("name") or "SpaceX launch"
        if not url:
            continue
        events.append(
            {
                "event_id": event_id("launch-library", url),
                "event_ts": launch.get("net") or ingested_at,
                "type": "launch",
                "source": "launch-library",
                "source_url": url,
                "title": title,
                "tickers": ["SPCX"],
                "agencies": ["SpaceX"],
                "severity": "medium",
                "ingested_at": ingested_at,
            }
        )
    return events


def daily_counts(events: list[dict]) -> list[dict]:
    counts: dict[tuple[str, str], int] = {}
    for event in events:
        day = str(event.get("event_ts") or "")[:10]
        kind = event.get("type") or "other"
        if len(day) == 10:
            counts[(day, kind)] = counts.get((day, kind), 0) + 1
    return [{"date": day, "type": kind, "count": count} for (day, kind), count in sorted(counts.items())]


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    import time

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
        user_agent = os.getenv("SEC_USER_AGENT", "")
        if os.getenv("INVDASH_ENV") == "prod":
            assert_prod_user_agent(user_agent)
        ingested_at = datetime.now(UTC).isoformat(timespec="seconds")
        run_id = ingested_at.replace(":", "")
        state = read_json(STATE_KEY) or {}
        headers = {"User-Agent": user_agent} if user_agent else None
        filings: list[dict] = []
        events: list[dict] = []
        companies = {"TSLA": TSLA_CIK}

        with get_client(headers=headers) as http:
            with source_run("edgar-tickers") as record:
                payload = request_with_retry(http, "GET", "https://www.sec.gov/files/company_tickers.json").json()
                spcx = resolve_cik(payload, "SPCX")
                if spcx:
                    companies["SPCX"] = spcx
                record["rows"] = 1 if spcx else 0
            time.sleep(0.2)
            for ticker, cik in companies.items():
                with source_run(f"edgar-{ticker}") as record:
                    conditional = {}
                    etag = (state.get("etag") or {}).get(cik)
                    if etag:
                        conditional["headers"] = {"If-None-Match": etag}
                    response = request_with_retry(
                        http,
                        "GET",
                        f"https://data.sec.gov/submissions/CIK{cik}.json",
                        **conditional,
                    )
                    if response.status_code == 304:
                        record["rows"] = 0
                    else:
                        body = response.json()
                        write_json(body, f"raw/edgar/date={now.date().isoformat()}/{cik}-{run_id}.json")
                        rows = parse_submissions(body, ticker, now.date(), ingested_at)
                        filings.extend(rows)
                        state.setdefault("etag", {})[cik] = response.headers.get("ETag")
                        record["rows"] = len(rows)
                time.sleep(0.2)
            with source_run("federal-register") as record:
                payload = request_with_retry(
                    http,
                    "GET",
                    "https://www.federalregister.gov/api/v1/documents.json",
                    params={"per_page": 20, "conditions[term]": "tariff OR sanctions OR spectrum"},
                ).json()
                write_json(payload, f"raw/regfeeds/federal-register/date={now.date().isoformat()}/{run_id}.json")
                found = parse_federal_register(payload, ingested_at)
                events.extend(found)
                record["rows"] = len(found)
            for source, url in (
                ("fed", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
                ("white-house", "https://www.whitehouse.gov/presidential-actions/feed/"),
            ):
                with source_run(source) as record:
                    text = request_with_retry(http, "GET", url).text
                    raw_key = f"raw/regfeeds/{source}/date={now.date().isoformat()}/{run_id}.json"
                    write_json({"xml": text[:4000]}, raw_key)
                    found = parse_rss_events(text, source, ingested_at)
                    events.extend(found)
                    record["rows"] = len(found)
            with source_run("launch-library") as record:
                payload = request_with_retry(
                    http,
                    "GET",
                    "https://ll.thespacedevs.com/2.3.0/launches/upcoming/",
                    params={"lsp__name": "SpaceX", "limit": 5},
                ).json()
                write_json(payload, f"raw/regfeeds/launch-library/date={now.date().isoformat()}/{run_id}.json")
                found = parse_launches(payload, ingested_at)
                events.extend(found)
                record["rows"] = len(found)
            with source_run("fcc") as record:
                payload = request_with_retry(
                    http,
                    "GET",
                    "https://publicapi.fcc.gov/ecfs/filings",
                    params={"q": "starlink", "api_key": api_key("api-data-gov")},
                ).json()
                record["rows"] = len(payload.get("filings") or payload.get("results") or [])

        fresh_filings = dedupe_filings(filings)
        if fresh_filings:
            frame = pl.DataFrame(fresh_filings)
            for (ticker, year), part in (
                frame.with_columns(pl.col("filed_at").str.slice(0, 4).alias("_year"))
                .partition_by(["ticker", "_year"], as_dict=True)
                .items()
            ):
                key = f"curated/filings/ticker={ticker}/year={int(year)}/filings.parquet"
                existing = read_parquet_prefix(f"curated/filings/ticker={ticker}/year={int(year)}/")
                merged = dedupe_filings((existing.to_dicts() if not existing.is_empty() else []) + part.to_dicts())
                write_parquet(pl.DataFrame(merged).drop("_year", strict=False), key)
        fresh_events = dedupe_events(events)
        if fresh_events:
            write_parquet(pl.DataFrame(fresh_events), f"curated/events/date={now.date().isoformat()}/{run_id}.parquet")
            counts_key = f"curated/events_daily/date={now.date().isoformat()}/counts.parquet"
            write_parquet(pl.DataFrame(daily_counts(fresh_events)), counts_key)
        write_json(state, STATE_KEY)
        result = {"status": "success", "filings": len(fresh_filings), "events": len(fresh_events)}
        filing = earnings_filing_detail(fresh_filings, now.date())
        if filing:
            result["filing"] = filing
        return result

    return run(event, context)
