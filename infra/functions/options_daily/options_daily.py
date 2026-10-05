"""options_daily job (DS-92): put/call volume ratio and IV30 per ticker after the close.

Runs with D4. Disabled (OPTIONS_ENABLED=false) until a first manual run confirms the
free Alpaca account returns options snapshots on the indicative feed, and whether those
snapshots carry implied volatility. Writes curated/options_daily/date=<d>/options_daily.parquet.
"""

from __future__ import annotations

import os
from datetime import datetime

import polars as pl

ET_TZ = "America/New_York"


def handler(event, context):  # pragma: no cover - thin AWS wrapper over collectors.alpaca
    from zoneinfo import ZoneInfo

    from api_keys import api_key
    from collectors import alpaca
    from http_client import get_client
    from lake import write_parquet
    from observability import job_handler, source_run
    from universe import collection_universe, user_ticker_union

    @job_handler("OPTIONS")
    def run(event, context):
        if os.getenv("OPTIONS_ENABLED", "false") != "true" and not event.get("force"):
            return {"skipped": "OPTIONS_ENABLED is false"}
        today = datetime.now(ZoneInfo(ET_TZ)).date()
        headers = {"APCA-API-KEY-ID": api_key("alpaca-key-id"), "APCA-API-SECRET-KEY": api_key("alpaca-secret-key")}
        tickers = collection_universe(user_ticker_union())["equities"]
        out = []
        with get_client(headers=headers) as http:
            spots = http.get(
                f"{alpaca.DATA}/v2/stocks/snapshots", params={"symbols": ",".join(tickers), "feed": "iex"}
            ).json()
            for t in tickers:
                with source_run("DS-92") as rec:
                    snaps, token = {}, None
                    while True:
                        r = http.get(
                            alpaca.DATA + alpaca.CHAIN_PATH.format(underlying=t),
                            params=alpaca.chain_params(today, token),
                        )
                        r.raise_for_status()
                        body = r.json()
                        snaps.update(body.get("snapshots") or {})
                        token = body.get("next_page_token")
                        if not token:
                            break
                    spot = ((spots.get(t) or {}).get("latestTrade") or {}).get("p")
                    if not snaps or spot is None:
                        rec["rows"] = 0
                        continue
                    out.append({"ticker": t, "date": today, **alpaca.options_metrics(snaps, float(spot), today)})
                    rec["rows"] = 1
        if out:
            write_parquet(pl.DataFrame(out), f"curated/options_daily/date={today}/options_daily.parquet")
        return {"tickers": len(out), "iv_available": any(r["iv_available"] for r in out)}

    return run(event, context)
