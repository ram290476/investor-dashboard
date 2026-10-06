"""D4: collect recent Alpaca daily bars for the ticker universe and the index ETF proxies."""

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
                    "adj_close": float(bar["c"]),
                    "volume": int(bar["v"]),
                    "source_id": "DS-02",
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

        with get_client(headers=headers) as http:
            for params in alpaca.bars_params(tickers, start, end, timeframe="1Day"):
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
                        if rows:
                            upsert_prices(pl.DataFrame(rows))  # ticker=<T>/year=<YYYY>/prices.parquet
                            record["rows"] = len(rows)
                            stored += len(rows)
                    if record["outcome"] == "failure":
                        failed_pages += 1
                        break
                    page_token = payload.get("next_page_token")
                    if not page_token:
                        break

        if failed_pages:
            raise RuntimeError(f"Alpaca daily-bar collection failed for {failed_pages} page(s)")

        return {"status": "success", "date": today.isoformat(), "rows_stored": stored}

    return run(event, context)
