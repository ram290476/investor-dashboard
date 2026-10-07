"""trend_metrics build job: how each macro driver is moving and how much it matters to each ticker.

Runs after D4 (daily close) and after M1 (release days), triggered by their
'Job Finished' events. Reads curated/prices_daily and curated/macro_daily,
writes Parquet and a small latest-day JSON to the serving prefix:

    serving/trend_metrics/ticker=<T>/trend_metrics.parquet   full history
    serving/trend_metrics/latest/<T>.json                     last trading day, one row per driver
                                                          plus PX:<T>, that ticker's own 20/50-day price trend

Key: (series_id, ticker, date). Columns, per the UI brief:
    value, chg_1w, chg_1m, chg_3m, z_1w, z_1m, z_3m      changes over 5/21/63 trading days and their z-scores
                                                          against the trailing 252-day distribution of that change
    range_pct_1y                                          where today's value sits in its 1-year high-low range (0-100)
    trend_state, days_in_state                            up / down / flat from z_1m (+/-0.5), and its run length
    corr_30d, corr_90d                                    rolling correlation of the driver's daily change
                                                          with the ticker's daily return
    effect        = corr_90d * z_1m
    net_pressure  = tanh(sum of effect over all drivers for that ticker and date / 3)
                    (the same value on every row of a ticker-date)
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import polars as pl

from lake import dedupe_prices  # noqa: F401  (re-exported: the shared (ticker, date) dedupe lives in lake)

WINDOWS = {"1w": 5, "1m": 21, "3m": 63}
Z_LOOKBACK, Z_MIN = 252, 126
RANGE_LOOKBACK, RANGE_MIN = 252, 60
TREND_Z = 0.5

# series_id -> how its change is measured: "pct" (prices, indices) or "level" (rates, spreads, ratios).
DRIVERS: dict[str, str] = {
    "DGS2": "level",
    "DGS10": "level",
    "DGS30": "level",
    "T10Y2Y": "level",
    "DFII10": "level",
    "T10YIE": "level",
    "DFEDTARU": "level",
    "EFFR": "level",
    "SOFR": "level",
    "VIXCLS": "pct",
    "DTWEXBGS": "pct",
    "DCOILWTICO": "pct",
    "USEPUINDXD": "pct",
    "CPI_YOY": "level",
    "CORE_CPI_YOY": "level",
    "PCE_YOY": "level",
    "ETF:SPY": "pct",
    "ETF:DIA": "pct",
    "ETF:QQQ": "pct",
    "ETF:IWM": "pct",
    "ETF:XLY": "pct",
    "ETF:ITA": "pct",
    "ETF:SMH": "pct",
}

OUTPUT_COLUMNS = [
    "series_id",
    "ticker",
    "date",
    "value",
    "chg_1w",
    "chg_1m",
    "chg_3m",
    "z_1w",
    "z_1m",
    "z_3m",
    "range_pct_1y",
    "trend_state",
    "days_in_state",
    "corr_30d",
    "corr_90d",
    "effect",
    "net_pressure",
]


def _change(col: pl.Expr, n: int, kind: str) -> pl.Expr:
    return (col / col.shift(n) - 1.0) if kind == "pct" else (col - col.shift(n))


def driver_metrics(ticker_px: pl.DataFrame, driver: pl.DataFrame, series_id: str, kind: str) -> pl.DataFrame:
    """ticker_px: (date, close) for one ticker. driver: (date, value) for one series, any frequency.

    The driver is carried forward onto the ticker's trading days, so a monthly series (CPI)
    holds its last released value until the next release.
    """
    if ticker_px.is_empty() or driver.is_empty():
        return pl.DataFrame()
    df = (
        ticker_px.sort("date")
        .join_asof(driver.sort("date"), on="date", strategy="backward")
        .filter(pl.col("value").is_not_null())
    )
    v, px = pl.col("value"), pl.col("close")
    lo = v.rolling_min(RANGE_LOOKBACK, min_samples=RANGE_MIN)
    hi = v.rolling_max(RANGE_LOOKBACK, min_samples=RANGE_MIN)
    exprs = []
    for name, n in WINDOWS.items():
        exprs.append(_change(v, n, kind).alias(f"chg_{name}"))
    df = df.with_columns(exprs)
    df = df.with_columns(
        [
            (
                (pl.col(f"chg_{w}") - pl.col(f"chg_{w}").rolling_mean(Z_LOOKBACK, min_samples=Z_MIN))
                / pl.col(f"chg_{w}").rolling_std(Z_LOOKBACK, min_samples=Z_MIN)
            ).alias(f"z_{w}")
            for w in WINDOWS
        ]
        + [
            (100.0 * (v - lo) / (hi - lo)).clip(0.0, 100.0).alias("range_pct_1y"),
            _change(v, 1, kind).alias("_d_driver"),
            (px / px.shift(1) - 1.0).alias("_d_ticker"),
        ]
    )
    df = df.with_columns(
        [
            pl.rolling_corr("_d_driver", "_d_ticker", window_size=30, min_samples=20).alias("corr_30d"),
            pl.rolling_corr("_d_driver", "_d_ticker", window_size=90, min_samples=60).alias("corr_90d"),
            pl.when(pl.col("z_1m") > TREND_Z)
            .then(pl.lit("up"))
            .when(pl.col("z_1m") < -TREND_Z)
            .then(pl.lit("down"))
            .when(pl.col("z_1m").is_not_null())
            .then(pl.lit("flat"))
            .otherwise(None)
            .alias("trend_state"),
        ]
    )
    # Clean up NaN/inf from zero variance or a flat 1-year range.
    float_cols = ["z_1w", "z_1m", "z_3m", "range_pct_1y", "corr_30d", "corr_90d"]
    df = df.with_columns([pl.when(pl.col(c).is_finite()).then(pl.col(c)).otherwise(None).alias(c) for c in float_cols])
    df = df.with_columns(pl.col("trend_state").rle_id().alias("_run"))
    df = df.with_columns((pl.int_range(pl.len()).over("_run") + 1).alias("days_in_state"))
    df = df.with_columns(
        pl.when(pl.col("trend_state").is_null()).then(None).otherwise(pl.col("days_in_state")).alias("days_in_state"),
        (pl.col("corr_90d") * pl.col("z_1m")).alias("effect"),
        pl.lit(series_id).alias("series_id"),
    )
    return df.drop(["_d_driver", "_d_ticker", "_run", "close"])


def ticker_metrics(ticker: str, ticker_px: pl.DataFrame, drivers: dict[str, pl.DataFrame]) -> pl.DataFrame:
    parts = [
        driver_metrics(ticker_px, frame, sid, DRIVERS.get(sid, "pct"))
        for sid, frame in drivers.items()
        if sid != f"ETF:{ticker}"  # an ETF is not its own driver
    ]
    parts = [p for p in parts if not p.is_empty()]
    if not parts:
        return pl.DataFrame(schema={c: pl.Utf8 for c in OUTPUT_COLUMNS})
    df = pl.concat(parts, how="diagonal_relaxed").with_columns(pl.lit(ticker).alias("ticker"))
    pressure = df.group_by("date").agg(pl.col("effect").sum().alias("_sum_effect"))
    pressure = pressure.with_columns(
        pl.col("_sum_effect").map_elements(lambda s: math.tanh(s / 3.0), return_dtype=pl.Float64).alias("net_pressure")
    )
    df = df.join(pressure.select("date", "net_pressure"), on="date", how="left")
    return df.select(OUTPUT_COLUMNS).sort(["date", "series_id"])


MACRO_COLUMNS = {"series_id", "obs_date", "value"}
PRICE_TREND_SHORT, PRICE_TREND_LONG = 20, 50


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _price_state_at(closes: list[float], index: int) -> str | None:
    """Same 20/50 rule as apps/web/trend-state.js. up/down/flat match the serving vocabulary."""
    if index < PRICE_TREND_LONG - 1:
        return None
    ma20 = _mean(closes[index - (PRICE_TREND_SHORT - 1) : index + 1])
    ma50 = _mean(closes[index - (PRICE_TREND_LONG - 1) : index + 1])
    close = closes[index]
    if close > ma20 and ma20 > ma50:
        return "up"
    if close < ma20 and ma20 < ma50:
        return "down"
    return "flat"


def price_trend_row(ticker: str, prices: pl.DataFrame) -> dict | None:
    """One PX:<ticker> row from that ticker's closes. None until 50 closes exist.

    days_in_state counts back only through sessions that have a 50-day average.
    from_start is true when that run reaches the first such session.
    """
    if prices.is_empty() or not {"date", "close"} <= set(prices.columns):
        return None
    frame = prices.filter(pl.col("close").is_not_null()).unique(subset=["date"], keep="last").sort("date")
    if frame.height < PRICE_TREND_LONG:
        return None
    closes = [float(value) for value in frame["close"].to_list()]
    dates = [str(value)[:10] for value in frame["date"].to_list()]
    latest = len(closes) - 1
    state = _price_state_at(closes, latest)
    if state is None:
        return None
    ma20 = _mean(closes[-PRICE_TREND_SHORT:])
    ma50 = _mean(closes[-PRICE_TREND_LONG:])
    close = closes[-1]
    days = 0
    since_index = latest
    for index in range(latest, PRICE_TREND_LONG - 2, -1):
        if _price_state_at(closes, index) != state:
            break
        days += 1
        since_index = index
    return {
        "series_id": f"PX:{ticker}",
        "ticker": ticker,
        "date": dates[-1],
        "trend_state": state,
        "days_in_state": days,
        "since": dates[since_index],
        "from_start": since_index == PRICE_TREND_LONG - 1,
        "vs_ma20": None if ma20 == 0 else close / ma20 - 1.0,
        "close": close,
        "ma20": ma20,
        "ma50": ma50,
    }


def latest_document(ticker: str, metrics: pl.DataFrame, prices: pl.DataFrame) -> dict | None:
    """Last driver day for one ticker, plus that ticker's own price trend when it can be computed."""
    rows: list[dict] = []
    as_of = None
    if metrics is not None and not metrics.is_empty():
        last = metrics.filter(pl.col("date") == metrics["date"].max())
        rows = last.to_dicts()
        as_of = str(last["date"][0])[:10]
    price_row = price_trend_row(ticker, prices)
    if price_row:
        rows.append(price_row)
        as_of = as_of or price_row["date"]
    if not rows:
        return None
    return {"ticker": ticker, "date": as_of, "rows": rows}


def analysis_prices(prices: pl.DataFrame) -> pl.DataFrame:
    """Prices for return and trend math: `close` becomes adj_close (split- and dividend-adjusted)
    where known, else the stored close (rows written before adj_close was populated)."""
    if prices.is_empty() or "close" not in prices.columns:
        return prices
    close = pl.col("close").cast(pl.Float64)
    value = pl.coalesce(pl.col("adj_close").cast(pl.Float64), close) if "adj_close" in prices.columns else close
    return prices.with_columns(value.alias("close"))


def split_inputs(prices_daily: pl.DataFrame, macro_daily: pl.DataFrame, etfs: list[str]):
    """prices_daily: (ticker, date, close). macro_daily: (series_id, obs_date, value).

    Either frame may be empty or column-less (read_parquet_prefix on an empty prefix, e.g. before
    the M1 macro collector exists); the missing drivers are simply skipped.
    """
    drivers: dict[str, pl.DataFrame] = {}
    if not macro_daily.is_empty() and MACRO_COLUMNS <= set(macro_daily.columns):
        for (sid,), frame in macro_daily.group_by(["series_id"]):
            if sid in DRIVERS:
                drivers[sid] = frame.select(pl.col("obs_date").alias("date"), pl.col("value").cast(pl.Float64))
    if prices_daily.is_empty() or not {"ticker", "date", "close"} <= set(prices_daily.columns):
        return drivers
    for etf in etfs:
        f = prices_daily.filter(pl.col("ticker") == etf)
        if not f.is_empty():
            drivers[f"ETF:{etf}"] = f.select("date", pl.col("close").alias("value").cast(pl.Float64))
    return drivers


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from lake import read_parquet_prefix, read_prices, write_json, write_parquet
    from observability import job_handler
    from universe import collection_universe, user_ticker_union

    @job_handler("TREND")
    def run(event, context):
        uni = collection_universe(user_ticker_union())
        # yearly partitions (+ any uncompacted legacy objects), deduped; adjusted closes for the math
        prices = analysis_prices(read_prices())
        macro = read_parquet_prefix("curated/macro_daily/")
        drivers = split_inputs(prices, macro, uni["etfs"])
        written = 0
        if prices.is_empty() or "ticker" not in prices.columns:
            return {"tickers": 0, "as_of": str(datetime.now(UTC).date()), "dropped_over_cap": uni["dropped"]}
        for t in uni["equities"]:
            px = prices.filter(pl.col("ticker") == t).select("date", pl.col("close").cast(pl.Float64))
            out = ticker_metrics(t, px, drivers)
            document = latest_document(t, out, px)
            if document is None:
                continue
            if not out.is_empty():
                write_parquet(out, f"serving/trend_metrics/ticker={t}/trend_metrics.parquet")
            write_json(document, f"serving/trend_metrics/latest/{t}.json")
            written += 1
        return {"tickers": written, "as_of": str(datetime.now(UTC).date()), "dropped_over_cap": uni["dropped"]}

    return run(event, context)
