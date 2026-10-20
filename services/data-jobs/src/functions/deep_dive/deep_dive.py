"""Deep-dive metrics: catalog layout, NHTSA SGO counts, manual entries, forecast contract.

The collector is source-keyed. Which tickers and which reporting entities to keep live in
the packaged metric catalog, not in this module. Collected and manual values stay proposed.
Nothing here sets approval.

The forecast block is a documented seasonal-naive baseline. It is not an in-house model,
carries no confidence band, and does not call a consensus feed.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime

import httpx
from botocore.exceptions import BotoCoreError, ClientError

import deep_dive_sources as sources

PANEL_KINDS = frozenset({
    "scoreboard",
    "trend-bars",
    "small-multiples",
    "milestone-timeline",
    "diverging-bars",
    "map-us-bubbles",
    "map-world-bubbles",
    "map-sites",
    "forecast-band",
    "table",
})
PLACEMENTS = frozenset({"primary", "secondary"})
WIDTHS = frozenset({"wide", "half"})
CONFIDENCE_LEVELS = frozenset({"low", "medium", "high", "placeholder"})
CADENCES = frozenset({"quarterly", "monthly", "weekly"})

# Structured-field buckets. Narratives are redacted, so kind comes from these fields only.
KIND_LABELS = {
    "vulnerable_road_user": "Vulnerable road user",
    "fixed_object": "Fixed object",
    "rear_contact": "Rear contact",
    "side_contact": "Side contact",
    "front_contact": "Front contact",
    "intersection": "Intersection / turning",
    "other": "Other / unknown",
}
KIND_ORDER = list(KIND_LABELS)

EMPTY_LABELS = {
    "not_disclosed": "Not yet disclosed",
    "chart_only": "Chart only — no numeric disclosure",
    "narrative": "Narrative only",
    "not_collected": "Not yet collected",
    "not_estimated": "Not estimated",
}

PLACEHOLDER_DISCLAIMER = (
    "Placeholder only — not a real forecast model. "
    "Each future quarter copies the reported actual from the same fiscal quarter a year earlier "
    "(a seasonal-naive baseline). Bands are not estimated. "
    "Consensus is omitted until a licensed estimates feed exists."
)
PRIOR_EMPTY = (
    "No prior projection is stored yet. "
    "Deep-dive collect locks each next-quarter headline so it can be compared after the report."
)
PRIOR_WAITING = (
    "Each next-quarter headline from this placeholder is stored when deep-dive collect runs. "
    "It is compared with the reported actual after that quarter is in. A stored headline is not replaced."
)
PRIOR_COMPARE = (
    "Stored headline versus the reported actual. "
    "The stored figure is the seasonal-naive placeholder from before that quarter was reported, and it is not replaced."
)
FORECAST_TODOS = (
    "Build and backtest an in-house model on the deep-dive series. This baseline is not that model.",
    "Licensed consensus estimates feed — no key is configured, and none should be added without review.",
    "NREL AFDC (or another licensed site feed) for charger locations — no API key is configured.",
)

NHTSA_ADS_URL = "https://static.nhtsa.gov/odi/ffdd/sgo-2021-01/SGO-2021-01_Incident_Reports_ADS.csv"
NHTSA_ADAS_URL = "https://static.nhtsa.gov/odi/ffdd/sgo-2021-01/SGO-2021-01_Incident_Reports_ADAS.csv"
NHTSA_MODES = {"ads": NHTSA_ADS_URL, "adas": NHTSA_ADAS_URL}
NHTSA_NOTES = (
    "Counts are incident reports, not fault determinations.",
    "Narratives may be redacted, so the kind of issue is taken from structured fields only.",
    "One report id keeps its latest Report Version. Same Incident ID is not collapsed further.",
    "SGO amendments changed reporting thresholds. A series break in the catalog marks that shift.",
)

_QUARTER = re.compile(r"^(\d{4})Q([1-4])$")
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_METRIC_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VRU = ("pedestrian", "bicycle", "cyclist", "vulnerable")
_FIXED = ("fixed object", "pole", "tree", "animal", "building", "curb")
_TURN = ("turn", "crossing", "u-turn", "u turn")
_YES = frozenset({"y", "yes", "true", "1"})


def observations_key(ticker: str) -> str:
    return f"curated/deep_dive/ticker={str(ticker or '').upper()}/observations.json"


def manual_key(ticker: str) -> str:
    return f"manual/deep_dive/{str(ticker or '').upper()}.json"


def validate_deep_dive(block) -> list[str]:
    """Catalog errors for an optional deep_dive object. None means the ticker has no deep dive."""
    if block is None:
        return []
    errors = []
    if not isinstance(block, dict):
        return ["deep_dive must be an object"]
    sections = block.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("deep_dive.sections must be a non-empty list")
        sections = []
    seen = set()
    for section in sections:
        if not isinstance(section, dict):
            errors.append("deep_dive section must be an object")
            continue
        sid = str(section.get("id") or "")
        if not sid:
            errors.append("deep_dive section id is required")
        elif sid in seen:
            errors.append(f"duplicate deep_dive section {sid}")
        seen.add(sid)
        if section.get("placement", "primary") not in PLACEMENTS:
            errors.append(f"{sid or 'section'} has unsupported placement")
        headline = section.get("headline")
        if headline not in (None, ""):
            if not isinstance(headline, str) or not _METRIC_ID.match(headline):
                errors.append(f"{sid or 'section'} headline must be a metric id")
        panels = section.get("panels")
        if not isinstance(panels, list) or not panels:
            errors.append(f"{sid or 'section'} needs panels")
            continue
        for panel in panels:
            errors.extend(_validate_panel(sid, panel))
    for source in block.get("sources") or []:
        if not isinstance(source, dict) or not str(source.get("id") or "").strip():
            errors.append("deep_dive source needs an id")
            continue
        if source.get("id") == "nhtsa_sgo" and not str(source.get("reporting_entity") or "").strip():
            errors.append("nhtsa_sgo source needs reporting_entity")
        errors.extend(sources.validate_extra_source(source))
    forecast = block.get("forecast")
    if forecast is not None:
        if not isinstance(forecast, dict) or not isinstance(forecast.get("segments"), list):
            errors.append("deep_dive.forecast.segments must be a list")
    return errors


def _validate_panel(section_id: str, panel) -> list[str]:
    if not isinstance(panel, dict):
        return [f"{section_id} panel must be an object"]
    errors = []
    pid = str(panel.get("id") or "")
    where = pid or section_id
    if not pid:
        errors.append(f"{section_id} panel id is required")
    if panel.get("kind") not in PANEL_KINDS:
        errors.append(f"{where} has unsupported panel kind {panel.get('kind')!r}")
    if not str(panel.get("title") or "").strip():
        errors.append(f"{where} needs a title")
    if panel.get("width", "wide") not in WIDTHS:
        errors.append(f"{where} has unsupported width")
    if panel.get("cadence") is not None and panel.get("cadence") not in CADENCES:
        errors.append(f"{where} has unsupported cadence")
    return errors


def shift_period(period: str, steps: int) -> str | None:
    """Move a fiscal quarter or calendar month by `steps`. Negative steps go backward."""
    quarter = _QUARTER.match(str(period or ""))
    if quarter:
        index = int(quarter.group(1)) * 4 + (int(quarter.group(2)) - 1) + steps
        year, slot = divmod(index, 4)
        return f"{year}Q{slot + 1}"
    month = _MONTH.match(str(period or ""))
    if month:
        index = int(month.group(1)) * 12 + (int(month.group(2)) - 1) + steps
        year, slot = divmod(index, 12)
        return f"{year}-{slot + 1:02d}"
    return None


def _period_sort_key(period: str) -> tuple:
    quarter = _QUARTER.match(str(period or ""))
    if quarter:
        return (0, int(quarter.group(1)), int(quarter.group(2)))
    month = _MONTH.match(str(period or ""))
    if month:
        return (1, int(month.group(1)), int(month.group(2)))
    return (2, 0, 0)


def _change(latest: float | None, prior: float | None) -> float | None:
    if latest is None or prior in (None, 0):
        return None
    return (latest - prior) / abs(prior)


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _confidence(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().lower() in CONFIDENCE_LEVELS:
        return value.strip().lower()
    number = _as_float(value)
    if number is None:
        return None
    if number >= 0.8:
        return "high"
    if number >= 0.5:
        return "medium"
    return "low"


def _flag(value) -> bool:
    return str(value or "").strip().lower() in _YES


def classify_crash(row: dict) -> str:
    """One kind per report, from structured fields. Narrative text is ignored."""
    crash_with = str(row.get("Crash With") or "").lower()
    if any(token in crash_with for token in _VRU):
        return "vulnerable_road_user"
    if any(token in crash_with for token in _FIXED):
        return "fixed_object"
    rear = _flag(row.get("SV Contact Area - Rear"))
    side = any(_flag(row.get(name)) for name in (
        "SV Contact Area - Left",
        "SV Contact Area - Right",
        "SV Contact Area - Rear Left",
        "SV Contact Area - Rear Right",
        "SV Contact Area - Front Left",
        "SV Contact Area - Front Right",
    ))
    front = _flag(row.get("SV Contact Area - Front"))
    if rear and not side and not front:
        return "rear_contact"
    if side:
        return "side_contact"
    if front:
        return "front_contact"
    movement = str(row.get("SV Pre-Crash Movement") or "").lower()
    if any(token in movement for token in _TURN):
        return "intersection"
    return "other"


def _report_period(row: dict) -> str | None:
    year = str(row.get("Report Year") or "").strip()
    month = str(row.get("Report Month") or "").strip()
    if year.isdigit() and month.isdigit():
        number = int(month)
        if 1 <= number <= 12 and len(year) == 4:
            return f"{int(year)}-{number:02d}"
    return None


def _version(value) -> int:
    try:
        return int(str(value or "0").strip() or "0")
    except ValueError:
        return 0


def parse_sgo_csv(text: str, *, reporting_entity: str, mode: str) -> dict:
    """Aggregate one SGO file for a reporting entity. Row-level identifiers are not returned."""
    wanted = str(reporting_entity or "").casefold().strip()
    latest: dict[str, tuple[int, dict]] = {}
    reader = csv.DictReader(io.StringIO(text or ""))
    seen = 0
    for row in reader:
        seen += 1
        entity = str(row.get("Reporting Entity") or "")
        if not wanted or wanted not in entity.casefold():
            continue
        report_id = str(row.get("Report ID") or "").strip()
        if not report_id:
            continue
        version = _version(row.get("Report Version"))
        current = latest.get(report_id)
        if current is None or version >= current[0]:
            latest[report_id] = (version, row)
    counts: dict[str, int] = {}
    by_kind: dict[str, dict[str, int]] = {}
    for _kept, row in latest.values():
        period = _report_period(row)
        if not period:
            continue
        kind = classify_crash(row)
        counts[period] = counts.get(period, 0) + 1
        bucket = by_kind.setdefault(period, {})
        bucket[kind] = bucket.get(kind, 0) + 1
    return {
        "mode": mode,
        "matched": len(latest),
        "scanned": seen,
        "counts": counts,
        "by_kind": by_kind,
    }


def _series_from_counts(metric_id: str, parsed: dict, source: dict) -> dict:
    periods = sorted(set(parsed["counts"]) | set(parsed["by_kind"]), key=_period_sort_key)
    points = []
    breakdowns = []
    for period in periods:
        points.append({"period": period, "value": parsed["counts"].get(period, 0), "reported": True})
        for kind in KIND_ORDER:
            value = (parsed["by_kind"].get(period) or {}).get(kind, 0)
            if value:
                breakdowns.append({"period": period, "kind": kind, "label": KIND_LABELS[kind], "value": value})
    notes = list(NHTSA_NOTES)
    for item in source.get("series_breaks") or []:
        if isinstance(item, dict) and item.get("note"):
            notes.append(str(item["note"]))
    return {
        "metric_id": metric_id,
        "unit": "reports",
        "cadence": "monthly",
        "confidence": "high",
        "approval_state": "proposed",
        "approved": False,
        "source_kind": "nhtsa_sgo",
        "source_url": NHTSA_MODES.get(parsed["mode"], NHTSA_ADS_URL),
        "source_title": f"NHTSA SGO 2021-01 {parsed['mode'].upper()} incident reports",
        "disclosure_status": "numeric" if points else "not_collected",
        "points": points,
        "breakdowns": breakdowns,
        "notes": notes,
        "series_breaks": [item for item in source.get("series_breaks") or [] if isinstance(item, dict)],
    }


def normalize_manual_entry(entry: dict) -> dict | None:
    """Keep a sourced manual value and force it to pending review."""
    if not isinstance(entry, dict):
        return None
    metric_id = str(entry.get("metric_id") or "")
    period = str(entry.get("period") or "")
    if not _METRIC_ID.match(metric_id):
        return None
    if not (_QUARTER.match(period) or _MONTH.match(period)):
        return None
    url = str(entry.get("source_url") or "")
    if not url.startswith("https://"):
        return None
    confidence = _confidence(entry.get("confidence")) or "low"
    dimensions = entry.get("dimensions") if isinstance(entry.get("dimensions"), dict) else {}
    clean_dimensions = {}
    for key, value in dimensions.items():
        if value is None or isinstance(value, (dict, list)):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            clean_dimensions[str(key)[:40]] = str(value)[:200]
        else:
            clean_dimensions[str(key)[:40]] = value
    value = _as_float(entry.get("value"))
    return {
        "metric_id": metric_id,
        "period": period,
        "value": value,
        "unit": str(entry.get("unit") or "")[:40],
        "confidence": confidence,
        "source_url": url,
        "source_title": str(entry.get("source_title") or "")[:200],
        "note": str(entry.get("note") or "")[:500],
        "dimensions": clean_dimensions,
        "approval_state": "proposed",
        "approved": False,
    }


def series_from_manual(entries: list[dict]) -> list[dict]:
    """Group manual points by metric. Approval flags in the file are ignored."""
    grouped: dict[str, list[dict]] = {}
    for raw in entries or []:
        cleaned = normalize_manual_entry(raw)
        if cleaned is None:
            continue
        grouped.setdefault(cleaned["metric_id"], []).append(cleaned)
    series = []
    for metric_id, points in grouped.items():
        points.sort(key=lambda item: _period_sort_key(item["period"]))
        latest = points[-1]
        series.append({
            "metric_id": metric_id,
            "unit": latest.get("unit") or "",
            "cadence": "monthly" if _MONTH.match(latest["period"]) else "quarterly",
            "confidence": latest.get("confidence") or "low",
            "approval_state": "proposed",
            "approved": False,
            "source_kind": "manual",
            "source_url": latest.get("source_url"),
            "source_title": latest.get("source_title") or "Manual entry",
            "disclosure_status": "numeric",
            "points": [
                {
                    "period": item["period"],
                    "value": item["value"],
                    "reported": item["value"] is not None,
                    "dimensions": item.get("dimensions") or {},
                    "note": item.get("note") or "",
                    "source_url": item.get("source_url"),
                }
                for item in points
            ],
            "notes": ["Manual entry. Pending review. Not an approved company metric."],
        })
    return series


def _index_series(series_list: list[dict]) -> dict[str, dict]:
    return {item["metric_id"]: item for item in series_list if item.get("metric_id")}


def _company_series(metric_id: str, rows: list[dict], entry: dict | None) -> dict | None:
    if not rows:
        return None
    points = []
    for row in sorted(rows, key=lambda item: _period_sort_key(str(item.get("fiscal_period") or ""))):
        period = str(row.get("fiscal_period") or "")
        if not period:
            continue
        value = _as_float(row.get("value"))
        points.append({"period": period, "value": value, "reported": value is not None})
    if not points:
        return None
    status = (entry or {}).get("status") or "proposed"
    if status not in {"approved", "proposed"}:
        return None
    latest_row = rows[-1]
    return {
        "metric_id": metric_id,
        "label": (entry or {}).get("display_name") or metric_id,
        "unit": (entry or {}).get("unit") or latest_row.get("unit") or "",
        "cadence": "quarterly",
        "confidence": _confidence(latest_row.get("confidence")),
        "approval_state": status,
        "approved": status == "approved",
        "source_kind": "company_metrics",
        "source_url": latest_row.get("source_url"),
        "source_title": (entry or {}).get("display_name") or metric_id,
        "disclosure_status": (entry or {}).get("disclosure_status") or "numeric",
        "definition": (entry or {}).get("definition") or "",
        "points": points,
        "notes": [],
    }


def _points_map(series: dict | None) -> dict[str, dict]:
    if not series:
        return {}
    return {point["period"]: point for point in series.get("points") or [] if point.get("period")}


def _tile(metric_id: str, series: dict | None, spec: dict) -> dict:
    label = spec.get("label") or (series or {}).get("label") or metric_id
    unit = spec.get("unit") or (series or {}).get("unit") or ""
    points = list((series or {}).get("points") or [])
    points.sort(key=lambda item: _period_sort_key(item.get("period") or ""))
    reported = [point for point in points if point.get("reported") and point.get("value") is not None]
    latest = reported[-1] if reported else None
    disclosure = (series or {}).get("disclosure_status") or spec.get("disclosure_status") or "not_disclosed"
    if latest is None and disclosure == "numeric":
        disclosure = "not_collected"
    qoq = yoy = None
    if latest:
        by_period = _points_map(series)
        step = 12 if (series or {}).get("cadence") == "monthly" else 4
        prior_q = by_period.get(shift_period(latest["period"], -1) or "")
        prior_y = by_period.get(shift_period(latest["period"], -step) or "")
        qoq = _change(latest.get("value"), None if not prior_q else prior_q.get("value"))
        yoy = _change(latest.get("value"), None if not prior_y else prior_y.get("value"))
    spark = [point.get("value") if point.get("reported") else None for point in points[-11:]]
    approval = (series or {}).get("approval_state") or "proposed"
    return {
        "metric_id": metric_id,
        "label": label,
        "unit": unit,
        "value": None if latest is None else latest.get("value"),
        "period": None if latest is None else latest.get("period"),
        "qoq": qoq,
        "yoy": yoy,
        "sparkline": spark,
        "inverse": bool(spec.get("inverse")),
        "approval_state": approval,
        "approved": bool((series or {}).get("approved")) and latest is not None,
        "confidence": (series or {}).get("confidence"),
        "disclosure_status": disclosure,
        "empty_label": "" if latest is not None else EMPTY_LABELS.get(disclosure, EMPTY_LABELS["not_disclosed"]),
        "source_kind": (series or {}).get("source_kind"),
        "source_url": (series or {}).get("source_url"),
    }


def _chart_series(spec: dict, found: dict | None, color_index: int) -> dict:
    points = list((found or {}).get("points") or [])
    points.sort(key=lambda item: _period_sort_key(item.get("period") or ""))
    reported = [point for point in points if point.get("reported") and point.get("value") is not None]
    return {
        "metric_id": spec.get("metric_id"),
        "label": spec.get("label") or (found or {}).get("label") or spec.get("metric_id"),
        "role": spec.get("role") or "bar",
        "axis": spec.get("axis") or "left",
        "stack": spec.get("stack") or "",
        "unit": spec.get("unit") or (found or {}).get("unit") or "",
        "color_index": color_index,
        "points": [
            {
                "period": point.get("period"),
                "value": point.get("value"),
                "reported": bool(point.get("reported")),
                "estimate": bool(point.get("estimate") or (found or {}).get("estimate")),
                "source_url": point.get("source_url") or "",
            }
            for point in points
        ],
        "approval_state": (found or {}).get("approval_state") or "proposed",
        "approved": bool((found or {}).get("approved")),
        "estimate": bool((found or {}).get("estimate")),
        "source_url": (found or {}).get("source_url") or "",
        "confidence": (found or {}).get("confidence"),
        "disclosure_status": (found or {}).get("disclosure_status") or ("numeric" if reported else "not_disclosed"),
        "empty_label": "" if reported else EMPTY_LABELS.get(
            (found or {}).get("disclosure_status") or "",
            EMPTY_LABELS["not_disclosed"],
        ),
        "source_kind": (found or {}).get("source_kind"),
    }


def _stacked_from_breakdown(found: dict | None) -> list[dict]:
    if not found or not found.get("breakdowns"):
        return []
    by_kind: dict[str, list[dict]] = {}
    periods = []
    for item in found["breakdowns"]:
        by_kind.setdefault(item["kind"], []).append(item)
        if item["period"] not in periods:
            periods.append(item["period"])
    periods.sort(key=_period_sort_key)
    series = []
    for index, kind in enumerate(kind for kind in KIND_ORDER if kind in by_kind):
        values = {item["period"]: item["value"] for item in by_kind[kind]}
        series.append({
            "metric_id": f"{found['metric_id']}_{kind}",
            "label": KIND_LABELS.get(kind, kind),
            "role": "bar",
            "axis": "left",
            "stack": "kind",
            "unit": "reports",
            "color_index": (index % 6) + 1,
            "points": [
                {"period": period, "value": values.get(period, 0), "reported": period in values}
                for period in periods
            ],
            "approval_state": "proposed",
            "approved": False,
            "confidence": found.get("confidence") or "high",
            "disclosure_status": "numeric",
            "empty_label": "",
            "source_kind": "nhtsa_sgo",
        })
    return series


def _markers(found: dict | None) -> list[dict]:
    markers = []
    for point in (found or {}).get("points") or []:
        dimensions = point.get("dimensions") if isinstance(point.get("dimensions"), dict) else {}
        place = str(dimensions.get("place") or dimensions.get("country") or dimensions.get("market") or "")
        if not place:
            continue
        markers.append({
            "place": place,
            "value": point.get("value"),
            "reported": bool(point.get("reported")) and point.get("value") is not None,
            "estimate": bool(point.get("estimate") or (found or {}).get("estimate")),
            "status": str(dimensions.get("status") or ""),
            "delta": _as_float(dimensions.get("delta")),
            "approval_state": "proposed",
            "confidence": point.get("confidence") or (found or {}).get("confidence") or "low",
            "period": point.get("period"),
            "note": point.get("note") or "",
            "source_url": point.get("source_url") or (found or {}).get("source_url") or "",
        })
    return markers


def _milestones(specs: list[dict], found: dict | None) -> list[dict]:
    by_id = {}
    for point in (found or {}).get("points") or []:
        dimensions = point.get("dimensions") if isinstance(point.get("dimensions"), dict) else {}
        key = str(dimensions.get("milestone_id") or "")
        if not key:
            continue
        current = by_id.get(key)
        new_date = str(dimensions.get("date") or "")
        old_date = str(((current or {}).get("dimensions") or {}).get("date") or "")
        if current is None or (new_date and (not old_date or new_date < old_date)):
            by_id[key] = point
    rendered = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        point = by_id.get(str(spec.get("id") or ""))
        dimensions = point.get("dimensions") if point and isinstance(point.get("dimensions"), dict) else {}
        reported = point is not None and bool(dimensions.get("date") or dimensions.get("state"))
        rendered.append({
            "id": spec.get("id"),
            "label": spec.get("label") or spec.get("id"),
            "date": dimensions.get("date") or "",
            "state": dimensions.get("state") or "",
            "note": (point or {}).get("note") or "",
            "reported": reported,
            "estimate": bool(reported and ((point or {}).get("estimate") or (found or {}).get("estimate"))),
            "source_url": (point or {}).get("source_url") or "",
            "empty_label": "" if reported else "Not yet disclosed",
            "approval_state": "proposed" if reported else "",
            "confidence": (point or {}).get("confidence") or (found or {}).get("confidence") if reported else None,
        })
    return rendered


def _table(spec: dict | None, found: dict | None) -> dict | None:
    if not isinstance(spec, dict):
        return None
    columns = spec.get("columns") if isinstance(spec.get("columns"), list) else []
    rows = []
    for point in (found or {}).get("points") or []:
        dimensions = point.get("dimensions") if isinstance(point.get("dimensions"), dict) else {}
        cells = []
        for column in columns:
            key = column.get("id")
            if key in {"value", "period"}:
                cells.append(point.get(key))
            else:
                cells.append(dimensions.get(key))
        rows.append({
            "cells": cells,
            "approval_state": "proposed",
            "approved": False,
            "confidence": point.get("confidence") or (found or {}).get("confidence"),
            "estimate": bool(point.get("estimate") or (found or {}).get("estimate")),
            "source_url": point.get("source_url") or "",
            "source_title": point.get("source_title") or (found or {}).get("source_title") or "",
        })
    return {"columns": columns, "rows": rows}


def placeholder_forecast(forecast_spec: dict | None, metrics: dict[str, dict]) -> dict:
    """Seasonal-naive contract. Missing actuals stay null. Never an approved forecast."""
    segments_out = []
    spec = forecast_spec if isinstance(forecast_spec, dict) else {}
    for segment in spec.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        metrics_out = []
        for field, metric_name in (("revenue", "revenue_metric"), ("earnings", "eps_metric")):
            metric_id = segment.get(metric_name)
            if not metric_id:
                metrics_out.append(_empty_forecast_metric(field))
                continue
            metrics_out.append(_forecast_metric(field, str(metric_id), metrics.get(str(metric_id))))
        segments_out.append({
            "id": segment.get("id"),
            "label": segment.get("label") or segment.get("id"),
            "metrics": metrics_out,
        })
    return {
        "model_id": "seasonal_naive_v0",
        "model_status": "placeholder",
        "disclaimer": PLACEHOLDER_DISCLAIMER,
        "todos": list(FORECAST_TODOS),
        "consensus": None,
        "consensus_note": "Consensus needs a licensed estimates feed. None is configured.",
        "prior_projection": [],
        "prior_projection_note": PRIOR_EMPTY,
        "stored_headlines": [],
        "segments": segments_out,
    }


def _headline_key(item: dict) -> tuple:
    return (item.get("segment_id"), item.get("field"), item.get("period"))


def lock_headlines(forecast: dict, stored: list | None, generated_at: str | None) -> list[dict]:
    """Keep every stored next-quarter headline. Add one only before that quarter is reported."""
    headlines = [dict(item) for item in stored or [] if isinstance(item, dict)]
    known = {_headline_key(item) for item in headlines}
    actuals = set()
    for segment in forecast.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for metric in segment.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            for point in metric.get("actuals") or []:
                if point.get("value") is not None:
                    actuals.add((segment.get("id"), metric.get("field"), point.get("period")))
    for segment in forecast.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for metric in segment.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            points = [
                point for point in metric.get("forecast") or []
                if point.get("value") is not None and point.get("reported")
            ]
            if not points:
                continue
            head = points[0]
            key = (segment.get("id"), metric.get("field"), head.get("period"))
            if key in known or key in actuals or not head.get("period"):
                continue
            headlines.append({
                "segment_id": segment.get("id"),
                "segment_label": segment.get("label") or segment.get("id"),
                "field": metric.get("field"),
                "metric_id": metric.get("metric_id"),
                "period": head.get("period"),
                "value": head.get("value"),
                "unit": metric.get("unit"),
                "stored_at": generated_at,
                "model_id": forecast.get("model_id"),
                "model_status": "placeholder",
                "approval_state": "proposed",
            })
            known.add(key)
    return headlines


def projection_comparisons(forecast: dict, stored: list | None) -> list[dict]:
    actuals = {}
    for segment in forecast.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for metric in segment.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            for point in metric.get("actuals") or []:
                if point.get("value") is not None:
                    actuals[(segment.get("id"), metric.get("field"), point.get("period"))] = point.get("value")
    rows = []
    for item in stored or []:
        if not isinstance(item, dict):
            continue
        key = _headline_key(item)
        if key not in actuals:
            continue
        projected = item.get("value")
        actual = actuals[key]
        delta = None if projected is None or actual is None else actual - projected
        rows.append({
            "segment_id": item.get("segment_id"),
            "segment_label": item.get("segment_label") or item.get("segment_id"),
            "field": item.get("field"),
            "metric_id": item.get("metric_id"),
            "period": item.get("period"),
            "projected": projected,
            "actual": actual,
            "delta": delta,
            "unit": item.get("unit"),
            "stored_at": item.get("stored_at"),
            "model_id": item.get("model_id"),
            "model_status": "placeholder",
            "approval_state": "proposed",
        })
    return rows


def apply_projections(forecast: dict, stored: list | None, generated_at: str | None, *, lock: bool) -> dict:
    headlines = lock_headlines(forecast, stored, generated_at) if lock else [
        dict(item) for item in stored or [] if isinstance(item, dict)
    ]
    compared = projection_comparisons(forecast, headlines)
    forecast["stored_headlines"] = headlines
    forecast["prior_projection"] = compared
    if compared:
        forecast["prior_projection_note"] = PRIOR_COMPARE
    elif headlines:
        forecast["prior_projection_note"] = PRIOR_WAITING
    else:
        forecast["prior_projection_note"] = PRIOR_EMPTY
    return forecast


def _empty_forecast_metric(field: str) -> dict:
    return {
        "field": field,
        "metric_id": None,
        "unit": "USD" if field == "revenue" else "per_share",
        "actuals": [],
        "forecast": [],
        "disclosure_status": "not_disclosed",
        "empty_label": "Not yet disclosed",
        "approval_state": "proposed",
    }


def _forecast_metric(field: str, metric_id: str, series: dict | None) -> dict:
    points = [
        point for point in (series or {}).get("points") or []
        if point.get("reported") and point.get("value") is not None
    ]
    points.sort(key=lambda item: _period_sort_key(item.get("period") or ""))
    if not points:
        empty = _empty_forecast_metric(field)
        empty["metric_id"] = metric_id
        if series is None:
            empty["disclosure_status"] = "not_disclosed"
        else:
            empty["disclosure_status"] = series.get("disclosure_status") or "not_collected"
        empty["empty_label"] = EMPTY_LABELS.get(empty["disclosure_status"], "Not yet collected")
        return empty
    latest = points[-1]["period"]
    by_period = {point["period"]: point for point in points}
    forecast_points = []
    for step in range(1, 5):
        period = shift_period(latest, step)
        if not period:
            continue
        baseline_period = shift_period(period, -4)
        baseline = by_period.get(baseline_period or "")
        forecast_points.append({
            "period": period,
            "value": None if baseline is None else baseline.get("value"),
            "band_low": None,
            "band_high": None,
            "reported": baseline is not None,
            "method": "seasonal_naive",
            "baseline_period": baseline_period,
            "approval_state": "proposed",
            "confidence": "placeholder",
            "model_status": "placeholder",
        })
    return {
        "field": field,
        "metric_id": metric_id,
        "unit": (series or {}).get("unit") or ("USD" if field == "revenue" else "per_share"),
        "actuals": [
            {
                "period": point["period"],
                "value": point["value"],
                "reported": True,
                "approval_state": (series or {}).get("approval_state") or "proposed",
                "approved": bool((series or {}).get("approved")),
                "source_kind": (series or {}).get("source_kind"),
            }
            for point in points
        ],
        "forecast": forecast_points,
        "disclosure_status": "numeric",
        "empty_label": "",
        "approval_state": (series or {}).get("approval_state") or "proposed",
        "approved": bool((series or {}).get("approved")),
    }


def _lookup_metric(metric_id: str | None, metrics: dict[str, dict], catalog: dict) -> dict | None:
    if not metric_id:
        return None
    if metric_id in metrics:
        return metrics[metric_id]
    entry = next((item for item in catalog.get("metrics") or [] if item.get("metric_id") == metric_id), None)
    if entry is None:
        return None
    return {
        "metric_id": metric_id,
        "label": entry.get("display_name") or metric_id,
        "unit": entry.get("unit") or "",
        "disclosure_status": entry.get("disclosure_status") or "not_disclosed",
        "definition": entry.get("definition") or "",
        "approval_state": entry.get("status") or "proposed",
        "approved": entry.get("status") == "approved",
        "points": [],
        "notes": [],
    }


def assemble_deep_dive(
    catalog: dict,
    company_rows: list[dict] | None = None,
    observations: dict | None = None,
    manual: dict | None = None,
    *,
    generated_at: str | None = None,
    projections: list | None = None,
    news: dict | None = None,
    lock_projections: bool = False,
) -> dict | None:
    """Layout from the catalog, filled with real series. Missing series stay empty, never zero."""
    block = catalog.get("deep_dive") if isinstance(catalog, dict) else None
    if not isinstance(block, dict) or not block.get("sections"):
        return None
    by_metric_rows: dict[str, list[dict]] = {}
    for row in company_rows or []:
        metric_id = str(row.get("metric_id") or "")
        if metric_id:
            by_metric_rows.setdefault(metric_id, []).append(row)
    metrics: dict[str, dict] = {}
    for metric_id, rows in by_metric_rows.items():
        entry = next((item for item in catalog.get("metrics") or [] if item.get("metric_id") == metric_id), None)
        built = _company_series(metric_id, rows, entry)
        if built:
            metrics[metric_id] = built
    observed = _index_series(list((observations or {}).get("series") or []))
    manual_series = _index_series(series_from_manual(list((manual or {}).get("entries") or [])))
    estimates: dict[str, dict] = {}
    # Collected series fill gaps. A news estimate never replaces a reported or manual series.
    for metric_id, series in observed.items():
        if series.get("estimate") or series.get("source_kind") == "news_estimate":
            estimates[metric_id] = _force_news_estimate(series)
            continue
        metrics.setdefault(metric_id, series)
    for metric_id, series in manual_series.items():
        current = metrics.get(metric_id)
        reported = current and any(point.get("reported") for point in current.get("points") or [])
        if current and current.get("source_kind") == "company_metrics" and reported:
            continue
        metrics[metric_id] = series
    topic_rows, estimate_rows = sources.series_from_rollup(news)
    for series in topic_rows:
        metrics[series["metric_id"]] = series
    for series in estimate_rows:
        estimates[series["metric_id"]] = series
    forecast = apply_projections(
        placeholder_forecast(block.get("forecast"), metrics),
        projections if projections is not None else (observations or {}).get("stored_headlines"),
        generated_at,
        lock=lock_projections,
    )
    sections = []
    for section in block.get("sections") or []:
        panels = []
        for panel in section.get("panels") or []:
            panels.append(_fill_panel(panel, metrics, estimates, catalog, forecast))
        built = {
            "id": section.get("id"),
            "title": section.get("title"),
            "placement": section.get("placement") or "primary",
            "panels": panels,
        }
        headline = str(section.get("headline") or "").strip()
        if headline:
            built["headline"] = headline
        sections.append(built)
    return {
        "version": 1,
        "title": block.get("title") or "Deep dive",
        "generated_at": generated_at,
        "model_status": "placeholder",
        "disclaimer": PLACEHOLDER_DISCLAIMER,
        "sections": sections,
        "forecast": forecast,
        "sources": list((observations or {}).get("sources") or []),
    }


def _force_news_estimate(series: dict) -> dict:
    series["approval_state"] = "proposed"
    series["approved"] = False
    series["estimate"] = True
    series["source_kind"] = "news_estimate"
    if series.get("confidence") == "high":
        series["confidence"] = "medium"
    return series


def _estimate_points(series: dict | None) -> list[dict]:
    return [
        point for point in (series or {}).get("points") or []
        if point.get("value") is not None and point.get("reported")
    ]


def _attach_estimate(tile: dict, estimate: dict | None) -> dict:
    """Show a news estimate beside a reported value, or alone when nothing was reported."""
    points = _estimate_points(estimate)
    if not points:
        return tile
    latest = points[-1]
    tile["estimate"] = True
    tile["estimate_value"] = latest.get("value")
    tile["estimate_period"] = latest.get("period")
    tile["estimate_confidence"] = (estimate or {}).get("confidence") or latest.get("confidence")
    tile["estimate_source_url"] = latest.get("source_url") or (estimate or {}).get("source_url") or ""
    tile["estimate_source_title"] = latest.get("source_title") or (estimate or {}).get("source_title") or ""
    if tile.get("value") is None:
        tile["value"] = latest.get("value")
        tile["period"] = latest.get("period")
        tile["approval_state"] = "proposed"
        tile["approved"] = False
        tile["confidence"] = tile["estimate_confidence"]
        tile["source_kind"] = "news_estimate"
        tile["source_url"] = tile["estimate_source_url"]
        tile["empty_label"] = ""
        tile["estimate_only"] = True
        tile["sparkline"] = [point.get("value") for point in points[-11:]]
    return tile


def _attach_regulator(tile: dict, series: dict | None) -> dict:
    """Sit a public regulator count beside a news estimate. It stays pending review."""
    points = [
        point for point in (series or {}).get("points") or []
        if isinstance(point, dict) and point.get("reported") and point.get("value") is not None
    ]
    if not points:
        return tile
    points.sort(key=lambda item: _period_sort_key(item.get("period") or ""))
    latest = points[-1]
    tile["regulator_value"] = latest.get("value")
    tile["regulator_period"] = latest.get("period")
    tile["regulator_source_url"] = latest.get("source_url") or (series or {}).get("source_url") or ""
    tile["regulator_source_title"] = (series or {}).get("source_title") or "Regulator records"
    tile["regulator_approval_state"] = (series or {}).get("approval_state") or "proposed"
    return tile


def _merge_points(reported: dict | None, estimate: dict | None) -> dict | None:
    extra = [point for point in (estimate or {}).get("points") or [] if isinstance(point, dict)]
    if not extra:
        return reported
    if not reported:
        return estimate
    merged = dict(reported)
    merged["points"] = [*list(reported.get("points") or []), *extra]
    return merged


def _estimate_companion(spec: dict, estimate: dict, color: int) -> dict:
    label = spec.get("label") or spec.get("metric_id") or "Estimate"
    companion = _chart_series(
        {
            "metric_id": spec.get("metric_id"),
            "label": f"{label} estimate",
            "role": "line-dashed",
            "axis": spec.get("axis") or "left",
            "stack": "",
            "unit": spec.get("unit"),
        },
        estimate,
        color,
    )
    companion["estimate"] = True
    return companion


def _fill_panel(
    panel: dict,
    metrics: dict[str, dict],
    estimates: dict[str, dict],
    catalog: dict,
    forecast: dict,
) -> dict:
    kind = panel.get("kind")
    kpis = []
    for spec in panel.get("kpis") or panel.get("tiles") or []:
        if isinstance(spec, str):
            spec = {"metric_id": spec}
        metric_id = spec.get("metric_id")
        found = _lookup_metric(metric_id, metrics, catalog)
        tile = _attach_estimate(_tile(metric_id, found, spec), estimates.get(metric_id))
        regulator_id = spec.get("regulator_metric_id")
        if regulator_id:
            tile = _attach_regulator(tile, _lookup_metric(regulator_id, metrics, catalog))
        kpis.append(tile)
    chart = []
    color = 1
    for spec in panel.get("series") or []:
        if not isinstance(spec, dict):
            continue
        found = _lookup_metric(spec.get("metric_id"), metrics, catalog)
        if spec.get("stack_by") == "kind" and found and found.get("breakdowns"):
            chart.extend(_stacked_from_breakdown(found))
            continue
        chart.append(_chart_series(spec, found, color))
        estimate = estimates.get(spec.get("metric_id"))
        if estimate and _estimate_points(estimate):
            chart.append(_estimate_companion(spec, estimate, color))
        color = color % 6 + 1
    map_metric = _lookup_metric(panel.get("map_metric"), metrics, catalog)
    map_estimate = estimates.get(panel.get("map_metric"))
    multiples = []
    for spec in panel.get("multiples") or []:
        if not isinstance(spec, dict):
            continue
        found = _lookup_metric(spec.get("metric_id"), metrics, catalog)
        estimate = estimates.get(spec.get("metric_id"))
        multiples.append(_attach_estimate(_tile(spec.get("metric_id"), found, spec), estimate))
    modes = []
    for spec in panel.get("modes") or []:
        if not isinstance(spec, dict):
            continue
        found = _lookup_metric(spec.get("metric_id"), metrics, catalog)
        mode_series = _stacked_from_breakdown(found) if found and found.get("breakdowns") else []
        if not mode_series and found:
            mode_series = [_chart_series(
                {"metric_id": spec.get("metric_id"), "label": spec.get("label"), "role": "bar"},
                found,
                1,
            )]
        modes.append({
            "id": spec.get("id"),
            "label": spec.get("label") or spec.get("id"),
            "metric_id": spec.get("metric_id"),
            "series": mode_series,
            "kpis": [_tile(spec.get("metric_id"), found, {"label": spec.get("label"), "inverse": spec.get("inverse")})],
            "notes": list((found or {}).get("notes") or []),
            "series_breaks": list((found or {}).get("series_breaks") or []),
        })
    milestone_series = _merge_points(
        _lookup_metric(panel.get("milestone_metric"), metrics, catalog),
        estimates.get(panel.get("milestone_metric")),
    )
    table_spec = panel.get("table") if isinstance(panel.get("table"), dict) else {}
    table_series = _merge_points(
        _lookup_metric(table_spec.get("metric_id"), metrics, catalog),
        estimates.get(table_spec.get("metric_id")),
    )
    any_value = any(item.get("value") is not None for item in kpis) or any(
        point.get("reported") for series in chart for point in series.get("points") or []
    )
    filled = {
        "id": panel.get("id"),
        "kind": kind,
        "title": panel.get("title"),
        "subtitle": panel.get("subtitle") or "",
        "width": panel.get("width") or "wide",
        "cadence": panel.get("cadence") or "quarterly",
        "source_note": panel.get("source_note") or "",
        "kpis": kpis,
        "series": chart,
        "modes": modes,
        "map": panel.get("map"),
        "markers": [*_markers(map_metric), *_markers(map_estimate)],
        "multiples": multiples,
        "milestones": _milestones(panel.get("milestones") or [], milestone_series),
        "table": _table(panel.get("table") if isinstance(panel.get("table"), dict) else None, table_series),
        "forecast": forecast if kind == "forecast-band" else None,
        "notes": list(panel.get("notes") or []),
        "empty_label": "" if any_value or kind == "forecast-band" else (
            panel.get("empty_label") or "Not yet disclosed"
        ),
    }
    return filled


def build_observations(
    catalog: dict,
    *,
    parsed_by_mode: dict[str, dict] | None = None,
    manual: dict | None = None,
    source_statuses: list[dict] | None = None,
    generated_at: str,
    extra_series: list[dict] | None = None,
    news: dict | None = None,
) -> dict:
    """Curated deep-dive file. Every series is proposed."""
    block = catalog.get("deep_dive") if isinstance(catalog, dict) else {}
    series = []
    for source in (block or {}).get("sources") or []:
        if not isinstance(source, dict) or source.get("id") != "nhtsa_sgo":
            continue
        outputs = source.get("outputs") if isinstance(source.get("outputs"), dict) else {}
        for mode, metric_id in outputs.items():
            parsed = (parsed_by_mode or {}).get(mode)
            if not parsed or not metric_id:
                continue
            series.append(_series_from_counts(str(metric_id), parsed, source))
    for item in extra_series or []:
        if isinstance(item, dict) and item.get("metric_id"):
            series.append(item)
    # Manual series are stored too, still proposed, so a later serve can show them
    # even when the manual object is unreadable.
    for item in series_from_manual(list((manual or {}).get("entries") or [])):
        series.append(item)
    topic_rows, estimate_rows = sources.series_from_rollup(news)
    series.extend(topic_rows)
    series.extend(estimate_rows)
    for item in series:
        if item.get("estimate") or item.get("source_kind") == "news_estimate":
            _force_news_estimate(item)
        else:
            item["approval_state"] = "proposed"
            item["approved"] = False
            item["estimate"] = False
    return {
        "ticker": str(catalog.get("ticker") or "").upper(),
        "generated_at": generated_at,
        "series": series,
        "sources": list(source_statuses or []),
        "forecast_model": {
            "model_id": "seasonal_naive_v0",
            "model_status": "placeholder",
            "disclaimer": PLACEHOLDER_DISCLAIMER,
        },
    }


def _collect_catalog_sources(
    catalog: dict,
    fetch,
    *,
    generated_at: str,
    previous: dict | None,
) -> tuple[list[dict], list[dict]]:
    """Launch Library 2, CelesTrak, and AV regulator pages, only when the catalog declares them."""
    series: list[dict] = []
    statuses: list[dict] = []
    for source in (catalog.get("deep_dive") or {}).get("sources") or []:
        if not isinstance(source, dict):
            continue
        if source.get("id") == "launch_library":
            found, status = sources.collect_launches(source, fetch)
            series.extend(found)
            statuses.append(status)
        elif source.get("id") == "celestrak":
            found, status = sources.collect_constellation(source, fetch, generated_at, previous)
            series.extend(found)
            statuses.append(status)
        elif source.get("id") == "av_regulator":
            found, status = sources.collect_av_regulator(source, fetch, generated_at, previous)
            series.extend(found)
            statuses.append(status)
    return series, statuses


def prepare_news_articles(rows: list[dict], tickers: list[str], load_catalog) -> list[dict]:
    """Tag catalog energy topics onto articles. Tickers without a deep dive are unchanged."""
    catalogs = {}
    for ticker in tickers or []:
        symbol = str(ticker or "").upper()
        if symbol:
            catalogs[symbol] = load_catalog(symbol) or {}
    return sources.tag_catalog_topics(rows, sources.topic_rules_for(catalogs))


def write_news_rollups(
    tickers: list[str],
    articles: list[dict],
    load_catalog,
    write_json,
    generated_at: str,
) -> list[str]:
    """Write one small topic-and-estimate file per deep-dive ticker. The news job already holds the articles."""
    written = []
    for ticker in tickers or []:
        symbol = str(ticker or "").upper()
        if not symbol:
            continue
        catalog = load_catalog(symbol) or {}
        block = catalog.get("deep_dive") if isinstance(catalog, dict) else None
        if not isinstance(block, dict):
            continue
        wanted = {"news_topics", "news_estimates"}
        if not any(isinstance(item, dict) and item.get("id") in wanted for item in block.get("sources") or []):
            continue
        owned = [
            row for row in articles or []
            if isinstance(row, dict) and symbol in {str(item).upper() for item in (row.get("tickers") or []) if item}
        ]
        write_json(sources.news_topics_key(symbol), sources.build_news_rollup(owned, block, generated_at))
        written.append(symbol)
    return written


def run_collect(
    event: dict | None,
    *,
    fetch,
    read_json,
    write_json,
    tickers: list[str],
    load_catalog,
    now: str | None = None,
) -> dict:
    """Collect NHTSA SGO aggregates and copy manual entries. No metric is approved."""
    generated_at = now or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    catalogs = []
    for ticker in tickers:
        catalog = load_catalog(ticker) or {}
        block = catalog.get("deep_dive") if isinstance(catalog, dict) else None
        if isinstance(block, dict) and block.get("sections"):
            catalogs.append(catalog)
    if not catalogs:
        return {"status": "ok", "run_status": "ok", "tickers": [], "served_collect": 0, "sources": []}
    needs_nhtsa = any(_nhtsa_source(catalog) for catalog in catalogs)
    downloaded: dict[str, str] = {}
    fetch_error = ""
    if needs_nhtsa:
        for mode, url in NHTSA_MODES.items():
            try:
                downloaded[mode] = fetch(url)
            except (httpx.HTTPError, OSError, TimeoutError, UnicodeError) as exc:
                fetch_error = f"{mode}: {type(exc).__name__}"
                downloaded[mode] = ""
    written = []
    source_rows = []
    for catalog in catalogs:
        ticker = str(catalog.get("ticker") or "").upper()
        parsed: dict[str, dict] = {}
        statuses = []
        for source in (catalog.get("deep_dive") or {}).get("sources") or []:
            if not isinstance(source, dict) or source.get("id") != "nhtsa_sgo":
                continue
            entity = str(source.get("reporting_entity") or "")
            if fetch_error:
                statuses.append({"id": "nhtsa_sgo", "status": "failed", "note": fetch_error})
                continue
            for mode in (source.get("outputs") or {}):
                if mode not in NHTSA_MODES:
                    continue
                parsed[mode] = parse_sgo_csv(downloaded.get(mode) or "", reporting_entity=entity, mode=mode)
            statuses.append({
                "id": "nhtsa_sgo",
                "status": "ok",
                "matched": {mode: parsed[mode]["matched"] for mode in parsed},
                "note": "Aggregated counts only. Report rows are not stored.",
            })
        manual = read_optional(read_json, manual_key(ticker))
        previous = read_optional(read_json, observations_key(ticker))
        company_doc = read_optional(read_json, f"curated/company_metrics/ticker={ticker}/observations.json")
        news_doc = read_optional(read_json, f"curated/news_topics/ticker={ticker}/topics.json")
        extra, extra_status = _collect_catalog_sources(
            catalog,
            fetch,
            generated_at=generated_at,
            previous=previous,
        )
        statuses.extend(extra_status)
        document = build_observations(
            catalog,
            parsed_by_mode=parsed,
            manual=manual,
            source_statuses=statuses,
            generated_at=generated_at,
            extra_series=extra,
            news=news_doc,
        )
        rows = company_doc.get("rows") if isinstance(company_doc.get("rows"), list) else []
        preview = assemble_deep_dive(
            catalog,
            rows,
            document,
            manual,
            projections=previous.get("stored_headlines") if isinstance(previous.get("stored_headlines"), list) else [],
            news=news_doc,
            lock_projections=True,
            generated_at=generated_at,
        )
        document["stored_headlines"] = (preview or {}).get("forecast", {}).get("stored_headlines") or []
        write_json(observations_key(ticker), document)
        written.append(ticker)
        source_rows.extend(statuses)
    failed = any(item.get("status") == "failed" for item in source_rows)
    status = "partial" if failed else "ok"
    return {
        "status": status,
        "run_status": status,
        "tickers": written,
        "served_collect": len(written),
        "sources": source_rows,
    }


def _nhtsa_source(catalog: dict) -> dict | None:
    for source in (catalog.get("deep_dive") or {}).get("sources") or []:
        if isinstance(source, dict) and source.get("id") == "nhtsa_sgo":
            return source
    return None


def read_optional(read_json, key: str) -> dict:
    """Missing lake objects are empty. A 403 on GET is how S3 reports a missing key without ListBucket."""
    try:
        loaded = read_json(key)
    except (ClientError, BotoCoreError, OSError) as exc:
        from lake import lake_read_denied_or_missing

        if lake_read_denied_or_missing(exc):
            return {}
        raise
    return loaded if isinstance(loaded, dict) else {}


def handler(event, context):
    import os

    from company_ir import load_catalog, unwrap_job_event
    from http_client import get_client
    from lake import read_json as lake_read_json
    from lake import write_json
    from observability import job_handler
    from universe import user_ticker_union, watchlist_universe

    @job_handler("DEEP")
    def run(event, context):
        bucket = os.environ.get("LAKE_BUCKET")

        def fetch(url: str) -> str:
            response = http.get(url, timeout=60.0)
            response.raise_for_status()
            return response.text

        def read(key: str) -> dict:
            return read_optional(lambda item: lake_read_json(item, bucket), key)

        universe = watchlist_universe(user_ticker_union())
        symbols = [str(item).upper() for item in (universe.get("tickers") or []) if item]
        flat = unwrap_job_event(event)
        if isinstance(flat.get("tickers"), list) and flat.get("tickers"):
            symbols = [str(item).upper() for item in flat["tickers"]]
        with get_client(headers={"User-Agent": "InvestorDashboardDeepDive"}) as http:
            return run_collect(
                flat,
                fetch=fetch,
                read_json=read,
                write_json=lambda key, obj: write_json(obj, key, bucket=bucket),
                tickers=symbols,
                load_catalog=load_catalog,
            )

    return run(event, context)
