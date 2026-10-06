from datetime import date

import pytest

import backfill
from collectors import yahoo


def test_backfill_batches_move_from_newest_to_oldest_without_overlap():
    earliest = date(2021, 10, 4)
    first = backfill.batch_bounds(date(2026, 10, 4), earliest, batch_days=90)
    second = backfill.batch_bounds(first[0], earliest, batch_days=90)
    assert first == (date(2026, 7, 6), date(2026, 10, 4))
    assert second == (date(2026, 4, 7), date(2026, 7, 6))
    assert second[1] == first[0]


def test_backfill_clamps_last_batch_and_handles_leap_day():
    assert backfill.batch_bounds(date(2021, 11, 1), date(2021, 10, 4), 90) == (
        date(2021, 10, 4),
        date(2021, 11, 1),
    )
    assert backfill.years_ago(date(2028, 2, 29), 5) == date(2023, 2, 28)
    with pytest.raises(ValueError, match="positive"):
        backfill.batch_bounds(date(2026, 1, 1), date(2025, 1, 1), 0)


def test_checkpoint_contains_resume_cursor_and_deduplicated_tickers():
    state = backfill.new_backfill_state(["TSLA", "SPCX"], date(2026, 10, 4))
    assert state["earliest_date"] == "2021-10-04"
    assert state["tickers"]["TSLA"]["cursor"] == "2026-10-04"
    assert state["tickers"]["SPCX"]["complete"] is False
    assert backfill.tickers_from_event({"tickers": ["tsla", "TSLA", " spcx "]}) == (
        ["TSLA", "SPCX"],
        False,
    )


def test_backfill_queues_existing_recent_price_history_and_ignores_duplicate_events():
    state = backfill.new_backfill_state([], date(2026, 10, 4))
    backfill._queue_requested_tickers(state, ["TSLA"], False, date(2026, 10, 4))
    assert state["tickers"]["TSLA"]["complete"] is False
    state["tickers"]["TSLA"]["cursor"] = "2026-07-06"
    backfill._queue_requested_tickers(state, ["TSLA"], False, date(2026, 10, 5))
    assert state["tickers"]["TSLA"]["cursor"] == "2026-07-06"


def test_yahoo_batch_parameters_use_exclusive_utc_end():
    params = yahoo.chart_params(start=date(2026, 7, 6), end=date(2026, 10, 4))
    assert params["period2"] - params["period1"] == 90 * 24 * 60 * 60
    assert "range" not in params
    assert yahoo.chart_params(5)["range"] == "5y"
    with pytest.raises(ValueError, match="half-open"):
        yahoo.chart_params(start=date(2026, 1, 2), end=date(2026, 1, 2))


def test_empty_history_is_only_treated_as_a_data_boundary_when_explicit():
    assert backfill._is_empty_historical_window({"chart": {"result": [{"timestamp": []}]}})
    assert backfill._is_empty_historical_window(
        {"chart": {"result": None, "error": {"description": "No data found, symbol may be delisted"}}}
    )
    assert not backfill._is_empty_historical_window(
        {"chart": {"result": None, "error": {"code": "Not Found", "description": "invalid ticker"}}}
    )
