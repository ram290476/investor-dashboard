"""short_interest job (DS-91): poll FINRA each trading evening, store new settlement dates only."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime

import polars as pl


def handler(event, context):  # pragma: no cover - thin AWS wrapper over collectors.finra
    from api_keys import api_key
    from collectors import finra
    from http_client import get_client
    from lake import read_json, write_json, write_parquet
    from observability import job_handler, source_run
    from universe import collection_universe, user_ticker_union

    @job_handler("SHORT")
    def run(event, context):
        state_key = "curated/short_interest/_state.json"
        state = read_json(state_key) or {"settlement_dates": []}
        known = set(state["settlement_dates"])
        tickers = collection_universe(user_ticker_union())["equities"]
        with source_run("DS-91") as rec, get_client() as http:
            basic = base64.b64encode(f"{api_key('finra-client-id')}:{api_key('finra-client-secret')}".encode()).decode()
            token = http.post(finra.TOKEN_URL, headers={"Authorization": f"Basic {basic}"}).json()["access_token"]
            resp = http.post(
                finra.DATA_URL,
                json=finra.query_body(tickers, max(known) if known else None),
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            resp.raise_for_status()
            rows = finra.parse(resp.json())
            new_dates = finra.new_settlement_dates(rows, known)
            for d in sorted(new_dates):
                part = [r for r in rows if r["settlement_date"] == d]
                write_parquet(pl.DataFrame(part), f"curated/short_interest/settlement_date={d}/short_interest.parquet")
            rec["rows"] = sum(r["settlement_date"] in new_dates for r in rows)
        if new_dates:
            state["settlement_dates"] = sorted(known | new_dates)
            state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            write_json(state, state_key)
        return {"new_settlement_dates": sorted(new_dates), "bucket": os.environ.get("LAKE_BUCKET")}

    return run(event, context)
