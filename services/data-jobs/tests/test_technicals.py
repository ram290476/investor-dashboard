"""Live Technical Summary indicators (#196) checked against reference calculations."""

import math
import statistics

import pytest
from technicals import (
    adx,
    as_of_text,
    bollinger,
    build_technical_summary,
    ema,
    macd,
    obv,
    rating_label,
    rsi,
    sma,
    stochastic,
    support_resistance,
)

# StockCharts' RSI worked example: RSI(14) is 70.53 on the 15th close and 66.32 on the 16th
# (their table rounds the averages to 2 dp; unrounded Wilder math lands within 0.1).
WILDER = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
          46.28, 46.00]
WAVE = [100 + 10 * math.sin(i / 7) + i * 0.05 for i in range(300)]


def _ref_ema(values, period):
    alpha, out = 2 / (period + 1), []
    for i, value in enumerate(values):
        if i < period - 1:
            out.append(None)
        elif i == period - 1:
            out.append(sum(values[:period]) / period)
        else:
            out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def test_sma_matches_window_mean():
    got = sma(WAVE, 20)
    assert got[18] is None
    for i in (19, 150, 299):
        assert got[i] == pytest.approx(statistics.fmean(WAVE[i - 19:i + 1]))


def test_ema_matches_reference_recursion():
    for period in (20, 50):
        assert ema(WAVE, period) == pytest.approx(_ref_ema(WAVE, period))
    assert ema([1.0, 2.0], 20) == [None, None]


def test_rsi_matches_stockcharts_example():
    values = rsi(WILDER, 14)
    assert values[13] is None
    assert values[14] == pytest.approx(70.53, abs=0.1)
    assert values[15] == pytest.approx(66.32, abs=0.1)
    assert rsi([1.0] * 20)[-1] == 50.0
    assert rsi([float(i) for i in range(20)])[-1] == 100.0


def test_macd_matches_reference():
    line, sig, hist = macd(WAVE)
    fast, slow = _ref_ema(WAVE, 12), _ref_ema(WAVE, 26)
    ref_line = [None if a is None or b is None else a - b for a, b in zip(fast, slow, strict=True)]
    ref_sig = [None] * 25 + _ref_ema(ref_line[25:], 9)
    assert line == pytest.approx(ref_line)
    assert sig == pytest.approx(ref_sig)
    assert hist[-1] == pytest.approx(ref_line[-1] - ref_sig[-1])
    assert sig[32] is None and sig[33] is not None


def test_stochastic_close_only():
    closes = [float(i) for i in range(1, 21)]  # new high every bar
    k, d = stochastic(closes)
    assert k[-1] == pytest.approx(100.0) and d[-1] == pytest.approx(100.0)
    k, _ = stochastic([10.0] * 20)
    assert k[-1] == 50.0
    raw = [100 * (WAVE[i] - min(WAVE[i - 13:i + 1])) / (max(WAVE[i - 13:i + 1]) - min(WAVE[i - 13:i + 1]))
           for i in range(297, 300)]
    k, _ = stochastic(WAVE)
    assert k[-1] == pytest.approx(statistics.fmean(raw))


def test_adx_close_only_trend_and_chop():
    up = [float(i) for i in range(1, 60)]
    strength, plus, minus = adx(up)
    assert strength[-1] == pytest.approx(100.0)
    assert plus[-1] == pytest.approx(100.0) and minus[-1] == pytest.approx(0.0)
    chop = [10.0 + (i % 2) for i in range(60)]
    strength, plus, minus = adx(chop)
    assert strength[-1] < 10 and plus[-1] == pytest.approx(minus[-1], abs=5)
    assert adx(up[:20])[0][-1] is None  # needs 2 x 14 sessions


def test_bollinger_uses_population_sd():
    mid, upper, lower, pct_b = bollinger(WAVE)
    window = WAVE[-20:]
    sd = statistics.pstdev(window)
    assert mid[-1] == pytest.approx(statistics.fmean(window))
    assert upper[-1] == pytest.approx(mid[-1] + 2 * sd)
    assert lower[-1] == pytest.approx(mid[-1] - 2 * sd)
    assert pct_b[-1] == pytest.approx((WAVE[-1] - lower[-1]) / (upper[-1] - lower[-1]))


def test_obv_adds_up_and_down_volume():
    assert obv([10, 11, 11, 9, 12], [100, 10, 20, 30, 40]) == [0, 10, 10, -20, 20]


def test_support_resistance_swing_closes():
    closes = [10, 12, 14, 12, 10, 8, 10, 12, 15, 13, 11, 9, 11, 12]
    support, resistance = support_resistance(closes, 20, span=2)
    assert support == [9, 8]
    assert resistance == [14, 15]
    assert support_resistance([1.0], 20) == ([], [])


def _history(closes, volume=1000):
    return [{"date": f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" if i < 336 else "2026-01-01",
             "close": c, "volume": volume + i} for i, c in enumerate(closes)]


def test_summary_votes_every_horizon_on_a_steady_uptrend():
    closes = [50 + i * 0.5 + (0.8 if i % 3 == 0 else 0) for i in range(260)]
    doc = build_technical_summary("ANY", _history(closes))
    assert doc["example"] is False and doc["delayed"] is True
    assert doc["as_of_date"] == "2025-10-08"
    assert doc["asOf"] == "Oct 8, 2025 4:00 PM ET"
    ratings = {h["id"]: rating_label(h["summary"]) for h in doc["horizons"]}
    assert ratings["long"] == "Strong Buy"
    assert ratings["intermediate"] in ("Buy", "Strong Buy")
    long = next(h for h in doc["horizons"] if h["id"] == "long")
    names = [i["name"] for i in long["indicators"]]
    assert "SMA 50 / SMA 200 cross (golden/death)" in names and "Price vs SMA 150" in names
    short = doc["horizons"][0]
    assert {i["name"] for i in short["indicators"]} >= {"RSI (14)", "MACD (12,26,9)", "Stochastic (14,3,3)"}
    inter = doc["horizons"][1]
    assert {i["name"] for i in inter["indicators"]} >= {"ADX (14)", "Bollinger (20,2) %B", "OBV / volume",
                                                       "EMA 20 / EMA 50 cross"}
    for h in doc["horizons"]:
        assert sum(h["summary"].values()) == len(h["indicators"])
        assert h["explanation"].endswith(".")


def test_short_history_leaves_horizons_without_votes():
    doc = build_technical_summary("NEW", _history([10.0 + i for i in range(80)]))
    by_id = {h["id"]: h for h in doc["horizons"]}
    assert "summary" in by_id["short"] and "summary" in by_id["intermediate"]
    assert "summary" not in by_id["long"] and by_id["long"]["availableBars"] == 80
    empty = build_technical_summary("NONE", [])
    assert empty["asOf"] is None and all("summary" not in h for h in empty["horizons"])


def test_missing_volume_drops_only_the_obv_vote():
    closes = [50 + i * 0.3 for i in range(100)]
    history = [{"date": f"2025-01-{i:02d}", "close": c} for i, c in enumerate(closes)]
    inter = build_technical_summary("X", history)["horizons"][1]
    assert "OBV / volume" not in [i["name"] for i in inter["indicators"]]


def test_as_of_text_and_labels():
    assert as_of_text("2026-10-13") == "Oct 13, 2026 4:00 PM ET"
    assert as_of_text("bad") is None
    assert rating_label({"buy": 7, "neutral": 3, "sell": 4}) == "Buy"
    assert rating_label({"buy": 0, "neutral": 0, "sell": 0}) is None
