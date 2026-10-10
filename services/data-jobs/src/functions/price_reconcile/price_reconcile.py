"""RECONCILE: nightly split/dividend check that re-adjusts stored price history (DS-05 Yahoo events).

Stored price columns (curated/prices_daily, one row per ticker and date):
    close_raw      actual traded close (never changes once written)
    close          split-adjusted as of the last write/rebuild (Yahoo's `close`)
    adj_close      split- and dividend-adjusted as of the last write/rebuild (Yahoo's `adjclose`)
    volume         shares the dashboard charts. Yahoo consolidated when that session was reconciled
    volume_iex     Alpaca IEX volume for a daily_prices row; null on Yahoo-only backfill rows
    volume_source  DS-05 (Yahoo consolidated) or DS-02 (Alpaca IEX). Independent of source_id
    source_id      price source: DS-02 daily_prices, DS-05 backfill

daily_prices only re-adjusts its own 7-day window, so a split or dividend leaves older rows on
the old basis. Every weekday evening this job, for each collected ticker:

1. asks Yahoo for the last WINDOW_DAYS of events (events=split,div);
2. rebuilds the ticker's full history when there is an event it has not seen yet that falls
   after the first stored day, or when stored rows lack close_raw / adj_close (backfill rows and
   rows written before these columns existed);
3. a rebuild fetches Yahoo daily history from the first stored day to today and, per day:
       close_raw = stored close_raw for daily_prices (DS-02) rows, else Yahoo close x split factor
       close     = close_raw / split factor      (product of split ratios after that day)
       adj_close = close x Yahoo dividend factor (adjclose / close for that day; 1 after Yahoo's last day)
   and writes it through lake.upsert_prices, rewriting every yearly partition of the ticker.
   Stored backfill rows Yahoo no longer returns are left as they are.
4. reconciles volume from that same Yahoo response (no extra call). The last VOLUME_WINDOW_SESSIONS
   NYSE sessions take Yahoo consolidated volume when Yahoo returned the day. Older volumes are not
   rescaled. Every row gets volume_source, filled from source_id when the column was missing.
   Rows outside the window that daily_prices wrote stay IEX and stay marked DS-02.

State (seen event ids per ticker) lives in curated/prices_daily/_reconcile/state.json.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl

STATE_KEY = "curated/prices_daily/_reconcile/state.json"
WINDOW_DAYS = 35  # recent-event lookback; covers a missed week of runs
VOLUME_WINDOW_SESSIONS = 10  # consolidated volume refresh; older rows are marked, not rescaled
SEEN_RETENTION_DAYS = 400
DAILY_SOURCE = "DS-02"  # Alpaca IEX price rows, and IEX volume
YAHOO_SOURCE = "DS-05"  # Yahoo consolidated volume, and backfill prices
PRICE_COLUMNS = [
    "ticker",
    "date",
    "close",
    "close_raw",
    "adj_close",
    "volume",
    "volume_iex",
    "volume_source",
    "source_id",
]


def current_date() -> date:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/New_York")).date()


def event_id(event: dict) -> str:
    if event["type"] == "split":
        return f"split:{event['date'].isoformat()}:{event.get('label') or event['ratio']}"
    return f"dividend:{event['date'].isoformat()}:{event['amount']:.6f}"


def _event_day(eid: str) -> date:
    return date.fromisoformat(eid.split(":")[1])


def needs_rebuild(existing: pl.DataFrame, recent_events: list[dict], seen: set[str]) -> str | None:
    """Why this ticker's history must be rebuilt, or None."""
    if existing.is_empty():
        return None
    for column in ("close_raw", "adj_close"):
        if column not in existing.columns or existing[column].null_count():
            return f"missing {column}"
    first = existing["date"].min()
    new = [event_id(e) for e in recent_events if e["date"] > first and event_id(e) not in seen]
    return f"new event {', '.join(new)}" if new else None


def _iex_volume_unlabeled() -> pl.Expr:
    """A daily_prices row whose stored volume is still the IEX print (no volume_iex yet)."""
    source = pl.col("ex_volume_source")
    return source.is_null() | (source == DAILY_SOURCE)


def _split_factor(events: list[dict]) -> pl.Expr:
    """Product of split ratios with an ex-date after each row's date."""
    factor = pl.lit(1.0)
    for e in events:
        if e["type"] == "split":
            factor = factor * pl.when(pl.col("date") < pl.lit(e["date"])).then(pl.lit(e["ratio"])).otherwise(1.0)
    return factor


def _with_columns(frame: pl.DataFrame, columns: dict[str, pl.DataType]) -> pl.DataFrame:
    missing = [pl.lit(None, dtype=t).alias(c) for c, t in columns.items() if c not in frame.columns]
    return frame.with_columns(missing) if missing else frame


def rebuild_history(existing: pl.DataFrame, yahoo_rows: list[dict], events: list[dict]) -> pl.DataFrame:
    """Re-adjusted rows for one ticker (see module docstring). Returns PRICE_COLUMNS."""
    from lake import dedupe_prices

    ticker = existing["ticker"][0]
    ex = _with_columns(
        dedupe_prices(existing),
        {
            "close_raw": pl.Float64,
            "adj_close": pl.Float64,
            "volume": pl.Int64,
            "volume_iex": pl.Int64,
            "volume_source": pl.Utf8,
            "source_id": pl.Utf8,
        },
    ).select(
        pl.col("date").cast(pl.Date),
        pl.col("close").cast(pl.Float64).alias("ex_close"),
        pl.col("close_raw").cast(pl.Float64).alias("ex_close_raw"),
        pl.col("volume").cast(pl.Int64).alias("ex_volume"),
        pl.col("volume_iex").cast(pl.Int64).alias("ex_volume_iex"),
        pl.col("volume_source").cast(pl.Utf8).alias("ex_volume_source"),
        pl.col("source_id").cast(pl.Utf8).alias("ex_source"),
    )
    y = _with_columns(
        pl.DataFrame(yahoo_rows) if yahoo_rows else pl.DataFrame({"date": []}, schema={"date": pl.Date}),
        {"close": pl.Float64, "adj_close": pl.Float64, "volume": pl.Int64},
    ).select(
        pl.col("date").cast(pl.Date),
        pl.col("close").cast(pl.Float64).alias("y_close"),
        pl.col("adj_close").cast(pl.Float64).alias("y_adj"),
        pl.col("volume").cast(pl.Int64).alias("y_volume"),
    )
    dividend_factor = (
        y.filter(pl.col("y_close").is_not_null() & (pl.col("y_close") > 0) & pl.col("y_adj").is_not_null())
        .select("date", (pl.col("y_adj") / pl.col("y_close")).alias("div_factor"))
        .sort("date")
    )
    daily = pl.col("ex_source") == DAILY_SOURCE
    merged = (
        ex.join(y, on="date", how="full", coalesce=True)
        .filter(daily.fill_null(False) | pl.col("y_close").is_not_null())  # untouched: backfill rows Yahoo lacks
        .with_columns(_split_factor(events).alias("split_factor"))
        .with_columns(
            pl.when(daily.fill_null(False))
            .then(pl.coalesce("ex_close_raw", "ex_close"))
            .otherwise(pl.col("y_close") * pl.col("split_factor"))
            .alias("close_raw"),
            pl.when(daily.fill_null(False))
            .then(pl.lit(DAILY_SOURCE))
            .otherwise(pl.lit(YAHOO_SOURCE))
            .alias("source_id"),
            pl.when(daily.fill_null(False))
            .then(pl.col("ex_volume"))
            .otherwise(pl.coalesce("y_volume", "ex_volume"))
            .alias("volume"),
            pl.when(pl.col("ex_volume_source").is_not_null())
            .then(pl.col("ex_volume_source"))
            .when(daily.fill_null(False))
            .then(pl.lit(DAILY_SOURCE))
            .otherwise(pl.lit(YAHOO_SOURCE))
            .alias("volume_source"),
            pl.when(pl.col("ex_volume_iex").is_not_null())
            .then(pl.col("ex_volume_iex"))
            .when(daily.fill_null(False) & _iex_volume_unlabeled())
            .then(pl.col("ex_volume"))
            .otherwise(pl.lit(None, dtype=pl.Int64))
            .alias("volume_iex"),
        )
        .with_columns((pl.col("close_raw") / pl.col("split_factor")).alias("close"))
        .sort("date")
        .join_asof(dividend_factor, on="date", strategy="forward")
        .with_columns(
            (pl.col("close") * pl.col("div_factor").fill_null(1.0)).alias("adj_close"),
            pl.lit(ticker).alias("ticker"),
        )
    )
    return merged.select(PRICE_COLUMNS)


def recent_sessions(as_of: date, sessions: int = VOLUME_WINDOW_SESSIONS) -> list[date]:
    """The `sessions` NYSE sessions on or before `as_of`, oldest first."""
    from daily_prices import is_market_day

    if sessions < 1:
        raise ValueError("sessions must be positive")
    found: list[date] = []
    cursor = as_of
    for _ in range(366):
        if is_market_day(cursor):
            found.append(cursor)
            if len(found) == sessions:
                return list(reversed(found))
        cursor -= timedelta(days=1)
    raise RuntimeError(f"found {len(found)} NYSE sessions on or before {as_of.isoformat()}")


def _stamp_volume_columns(frame: pl.DataFrame) -> pl.DataFrame:
    """Fill volume_source from source_id, and volume_iex from volume on IEX rows that lack it."""
    stamped = _with_columns(
        frame,
        {"volume": pl.Int64, "volume_iex": pl.Int64, "volume_source": pl.Utf8, "source_id": pl.Utf8},
    ).with_columns(pl.col("date").cast(pl.Date), pl.col("volume").cast(pl.Int64), pl.col("volume_iex").cast(pl.Int64))
    iex = pl.col("source_id") == DAILY_SOURCE
    consolidated = pl.col("source_id") == YAHOO_SOURCE
    return stamped.with_columns(
        pl.when(pl.col("volume_source").is_not_null())
        .then(pl.col("volume_source"))
        .when(iex)
        .then(pl.lit(DAILY_SOURCE))
        .when(consolidated)
        .then(pl.lit(YAHOO_SOURCE))
        .otherwise(pl.col("volume_source"))
        .alias("volume_source")
    ).with_columns(
        pl.when(pl.col("volume_iex").is_null() & (pl.col("volume_source") == DAILY_SOURCE))
        .then(pl.col("volume"))
        .otherwise(pl.col("volume_iex"))
        .alias("volume_iex")
    )


def _yahoo_volume(yahoo_rows: list[dict]) -> pl.DataFrame:
    rows = []
    for row in yahoo_rows:
        raw = row.get("volume")
        if raw is None:
            continue
        rows.append({"date": row["date"], "y_volume": int(raw)})
    schema = {"date": pl.Date, "y_volume": pl.Int64}
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Date), pl.col("y_volume").cast(pl.Int64)).unique(
        "date", keep="last"
    )


def reconcile_volume(
    existing: pl.DataFrame,
    yahoo_rows: list[dict],
    as_of: date,
    sessions: int = VOLUME_WINDOW_SESSIONS,
) -> pl.DataFrame:
    """Stamp every row's volume source. Replace volume only inside the last `sessions`.

    Inside the window, Yahoo consolidated volume replaces `volume` and sets `volume_source`
    to DS-05. `volume_iex` keeps the IEX print. Outside the window, volume is unchanged and a
    missing `volume_source` is copied from `source_id`.
    """
    if existing.is_empty() or "date" not in existing.columns:
        return existing
    window = recent_sessions(as_of, sessions)
    stamped = _stamp_volume_columns(existing)
    merged = stamped.join(_yahoo_volume(yahoo_rows), on="date", how="left")
    in_window = pl.col("date").is_in(window) & pl.col("y_volume").is_not_null()
    return merged.with_columns(
        pl.when(in_window).then(pl.col("y_volume")).otherwise(pl.col("volume")).cast(pl.Int64).alias("volume"),
        pl.when(in_window).then(pl.lit(YAHOO_SOURCE)).otherwise(pl.col("volume_source")).alias("volume_source"),
    ).drop("y_volume")


def _volume_view(frame: pl.DataFrame) -> pl.DataFrame:
    return _stamp_volume_columns(frame).select(
        pl.col("date").cast(pl.Date),
        pl.col("volume").cast(pl.Int64),
        pl.col("volume_iex").cast(pl.Int64),
        pl.col("volume_source").cast(pl.Utf8),
    ).sort("date")


def needs_volume_write(before: pl.DataFrame, after: pl.DataFrame) -> bool:
    """True when the stored frame is missing a volume source or the reconciled values differ."""
    if after.is_empty():
        return False
    if "volume_source" not in before.columns or before["volume_source"].null_count() > 0:
        return True
    if "volume_iex" not in before.columns:
        return True
    return not _volume_view(before).equals(_volume_view(after))


def _chart_rows(payload: dict, ticker: str) -> list[dict]:
    from collectors import yahoo

    result = (payload.get("chart") or {}).get("result") or []
    if not result or not (result[0].get("timestamp") or []):
        return []
    return yahoo.parse_chart(payload, ticker)


def handler(event, context):
    from collectors import yahoo
    from http_client import get_client, request_with_retry
    from lake import read_json, read_prices, upsert_prices, write_json
    from observability import job_handler, logger, source_run
    from universe import collection_universe, user_ticker_union

    def chart(http, ticker: str, start: date, end: date) -> dict:
        params = yahoo.chart_params(start=start, end=end)
        return request_with_retry(http, "GET", yahoo.CHART_URL.format(ticker=ticker), params=params).json()

    @job_handler("RECONCILE")
    def run(event, context):
        today = current_date()
        tomorrow = today + timedelta(days=1)
        cutoff = today - timedelta(days=SEEN_RETENTION_DAYS)
        uni = collection_universe(user_ticker_union())
        tickers = list(dict.fromkeys([*uni["equities"], *uni["etfs"]]))
        state = read_json(STATE_KEY) or {"tickers": {}}
        rebuilt: list[str] = []
        failed: list[str] = []

        with get_client() as http:
            for ticker in tickers:
                entry = dict(state["tickers"].get(ticker, {}))
                seen = set(entry.get("seen_events", []))
                with source_run(YAHOO_SOURCE) as record:
                    recent = chart(http, ticker, today - timedelta(days=WINDOW_DAYS), tomorrow)
                    events = yahoo.parse_events(recent)
                    existing = read_prices(ticker)
                    reason = needs_rebuild(existing, events, seen)
                    if reason:
                        payload = chart(http, ticker, existing["date"].min(), tomorrow)
                        events = yahoo.parse_events(payload)
                        rebuilt_rows = rebuild_history(existing, yahoo.parse_chart(payload, ticker), events)
                        rows = reconcile_volume(rebuilt_rows, _chart_rows(payload, ticker), today)
                        upsert_prices(rows)
                        record["rows"] = rows.height
                        rebuilt.append(ticker)
                        entry.update(last_rebuild=today.isoformat(), last_reason=reason, rows_rebuilt=rows.height)
                        logger.info("price_history_rebuilt", extra={"ticker": ticker, "reason": reason})
                    else:
                        rows = reconcile_volume(existing, _chart_rows(recent, ticker), today)
                        if needs_volume_write(existing, rows):
                            upsert_prices(rows)
                            record["rows"] = rows.height
                            logger.info(
                                "volume_reconciled",
                                extra={"ticker": ticker, "sessions": VOLUME_WINDOW_SESSIONS, "rows": rows.height},
                            )
                    seen |= {event_id(e) for e in events}
                    entry["seen_events"] = sorted(e for e in seen if _event_day(e) >= cutoff)
                    entry["last_checked"] = today.isoformat()
                    state["tickers"][ticker] = entry
                if record["outcome"] == "failure":
                    failed.append(ticker)

        state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        write_json(state, STATE_KEY, cache_seconds=0)
        result = {
            "status": "partial" if failed else "success",
            "checked": len(tickers),
            "rebuilt": rebuilt,
            "failed_tickers": failed,
        }
        logger.info("price_reconcile_finished", extra=result)
        return result

    return run(event, context)
