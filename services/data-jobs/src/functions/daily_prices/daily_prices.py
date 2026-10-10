"""D4: collect recent Alpaca daily bars for the ticker universe and the index ETF proxies.

Each run requests the last 7 days three times, once per Alpaca adjustment, and stores per day:
    close_raw   adjustment=raw    the actual traded close
    close       adjustment=split  split-adjusted as of today (same meaning as Yahoo's close)
    adj_close   adjustment=all    split- and dividend-adjusted as of today (Yahoo's adjclose)
Rows older than the window are re-adjusted by the nightly price_reconcile job when a split or
dividend appears.

Volume on these rows is Alpaca's free IEX feed (volume_source DS-02, also kept on volume_iex).
That print is a few percent of consolidated volume. price_reconcile replaces the last 10 sessions
with Yahoo consolidated volume (DS-05) when Yahoo has the session.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl


def is_market_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    import holidays

    return day not in holidays.financial_holidays("NYSE", years=[day.year])


def collection_symbols(universe: dict[str, list[str]]) -> list[str]:
    """Equities plus the index ETF proxies (trend_metrics drivers), de-duplicated, order kept."""
    return list(dict.fromkeys([*universe.get("equities", []), *universe.get("etfs", [])]))


def normalize_daily_bars(payload: dict) -> list[dict]:
    rows = []
    for ticker, bars in (payload.get("bars") or {}).items():
        for bar in bars:
            stamp = datetime.fromisoformat(str(bar["t"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError(f"Alpaca returned a timezone-naive bar timestamp for {ticker}")
            rows.append(
                {
                    "ticker": ticker.upper(),
                    "date": stamp.astimezone(UTC).date(),
                    "close": float(bar["c"]),
                    "volume": int(bar["v"]),
                    "volume_iex": int(bar["v"]),
                    "volume_source": "DS-02",
                    "source_id": "DS-02",
                }
            )
    return rows


# Alpaca adjustment -> stored column
ADJUSTMENTS: dict[str, str] = {"raw": "close_raw", "split": "close", "all": "adj_close"}


def combine_daily_bars(raw: list[dict], split: list[dict], adjusted: list[dict]) -> list[dict]:
    """Merge one normalized row list per adjustment into stored rows keyed by (ticker, date).

    The raw call defines which days exist (and the traded volume). A failed split call falls back
    to the raw close; a failed "all" call leaves adj_close null for the reconcile job to fill.
    """
    split_close = {(r["ticker"], r["date"]): r["close"] for r in split}
    adj_close = {(r["ticker"], r["date"]): r["close"] for r in adjusted}
    rows = []
    for r in raw:
        key = (r["ticker"], r["date"])
        rows.append(
            {
                "ticker": r["ticker"],
                "date": r["date"],
                "close": split_close.get(key, r["close"]),
                "close_raw": r["close"],
                "adj_close": adj_close.get(key),
                "volume": r["volume"],
                "volume_iex": r.get("volume_iex", r["volume"]),
                "volume_source": r.get("volume_source", "DS-02"),
                "source_id": r["source_id"],
            }
        )
    return rows


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from api_keys import api_key
    from collectors import alpaca
    from http_client import get_client, request_with_retry
    from lake import upsert_prices
    from observability import job_handler, logger, source_run
    from universe import collection_universe, user_ticker_union

    @job_handler("D4")
    def run(event, context):
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("America/New_York")).date()
        if not is_market_day(today):
            logger.info("market_calendar_skip", extra={"job": "D4", "date": today.isoformat()})
            return {"status": "skipped", "reason": "NYSE holiday or weekend", "date": today.isoformat()}

        tickers = collection_symbols(collection_universe(user_ticker_union()))
        headers = {
            "APCA-API-KEY-ID": api_key("alpaca-key-id"),
            "APCA-API-SECRET-KEY": api_key("alpaca-secret-key"),
        }
        start = (today - timedelta(days=7)).isoformat()
        end = (today + timedelta(days=1)).isoformat()
        stored = 0
        failed_pages = 0
        by_adjustment: dict[str, list[dict]] = {a: [] for a in ADJUSTMENTS}

        with get_client(headers=headers) as http:
            for adjustment in ADJUSTMENTS:
                for params in alpaca.bars_params(tickers, start, end, timeframe="1Day", adjustment=adjustment):
                    page_token = None
                    while True:
                        page_params = {**params}
                        if page_token:
                            page_params["page_token"] = page_token
                        payload = {}
                        with source_run("DS-02") as record:
                            response = request_with_retry(
                                http,
                                "GET",
                                f"{alpaca.DATA}{alpaca.BARS_PATH}",
                                params=page_params,
                            )
                            payload = response.json()
                            rows = normalize_daily_bars(payload)
                            by_adjustment[adjustment].extend(rows)
                            record["rows"] = len(rows)
                        if record["outcome"] == "failure":
                            failed_pages += 1
                            break
                        page_token = payload.get("next_page_token")
                        if not page_token:
                            break

        rows = combine_daily_bars(by_adjustment["raw"], by_adjustment["split"], by_adjustment["all"])
        if rows:
            upsert_prices(pl.DataFrame(rows))  # ticker=<T>/year=<YYYY>/prices.parquet
            stored = len(rows)

        if failed_pages:
            raise RuntimeError(f"Alpaca daily-bar collection failed for {failed_pages} page(s)")

        return {"status": "success", "date": today.isoformat(), "rows_stored": stored}

    return run(event, context)
