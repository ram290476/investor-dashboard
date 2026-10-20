"""Deterministic pattern detection on a reported series.

This is not a forecast and it does not call a model. Each flag describes the
reported points already in the series:

- growth or decline streak: the last three or more period changes share a sign
- sustained acceleration or deceleration: the last three changes in that
  period-over-period rate all rise, or all fall
- inflection: the latest change flips sign after at least two changes in the
  previous direction
- unusual move: the latest change is at least two sample standard deviations
  from the mean of the earlier changes, and there are at least four earlier
  changes

A series that is too short for a rule stays quiet. Nothing here is approved.
"""

from __future__ import annotations

MIN_STREAK = 3
MIN_RUN = 3
MIN_PRIOR_CHANGES = 4
Z_THRESHOLD = 2.0
PATTERN_NOTE = "Pattern detection. Not a forecast."

_PRIORITY = (
    "unusual_move",
    "inflection",
    "acceleration",
    "deceleration",
    "growth_streak",
    "decline_streak",
)


def _change(latest: float, prior: float) -> float | None:
    if prior == 0:
        return None
    return (latest - prior) / abs(prior)


def _changes(values: list[float]) -> list[float]:
    found = []
    for index in range(1, len(values)):
        change = _change(values[index], values[index - 1])
        if change is not None:
            found.append(change)
    return found


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _sample_z(latest: float, prior: list[float]) -> float | None:
    count = len(prior)
    if count < MIN_PRIOR_CHANGES:
        return None
    mean = sum(prior) / count
    variance = sum((item - mean) ** 2 for item in prior) / (count - 1)
    if variance <= 0:
        return None
    return (latest - mean) / variance**0.5


def _flag(kind: str, label: str, detail: str) -> dict:
    return {
        "kind": kind,
        "label": label,
        "detail": detail,
        "detection": "pattern",
        "forecast": False,
        "note": PATTERN_NOTE,
    }


def detect_patterns(points: list[dict] | None) -> list[dict]:
    """Flags for one series, most notable first. Empty when the history is too short."""
    reported = []
    for point in points or []:
        if not isinstance(point, dict) or not point.get("reported"):
            continue
        value = point.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        reported.append(float(value))
    changes = _changes(reported)
    if len(changes) < 2:
        return []
    flags: dict[str, dict] = {}
    signs = [_sign(item) for item in changes]
    last = signs[-1]
    if last != 0:
        streak = 1
        for sign in reversed(signs[:-1]):
            if sign != last:
                break
            streak += 1
        if streak >= MIN_STREAK:
            kind = "growth_streak" if last > 0 else "decline_streak"
            word = "increases" if last > 0 else "decreases"
            flags[kind] = _flag(
                kind,
                f"{'Growth' if last > 0 else 'Decline'} streak, {streak} periods",
                f"The last {streak} reported period changes were {word}.",
            )
    if len(changes) >= MIN_RUN + 1:
        steps = [changes[index] - changes[index - 1] for index in range(1, len(changes))]
        recent = steps[-MIN_RUN:]
        if all(step > 0 for step in recent):
            flags["acceleration"] = _flag(
                "acceleration",
                "Sustained acceleration",
                f"The period change rose for {MIN_RUN} consecutive comparisons.",
            )
        elif all(step < 0 for step in recent):
            flags["deceleration"] = _flag(
                "deceleration",
                "Sustained deceleration",
                f"The period change fell for {MIN_RUN} consecutive comparisons.",
            )
    if last != 0:
        previous = 0
        run = 0
        for sign in reversed(signs[:-1]):
            if sign == 0:
                continue
            if previous == 0:
                previous = sign
            if sign != previous:
                break
            run += 1
        if previous != 0 and previous != last and run >= 2:
            turned = "growth" if last > 0 else "decline"
            was = "decline" if previous < 0 else "growth"
            flags["inflection"] = _flag(
                "inflection",
                f"Inflection to {turned}",
                f"The change turned to {turned} after {run} periods of {was}.",
            )
    z_score = _sample_z(changes[-1], changes[:-1])
    if z_score is not None and abs(z_score) >= Z_THRESHOLD:
        flags["unusual_move"] = _flag(
            "unusual_move",
            f"Unusual move, z {z_score:.1f}",
            "The latest period change is at least two standard deviations from the earlier changes.",
        )
    return [flags[kind] for kind in _PRIORITY if kind in flags]
