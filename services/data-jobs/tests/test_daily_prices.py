from datetime import date

import pytest

from daily_prices import combine_daily_bars, is_market_day, normalize_daily_bars


def test_normalize_daily_bars_to_utc_calendar_date():
    rows = normalize_daily_bars(
        {
            "bars": {
                "tsla": [
                    {
                        "t": "2026-10-05T00:30:00-04:00",
                        "c": 450.25,
                        "v": 1234,
                    }
                ]
            }
        }
    )
    assert rows == [
        {
            "ticker": "TSLA",
            "date": date(2026, 10, 5),
            "close": 450.25,
            "volume": 1234,
            "source_id": "DS-02",
        }
    ]


def test_normalize_daily_bars_rejects_timezone_naive_timestamps():
    with pytest.raises(ValueError, match="timezone-naive"):
        normalize_daily_bars({"bars": {"TSLA": [{"t": "2026-10-05T00:00:00", "c": 1, "v": 1}]}})


def test_market_calendar_skips_weekends_and_nyse_holidays():
    assert not is_market_day(date(2026, 10, 3))
    assert not is_market_day(date(2026, 12, 25))
    assert is_market_day(date(2026, 10, 5))


def test_normalize_daily_bars_no_longer_copies_close_into_adj_close():
    rows = normalize_daily_bars({"bars": {"TSLA": [{"t": "2026-10-05T04:00:00Z", "c": 1.0, "v": 1}]}})
    assert "adj_close" not in rows[0]


def test_combine_daily_bars_maps_each_adjustment_to_its_column():
    def bars(close):
        return normalize_daily_bars({"bars": {"TSLA": [{"t": "2026-10-05T04:00:00Z", "c": close, "v": 7}]}})

    rows = combine_daily_bars(raw=bars(300.0), split=bars(150.0), adjusted=bars(148.5))
    assert rows == [
        {
            "ticker": "TSLA",
            "date": date(2026, 10, 5),
            "close": 150.0,
            "close_raw": 300.0,
            "adj_close": 148.5,
            "volume": 7,
            "source_id": "DS-02",
        }
    ]
    # A failed adjustment call leaves adj_close unknown (the nightly reconcile fills it) and close raw.
    partial = combine_daily_bars(raw=bars(300.0), split=[], adjusted=[])
    assert partial[0]["close"] == 300.0 and partial[0]["adj_close"] is None
