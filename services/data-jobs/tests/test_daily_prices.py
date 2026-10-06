from datetime import date

import pytest
from daily_prices import is_market_day, normalize_daily_bars


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
            "adj_close": 450.25,
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
