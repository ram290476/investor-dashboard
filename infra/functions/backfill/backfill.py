"""backfill job: 5 years of daily closes per ticker from Yahoo (DS-05).

Triggers:
- TickerAdded event from the prefs API ({"detail": {"ticker": "RIVN"}}): backfill that ticker if
  the lake has no history for it yet.
- Manual / one-time setup (O1): {"tickers": ["TSLA", "SPCX", ...], "force": false}.
Writes curated/prices_daily/ticker=<T>/backfill.parquet. Daily updates come from D4.
"""

from __future__ import annotations

import polars as pl


def tickers_from_event(event: dict) -> tuple[list[str], bool]:
    if event.get("detail-type") == "TickerAdded":
        t = str((event.get("detail") or {}).get("ticker", "")).upper()
        return ([t] if t else []), False
    return [str(t).upper() for t in event.get("tickers", [])], bool(event.get("force"))


def handler(event, context):  # pragma: no cover - thin AWS wrapper over collectors.yahoo
    from collectors import yahoo
    from http_client import get_client
    from lake import LAKE_BUCKET, s3, write_parquet
    from observability import job_handler, source_run

    @job_handler("BACKFILL")
    def run(event, context):
        tickers, force = tickers_from_event(event)
        done = []
        with get_client() as http:
            for t in tickers:
                prefix = f"curated/prices_daily/ticker={t}/"
                exists = s3().list_objects_v2(Bucket=LAKE_BUCKET, Prefix=prefix, MaxKeys=1).get("KeyCount", 0)
                if exists and not force:
                    continue
                with source_run("DS-05") as rec:
                    r = http.get(yahoo.CHART_URL.format(ticker=t), params=yahoo.chart_params(5))
                    r.raise_for_status()
                    rows = yahoo.parse_chart(r.json(), t)
                    write_parquet(pl.DataFrame(rows), prefix + "backfill.parquet")
                    rec["rows"] = len(rows)
                    done.append(t)
        return {"backfilled": done}

    return run(event, context)
