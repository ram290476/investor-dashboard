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
