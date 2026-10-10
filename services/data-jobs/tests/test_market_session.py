from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from market_session import market_block

ET = ZoneInfo("America/New_York")


def at(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=ET)


def test_regular_session_is_open_until_16_00_et_and_names_the_next_open():
    block = market_block(at(2026, 10, 7, 11, 0))
    assert block["status"] == "open"
    assert block["next_close"] == "2026-10-07T20:00:00+00:00"
    assert block["next_open"] == "2026-10-08T13:30:00+00:00"
    assert block["as_of"] == "2026-10-07T15:00:00+00:00"


def test_pre_market_waits_for_the_same_day_open():
    block = market_block(at(2026, 10, 7, 8, 0))
    assert block["status"] == "pre"
    assert block["next_open"] == "2026-10-07T13:30:00+00:00"
    assert block["next_close"] == "2026-10-07T20:00:00+00:00"


def test_the_close_instant_is_after_hours_and_the_open_instant_is_open():
    assert market_block(at(2026, 10, 7, 16, 0))["status"] == "post"
    assert market_block(at(2026, 10, 7, 9, 30))["status"] == "open"
    post = market_block(at(2026, 10, 7, 16, 30))
    assert post["status"] == "post"
    assert post["next_open"] == "2026-10-08T13:30:00+00:00"
    assert post["next_close"] == "2026-10-08T20:00:00+00:00"


def test_weekend_is_closed_until_monday():
    block = market_block(at(2026, 10, 10, 12, 0))
    assert block["status"] == "closed"
    assert block["next_open"] == "2026-10-12T13:30:00+00:00"
    assert block["next_close"] == "2026-10-12T20:00:00+00:00"


def test_thanksgiving_is_closed_and_black_friday_closes_at_13_00_et():
    holiday = market_block(at(2026, 11, 26, 12, 0))
    assert holiday["status"] == "closed"
    assert holiday["next_open"] == "2026-11-27T14:30:00+00:00"
    assert holiday["next_close"] == "2026-11-27T18:00:00+00:00"

    early = market_block(at(2026, 11, 27, 12, 0))
    assert early["status"] == "open"
    assert early["next_close"] == "2026-11-27T18:00:00+00:00"
    assert market_block(at(2026, 11, 27, 13, 0))["status"] == "post"


def test_july_3_is_an_early_close_only_when_independence_day_is_a_weekday():
    early = market_block(at(2025, 7, 3, 12, 0))
    assert early["status"] == "open"
    assert early["next_close"] == "2025-07-03T17:00:00+00:00"

    observed = market_block(at(2026, 7, 3, 12, 0))
    assert observed["status"] == "closed"


def test_christmas_eve_closes_early_and_a_naive_clock_is_rejected():
    eve = market_block(at(2026, 12, 24, 12, 30))
    assert eve["status"] == "open"
    assert eve["next_close"] == "2026-12-24T18:00:00+00:00"
    try:
        market_block(datetime(2026, 10, 7, 15, 0, tzinfo=UTC).replace(tzinfo=None))
    except ValueError as error:
        assert "timezone-aware" in str(error)
    else:
        raise AssertionError("naive clock was accepted")
