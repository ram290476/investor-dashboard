"""Release trend, surprise, and the watchlist windows around past release dates."""

from datetime import date, timedelta

import polars as pl

import release_links as links


def _business_days(n, start=date(2024, 6, 3)):
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def test_trend_run_counts_consecutive_accelerating_and_decelerating_prints():
    runs = links.trend_run([2.0, 2.2, 2.5, 2.4, 2.1])
    assert runs == [
        (None, 0),
        ("accelerating", 1),
        ("accelerating", 2),
        ("decelerating", 1),
        ("decelerating", 2),
    ]


def test_surprise_uses_consensus_then_the_change_versus_the_previous_release():
    assert links.yoy_surprise(103.0, 102.0, 100.0, 3.0, 9.0) == 1.0
    assert abs(links.yoy_surprise(103.0, None, 100.0, 3.2, 3.0) - 0.2) < 1e-9
    assert links.yoy_surprise(103.0, None, None, 3.2, None) is None


def test_window_returns_use_the_sessions_before_the_release_and_the_release_close():
    days = _business_days(11)
    closes = {day: 100.0 + i for i, day in enumerate(days)}
    release = days[10]
    got = links.window_returns(days, closes, release)
    assert got["week_before"] == (109.0 / 104.0) - 1.0
    assert got["days_before"] == (109.0 / 107.0) - 1.0
    assert got["release_day"] == (110.0 / 109.0) - 1.0
    # The release-day move waits until that close is stored.
    pending = links.window_returns(days[:-1], {d: closes[d] for d in days[:-1]}, release)
    assert pending["release_day"] is None
    assert pending["week_before"] == got["week_before"]


def _releases(days, surprises):
    yoys = [2.0]
    for surprise in surprises:
        yoys.append(yoys[-1] + surprise)
    release_dates = [days[8 + i * 4] for i in range(len(yoys))]
    closes = {day: 100.0 for day in days}
    for i, surprise in enumerate(surprises):
        day = release_dates[i + 1]
        previous = max(item for item in days if item < day)
        closes[day] = closes[previous] * (1.0 + surprise)
    releases = [
        {
            "series_id": "CUSR0000SA0",
            "period": f"{2024 + i // 12:04d}-{(i % 12) + 1:02d}",
            "release_ts": f"{day.isoformat()}T12:30:00+00:00",
            "actual": 100.0 + yoy,
            "consensus": None,
            "yoy": yoy,
        }
        for i, (day, yoy) in enumerate(zip(release_dates, yoys, strict=True))
    ]
    return releases, closes, yoys


def test_release_links_follow_the_watchlist_once_twelve_releases_are_covered():
    pattern = [0.4, -0.3, 0.5, -0.4, 0.6, -0.2]
    surprises = pattern + pattern
    days = _business_days(80)
    releases, closes, yoys = _releases(days, surprises)
    short = days[-30:]
    prices = pl.DataFrame(
        {
            "ticker": ["TSLA"] * len(days) + ["AAPL"] * len(short) + ["NVDA"] * len(days),
            "date": days + short + days,
            "close": [closes[day] for day in days] + [100.0] * len(short) + [50.0] * len(days),
        }
    )
    table = links.build_release_links(releases, prices, {"CUSR0000SA0": "CPI_YOY"}, ["TSLA", "AAPL", "NVDA"])
    assert set(table.filter(pl.col("row_kind") == "summary")["ticker"].to_list()) == {"TSLA", "AAPL", "NVDA"}
    trend = table.filter(pl.col("row_kind") == "trend").row(0, named=True)
    assert trend["series_id"] == "CPI_YOY"
    assert trend["trend_direction"] == "decelerating"
    assert trend["consecutive_releases"] == 1
    assert trend["yoy"] == yoys[-1]

    summary = table.filter(
        (pl.col("row_kind") == "summary") & (pl.col("ticker") == "TSLA") & (pl.col("window") == "release_day")
    ).row(0, named=True)
    assert summary["n_releases"] == len(surprises)
    assert summary["correlation_surprise"] > 0.99
    assert summary["correlation_trend"] > 0.95
    assert summary["n_positive_surprise"] == 6
    assert summary["n_negative_surprise"] == 6
    assert abs(summary["avg_move_positive_surprise"] - (0.4 + 0.5 + 0.6) / 3) < 1e-9
    assert abs(summary["avg_move_negative_surprise"] - (-0.3 + -0.4 + -0.2) / 3) < 1e-9

    # A name absent from the watchlist is not linked, even when its closes are in the lake.
    parked = links.build_release_links(releases, prices, {"CUSR0000SA0": "CPI_YOY"}, ["TSLA"])
    assert "NVDA" not in parked["ticker"].drop_nulls().to_list()

    short_row = table.filter(
        (pl.col("row_kind") == "summary") & (pl.col("ticker") == "AAPL") & (pl.col("window") == "release_day")
    ).row(0, named=True)
    assert short_row["n_releases"] < links.MIN_RELEASES
    assert short_row["correlation_surprise"] is None
    assert short_row["correlation_trend"] is None
    assert short_row["avg_move_positive_surprise"] is None
    assert short_row["n_positive_surprise"] is None
