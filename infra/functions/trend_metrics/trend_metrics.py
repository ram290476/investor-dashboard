"""trend_metrics build job: how each macro driver is moving and how much it matters to each ticker.

Runs after D4 (daily close) and after M1 (release days), triggered by their
'Job Finished' events. Reads curated/prices_daily and curated/macro_daily,
writes Parquet and a small latest-day JSON to the serving prefix:

    serving/trend_metrics/ticker=<T>/trend_metrics.parquet   full history
    serving/trend_metrics/latest/<T>.json                     last trading day, one row per driver

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


def split_inputs(prices_daily: pl.DataFrame, macro_daily: pl.DataFrame, etfs: list[str]):
    """prices_daily: (ticker, date, close). macro_daily: (series_id, obs_date, value)."""
    drivers: dict[str, pl.DataFrame] = {}
    for (sid,), frame in macro_daily.group_by(["series_id"]):
        if sid in DRIVERS:
            drivers[sid] = frame.select(pl.col("obs_date").alias("date"), pl.col("value").cast(pl.Float64))
    for etf in etfs:
        f = prices_daily.filter(pl.col("ticker") == etf)
        if not f.is_empty():
            drivers[f"ETF:{etf}"] = f.select("date", pl.col("close").alias("value").cast(pl.Float64))
    return drivers


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from lake import read_parquet_prefix, write_json, write_parquet
    from observability import job_handler
    from universe import collection_universe, user_ticker_union

    @job_handler("TREND")
    def run(event, context):
        uni = collection_universe(user_ticker_union())
        prices = read_parquet_prefix("curated/prices_daily/")
        macro = read_parquet_prefix("curated/macro_daily/")
        drivers = split_inputs(prices, macro, uni["etfs"])
        written = 0
        for t in uni["equities"]:
            px = prices.filter(pl.col("ticker") == t).select("date", pl.col("close").cast(pl.Float64))
            out = ticker_metrics(t, px, drivers)
            if out.is_empty():
                continue
            write_parquet(out, f"serving/trend_metrics/ticker={t}/trend_metrics.parquet")
            last = out.filter(pl.col("date") == out["date"].max())
            write_json(
                {"ticker": t, "date": str(last["date"][0]), "rows": last.to_dicts()},
                f"serving/trend_metrics/latest/{t}.json",
            )
            written += 1
        return {"tickers": written, "as_of": str(datetime.now(UTC).date()), "dropped_over_cap": uni["dropped"]}

    return run(event, context)
