"""NYSE session clock for the dashboard header.

Regular hours are 09:30-16:00 ET. Early-close days end at 13:00 ET. That close is the
trading session, not the 13:05 hourly-collector cutoff. Weekends and NYSE holidays are
closed. The browser turns next_open / next_close into the live label, so this is not a
new scheduled job.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hourly_prices import is_early_close, is_market_day

ET = ZoneInfo("America/New_York")
OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def session_bounds(day: date) -> tuple[datetime, datetime] | None:
    """Open and close instants for a NYSE session, or None when the day is closed."""
    if not is_market_day(day):
        return None
    close = EARLY_CLOSE if is_early_close(day) else REGULAR_CLOSE
    return datetime.combine(day, OPEN, ET), datetime.combine(day, close, ET)


def next_session_on_or_after(day: date) -> tuple[datetime, datetime]:
    cursor = day
    for _ in range(14):
        bounds = session_bounds(cursor)
        if bounds:
            return bounds
        cursor += timedelta(days=1)
    raise RuntimeError("no NYSE session within 14 days")


def _iso(stamp: datetime) -> str:
    return stamp.astimezone(UTC).isoformat(timespec="seconds")


def market_block(now: datetime) -> dict:
    """Session status at `now`, plus the open and close the header can count toward."""
    if now.tzinfo is None:
        raise ValueError("market clock must be timezone-aware")
    local = now.astimezone(ET)
    today = session_bounds(local.date())
    if today and today[0] <= local < today[1]:
        status = "open"
        next_open, _following_close = next_session_on_or_after(local.date() + timedelta(days=1))
        next_close = today[1]
    elif today and local < today[0]:
        status = "pre"
        next_open, next_close = today
    elif today:
        status = "post"
        next_open, next_close = next_session_on_or_after(local.date() + timedelta(days=1))
    else:
        status = "closed"
        next_open, next_close = next_session_on_or_after(local.date() + timedelta(days=1))
    return {
        "status": status,
        "next_open": _iso(next_open),
        "next_close": _iso(next_close),
        "as_of": _iso(now),
    }
