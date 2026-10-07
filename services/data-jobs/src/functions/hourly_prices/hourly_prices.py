"""H1: 1-hour bars for the ticker universe. Intraday display only — D4 owns the daily close.

Primary source is Alpaca IEX (DS-02). Finnhub quotes (DS-04) and Alpha Vantage GLOBAL_QUOTE
(DS-03) are used only when Alpaca fails, and each row records which source it came from.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import polars as pl

JOB_ID = "H1"
ET = ZoneInfo("America/New_York")
EARLY_CLOSE = time(13, 5)
BACKFILL_DAYS = 30
SOURCE_RANK = {"DS-02": 3, "DS-04": 2, "DS-03": 1}
HOURLY_PREFIX = "curated/prices_hourly/"


def is_market_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    import holidays

    return day not in holidays.financial_holidays("NYSE", years=[day.year])


def is_early_close(day: date) -> bool:
    """NYSE 13:00 ET close: July 3 (when the 4th is a weekday), Black Friday, Christmas Eve.

    A day that is itself a full holiday (July 3 when Independence Day is a Saturday, Christmas
    Eve when Christmas is a Saturday) is not an early close.
    """
    if day.weekday() >= 5 or not is_market_day(day):
        return False
    if day.month == 11 and day.weekday() == 4:
        thursday = day - timedelta(days=1)
        if thursday.month == 11 and thursday.weekday() == 3 and 22 <= thursday.day <= 28:
            return True
    if day.month == 12 and day.day == 24:
        return True
    if day.month == 7 and day.day == 3 and date(day.year, 7, 4).weekday() < 5:
        return True
    return False


def should_collect(now: datetime) -> tuple[bool, str]:
    """False on weekends, NYSE holidays, and after the 13:05 run on early-close days."""
    local = now.astimezone(ET)
    day = local.date()
    if not is_market_day(day):
        return False, "NYSE holiday or weekend"
    if is_early_close(day) and (local.hour, local.minute) > (EARLY_CLOSE.hour, EARLY_CLOSE.minute):
        return False, "early close; last run was 13:05 ET"
    return True, ""


def fetch_start(last_ts: str | None, today: date) -> str:
    """First deploy and a new ticker pull 30 days; otherwise resume at the last stored bar."""
    if not last_ts:
        return (today - timedelta(days=BACKFILL_DAYS)).isoformat()
    stamp = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
    return stamp.astimezone(UTC).date().isoformat()


def to_hourly_rows(parsed: list[dict], source: str, ingested_at: str) -> list[dict]:
    rows = []
    for bar in parsed:
        stamp = datetime.fromisoformat(str(bar["bar_ts"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError(f"hourly bar timestamp for {bar.get('ticker')} has no timezone")
        rows.append(
            {
                "ticker": str(bar["ticker"]).upper(),
                "ts_utc": stamp.astimezone(UTC).isoformat(timespec="seconds"),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": int(bar["volume"] or 0),
                "vwap": None if bar.get("vwap") is None else float(bar["vwap"]),
                "trade_count": None if bar.get("trade_count") is None else int(bar["trade_count"]),
                "source": source,
                "ingested_at": ingested_at,
                "_rank": SOURCE_RANK[source],
            }
        )
    return rows


def finnhub_quote_bar(ticker: str, payload: dict, ingested_at: str) -> dict | None:
    price = payload.get("c")
    if not isinstance(price, (int, float)) or price == 0:
        return None
    stamp = payload.get("t")
    ts = datetime.fromtimestamp(int(stamp), UTC) if stamp else datetime.now(UTC)
    return to_hourly_rows(
        [
            {
                "ticker": ticker,
                "bar_ts": ts.isoformat(),
                "open": payload.get("o") or price,
                "high": payload.get("h") or price,
                "low": payload.get("l") or price,
                "close": price,
                "volume": 0,
                "vwap": None,
                "trade_count": None,
            }
        ],
        "DS-04",
        ingested_at,
    )[0]


def alpha_vantage_quote_bar(payload: dict, ingested_at: str) -> dict | None:
    quote = payload.get("Global Quote") or payload.get("Global Quote".lower()) or {}
    symbol = quote.get("01. symbol")
    price = quote.get("05. price")
    if not symbol or price in (None, "", "None"):
        return None
    day = quote.get("07. latest trading day") or ingested_at[:10]
    close = float(price)
    return to_hourly_rows(
        [
            {
                "ticker": symbol,
                "bar_ts": f"{day}T20:00:00+00:00",
                "open": float(quote.get("02. open") or close),
                "high": float(quote.get("03. high") or close),
                "low": float(quote.get("04. low") or close),
                "close": close,
                "volume": int(float(quote.get("06. volume") or 0)),
                "vwap": None,
                "trade_count": None,
            }
        ],
        "DS-03",
        ingested_at,
    )[0]


def select_bars(alpaca_rows: list[dict], alpaca_failed: bool, fallback_rows: list[dict]) -> list[dict]:
    """Backups run only when Alpaca fails. A successful Alpaca page is never overwritten here."""
    if alpaca_rows and not alpaca_failed:
        return alpaca_rows
    return fallback_rows or alpaca_rows


def dedupe_hourly(rows: list[dict]) -> list[dict]:
    """(ticker, ts_utc) primary key. DS-02 beats DS-04 and DS-03; otherwise the later row wins."""
    best: dict[tuple, dict] = {}
    for index, row in enumerate(rows):
        key = (row["ticker"], row["ts_utc"])
        current = best.get(key)
        rank = row.get("_rank", SOURCE_RANK.get(row["source"], 0))
        if current is None or rank > current["_rank"] or (rank == current["_rank"] and index >= current["_index"]):
            best[key] = {**row, "_rank": rank, "_index": index}
    cleaned = []
    for row in best.values():
        item = {k: v for k, v in row.items() if k not in ("_rank", "_index")}
        cleaned.append(item)
    return cleaned


def intraday_rollup(bars: list[dict], prior_close: float | None) -> dict | None:
    """Last price, change versus the prior daily close, session high/low, cumulative volume."""
    if not bars:
        return None
    ordered = sorted(bars, key=lambda row: row["ts_utc"])
    last = ordered[-1]
    change = None if not prior_close else last["close"] / prior_close - 1
    return {
        "as_of": last["ts_utc"],
        "last": last["close"],
        "change_pct": change,
        "session_high": max(row["high"] for row in ordered),
        "session_low": min(row["low"] for row in ordered),
        "volume": sum(int(row["volume"] or 0) for row in ordered),
        "bars": [{"ts": row["ts_utc"], "close": row["close"], "volume": row["volume"]} for row in ordered],
    }


def partition_key(ticker: str, year: int) -> str:
    return f"{HOURLY_PREFIX}ticker={ticker}/year={year}/prices.parquet"


def raw_key(day: date, run_id: str) -> str:
    return f"raw/alpaca_bars_1h/date={day.isoformat()}/{run_id}.json"


def store_hourly(rows: list[dict]) -> int:
    """Upsert into curated/prices_hourly only. Never writes curated/prices_daily."""
    from lake import upsert_ranked

    if not rows:
        return 0
    frame = pl.DataFrame(rows).with_columns(pl.col("source").replace_strict(SOURCE_RANK, default=0).alias("_rank"))
    stored = 0
    grouped = frame.with_columns(pl.col("ts_utc").str.slice(0, 4).cast(pl.Int32).alias("_year")).partition_by(
        ["ticker", "_year"], as_dict=True
    )
    for (ticker, year), part in grouped.items():
        key = partition_key(str(ticker), int(year))
        if "prices_daily" in key:
            raise RuntimeError("hourly bars must not be written to prices_daily")
        stored += upsert_ranked(part.drop("_year"), key, ["ticker", "ts_utc"], "_rank")
    return stored


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from api_keys import api_key
    from collectors import alpaca
    from http_client import get_client, request_with_retry
    from lake import read_parquet_prefix, write_json
    from observability import job_handler, logger, source_run
    from universe import collection_universe, user_ticker_union

    @job_handler(JOB_ID)
    def run(event, context):
        now = datetime.now(ET)
        ok, reason = should_collect(now)
        if not ok:
            logger.info("market_calendar_skip", extra={"job": JOB_ID, "reason": reason})
            return {"status": "skipped", "reason": reason, "date": now.date().isoformat()}

        universe = collection_universe(user_ticker_union())
        tickers = list(dict.fromkeys([*universe.get("equities", []), *universe.get("etfs", [])]))
        ingested_at = datetime.now(UTC).isoformat(timespec="seconds")
        start = fetch_start(None, now.date())
        existing = read_parquet_prefix(HOURLY_PREFIX)
        if not existing.is_empty() and "ts_utc" in existing.columns:
            last = existing.select(pl.col("ts_utc").max()).item()
            start = fetch_start(str(last) if last else None, now.date())
        end = (now.date() + timedelta(days=1)).isoformat()
        alpaca_rows: list[dict] = []
        failed_pages = 0
        raw_pages: list[dict] = []
        headers = {"APCA-API-KEY-ID": api_key("alpaca-key-id"), "APCA-API-SECRET-KEY": api_key("alpaca-secret-key")}
        with get_client(headers=headers) as http:
            for params in alpaca.bars_params(tickers, start, end, timeframe="1Hour", adjustment="split"):
                page_token = None
                while True:
                    page_params = {**params, **({"page_token": page_token} if page_token else {})}
                    payload: dict = {}
                    with source_run("DS-02") as record:
                        response = request_with_retry(
                            http, "GET", f"{alpaca.DATA}{alpaca.BARS_PATH}", params=page_params
                        )
                        payload = response.json()
                        parsed = alpaca.parse_bars(payload)
                        alpaca_rows.extend(to_hourly_rows(parsed, "DS-02", ingested_at))
                        record["rows"] = len(parsed)
                    raw_pages.append(payload)
                    if record["outcome"] == "failure":
                        failed_pages += 1
                        break
                    page_token = payload.get("next_page_token")
                    if not page_token:
                        break

        fallback: list[dict] = []
        if failed_pages or not alpaca_rows:
            finnhub_key = api_key("finnhub")
            av_key = api_key("alpha-vantage")
            with get_client() as http:
                for ticker in tickers:
                    with source_run("DS-04") as record:
                        payload = request_with_retry(
                            http,
                            "GET",
                            "https://finnhub.io/api/v1/quote",
                            params={"symbol": ticker, "token": finnhub_key},
                        ).json()
                        row = finnhub_quote_bar(ticker, payload, ingested_at)
                        if row:
                            fallback.append(row)
                        record["rows"] = 1 if row else 0
                    if record["outcome"] == "failure":
                        with source_run("DS-03") as av_record:
                            payload = request_with_retry(
                                http,
                                "GET",
                                "https://www.alphavantage.co/query",
                                params={"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": av_key},
                            ).json()
                            row = alpha_vantage_quote_bar(payload, ingested_at)
                            if row:
                                fallback.append(row)
                            av_record["rows"] = 1 if row else 0

        rows = dedupe_hourly(select_bars(alpaca_rows, bool(failed_pages), fallback))
        write_json({"pages": raw_pages}, raw_key(now.date(), ingested_at.replace(":", "")))
        stored = store_hourly(rows)
        if failed_pages and not rows:
            raise RuntimeError(f"Alpaca hourly bars failed for {failed_pages} page(s) and backups returned nothing")
        return {
            "status": "partial" if failed_pages else "success",
            "rows_stored": stored,
            "date": now.date().isoformat(),
        }

    return run(event, context)
