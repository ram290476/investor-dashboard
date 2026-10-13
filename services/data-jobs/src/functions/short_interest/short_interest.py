"""short_interest job (DS-91): poll FINRA each trading evening, store new settlement dates only.

A ticker that has never had a full history pull (new watchlist names, and every name on
the first run after this ships) is fetched without the high-water mark. Those rows are
merged into the existing settlement_date partitions. Later runs stay incremental.
"""

from __future__ import annotations

import base64
import logging
import os
import random
import time
from datetime import UTC, datetime

import polars as pl

STATE_KEY = "curated/short_interest/_state.json"
PAGE_LIMIT = 5000
NON_JSON_ATTEMPTS = 3
logger = logging.getLogger(__name__)


def tickers_needing_history(tickers: list[str], state: dict) -> list[str]:
    """Tickers that still need a full FINRA pull. A successful pull records them on the state."""
    done = {str(item).upper() for item in state.get("history_complete") or []}
    return [ticker for ticker in tickers if ticker.upper() not in done]


def merge_settlement_rows(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """One row per (ticker, settlement_date). Incoming rows replace an existing duplicate."""
    merged: dict[tuple[str, str], dict] = {}
    for row in [*existing, *incoming]:
        ticker = str(row.get("ticker") or "").upper()
        day = str(row.get("settlement_date") or "")[:10]
        if not ticker or not day:
            continue
        merged[(ticker, day)] = {**row, "ticker": ticker, "settlement_date": day}
    return [merged[key] for key in sorted(merged)]


def partition_key(day: str) -> str:
    return f"curated/short_interest/settlement_date={day}/short_interest.parquet"


def _access_token(http) -> str:
    from api_keys import api_key
    from collectors import finra

    basic = base64.b64encode(f"{api_key('finra-client-id')}:{api_key('finra-client-secret')}".encode()).decode()
    token_response = http.post(finra.TOKEN_URL, headers={"Authorization": f"Basic {basic}"})
    token_response.raise_for_status()
    token = token_response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("FINRA token response omitted access_token")
    return token


def fetch_short_interest(http, token: str, tickers: list[str], after: str | None) -> list[dict]:
    """Every settlement FINRA returns for these tickers, paging past the 5000-row cap."""
    from collectors import finra

    if not tickers:
        return []
    rows: list[dict] = []
    offset = 0
    while True:
        records = _fetch_page(http, token, finra.query_body(tickers, after, offset=offset, limit=PAGE_LIMIT))
        rows.extend(finra.parse(records))
        if len(records) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    return rows


def _fetch_page(http, token: str, body: dict, *, sleep=time.sleep) -> list[dict]:
    """One FINRA page. 204/empty is an empty page; a non-JSON 2xx is retried with backoff."""
    from collectors import finra

    for attempt in range(NON_JSON_ATTEMPTS):
        response = http.post(
            finra.DATA_URL,
            json=body,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        response.raise_for_status()
        try:
            return finra.decode_records(
                response.status_code, response.headers.get("content-type", ""), response.content
            )
        except finra.FinraResponseError:
            logger.warning(
                "finra_non_json_response",
                extra={
                    "status": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "body_head": response.content[:200].decode("utf-8", "replace"),
                    "attempt": attempt + 1,
                },
            )
            if attempt + 1 == NON_JSON_ATTEMPTS:
                raise
            sleep(min(2.0 * (2**attempt) * (0.5 + random.random()), 20.0))
    return []  # pragma: no cover - loop always returns or raises


def store_settlement_partition(rows: list[dict], day: str) -> None:
    """Merge rows into settlement_date=<day>, keeping one row per ticker."""
    from lake import upsert_ranked

    deduped = merge_settlement_rows([], rows)
    if not deduped:
        return
    frame = pl.DataFrame([{**row, "_rank": 1} for row in deduped])
    upsert_ranked(frame, partition_key(day), ["ticker", "settlement_date"], "_rank")


def handler(event, context):  # pragma: no cover - thin AWS wrapper over collectors.finra
    from collectors import finra
    from http_client import get_client
    from lake import read_json, write_json
    from observability import job_handler, source_run
    from universe import collection_universe, user_ticker_union

    @job_handler("SHORT")
    def run(event, context):
        state = read_json(STATE_KEY) or {"settlement_dates": []}
        known = set(state.get("settlement_dates") or [])
        tickers = collection_universe(user_ticker_union())["equities"]
        missing = tickers_needing_history(tickers, state)
        missing_set = set(missing)
        incremental = [ticker for ticker in tickers if ticker not in missing_set]
        rows: list[dict] = []
        with source_run("DS-91") as rec, get_client() as http:
            token = _access_token(http)
            for ticker in missing:
                rows.extend(fetch_short_interest(http, token, [ticker], None))
            if incremental:
                rows.extend(fetch_short_interest(http, token, incremental, max(known) if known else None))
            new_dates = finra.new_settlement_dates(rows, known)
            by_date: dict[str, list[dict]] = {}
            for row in rows:
                by_date.setdefault(row["settlement_date"], []).append(row)
            written = 0
            for day, part in sorted(by_date.items()):
                if day in known and not any(row["ticker"] in missing_set for row in part):
                    continue
                store_settlement_partition(part, day)
                written += len(merge_settlement_rows([], part))
            rec["rows"] = written
        if rec["outcome"] != "success":
            raise RuntimeError(f"FINRA collection failed ({rec['error_type']}); see DS-91 source-run logs")
        if missing or new_dates:
            state["settlement_dates"] = sorted(known | {row["settlement_date"] for row in rows})
            state["history_complete"] = sorted({*(state.get("history_complete") or []), *missing})
            state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            write_json(state, STATE_KEY)
        return {
            "new_settlement_dates": sorted(new_dates),
            "history_catchup": missing,
            "bucket": os.environ.get("LAKE_BUCKET"),
        }

    return run(event, context)
