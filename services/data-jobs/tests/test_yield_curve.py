"""Yield curve, regime and the shared rates serving block."""

from datetime import date

import dashboard_build
import yield_curve as yc


def _obs(day: str, value: float) -> dict:
    return {"obs_date": date.fromisoformat(day), "value": value}


def _book(levels: dict[str, tuple[float, float]]) -> dict:
    return {
        series_id: [_obs("2026-09-08", ago), _obs("2026-10-08", today)]
        for series_id, (today, ago) in levels.items()
    }


def test_curve_carries_today_one_month_ago_and_basis_points_without_filling_gaps():
    observations = _book({
        "DGS2": (3.80, 3.90),
        "DGS10": (4.30, 4.10),
        "DGS1MO": (4.00, 4.00),
    })
    observations["DGS30"] = [_obs("2026-10-08", 4.70)]
    curve = yc.build_yield_curve(observations)
    assert curve["date"] == "2026-10-08"
    assert curve["ref_date_1m"] == "2026-09-08"
    assert curve["regime"] == "bear steepening"
    by_tenor = {row["tenor"]: row for row in curve["tenors"]}
    assert [row["tenor"] for row in curve["tenors"]] == [label for label, _ in yc.CURVE_TENORS]
    assert by_tenor["2Y"] == {
        "tenor": "2Y", "series_id": "DGS2", "yield": 3.80, "yield_1m": 3.90, "chg_1m_bp": -10,
    }
    assert by_tenor["10Y"]["chg_1m_bp"] == 20
    assert by_tenor["1M"]["chg_1m_bp"] == 0
    assert by_tenor["30Y"]["yield"] == 4.70
    assert by_tenor["30Y"]["yield_1m"] is None
    assert by_tenor["30Y"]["chg_1m_bp"] is None
    assert by_tenor["5Y"]["yield"] is None
    assert by_tenor["5Y"]["chg_1m_bp"] is None
    assert curve["tenors"][0]["yield"] != 0


def test_regime_labels_and_a_missing_leg_stay_unlabeled():
    assert yc.regime_label(3.4, 3.8, 4.0, 4.2) == "bull steepening"
    assert yc.regime_label(3.8, 3.9, 4.3, 4.1) == "bear steepening"
    assert yc.regime_label(3.7, 3.8, 4.0, 4.3) == "bull flattening"
    assert yc.regime_label(4.1, 3.8, 4.4, 4.3) == "bear flattening"
    assert yc.regime_label(4.0, 4.0, 4.2, 4.2) is None
    assert yc.regime_label(3.8, None, 4.3, 4.1) is None
    assert yc.to_bp(None, 4.0) is None
    assert yc.to_bp(4.25, 4.15) == 10


def test_stale_print_is_not_treated_as_today_or_as_zero():
    observations = {
        "DGS10": [_obs("2026-10-08", 4.2), _obs("2026-09-08", 4.1)],
        "DGS2": [_obs("2026-08-01", 3.5)],
    }
    curve = yc.build_yield_curve(observations)
    by_tenor = {row["tenor"]: row for row in curve["tenors"]}
    assert by_tenor["10Y"]["yield"] == 4.2
    assert by_tenor["2Y"]["yield"] is None
    assert by_tenor["2Y"]["chg_1m_bp"] is None
    assert yc.build_yield_curve({}) is None
    assert yc.build_yield_curve(None) is None


def test_rates_block_serves_curve_and_leaves_policy_path_unset():
    fomc = {
        "meeting_date": "2026-10-28",
        "outcomes": [{"label": "Cut", "prob": 0.62}, {"label": "Hold", "prob": None}],
        "history_14d": [{"date": "2026-10-08", "cut": 0.62, "hold": 0.3, "hike": 0.08}],
        "source": "Kalshi",
    }
    rates = yc.build_rates(_book({"DGS10": (4.2, 4.1)}), fomc, {"fomc": {"last_attempt": "2026-10-08T11:00:00+00:00"}})
    assert {row["tenor"]: row["series_id"] for row in rates["curve"]["tenors"]}["10Y"] == "DGS10"
    assert rates["fomc"]["outcomes"] == [{"label": "Cut", "prob": 0.62}]
    assert rates["fomc"]["history_14d"][0]["cut"] == 0.62
    assert rates["policy_path"] is None
    assert rates["status"]["policy_path"]["source"] is None
    assert rates["status"]["policy_path"]["state"] == "unavailable"
    assert "not been selected" in rates["status"]["policy_path"]["detail"]
    assert rates["status"]["curve"]["state"] == "ok"
    assert rates["status"]["curve"]["source"] == "FRED"
    assert rates["status"]["fomc"]["last_attempt"] == "2026-10-08T11:00:00+00:00"


def test_missing_inputs_are_explicit_on_the_snapshot():
    snapshot = dashboard_build.build_snapshot(["TSLA"], {}, {}, [], None)
    rates = snapshot["rates"]
    assert rates["curve"] is None
    assert rates["fomc"] is None
    assert rates["policy_path"] is None
    assert rates["status"]["curve"]["state"] == "unavailable"
    assert rates["status"]["curve"]["source"] == "FRED"
    assert rates["status"]["curve"]["last_attempt"] is None
    assert rates["status"]["fomc"]["source"] == "Kalshi"
    assert "terms" in rates["status"]["fomc"]["detail"]
    assert all(row["yield"] is None for row in (rates["curve"] or {"tenors": []})["tenors"]) or rates["curve"] is None
