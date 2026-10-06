import math
from datetime import date, timedelta

import numpy as np
import polars as pl

import trend_metrics as tm


def _days(n):
    d, out = date(2021, 1, 4), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _fixture(n=600, seed=7):
    rng = np.random.default_rng(seed)
    days = _days(n)
    dy = rng.normal(0, 0.05, n)  # 10Y yield daily change, in points
    y10 = 1.5 + np.cumsum(dy)
    ret = -0.8 * dy + rng.normal(0, 0.01, n)  # ticker falls when yields rise
    close = 100 * np.cumprod(1 + ret)
    spy = 400 * np.cumprod(1 + rng.normal(0, 0.01, n))
    prices = pl.DataFrame({"ticker": ["TSLA"] * n + ["SPY"] * n, "date": days * 2, "close": np.r_[close, spy]})
    monthly = [d for i, d in enumerate(days) if i % 21 == 0]
    macro = pl.DataFrame(
        {
            "series_id": ["DGS10"] * n + ["CPI_YOY"] * len(monthly),
            "obs_date": days + monthly,
            "value": np.r_[y10, np.linspace(2.0, 3.5, len(monthly))],
        }
    )
    return prices, macro


def test_columns_keys_and_ranges():
    prices, macro = _fixture()
    drivers = tm.split_inputs(prices, macro, ["SPY"])
    assert set(drivers) == {"DGS10", "CPI_YOY", "ETF:SPY"}
    out = tm.ticker_metrics("TSLA", prices.filter(pl.col("ticker") == "TSLA").select("date", "close"), drivers)
    assert out.columns == tm.OUTPUT_COLUMNS
    assert out.select(["series_id", "ticker", "date"]).is_duplicated().sum() == 0
    np_ = out["net_pressure"].drop_nulls()
    assert ((np_ > -1) & (np_ < 1)).all()
    r = out["range_pct_1y"].drop_nulls()
    assert ((r >= 0) & (r <= 100)).all()
    assert set(out["trend_state"].drop_nulls().unique()) <= {"up", "down", "flat"}


def test_correlation_sign_and_effect_formula():
    prices, macro = _fixture()
    drivers = tm.split_inputs(prices, macro, ["SPY"])
    out = tm.ticker_metrics("TSLA", prices.filter(pl.col("ticker") == "TSLA").select("date", "close"), drivers)
    y = out.filter(pl.col("series_id") == "DGS10").drop_nulls(["corr_90d", "z_1m"])
    assert y["corr_90d"].mean() < -0.8  # built with a strong negative link
    assert np.allclose(y["effect"].to_numpy(), (y["corr_90d"] * y["z_1m"]).to_numpy())


def test_net_pressure_is_tanh_of_summed_effects():
    prices, macro = _fixture()
    drivers = tm.split_inputs(prices, macro, ["SPY"])
    out = tm.ticker_metrics("TSLA", prices.filter(pl.col("ticker") == "TSLA").select("date", "close"), drivers)
    last = out.filter(pl.col("date") == out["date"].max())
    expected = math.tanh(last["effect"].fill_null(0).sum() / 3)
    assert abs(last["net_pressure"][0] - expected) < 1e-9


def test_days_in_state_counts_runs():
    prices, macro = _fixture()
    drivers = tm.split_inputs(prices, macro, [])
    y = tm.ticker_metrics("TSLA", prices.filter(pl.col("ticker") == "TSLA").select("date", "close"), drivers)
    y = y.filter(pl.col("series_id") == "DGS10").drop_nulls("trend_state")
    states, counts = y["trend_state"].to_list(), y["days_in_state"].to_list()
    for i in range(1, len(states)):
        assert counts[i] == (counts[i - 1] + 1 if states[i] == states[i - 1] else 1)


def test_monthly_series_is_carried_forward():
    prices, macro = _fixture()
    drivers = tm.split_inputs(prices, macro, [])
    out = tm.driver_metrics(
        prices.filter(pl.col("ticker") == "TSLA").select("date", "close"), drivers["CPI_YOY"], "CPI_YOY", "level"
    )
    assert out["value"].null_count() == 0 and out.height > 500


def test_split_inputs_tolerates_empty_macro():
    # read_parquet_prefix returns a column-less frame when curated/macro_daily/ is empty (M1 not deployed yet).
    prices, _ = _fixture(n=40)
    drivers = tm.split_inputs(prices, pl.DataFrame(), ["SPY"])
    assert set(drivers) == {"ETF:SPY"}
    assert drivers["ETF:SPY"].columns == ["date", "value"]


def test_split_inputs_tolerates_macro_without_series_id():
    prices, _ = _fixture(n=40)
    macro = pl.DataFrame({"obs_date": [date(2021, 1, 4)], "value": [1.0]})
    assert set(tm.split_inputs(prices, macro, ["SPY"])) == {"ETF:SPY"}


def test_empty_macro_still_yields_etf_driven_metrics():
    prices, _ = _fixture()
    drivers = tm.split_inputs(prices, pl.DataFrame(), ["SPY"])
    out = tm.ticker_metrics("TSLA", prices.filter(pl.col("ticker") == "TSLA").select("date", "close"), drivers)
    assert not out.is_empty()
    assert set(out["series_id"].unique()) == {"ETF:SPY"}


def test_dedupe_prices_prefers_daily_prices_over_backfill():
    d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
    prices = pl.DataFrame(
        {
            "ticker": ["TSLA", "TSLA", "TSLA", "TSLA", "SPY"],
            "date": [d1, d1, d2, d2, d1],
            "close": [100.0, 101.0, 200.0, 202.0, 500.0],
            # D4 (DS-02) row listed first for d1 and second for d2: source wins, not position.
            "source_id": ["DS-02", "DS-05", "DS-05", "DS-02", "DS-05"],
        }
    )
    out = tm.dedupe_prices(prices).sort(["ticker", "date"])
    assert out.height == 3
    tsla = out.filter(pl.col("ticker") == "TSLA")
    assert tsla["close"].to_list() == [100.0, 202.0]
    assert tsla["source_id"].to_list() == ["DS-02", "DS-02"]
    assert out.filter(pl.col("ticker") == "SPY")["close"].to_list() == [500.0]


def test_dedupe_prices_keeps_last_row_without_source_id():
    d1 = date(2026, 10, 1)
    prices = pl.DataFrame({"ticker": ["TSLA", "TSLA"], "date": [d1, d1], "close": [1.0, 2.0]})
    out = tm.dedupe_prices(prices)
    assert out.height == 1
    assert out["close"].to_list() == [2.0]


def test_dedupe_prices_handles_empty_frame():
    assert tm.dedupe_prices(pl.DataFrame()).is_empty()


def test_split_inputs_tolerates_empty_prices():
    assert tm.split_inputs(pl.DataFrame(), pl.DataFrame(), ["SPY"]) == {}


def test_duplicate_dates_do_not_duplicate_metric_rows():
    prices, macro = _fixture()
    tsla = prices.filter(pl.col("ticker") == "TSLA").with_columns(pl.lit("DS-05").alias("source_id"))
    overlap = tsla.tail(5).with_columns(pl.lit("DS-02").alias("source_id"))
    spy = prices.filter(pl.col("ticker") == "SPY").with_columns(pl.lit("DS-05").alias("source_id"))
    combined = pl.concat([tsla, overlap, spy])
    raw = tm.ticker_metrics(
        "TSLA",
        combined.filter(pl.col("ticker") == "TSLA").select("date", "close"),
        tm.split_inputs(combined, macro, ["SPY"]),
    )
    assert raw.select("series_id", "date").is_duplicated().sum() > 0  # the bug dedupe_prices prevents
    clean = tm.dedupe_prices(combined)
    drivers = tm.split_inputs(clean, macro, ["SPY"])
    out = tm.ticker_metrics("TSLA", clean.filter(pl.col("ticker") == "TSLA").select("date", "close"), drivers)
    assert out.select("series_id", "date").is_duplicated().sum() == 0
