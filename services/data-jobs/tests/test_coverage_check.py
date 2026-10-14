"""Per-ticker coverage report: required series, dashboard price status, optional hourly."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import coverage_check


def _series(**overrides):
    base = {
        "prices": [{"ticker": "META", "date": "2024-01-02", "close": 1.0}],
        "trend_metrics": [{"ticker": "META", "date": "2024-01-02", "value": 1.0}],
        "short_interest": [{"ticker": "META", "settlement_date": "2024-01-15", "short_interest": 10}],
        "fundamentals": [{"ticker": "META", "period_end": "2024-03-31", "value": 1.0}],
        "hourly": [],
        "options": [],
        "dashboard": {
            "tickers": {
                "META": {
                    "price_status": "available",
                    "price_history": [{"date": "2024-01-02", "close": 1.0}],
                }
            }
        },
        "chart": {"ticker": "META"},
    }
    base.update(overrides)
    return base


def test_a_short_history_passes_when_every_required_series_has_a_row():
    report = coverage_check.assess("META", _series())
    assert report["gaps"] == []
    assert report["series"]["prices"]["start"] == "2024-01-02"
    assert report["series"]["short_interest"]["rows"] == 1
    assert report["series"]["hourly"]["rows"] == 0


def test_missing_short_interest_dashboard_or_chart_is_a_gap_and_hourly_is_not():
    report = coverage_check.assess("META", _series(
        short_interest=[],
        hourly=[],
        options=[],
        chart=None,
        dashboard={"tickers": {"META": {"price_status": "unavailable", "price_history": []}}},
    ))
    assert report["gaps"] == ["short_interest", "dashboard", "chart_data"]
    assert "hourly" not in report["gaps"]
    assert "options" not in report["gaps"]


def test_another_ticker_in_the_same_frame_does_not_count():
    report = coverage_check.assess("EVGO", _series())
    assert "prices" in report["gaps"]
    assert "short_interest" in report["gaps"]


def test_report_names_the_gaps():
    text = coverage_check.format_report([
        coverage_check.assess("SPCX", _series(
            prices=[{"ticker": "SPCX", "date": "2026-06-01", "close": 2.0}],
            trend_metrics=[{"ticker": "SPCX", "date": "2026-06-01"}],
            short_interest=[{"ticker": "SPCX", "settlement_date": "2026-06-15"}],
            fundamentals=[{"ticker": "SPCX", "period_end": "2026-06-30"}],
            dashboard={"tickers": {"SPCX": {"price_status": "available", "price_history": [{"date": "2026-06-01"}]}}},
            chart={"ticker": "SPCX"},
        ))
    ])
    assert "SPCX" in text
    assert "gaps: none" in text
    assert "chart yes" in text
