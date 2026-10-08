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


def test_an_etf_is_not_its_own_driver():
    prices, macro = _fixture()
    drivers = tm.split_inputs(prices, macro, ["SPY"])
    spy = prices.filter(pl.col("ticker") == "SPY").select("date", "close")
    out = tm.ticker_metrics("SPY", spy, drivers)
    assert "ETF:SPY" in drivers
    assert "ETF:SPY" not in set(out["series_id"].to_list())
    assert "DGS10" in set(out["series_id"].to_list())


def test_macro_is_not_used_before_publication_and_keeps_provenance():
    prices, _ = _fixture(n=10)
    days = _days(10)
    macro = pl.DataFrame({
        "series_id": ["DGS10", "DGS10"],
        "obs_date": [days[0], days[2]],
        "available_date": [days[1], days[3]],
        "value": [4.0, 9.0],
    })
    drivers = tm.split_inputs(prices, macro, [])
    px = prices.filter(pl.col("ticker") == "TSLA").select("date", "close")
    out = tm.driver_metrics(px, drivers["DGS10"], "DGS10", "level")
    assert out["date"][0] == days[1]
    assert out.filter(pl.col("date") == days[2])["value"][0] == 4.0
    assert out.filter(pl.col("date") == days[3])["value"][0] == 9.0
    assert out.filter(pl.col("date") == days[3])["obs_date"][0] == days[2]


def test_unavailable_effects_do_not_fabricate_neutral_pressure():
    prices, macro = _fixture(n=10)
    out = tm.ticker_metrics(
        "TSLA", prices.filter(pl.col("ticker") == "TSLA").select("date", "close"),
        tm.split_inputs(prices, macro, []),
    )
    assert out["net_pressure"].null_count() == out.height
    assert out["effect_count"].to_list() == [0] * out.height


def test_shared_series_metrics_are_independent_of_ticker_history():
    prices, macro = _fixture()
    drivers = tm.split_inputs(prices, macro, ["SPY"])
    shared = tm.shared_metrics(prices["date"].unique().sort(), drivers)
    assert not shared.select("series_id", "date").is_duplicated().any()
    px = prices.filter(pl.col("ticker") == "TSLA").select("date", "close")
    full = tm.ticker_metrics("TSLA", px, drivers, shared)
    recent = tm.ticker_metrics("NEW", px.tail(90), drivers, shared)
    columns = ["date", "series_id", "z_1m", "range_pct_1y", "trend_state", "days_in_state"]
    assert full.filter(pl.col("date").is_in(px.tail(90)["date"].implode())).select(columns).equals(
        recent.select(columns),
    )


def test_nonfinite_changes_do_not_create_a_direction_state():
    days = _days(200)
    driver = pl.DataFrame({"date": days, "value": [0.0] * 179 + [1.0] * 21})
    px = pl.DataFrame({"date": days, "close": [100.0 + i for i in range(200)]})
    out = tm.driver_metrics(px, driver, "VIXCLS", "pct")
    assert out["chg_1m"].drop_nulls().is_finite().all()
    assert out["trend_state"][-1] is None


def test_rates_are_displayed_in_basis_points_and_shared_history_is_unique():
    prices, macro = _fixture()
    drivers = tm.split_inputs(prices, macro, ["SPY"])
    shared = tm.shared_metrics(prices["date"], drivers)
    assert shared.select("series_id", "date").is_duplicated().sum() == 0
    row = shared.filter(pl.col("series_id") == "DGS10").tail(1).row(0, named=True)
    assert row["unit"] == "%" and row["change_unit"] == "bp"
    assert abs(row["change_1m_display"] - row["chg_1m"] * 100) < 1e-9
    etf = shared.filter(pl.col("series_id") == "ETF:SPY").tail(1).row(0, named=True)
    assert etf["change_unit"] == "%"
    assert abs(etf["change_1m_display"] - etf["chg_1m"] * 100) < 1e-9


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


def test_monthly_series_skip_daily_correlation_and_keep_the_zscore_rule():
    prices, macro = _fixture()
    drivers = tm.split_inputs(prices, macro, ["SPY"])
    px = prices.filter(pl.col("ticker") == "TSLA").select("date", "close")
    out = tm.ticker_metrics("TSLA", px, drivers)
    cpi = out.filter(pl.col("series_id") == "CPI_YOY")
    assert cpi.height > 0
    assert cpi["corr_30d"].null_count() == cpi.height
    assert cpi["corr_90d"].null_count() == cpi.height
    assert cpi["effect"].null_count() == cpi.height
    daily = out.filter(pl.col("series_id") == "DGS10").drop_nulls("z_1m")
    assert daily["corr_90d"].null_count() < daily.height
    row = daily.row(0, named=True)
    z = row["z_1m"]
    if z > tm.TREND_Z:
        assert row["trend_state"] == "up"
    elif z < -tm.TREND_Z:
        assert row["trend_state"] == "down"
    else:
        assert row["trend_state"] == "flat"
    assert tm.DRIVERS["CORE_PCE_YOY"] == "level"
    assert "CORE_PCE_YOY" in tm.MONTHLY_SERIES


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


def test_each_ticker_gets_its_own_price_trend():
    days = _days(80)
    tsla = pl.DataFrame({"date": days, "close": [100.0 + i for i in range(80)]})
    spcx = pl.DataFrame({"date": days, "close": [300.0 - i for i in range(80)]})
    up = tm.price_trend_row("TSLA", tsla)
    down = tm.price_trend_row("SPCX", spcx)
    assert up["series_id"] == "PX:TSLA" and up["ticker"] == "TSLA"
    assert down["series_id"] == "PX:SPCX" and down["ticker"] == "SPCX"
    assert up["trend_state"] == "up" and down["trend_state"] == "down"
    assert up["vs_ma20"] > 0 > down["vs_ma20"]
    assert up["since"] and down["since"]
    assert up["from_start"] is True and down["from_start"] is True

    # A short run after a flat base is not the other ticker's long decline.
    mixed_days = _days(54)
    mixed = pl.DataFrame({"date": mixed_days, "close": [100.0] * 50 + [130.0, 140.0, 150.0, 160.0]})
    brief = tm.price_trend_row("TSLA", mixed)
    assert brief["trend_state"] == "up"
    assert brief["days_in_state"] == 4
    assert brief["from_start"] is False
    assert brief["since"] == str(mixed_days[50])
    assert brief["since"] != down["since"]

    shared_driver = pl.DataFrame(
        {
            "series_id": ["DGS10"],
            "ticker": ["TSLA"],
            "date": [days[-1]],
            "trend_state": ["up"],
            "days_in_state": [40],
        }
    )
    tsla_doc = tm.latest_document("TSLA", shared_driver, tsla)
    spcx_doc = tm.latest_document("SPCX", pl.DataFrame(), spcx)
    tsla_px = next(row for row in tsla_doc["rows"] if row["series_id"] == "PX:TSLA")
    spcx_px = next(row for row in spcx_doc["rows"] if row["series_id"] == "PX:SPCX")
    assert tsla_px["trend_state"] != spcx_px["trend_state"]
    assert tsla_px["vs_ma20"] != spcx_px["vs_ma20"]
    assert spcx_doc["ticker"] == "SPCX"
    # No driver rows still publishes the price trend, so a ticker is not skipped.
    assert [row["series_id"] for row in spcx_doc["rows"]] == ["PX:SPCX"]


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
