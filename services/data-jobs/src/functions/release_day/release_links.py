"""Per-series release trend, and how TSLA and SPCX moved around past releases.

Stored as curated/release_links/release_links.parquet for the dashboard to serve later.

Surprise is actual minus consensus, expressed in year-over-year percentage points
when the year-ago index is known. Otherwise it is the change in the year-over-year
rate versus the previous release.

Windows, in trading sessions relative to the release date T:
    week_before   close(T-1) / close(T-6) - 1    sessions T-5..T-1
    days_before   close(T-1) / close(T-3) - 1    sessions T-2..T-1
    release_day   close(T) / close(T-1) - 1      only once T's close is in the lake
"""

from __future__ import annotations

import math
from datetime import date, datetime

import polars as pl

TICKERS = ("TSLA", "SPCX")
# How many sessions before T the window starts and ends. End 1 is the session before T.
PRIOR_WINDOWS = {"week_before": (6, 1), "days_before": (3, 1)}
COLUMNS = [
    "row_kind",
    "series_id",
    "ticker",
    "release_date",
    "yoy",
    "surprise",
    "trend_direction",
    "consecutive_releases",
    "ret_week_before",
    "ret_days_before",
    "ret_release_day",
    "window",
    "n_releases",
    "correlation_surprise",
    "correlation_trend",
    "avg_move_positive_surprise",
    "avg_move_negative_surprise",
    "n_positive_surprise",
    "n_negative_surprise",
]
_STRINGS = ["row_kind", "series_id", "ticker", "release_date", "trend_direction", "window"]
_INTS = ["consecutive_releases", "n_releases", "n_positive_surprise", "n_negative_surprise"]
_TREND_CODE = {"accelerating": 1.0, "decelerating": -1.0, "unchanged": 0.0}


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "")[:10]
    if len(text) != 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def shift_period(period: str, months: int) -> str | None:
    if len(period) < 7 or period[4] != "-":
        return None
    try:
        year, month = int(period[:4]), int(period[5:7])
    except ValueError:
        return None
    index = year * 12 + (month - 1) - months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def yoy_surprise(actual, consensus, year_ago, yoy, previous_yoy) -> float | None:
    """Actual minus consensus, in YoY points. Otherwise the change versus the previous release."""
    agreed = _num(consensus)
    current = _num(actual)
    base = _num(year_ago)
    if agreed is not None and current is not None and base not in {None, 0.0}:
        return (current - agreed) / base * 100.0
    rate = _num(yoy)
    prior = _num(previous_yoy)
    if rate is not None and prior is not None:
        return rate - prior
    return None


def trend_run(values: list[float]) -> list[tuple[str | None, int]]:
    """Direction of the year-over-year rate, and how many releases it has lasted."""
    out: list[tuple[str | None, int]] = []
    direction: str | None = None
    count = 0
    previous: float | None = None
    for value in values:
        if previous is None:
            direction, count = None, 0
        elif value > previous:
            count = count + 1 if direction == "accelerating" else 1
            direction = "accelerating"
        elif value < previous:
            count = count + 1 if direction == "decelerating" else 1
            direction = "decelerating"
        else:
            direction, count = "unchanged", 1
        out.append((direction, count))
        previous = value
    return out


def _points(releases: list[dict], source_id: str, series_id: str) -> list[dict]:
    series = [row for row in releases if row.get("series_id") == source_id]
    by_period = {row.get("period"): row for row in series}
    ordered = sorted(
        (
            row
            for row in series
            if _num(row.get("yoy")) is not None and _as_date(row.get("release_ts")) is not None
        ),
        key=lambda row: str(row.get("period") or ""),
    )
    runs = trend_run([float(row["yoy"]) for row in ordered])
    points = []
    previous: float | None = None
    for row, (direction, count) in zip(ordered, runs, strict=True):
        period = str(row.get("period") or "")
        year_ago = by_period.get(shift_period(period, 12))
        points.append(
            {
                "series_id": series_id,
                "release_date": _as_date(row.get("release_ts")).isoformat(),
                "yoy": float(row["yoy"]),
                "surprise": yoy_surprise(
                    row.get("actual"),
                    row.get("consensus"),
                    None if year_ago is None else year_ago.get("actual"),
                    row.get("yoy"),
                    previous,
                ),
                "trend_direction": direction,
                "consecutive_releases": count,
            }
        )
        previous = float(row["yoy"])
    return points


def _closes(prices: pl.DataFrame, ticker: str) -> tuple[list[date], dict[date, float]]:
    if prices.is_empty() or not {"ticker", "date", "close"} <= set(prices.columns):
        return [], {}
    frame = prices.filter((pl.col("ticker") == ticker) & pl.col("close").is_not_null())
    if frame.is_empty():
        return [], {}
    frame = frame.with_columns(pl.col("date").cast(pl.Date)).unique(subset=["date"], keep="last").sort("date")
    ordered = frame["date"].to_list()
    closes = {day: float(value) for day, value in zip(ordered, frame["close"].to_list(), strict=True)}
    return ordered, closes


def window_returns(ordered: list[date], closes: dict[date, float], release: date) -> dict[str, float | None]:
    before = [day for day in ordered if day < release]
    out: dict[str, float | None] = {"week_before": None, "days_before": None, "release_day": None}
    for name, (start_back, end_back) in PRIOR_WINDOWS.items():
        if len(before) < start_back:
            continue
        start = closes[before[-start_back]]
        end = closes[before[-end_back]]
        if start != 0:
            out[name] = end / start - 1.0
    if release in closes and before and closes[before[-1]] != 0:
        out["release_day"] = closes[release] / closes[before[-1]] - 1.0
    return out


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    scale_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    scale_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if scale_x == 0 or scale_y == 0:
        return None
    return cov / (scale_x * scale_y)


def _blank() -> dict:
    return dict.fromkeys(COLUMNS)


def _summary(rows: list[dict], series_id: str, ticker: str) -> list[dict]:
    latest = rows[-1] if rows else None
    direction = None if latest is None else latest["trend_direction"]
    count = None if latest is None else latest["consecutive_releases"]
    summaries = []
    for window, column in (
        ("week_before", "ret_week_before"),
        ("days_before", "ret_days_before"),
        ("release_day", "ret_release_day"),
    ):
        paired_surprise: list[tuple[float, float]] = []
        paired_trend: list[tuple[float, float]] = []
        positive: list[float] = []
        negative: list[float] = []
        for row in rows:
            move = row.get(column)
            if move is None:
                continue
            surprise = row.get("surprise")
            if surprise is not None:
                paired_surprise.append((float(move), float(surprise)))
                if surprise > 0:
                    positive.append(float(move))
                elif surprise < 0:
                    negative.append(float(move))
            code = _TREND_CODE.get(row.get("trend_direction"))
            if code is not None:
                paired_trend.append((float(move), code))
        item = _blank()
        item.update(
            {
                "row_kind": "summary",
                "series_id": series_id,
                "ticker": ticker,
                "trend_direction": direction,
                "consecutive_releases": count,
                "window": window,
                "n_releases": len(paired_surprise),
                "correlation_surprise": pearson(
                    [pair[0] for pair in paired_surprise], [pair[1] for pair in paired_surprise]
                ),
                "correlation_trend": pearson([pair[0] for pair in paired_trend], [pair[1] for pair in paired_trend]),
                "avg_move_positive_surprise": None if not positive else sum(positive) / len(positive),
                "avg_move_negative_surprise": None if not negative else sum(negative) / len(negative),
                "n_positive_surprise": len(positive),
                "n_negative_surprise": len(negative),
            }
        )
        summaries.append(item)
    return summaries


def build_release_links(releases: list[dict], prices: pl.DataFrame, id_map: dict[str, str]) -> pl.DataFrame:
    """Release rows, one trend row per series, and one summary per series, ticker, and window."""
    books = {ticker: _closes(prices, ticker) for ticker in TICKERS}
    rows: list[dict] = []
    for source_id, series_id in id_map.items():
        points = _points(releases, source_id, series_id)
        if not points:
            continue
        latest = points[-1]
        trend = _blank()
        trend.update(
            {
                "row_kind": "trend",
                "series_id": series_id,
                "release_date": latest["release_date"],
                "yoy": latest["yoy"],
                "trend_direction": latest["trend_direction"],
                "consecutive_releases": latest["consecutive_releases"],
            }
        )
        rows.append(trend)
        by_ticker: dict[str, list[dict]] = {}
        for point in points:
            day = date.fromisoformat(point["release_date"])
            for ticker, (ordered, closes) in books.items():
                item = _blank()
                item.update(point)
                item["row_kind"] = "release"
                item["ticker"] = ticker
                rets = window_returns(ordered, closes, day)
                item["ret_week_before"] = rets["week_before"]
                item["ret_days_before"] = rets["days_before"]
                item["ret_release_day"] = rets["release_day"]
                rows.append(item)
                by_ticker.setdefault(ticker, []).append(item)
        for ticker in TICKERS:
            rows.extend(_summary(by_ticker.get(ticker, []), series_id, ticker))
    return _frame(rows)


def _frame(rows: list[dict]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema={column: pl.Utf8 for column in COLUMNS})
    frame = pl.from_dicts(rows, infer_schema_length=None).select(COLUMNS)
    frame = frame.with_columns([pl.col(column).cast(pl.Utf8) for column in _STRINGS])
    frame = frame.with_columns(
        [pl.col(column).cast(pl.Float64, strict=False) for column in COLUMNS if column not in _STRINGS + _INTS]
    )
    return frame.with_columns([pl.col(column).cast(pl.Int64, strict=False) for column in _INTS])
