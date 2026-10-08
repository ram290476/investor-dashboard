"""trend_metrics build job: how each macro driver is moving and how much it matters to each ticker.

Runs after D1, D4, M1, reconciliation and non-idle backfills, triggered by their
'Job Finished' events. Reads curated/prices_daily and curated/macro_daily,
writes Parquet and a small latest-day JSON to the serving prefix:

    serving/trend_metrics/series/metrics.parquet             shared (series_id, date) history
    serving/trend_metrics/ticker=<T>/trend_metrics.parquet   per-ticker links and full history
    serving/trend_metrics/latest/<T>.json                     last trading day, one row per driver
                                                          plus PX:<T>, that ticker's own 20/50-day price trend

Shared key: (series_id, date); ticker history key: (series_id, ticker, date).
Observation/availability provenance and display units are retained. Columns:
    value, chg_1w, chg_1m, chg_3m, z_1w, z_1m, z_3m      changes over 5/21/63 trading days and their z-scores
                                                          against the trailing 252-day distribution of that change
    range_pct_1y                                          where today's value sits in its 1-year high-low range (0-100)
    trend_state, days_in_state                            up / down / flat from z_1m (+/-0.5), and its run length
    corr_30d, corr_90d                                    rolling correlation of the driver's daily change
                                                          with the ticker's daily return. Monthly YoY
                                                          series leave both null; the driver arrow stays
                                                          on the z_1m rule above.
    effect        = corr_90d * z_1m
    net_pressure  = tanh(sum of effect over all drivers for that ticker and date / 3)
                    (the same value on every row of a ticker-date; null if no finite effects)
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
# Released once a month. A 30/90-day correlation of the carried-forward value is not a daily link.
MONTHLY_SERIES = frozenset({"CPI_YOY", "CORE_CPI_YOY", "PCE_YOY", "CORE_PCE_YOY"})

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
    "CORE_PCE_YOY": "level",
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
    "obs_date",
    "available_date",
    "unit",
    "change_unit",
    "change_1m_display",
    "pressure_direction",
    "strength",
    "effect_count",
]
RATE_SERIES = frozenset({
    "DGS2", "DGS10", "DGS30", "T10Y2Y", "DFII10", "T10YIE", "DFEDTARU", "EFFR", "SOFR",
})


def _change(col: pl.Expr, n: int, kind: str) -> pl.Expr:
    return (col / col.shift(n) - 1.0) if kind == "pct" else (col - col.shift(n))


def _finite(df: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    return df.with_columns([
        pl.when(pl.col(c).is_finite()).then(pl.col(c)).otherwise(None).alias(c) for c in columns
    ])


def series_metrics(calendar: pl.Series, driver: pl.DataFrame, series_id: str, kind: str) -> pl.DataFrame:
    """Shared metrics on stored trading sessions, aligned to when a print became available."""
    if calendar.is_empty() or driver.is_empty():
        return pl.DataFrame()
    driver = _finite(driver, ["value"]).filter(pl.col("value").is_not_null()).sort("date")
    if driver.is_empty():
        return pl.DataFrame()
    df = (
        pl.DataFrame({"date": calendar.unique().sort()})
        .join_asof(driver.unique(subset=["date"], keep="last", maintain_order=True), on="date", strategy="backward")
        .filter(pl.col("value").is_not_null())
    )
    v = pl.col("value")
    lo = v.rolling_min(RANGE_LOOKBACK, min_samples=RANGE_MIN)
    hi = v.rolling_max(RANGE_LOOKBACK, min_samples=RANGE_MIN)
    exprs = []
    for name, n in WINDOWS.items():
        exprs.append(_change(v, n, kind).alias(f"chg_{name}"))
    df = _finite(df.with_columns(exprs), [f"chg_{w}" for w in WINDOWS])
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
        ]
    )
    df = _finite(df, ["z_1w", "z_1m", "z_3m", "range_pct_1y"])
    df = df.with_columns(
        [
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
    df = df.with_columns(pl.col("trend_state").rle_id().alias("_run"))
    df = df.with_columns((pl.int_range(pl.len()).over("_run") + 1).alias("days_in_state"))
    df = df.with_columns(
        pl.when(pl.col("trend_state").is_null()).then(None).otherwise(pl.col("days_in_state")).alias("days_in_state"),
        pl.lit(series_id).alias("series_id"),
        pl.lit("%" if series_id in RATE_SERIES or series_id in MONTHLY_SERIES
               else "USD" if series_id == "DCOILWTICO" or series_id.startswith("ETF:") else "index").alias("unit"),
        pl.lit("bp" if series_id in RATE_SERIES else "%" if kind == "pct" else "pp").alias("change_unit"),
        (pl.col("chg_1m") * (100 if kind == "pct" or series_id in RATE_SERIES else 1)).alias("change_1m_display"),
    )
    if "obs_date" not in df.columns:
        df = df.with_columns(pl.col("date").alias("obs_date"), pl.col("date").alias("available_date"))
    return df.drop("_run")


def shared_metrics(calendar: pl.Series, drivers: dict[str, pl.DataFrame]) -> pl.DataFrame:
    parts = [series_metrics(calendar, frame, sid, DRIVERS.get(sid, "pct")) for sid, frame in drivers.items()]
    parts = [part for part in parts if not part.is_empty()]
    return pl.concat(parts, how="diagonal_relaxed").sort(["date", "series_id"]) if parts else pl.DataFrame()


def driver_metrics(
    ticker_px: pl.DataFrame, driver: pl.DataFrame, series_id: str, kind: str,
    shared: pl.DataFrame | None = None,
) -> pl.DataFrame:
    if ticker_px.is_empty() or driver.is_empty():
        return pl.DataFrame()
    metrics = series_metrics(ticker_px["date"], driver, series_id, kind) if shared is None else shared
    if metrics.is_empty():
        return pl.DataFrame()
    df = ticker_px.sort("date").join(metrics, on="date", how="left").sort("date")
    df = _finite(df.with_columns(
        _change(pl.col("value"), 1, kind).alias("_d_driver"),
        _change(pl.col("close"), 1, "pct").alias("_d_ticker"),
    ), ["_d_driver", "_d_ticker"])
    if series_id in MONTHLY_SERIES:
        df = df.with_columns(
            pl.lit(None).cast(pl.Float64).alias("corr_30d"),
            pl.lit(None).cast(pl.Float64).alias("corr_90d"),
        )
    else:
        df = _finite(df.with_columns(
            pl.rolling_corr("_d_driver", "_d_ticker", window_size=30, min_samples=20).alias("corr_30d"),
            pl.rolling_corr("_d_driver", "_d_ticker", window_size=90, min_samples=60).alias("corr_90d"),
        ), ["corr_30d", "corr_90d"])
    return df.with_columns(
        (pl.col("corr_90d") * pl.col("z_1m")).alias("effect"),
    ).filter(pl.col("value").is_not_null()).drop(["close", "_d_driver", "_d_ticker"])


def ticker_metrics(
    ticker: str, ticker_px: pl.DataFrame, drivers: dict[str, pl.DataFrame], shared: pl.DataFrame | None = None,
) -> pl.DataFrame:
    parts = [
        driver_metrics(
            ticker_px, frame, sid, DRIVERS.get(sid, "pct"),
            None if shared is None or shared.is_empty() else shared.filter(pl.col("series_id") == sid),
        )
        for sid, frame in drivers.items()
        if sid != f"ETF:{ticker}"  # an ETF is not its own driver
    ]
    parts = [p for p in parts if not p.is_empty()]
    if not parts:
        return pl.DataFrame(schema={c: pl.Utf8 for c in OUTPUT_COLUMNS})
    df = pl.concat(parts, how="diagonal_relaxed").with_columns(pl.lit(ticker).alias("ticker"))
    pressure = df.group_by("date").agg(
        pl.col("effect").sum().alias("_sum_effect"), pl.col("effect").count().alias("effect_count"),
    )
    pressure = pressure.with_columns(
        pl.when(pl.col("effect_count") > 0).then(
            pl.col("_sum_effect").map_elements(lambda s: math.tanh(s / 3.0), return_dtype=pl.Float64),
        ).otherwise(None).alias("net_pressure"),
    )
    df = df.join(pressure.select("date", "net_pressure", "effect_count"), on="date", how="left")
    df = df.with_columns(
        pl.when(pl.col("effect") > 0).then(pl.lit("tailwind"))
        .when(pl.col("effect") < 0).then(pl.lit("headwind"))
        .when(pl.col("effect").is_not_null()).then(pl.lit("neutral")).otherwise(None).alias("pressure_direction"),
        pl.when(pl.col("effect").is_null()).then(None)
        .when(pl.col("effect").abs() >= 1).then(pl.lit("strong"))
        .when(pl.col("effect").abs() >= 0.5).then(pl.lit("moderate"))
        .otherwise(pl.lit("weak")).alias("strength"),
    )
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
    drivers = [row for row in rows if not row["series_id"].startswith("PX:")]
    present = {row["series_id"] for row in drivers}
    return {
        "ticker": ticker, "date": as_of, "rows": rows,
        "coverage": {
            "available": len(drivers),
            "linked": sum(row.get("effect") is not None for row in drivers),
            "missing": sorted(set(DRIVERS) - {f"ETF:{ticker}"} - present),
        },
    }


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
                available = (
                    pl.coalesce("available_date", "obs_date")
                    if "available_date" in frame.columns else pl.col("obs_date")
                )
                drivers[sid] = frame.sort(
                    [c for c in ["obs_date", "ingested_at"] if c in frame.columns],
                ).select(
                    available.cast(pl.Date).alias("date"), pl.col("obs_date").cast(pl.Date),
                    available.cast(pl.Date).alias("available_date"), pl.col("value").cast(pl.Float64),
                )
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

    @job_handler("TREND", lease_key="serving/trend_metrics/_lease.json")
    def run(event, context):
        uni = collection_universe(user_ticker_union())
        # yearly partitions (+ any uncompacted legacy objects), deduped; adjusted closes for the math
        prices = analysis_prices(read_prices())
        macro = read_parquet_prefix("curated/macro_daily/")
        drivers = split_inputs(prices, macro, uni["etfs"])
        written = 0
        if prices.is_empty() or "ticker" not in prices.columns:
            return {"tickers": 0, "as_of": str(datetime.now(UTC).date()), "dropped_over_cap": uni["dropped"]}
        shared = shared_metrics(prices["date"], drivers)
        if not shared.is_empty():
            write_parquet(shared, "serving/trend_metrics/series/metrics.parquet")
        for t in uni["equities"]:
            px = prices.filter(pl.col("ticker") == t).select("date", pl.col("close").cast(pl.Float64))
            out = ticker_metrics(t, px, drivers, shared)
            document = latest_document(t, out, px)
            if document is None:
                continue
            if not out.is_empty():
                write_parquet(out, f"serving/trend_metrics/ticker={t}/trend_metrics.parquet")
            write_json(document, f"serving/trend_metrics/latest/{t}.json")
            written += 1
        return {"tickers": written, "as_of": str(datetime.now(UTC).date()), "dropped_over_cap": uni["dropped"]}

    return run(event, context)
