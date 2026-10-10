"""Treasury yield curve served on dashboard.json as rates.curve.

Eleven constant-maturity FRED tenors. The 1-month comparison is the print on or
before 30 calendar days earlier, not the 21-session trend-metrics window. A missing
print stays null. It is never stored as zero.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

# tenor label -> FRED constant-maturity series. Order is the curve x-axis.
CURVE_TENORS: tuple[tuple[str, str], ...] = (
    ("1M", "DGS1MO"),
    ("3M", "DGS3MO"),
    ("6M", "DGS6MO"),
    ("1Y", "DGS1"),
    ("2Y", "DGS2"),
    ("3Y", "DGS3"),
    ("5Y", "DGS5"),
    ("7Y", "DGS7"),
    ("10Y", "DGS10"),
    ("20Y", "DGS20"),
    ("30Y", "DGS30"),
)
CURVE_SERIES_IDS: tuple[str, ...] = tuple(series_id for _, series_id in CURVE_TENORS)
TODAY_SLACK_DAYS = 5
MONTH_SLACK_DAYS = 7
REGIME_MIN_BP = 1.0

_CURVE_UNAVAILABLE = "Constant-maturity Treasury yields are not in the curve block yet."
_FOMC_UNAVAILABLE = "Live collection waits until the Kalshi terms are accepted."
_POLICY_UNAVAILABLE = "Implied policy path source has not been selected."


def number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _day(value: object) -> date | None:
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


def _points(rows: list[dict] | None) -> list[tuple[date, float]]:
    best: dict[date, tuple[str, float]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        day = _day(row.get("obs_date") if row.get("obs_date") is not None else row.get("date"))
        value = number(row.get("value"))
        if day is None or value is None:
            continue
        stamp = str(row.get("ingested_at") or "")
        previous = best.get(day)
        if previous is None or stamp >= previous[0]:
            best[day] = (stamp, value)
    return sorted((day, value) for day, (_, value) in best.items())


def _on_or_before(points: list[tuple[date, float]], day: date, slack_days: int) -> float | None:
    chosen: tuple[date, float] | None = None
    for obs, value in points:
        if obs <= day:
            chosen = (obs, value)
        else:
            break
    if chosen is None or (day - chosen[0]).days > slack_days:
        return None
    return chosen[1]


def to_bp(today: float | None, ago: float | None) -> int | None:
    """Percentage-point difference as basis points. Missing either print is null, not zero."""
    if today is None or ago is None:
        return None
    return round((today - ago) * 100)


def regime_label(
    two_now: float | None, two_ago: float | None, ten_now: float | None, ten_ago: float | None,
) -> str | None:
    """Bull/bear steepening/flattening from the 2s10s change and the 10Y level change.

    Either move smaller than one basis point, or any missing print, leaves the regime unlabeled.
    """
    if None in {two_now, two_ago, ten_now, ten_ago}:
        return None
    spread_bp = ((ten_now - two_now) - (ten_ago - two_ago)) * 100
    level_bp = (ten_now - ten_ago) * 100
    if abs(spread_bp) < REGIME_MIN_BP or abs(level_bp) < REGIME_MIN_BP:
        return None
    steep = spread_bp > 0
    bull = level_bp < 0
    if steep and bull:
        return "bull steepening"
    if steep and not bull:
        return "bear steepening"
    if not steep and bull:
        return "bull flattening"
    return "bear flattening"


def build_yield_curve(observations: dict | None, as_of: date | None = None) -> dict | None:
    """Shared rates.curve block. `observations` maps a FRED series id to curated rows."""
    if not isinstance(observations, dict):
        return None
    series = {series_id: _points(observations.get(series_id)) for _, series_id in CURVE_TENORS}
    latest = [points[-1][0] for points in series.values() if points]
    if not latest:
        return None
    curve_date = min(as_of, max(latest)) if as_of is not None else max(latest)
    ref = curve_date - timedelta(days=30)
    tenors = []
    by_label: dict[str, dict] = {}
    for label, series_id in CURVE_TENORS:
        points = series[series_id]
        today = _on_or_before(points, curve_date, TODAY_SLACK_DAYS)
        ago = _on_or_before(points, ref, MONTH_SLACK_DAYS)
        row = {
            "tenor": label,
            "series_id": series_id,
            "yield": today,
            "yield_1m": ago,
            "chg_1m_bp": to_bp(today, ago),
        }
        tenors.append(row)
        by_label[label] = row
    if not any(row["yield"] is not None for row in tenors):
        return None
    two, ten = by_label["2Y"], by_label["10Y"]
    return {
        "date": curve_date.isoformat(),
        "ref_date_1m": ref.isoformat(),
        "regime": regime_label(two["yield"], two["yield_1m"], ten["yield"], ten["yield_1m"]),
        "tenors": tenors,
    }


def sanitize_fomc(document: dict | None) -> dict | None:
    """Public rates.fomc shape. Drops outcomes whose probability is missing or outside 0..1."""
    if not isinstance(document, dict):
        return None
    meeting = str(document.get("meeting_date") or "")[:10]
    if _day(meeting) is None:
        return None
    outcomes = []
    for row in document.get("outcomes") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        prob = number(row.get("prob"))
        if not label or prob is None or prob < 0 or prob > 1:
            continue
        outcomes.append({"label": label, "prob": prob})
    if not outcomes:
        return None
    history = []
    for row in document.get("history_14d") or []:
        if not isinstance(row, dict) or _day(row.get("date")) is None:
            continue
        cut = number(row.get("cut"))
        if cut is None or cut < 0 or cut > 1:
            continue
        history.append(
            {
                "date": _day(row.get("date")).isoformat(),
                "cut": cut,
                "hold": number(row.get("hold")),
                "hike": number(row.get("hike")),
            }
        )
    return {
        "meeting_date": meeting,
        "outcomes": outcomes,
        "history_14d": history[-14:],
        "source": "Kalshi",
    }


def _block_status(default_source: str, default_detail: str, attempt: dict | None, ready: bool) -> dict:
    attempt = attempt if isinstance(attempt, dict) else {}
    if ready:
        return {"source": default_source, "last_attempt": attempt.get("last_attempt"), "state": "ok", "detail": None}
    state = attempt.get("state") if attempt.get("state") in {"unavailable", "error"} else "unavailable"
    return {
        "source": default_source,
        "last_attempt": attempt.get("last_attempt"),
        "state": state,
        "detail": attempt.get("detail") or default_detail,
    }


def build_rates(observations: dict | None = None, fomc: dict | None = None, attempts: dict | None = None) -> dict:
    """dashboard.json `rates` object. policy_path stays unset until Ram chooses a source."""
    attempts = attempts if isinstance(attempts, dict) else {}
    curve = build_yield_curve(observations)
    served_fomc = sanitize_fomc(fomc)
    return {
        "curve": curve,
        "fomc": served_fomc,
        "policy_path": None,
        "status": {
            "curve": _block_status("FRED", _CURVE_UNAVAILABLE, attempts.get("curve"), curve is not None),
            "fomc": _block_status("Kalshi", _FOMC_UNAVAILABLE, attempts.get("fomc"), served_fomc is not None),
            "policy_path": {
                "source": None,
                "last_attempt": None,
                "state": "unavailable",
                "detail": _POLICY_UNAVAILABLE,
            },
        },
    }
