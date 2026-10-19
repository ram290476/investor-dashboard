"""Catalog-driven deep-dive sources that do not need an API key.

Launch Library 2, CelesTrak, and public AV regulator pages are keyless.
News estimates are extracted from articles the news collector already stored.
Nothing here approves a figure, and nothing here names a ticker. The catalog
supplies provider filters, metric ids, entity names, and the words that mark a topic.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode

import httpx

_QUARTER = re.compile(r"^(\d{4})Q([1-4])$")
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_NUMBER = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?P<pct>%)?(?:\s*(?P<mult>billion|million))?(?:\s*(?P<unit>gwh|mwh))?",
    re.I,
)
_MONEY = re.compile(r"\$\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<mult>billion|million|bn)?", re.I)
_PLACE = r"[A-Z][A-Za-z0-9.&'-]+(?:\s+[A-Z][A-Za-z0-9.&'-]+){0,2}"
_SITE_WORD = r"(?i:gigafactory|factory|plant|facility)"
_SITE_AFTER = re.compile(rf"\b{_SITE_WORD}\s+(?i:in|at|near)\s+({_PLACE})")
_SITE_BEFORE = re.compile(rf"\b({_PLACE})\s+{_SITE_WORD}\b")
_PARTNER = re.compile(
    r"\b(?i:with|from)\s+([A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){0,3})"
)
_FETCH_ERRORS = (httpx.HTTPError, OSError, TimeoutError, ValueError, TypeError)
_SITE_REJECT = frozenset({"the", "a", "an", "new", "this", "its", "their", "our", "first", "next"})
_PARTNER_REJECT = frozenset({"the", "a", "an", "its", "their", "our", "new"})
_MULT = {"billion": 1_000_000_000, "million": 1_000_000, "bn": 1_000_000_000}
# Place names are reference data for news sentences, not a company list.
_COUNTRIES = (
    ("United Arab Emirates", "United Arab Emirates"),
    ("United States of America", "United States"),
    ("United States", "United States"),
    ("United Kingdom", "United Kingdom"),
    ("South Korea", "South Korea"),
    ("Saudi Arabia", "Saudi Arabia"),
    ("Netherlands", "Netherlands"),
    ("Australia", "Australia"),
    ("Germany", "Germany"),
    ("Canada", "Canada"),
    ("Mexico", "Mexico"),
    ("Brazil", "Brazil"),
    ("France", "France"),
    ("Japan", "Japan"),
    ("India", "India"),
    ("China", "China"),
    ("Italy", "Italy"),
    ("Spain", "Spain"),
    ("Chile", "Chile"),
    ("Taiwan", "Taiwan"),
    ("Britain", "United Kingdom"),
    ("Korea", "South Korea"),
    ("U.S.", "United States"),
    ("USA", "United States"),
    ("UK", "United Kingdom"),
)

ESTIMATE_NOTE = (
    "News-derived estimate. Pending review. Not an approved figure, and not blended into a reported series."
)
TOPIC_NOTE = "Article count and mean sentiment for a catalog topic. Not a production or deployment figure."
LAUNCH_NOTE = (
    "Launch Library 2 public schedule. No API key. Counts stay pending review. "
    "Booster reuse is blank unless that launch payload includes it."
)
CONSTELLATION_NOTE = (
    "Count of objects in the public GP element set. Pending review. Not a company disclosure."
)


def news_topics_key(ticker: str) -> str:
    return f"curated/news_topics/ticker={str(ticker or '').upper()}/topics.json"


def quarter_of(stamp: str) -> str | None:
    day = _DAY.match(str(stamp or ""))
    month = _MONTH.match(str(stamp or "")[:7]) if day is None else None
    if day:
        year, month_number = int(day.group(1)), int(day.group(2))
    elif month:
        year, month_number = int(month.group(1)), int(month.group(2))
    else:
        quarter = _QUARTER.match(str(stamp or ""))
        return quarter.group(0) if quarter else None
    if not 1 <= month_number <= 12:
        return None
    return f"{year}Q{(month_number - 1) // 3 + 1}"


def period_sort_key(period: str) -> tuple:
    quarter = _QUARTER.match(str(period or ""))
    if quarter:
        return (0, int(quarter.group(1)), int(quarter.group(2)))
    month = _MONTH.match(str(period or ""))
    if month:
        return (1, int(month.group(1)), int(month.group(2)))
    return (2, 0, 0)


def _parse_stamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:10]).replace(tzinfo=UTC)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def article_text(article: dict) -> str:
    return " ".join(
        str(article.get(key) or "")
        for key in ("title", "summary", "snippet")
    )


def contains_term(text: str, term: str, *, boundary: bool) -> bool:
    needle = str(term or "").strip()
    if not needle or not text:
        return False
    if boundary and " " not in needle and len(needle) <= 8:
        return re.search(rf"\b{re.escape(needle)}\b", text, re.I) is not None
    return needle.casefold() in text.casefold()


def any_term(text: str, terms: list, *, boundary: bool) -> bool:
    return any(contains_term(text, term, boundary=boundary) for term in terms or [])


def validate_extra_source(source: dict) -> list[str]:
    """Catalog errors for the keyless sources. Unknown ids are left to the caller."""
    if not isinstance(source, dict):
        return ["deep_dive source must be an object"]
    sid = str(source.get("id") or "")
    errors = []
    if sid == "launch_library":
        if not str(source.get("lsp_name") or "").strip():
            errors.append("launch_library source needs lsp_name")
        if not str(source.get("base_url") or "").startswith("https://"):
            errors.append("launch_library base_url must be https")
        vehicles = source.get("vehicles")
        if not isinstance(vehicles, list) or not vehicles:
            errors.append("launch_library needs vehicles")
    elif sid == "celestrak":
        if not str(source.get("url") or "").startswith("https://"):
            errors.append("celestrak url must be https")
        if not str(source.get("metric_id") or "").strip():
            errors.append("celestrak needs metric_id")
    elif sid == "news_topics":
        if not str(source.get("metric_id") or "").strip():
            errors.append("news_topics needs metric_id")
        topics = source.get("topics")
        if not isinstance(topics, list) or not topics:
            errors.append("news_topics needs topics")
    elif sid == "news_estimates":
        rules = source.get("rules")
        if not isinstance(rules, list):
            errors.append("news_estimates rules must be a list")
        else:
            for rule in rules:
                if not isinstance(rule, dict) or not str(rule.get("metric_id") or "").strip():
                    errors.append("news estimate rule needs metric_id")
    elif sid == "av_regulator":
        errors.extend(_validate_av_regulator(source))
    return errors


def launch_page_url(source: dict, path: str, offset: int) -> str:
    """Public Launch Library URL. The query never carries a key."""
    base = str(source.get("base_url") or "").rstrip("/")
    limit = int(source.get("page_limit") or 100)
    query = urlencode(
        {
            "limit": max(1, min(limit, 100)),
            "offset": max(0, offset),
            "mode": "normal",
            "lsp__name": str(source.get("lsp_name") or ""),
        }
    )
    return f"{base}/launches/{path}/?{query}"


def _pages(fetch, source: dict, path: str) -> tuple[list[dict], str]:
    limit = max(1, min(int(source.get("page_limit") or 100), 100))
    cap = max(1, int(source.get("max_pages") or 4))
    if path == "upcoming":
        cap = min(cap, 2)
    rows: list[dict] = []
    error = ""
    for page in range(cap):
        url = launch_page_url(source, path, page * limit)
        try:
            payload = json.loads(fetch(url) or "")
        except _FETCH_ERRORS as exc:
            error = type(exc).__name__
            break
        if not isinstance(payload, dict):
            error = "unexpected_payload"
            break
        batch = payload.get("results") if isinstance(payload.get("results"), list) else []
        rows.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < limit:
            break
    return rows, error


def _booster(rocket: dict) -> str:
    stages = rocket.get("launcher_stage") if isinstance(rocket.get("launcher_stage"), list) else []
    stage = stages[0] if stages and isinstance(stages[0], dict) else {}
    if not stage:
        return ""
    reused = stage.get("reused")
    flights = stage.get("launcher_flight_number")
    if reused is True:
        if isinstance(flights, int) and flights > 1:
            return f"Reused (flight {flights})"
        return "Reused"
    if reused is False:
        return "New"
    return ""


def parse_launch(item: dict) -> dict | None:
    """One launch from a normal or detailed payload. Missing fields stay blank."""
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    net = str(item.get("net") or "").strip()
    if not name or len(net) < 10:
        return None
    rocket = item.get("rocket") if isinstance(item.get("rocket"), dict) else {}
    config = rocket.get("configuration") if isinstance(rocket.get("configuration"), dict) else {}
    vehicle = str(config.get("full_name") or config.get("name") or "").strip()
    if not vehicle and "|" in name:
        vehicle = name.split("|", 1)[0].strip()
    mission_obj = item.get("mission") if isinstance(item.get("mission"), dict) else {}
    mission = str(mission_obj.get("name") or "").strip()
    if not mission and "|" in name:
        mission = name.split("|", 1)[1].strip()
    agencies = mission_obj.get("agencies") if isinstance(mission_obj.get("agencies"), list) else []
    customer = ""
    if agencies and isinstance(agencies[0], dict):
        customer = str(agencies[0].get("name") or "").strip()
    if customer and customer.casefold() not in mission.casefold():
        mission = f"{mission} — {customer}" if mission else customer
    orbit_obj = mission_obj.get("orbit") if isinstance(mission_obj.get("orbit"), dict) else {}
    orbit = str(orbit_obj.get("name") or "").strip()
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    outcome = str(status.get("name") or status.get("abbrev") or "").strip()
    return {
        "url": str(item.get("url") or "").strip(),
        "name": name,
        "net": net,
        "date": net[:10],
        "vehicle": vehicle or name,
        "mission": mission,
        "orbit": orbit,
        "outcome": outcome,
        "booster": _booster(rocket),
    }


def _match_vehicle(record: dict, vehicles: list[dict]) -> dict | None:
    blob = f"{record.get('vehicle') or ''} {record.get('name') or ''}"
    for vehicle in vehicles:
        if not isinstance(vehicle, dict):
            continue
        if any_term(blob, list(vehicle.get("terms") or []), boundary=True):
            return vehicle
    return None


def _count_series(metric_id: str, counts: dict[str, int], source_url: str, *, unit: str = "launches") -> dict:
    points = [
        {"period": period, "value": counts[period], "reported": True}
        for period in sorted(counts, key=period_sort_key)
        if counts[period]
    ]
    return _collected_series(
        metric_id,
        points,
        unit=unit,
        source_url=source_url,
        source_title="Launch Library 2",
        source_kind="launch_library",
        confidence="high",
        notes=[LAUNCH_NOTE],
    )


def _collected_series(
    metric_id: str,
    points: list[dict],
    *,
    unit: str,
    source_url: str,
    source_title: str,
    source_kind: str,
    confidence: str,
    notes: list[str],
    cadence: str = "quarterly",
) -> dict:
    return {
        "metric_id": metric_id,
        "unit": unit,
        "cadence": cadence,
        "confidence": confidence,
        "approval_state": "proposed",
        "approved": False,
        "estimate": False,
        "source_kind": source_kind,
        "source_url": source_url,
        "source_title": source_title,
        "disclosure_status": "numeric" if points else "not_collected",
        "points": points,
        "notes": notes,
    }


def series_from_launches(previous: list[dict], upcoming: list[dict], source: dict) -> list[dict]:
    """Quarterly actuals, upcoming projected counts, Starship flights, and a schedule table."""
    vehicles = [item for item in (source.get("vehicles") or []) if isinstance(item, dict)]
    by_vehicle: dict[str, dict[str, int]] = {}
    flights: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    projected: dict[str, int] = {}
    schedule: list[dict] = []
    source_url = str(source.get("base_url") or "")

    def add_count(bucket: dict[str, int], period: str) -> None:
        bucket[period] = bucket.get(period, 0) + 1

    for raw in previous:
        record = parse_launch(raw)
        if record is None:
            continue
        period = quarter_of(record["date"])
        if not period:
            continue
        add_count(totals, period)
        vehicle = _match_vehicle(record, vehicles)
        if vehicle and vehicle.get("metric_id"):
            add_count(by_vehicle.setdefault(str(vehicle["metric_id"]), {}), period)
        if vehicle and vehicle.get("flights_metric"):
            add_count(flights.setdefault(str(vehicle["flights_metric"]), {}), period)
        schedule.append(record)
    for raw in upcoming:
        record = parse_launch(raw)
        if record is None:
            continue
        period = quarter_of(record["date"])
        if period:
            add_count(projected, period)
        schedule.append(record)

    series = []
    for vehicle in vehicles:
        metric_id = str(vehicle.get("metric_id") or "")
        if metric_id:
            series.append(_count_series(metric_id, by_vehicle.get(metric_id, {}), source_url))
        flights_metric = str(vehicle.get("flights_metric") or "")
        if flights_metric:
            series.append(_count_series(flights_metric, flights.get(flights_metric, {}), source_url, unit="flights"))
    total_metric = str(source.get("total_metric") or "")
    if total_metric:
        series.append(_count_series(total_metric, totals, source_url))
    projected_metric = str(source.get("projected_metric") or "")
    if projected_metric:
        projected_series = _count_series(projected_metric, projected, source_url)
        projected_series["notes"] = [
            "Upcoming launches on the public schedule, grouped by quarter. Not a forecast model.",
        ]
        series.append(projected_series)
    schedule_metric = str(source.get("schedule_metric") or "")
    if schedule_metric:
        series.append(_schedule_series(schedule, schedule_metric, source, source_url))
    return [item for item in series if item.get("points")]


def _schedule_series(records: list[dict], metric_id: str, source: dict, source_url: str) -> dict:
    limit = int(source.get("schedule_limit") or 40)
    ordered = sorted(records, key=lambda item: item.get("date") or "")

    def _upcoming(record: dict) -> bool:
        outcome = str(record.get("outcome") or "").casefold()
        return "go for launch" in outcome or outcome in {"go", "tbd", "tbc", "to be determined", "hold"}

    upcoming = [item for item in ordered if _upcoming(item)]
    past = [item for item in ordered if item not in upcoming]
    if len(upcoming) >= limit:
        kept = upcoming[:limit]
    else:
        room = max(0, limit - len(upcoming))
        kept = past[-room:] + upcoming
        kept.sort(key=lambda item: item.get("date") or "")
    points = []
    for record in kept:
        period = quarter_of(record.get("date") or "")
        if not period:
            continue
        points.append(
            {
                "period": period,
                "value": None,
                "reported": False,
                "source_url": record.get("url") or source_url,
                "dimensions": {
                    "date": record.get("date") or "",
                    "vehicle": record.get("vehicle") or "",
                    "mission": record.get("mission") or "",
                    "orbit": record.get("orbit") or "",
                    "outcome": record.get("outcome") or "",
                    "booster": record.get("booster") or "",
                },
            }
        )
    return _collected_series(
        metric_id,
        points,
        unit="launches",
        source_url=source_url,
        source_title="Launch Library 2",
        source_kind="launch_library",
        confidence="high",
        notes=[LAUNCH_NOTE],
    )


def collect_launches(source: dict, fetch) -> tuple[list[dict], dict]:
    if not str(source.get("lsp_name") or "").strip() or not str(source.get("base_url") or "").startswith("https://"):
        return [], {"id": "launch_library", "status": "failed", "note": "catalog source is incomplete"}
    previous, previous_error = _pages(fetch, source, "previous")
    upcoming, upcoming_error = _pages(fetch, source, "upcoming")
    series = series_from_launches(previous, upcoming, source)
    error = previous_error or upcoming_error
    if error and not previous and not upcoming:
        status = "failed"
    elif error:
        status = "partial"
    else:
        status = "ok"
    return series, {
        "id": "launch_library",
        "status": status,
        "rows": len(previous) + len(upcoming),
        "note": LAUNCH_NOTE if status != "failed" else error,
    }


def parse_constellation_count(payload) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("satellites", "data", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return len(rows)
    return None


def constellation_series(source: dict, count: int, generated_at: str) -> dict:
    period = quarter_of(generated_at) or ""
    points = []
    if period and count >= 0:
        points.append({"period": period, "value": count, "reported": True})
    return _collected_series(
        str(source.get("metric_id") or ""),
        points,
        unit=str(source.get("unit") or "satellites"),
        source_url=str(source.get("url") or ""),
        source_title="CelesTrak",
        source_kind="celestrak",
        confidence="high",
        notes=[CONSTELLATION_NOTE],
    )


def merge_level_series(previous: dict | None, current: dict) -> dict:
    """Keep earlier quarters of a level series. The latest collect replaces the current quarter."""
    by_period: dict[str, dict] = {}
    metric_id = current.get("metric_id")
    for series in (previous or {}).get("series") or []:
        if not isinstance(series, dict):
            continue
        if series.get("metric_id") != metric_id or series.get("source_kind") != current.get("source_kind"):
            continue
        for point in series.get("points") or []:
            if isinstance(point, dict) and point.get("period"):
                by_period[str(point["period"])] = point
    for point in current.get("points") or []:
        if isinstance(point, dict) and point.get("period"):
            by_period[str(point["period"])] = point
    current["points"] = [by_period[key] for key in sorted(by_period, key=period_sort_key)]
    current["disclosure_status"] = "numeric" if current["points"] else "not_collected"
    return current


def collect_constellation(source: dict, fetch, generated_at: str, previous: dict | None) -> tuple[list[dict], dict]:
    url = str(source.get("url") or "")
    if not url.startswith("https://") or not str(source.get("metric_id") or "").strip():
        return [], {"id": "celestrak", "status": "failed", "note": "catalog source is incomplete"}
    try:
        payload = json.loads(fetch(url) or "")
    except _FETCH_ERRORS as exc:
        return [], {"id": "celestrak", "status": "failed", "note": type(exc).__name__}
    count = parse_constellation_count(payload)
    if count is None:
        return [], {"id": "celestrak", "status": "failed", "note": "unexpected_payload"}
    series = merge_level_series(previous, constellation_series(source, count, generated_at))
    if not series.get("points"):
        return [], {"id": "celestrak", "status": "failed", "note": "no period"}
    return [series], {
        "id": "celestrak",
        "status": "ok",
        "rows": count,
        "note": CONSTELLATION_NOTE,
    }


REGULATOR_NOTE = (
    "Public regulator rosters. Pending review, never approved. "
    "A missing page is not stored as zero. "
    "Crash-report counts already on this page are a cross-check, not a registration."
)
_PERMIT_KINDS = frozenset({"permit_roster", "permit_table", "stated_count"})
_VEHICLE_COUNT = re.compile(
    r"(?:(?P<a>\d{1,6})\s+(?:registered\s+|permitted\s+|authorized\s+)?vehicles\b)"
    r"|(?:\bvehicles\b\s*[:\-]\s*(?P<b>\d{1,6}))"
    r"|(?:(?P<c>\d{1,6})\s+(?:registered|permitted)\b)",
    re.IGNORECASE,
)


class _HTMLText(HTMLParser):
    """Block tags become line breaks. Script and style text is dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "br", "section", "table"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag in {"p", "li", "tr", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(html or "")
        parser.close()
    except (ValueError, AssertionError, LookupError):
        return re.sub(r"<[^>]+>", " ", html or "")
    return "".join(parser.parts)


def _validate_av_regulator(source: dict) -> list[str]:
    errors = []
    entities = source.get("entities")
    if not isinstance(entities, list) or not any(str(item or "").strip() for item in entities):
        errors.append("av_regulator source needs entities")
    feeds = source.get("feeds")
    if not isinstance(feeds, list) or not feeds:
        errors.append("av_regulator source needs feeds")
    else:
        for feed in feeds:
            if not isinstance(feed, dict):
                errors.append("av_regulator feed must be an object")
                continue
            if not str(feed.get("url") or "").startswith("https://"):
                errors.append("av_regulator feed url must be https")
            if str(feed.get("kind") or "") not in _PERMIT_KINDS:
                errors.append("av_regulator feed kind is unsupported")
            if not str(feed.get("jurisdiction") or "").strip():
                errors.append("av_regulator feed needs jurisdiction")
    if not str(source.get("permits_metric") or "").strip():
        errors.append("av_regulator source needs permits_metric")
    if not str(source.get("vehicles_metric") or "").strip():
        errors.append("av_regulator source needs vehicles_metric")
    return errors


def _entity_names(source: dict) -> list[str]:
    names = []
    for item in source.get("entities") or []:
        text = str(item or "").strip()
        if len(text) >= 3:
            names.append(text)
    return sorted(names, key=len, reverse=True)


def entity_in_text(text: str, entities: list[str]) -> str:
    """Longest catalog entity name that appears as its own words, or ""."""
    low = str(text or "").lower()
    for name in entities:
        if re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", low):
            return name
    return ""


def _lines(html: str) -> list[str]:
    text = html_to_text(html)
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _heading_kind(line: str) -> str:
    low = line.lower()
    if len(low) > 140:
        return ""
    if "driverless" in low and "test" in low:
        return "driverless_testing"
    if "deployment" in low and "permit" in low:
        return "deployment"
    if "with a driver" in low or "safety driver" in low:
        return "testing_with_driver"
    return ""


def parse_permit_roster(html: str, entities: list[str]) -> list[dict]:
    """One hit per roster section that names an entity. The page's vehicle list is not guessed."""
    found = []
    section = ""
    seen: set[str] = set()
    for line in _lines(html):
        kind = _heading_kind(line)
        if kind:
            section = kind
            continue
        if not section:
            continue
        name = entity_in_text(line, entities)
        if not name or section in seen:
            continue
        seen.add(section)
        found.append({"permit": section, "entity": name})
    return found


def parse_permit_table(html: str, entities: list[str]) -> list[dict]:
    """One hit per table row that names an entity and reads as a permit."""
    found = []
    for line in _lines(html):
        low = line.lower()
        if not any(token in low for token in ("pilot", "deployment", "permit", "tcp")):
            continue
        name = entity_in_text(line, entities)
        if not name:
            continue
        found.append({"permit": "table_row", "entity": name, "row": line[:180]})
    return found


def _vehicle_count(text: str) -> int | None:
    match = _VEHICLE_COUNT.search(str(text or "").replace(",", ""))
    if not match:
        return None
    raw = next(group for group in match.groups() if group)
    value = int(raw)
    if value < 0 or value > 100_000:
        return None
    return value


def parse_stated_vehicle_count(html: str, entities: list[str]) -> int | None:
    """Sum counts the page states next to the entity. No number means unknown, not zero."""
    lines = _lines(html)
    total = 0
    found = False
    index = 0
    while index < len(lines):
        if not entity_in_text(lines[index], entities):
            index += 1
            continue
        window = " ".join(lines[index:index + 2])
        count = _vehicle_count(window)
        if count is None:
            index += 1
            continue
        total += count
        found = True
        index += 2
    return total if found else None


def month_of(stamp: str) -> str | None:
    day = _DAY.match(str(stamp or ""))
    if day:
        return f"{day.group(1)}-{day.group(2)}"
    month = _MONTH.match(str(stamp or "")[:7])
    return month.group(0) if month else None


def quarter_of_month(period: str) -> str | None:
    return quarter_of(f"{period}-01") if _MONTH.match(str(period or "")) else None


def _regulator_ids(source: dict) -> tuple[str, str, str, str]:
    permits = str(source.get("permits_metric") or "").strip()
    vehicles = str(source.get("vehicles_metric") or "").strip()
    permits_quarter = str(source.get("permits_quarter_metric") or f"{permits}_quarter").strip()
    vehicles_quarter = str(source.get("vehicles_quarter_metric") or f"{vehicles}_quarter").strip()
    return permits, vehicles, permits_quarter, vehicles_quarter


def _carried_regulator_series(previous: dict | None, source: dict) -> list[dict]:
    wanted = set(_regulator_ids(source))
    carried = []
    for series in (previous or {}).get("series") or []:
        if not isinstance(series, dict):
            continue
        if series.get("source_kind") == "av_regulator" and series.get("metric_id") in wanted:
            carried.append(series)
    return carried


def _quarter_series(monthly: dict, metric_id: str) -> dict:
    latest: dict[str, dict] = {}
    for point in monthly.get("points") or []:
        quarter = quarter_of_month(str(point.get("period") or ""))
        if quarter and point.get("value") is not None:
            latest[quarter] = point
    points = []
    for quarter in sorted(latest, key=period_sort_key):
        point = latest[quarter]
        points.append({
            "period": quarter,
            "value": point.get("value"),
            "reported": True,
            "source_url": point.get("source_url") or monthly.get("source_url") or "",
            "retrieved_at": point.get("retrieved_at") or "",
        })
    built = _collected_series(
        metric_id,
        points,
        unit=monthly.get("unit") or "count",
        source_url=monthly.get("source_url") or "",
        source_title=monthly.get("source_title") or "Public AV regulator records",
        source_kind="av_regulator",
        confidence=monthly.get("confidence") or "medium",
        notes=list(monthly.get("notes") or []),
        cadence="quarterly",
    )
    return built


def collect_av_regulator(source: dict, fetch, generated_at: str, previous: dict | None) -> tuple[list[dict], dict]:
    """Permits held, and a vehicle count only when a page states one. Always pending review."""
    if _validate_av_regulator(source):
        return [], {"id": "av_regulator", "status": "failed", "note": "catalog source is incomplete"}
    entities = _entity_names(source)
    permits_metric, vehicles_metric, permits_quarter, vehicles_quarter = _regulator_ids(source)
    period = month_of(generated_at)
    if not period:
        return _carried_regulator_series(previous, source), {
            "id": "av_regulator",
            "status": "failed",
            "note": "no period",
        }
    permit_hits = []
    vehicle_total = 0
    vehicle_found = False
    notes = [REGULATOR_NOTE]
    errors = []
    read = 0
    source_url = ""
    vehicle_url = ""
    for feed in source.get("feeds") or []:
        if not isinstance(feed, dict):
            continue
        url = str(feed.get("url") or "")
        kind = str(feed.get("kind") or "")
        jurisdiction = str(feed.get("jurisdiction") or "").strip() or "Regulator"
        try:
            html = fetch(url)
        except _FETCH_ERRORS as exc:
            errors.append(f"{jurisdiction}: {type(exc).__name__}")
            continue
        if not isinstance(html, str) or not html.strip():
            errors.append(f"{jurisdiction}: empty")
            continue
        read += 1
        if not source_url:
            source_url = url
        if kind == "permit_roster":
            hits = parse_permit_roster(html, entities)
            permit_hits.extend(hits)
            notes.append(f"{jurisdiction}: {len(hits)} permit section(s) name the catalog entity.")
        elif kind == "permit_table":
            hits = parse_permit_table(html, entities)
            permit_hits.extend(hits)
            notes.append(f"{jurisdiction}: {len(hits)} permit row(s) name the catalog entity.")
        elif kind == "stated_count":
            count = parse_stated_vehicle_count(html, entities)
            if count is None:
                notes.append(f"{jurisdiction}: no permitted-vehicle count is stated.")
            else:
                vehicle_total += count
                vehicle_found = True
                vehicle_url = url
                notes.append(f"{jurisdiction}: {count} permitted vehicles stated on the page.")
    if read == 0:
        return _carried_regulator_series(previous, source), {
            "id": "av_regulator",
            "status": "failed",
            "note": "; ".join(errors) or "no feed",
        }
    permit_point = {
        "period": period,
        "value": len(permit_hits),
        "reported": True,
        "source_url": source_url,
        "retrieved_at": generated_at,
    }
    permits = merge_level_series(previous, _collected_series(
        permits_metric,
        [permit_point],
        unit="permits",
        source_url=source_url,
        source_title="Public AV regulator records",
        source_kind="av_regulator",
        confidence="medium",
        notes=notes,
        cadence="monthly",
    ))
    series = [permits, _quarter_series(permits, permits_quarter)]
    if vehicle_found:
        vehicles = merge_level_series(previous, _collected_series(
            vehicles_metric,
            [{
                "period": period,
                "value": vehicle_total,
                "reported": True,
                "source_url": vehicle_url or source_url,
                "retrieved_at": generated_at,
            }],
            unit="vehicles",
            source_url=vehicle_url or source_url,
            source_title="Public AV regulator records",
            source_kind="av_regulator",
            confidence="low",
            notes=notes,
            cadence="monthly",
        ))
        series.extend([vehicles, _quarter_series(vehicles, vehicles_quarter)])
    else:
        # A page that stops stating a count must not wipe the earlier series.
        series.extend(
            item for item in _carried_regulator_series(previous, source)
            if item.get("metric_id") in {vehicles_metric, vehicles_quarter}
        )
    status = "partial" if errors else "ok"
    return series, {
        "id": "av_regulator",
        "status": status,
        "permits": len(permit_hits),
        "vehicles": vehicle_total if vehicle_found else None,
        "note": REGULATOR_NOTE if not errors else "; ".join(errors),
    }


def topic_rules(block: dict) -> list[dict]:
    for source in (block or {}).get("sources") or []:
        if isinstance(source, dict) and source.get("id") == "news_topics":
            return [item for item in (source.get("topics") or []) if isinstance(item, dict)]
    return []


def estimate_rules(block: dict) -> list[dict]:
    for source in (block or {}).get("sources") or []:
        if isinstance(source, dict) and source.get("id") == "news_estimates":
            return [item for item in (source.get("rules") or []) if isinstance(item, dict)]
    return []


def topic_rules_for(catalogs: dict[str, dict]) -> dict[str, list[dict]]:
    found = {}
    for ticker, catalog in catalogs.items():
        block = catalog.get("deep_dive") if isinstance(catalog, dict) else None
        rules = topic_rules(block if isinstance(block, dict) else {})
        if rules:
            found[str(ticker).upper()] = rules
    return found


def tag_catalog_topics(rows: list[dict], rules_by_ticker: dict[str, list[dict]]) -> list[dict]:
    """Add catalog topic tokens onto articles. Existing topics stay."""
    if not rules_by_ticker:
        return rows
    tagged = []
    for row in rows:
        topics = [str(topic) for topic in (row.get("topics") or [])]
        blob = article_text(row)
        tickers = {str(ticker).upper() for ticker in (row.get("tickers") or []) if ticker}
        for ticker in tickers:
            for topic in rules_by_ticker.get(ticker) or []:
                topic_id = str(topic.get("id") or "").strip()
                if not topic_id:
                    continue
                token = f"energy:{topic_id}"
                if token in topics:
                    continue
                if any_term(blob, list(topic.get("terms") or []), boundary=True):
                    topics.append(token)
        tagged.append({**row, "topics": topics} if topics != list(row.get("topics") or []) else row)
    return tagged


def _numbers_near(
    text: str,
    anchors: list[str],
    *,
    units: list[str] | None,
    max_value: float | None,
) -> list[tuple[float, str, int]]:
    """(value, confidence, position) for figures near an anchor. Years and percents are skipped."""
    wanted_units = {str(unit).casefold() for unit in units or []}
    hits = []
    folded = text.casefold()
    anchor_at = []
    for anchor in anchors or []:
        needle = str(anchor or "").casefold().strip()
        if not needle:
            continue
        start = 0
        while True:
            found = folded.find(needle, start)
            if found < 0:
                break
            anchor_at.append(found)
            start = found + len(needle)
    if not anchor_at:
        return []
    for match in _NUMBER.finditer(text):
        if match.group("pct"):
            continue
        raw = match.group("num").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        unit = (match.group("unit") or "").casefold()
        mult = (match.group("mult") or "").casefold()
        if mult in _MULT:
            value *= _MULT[mult]
        if wanted_units:
            if unit not in wanted_units:
                continue
            if unit == "mwh":
                value = value / 1000.0
        elif 1990 <= value <= 2040 and not unit and not mult:
            continue
        tail = text[match.end():match.end() + 1]
        if tail in {"Q", "q"}:
            continue
        if max_value is not None and value > max_value:
            continue
        if value <= 0:
            continue
        position = match.start()
        if min(abs(position - anchor) for anchor in anchor_at) > 80:
            continue
        confidence = "medium" if unit or mult else "low"
        hits.append((value, confidence, position))
    return hits


def _closest(hits: list[tuple[float, str, int]], anchors_text: str, terms: list[str]) -> tuple[float, str] | None:
    if not hits:
        return None
    folded = anchors_text.casefold()
    anchors = []
    for term in terms or []:
        needle = str(term or "").casefold()
        if needle:
            found = folded.find(needle)
            if found >= 0:
                anchors.append(found)
    if not anchors:
        anchors = [0]
    ranked = sorted(hits, key=lambda item: min(abs(item[2] - anchor) for anchor in anchors))
    value, confidence, _pos = ranked[0]
    if len({round(item[0], 4) for item in hits}) > 1:
        confidence = "low"
    return value, confidence


def _country_in(text: str) -> str:
    folded = text.casefold()
    for needle, label in _COUNTRIES:
        if len(needle) <= 3:
            if re.search(rf"\b{re.escape(needle)}\b", text, re.I):
                return label
        elif needle.casefold() in folded:
            return label
    return ""


def _site_name(text: str) -> str:
    for pattern in (_SITE_AFTER, _SITE_BEFORE):
        match = pattern.search(text)
        if not match:
            continue
        name = " ".join(match.group(1).split())
        first = name.split(" ", 1)[0].casefold()
        if first in _SITE_REJECT or len(name) < 3:
            continue
        return name[:80]
    return ""


def _partner_name(text: str) -> str:
    match = _PARTNER.search(text)
    if not match:
        return ""
    name = " ".join(match.group(1).split())
    if name.split(" ", 1)[0].casefold() in _PARTNER_REJECT:
        return ""
    return name[:80]


def _money(text: str) -> str:
    match = _MONEY.search(text)
    if not match:
        return ""
    number = match.group("num")
    mult = (match.group("mult") or "").casefold()
    label = {"bn": "billion", "billion": "billion", "million": "million"}.get(mult, "")
    return f"${number} {label}".strip()


def _https(article: dict) -> str:
    url = str(article.get("url") or "")
    return url if url.startswith("https://") else ""


def _hit(
    rule: dict,
    article: dict,
    period: str,
    value: float | None,
    confidence: str,
    *,
    dimensions: dict | None = None,
    note: str = "",
    metric_id: str | None = None,
) -> dict:
    url = _https(article)
    return {
        "metric_id": metric_id or str(rule.get("metric_id") or ""),
        "period": period,
        "value": value,
        "unit": str(rule.get("unit") or ""),
        "confidence": "low" if confidence not in {"low", "medium"} else confidence,
        "source_url": url,
        "source_title": str(article.get("title") or "")[:200],
        "note": note[:500],
        "dimensions": dimensions or {},
        "dedupe": (url, round(value, 4) if isinstance(value, float) else value),
    }


def apply_rule(rule: dict, article: dict, text: str, period: str) -> list[dict]:
    kind = str(rule.get("kind") or "quantity")
    match = list(rule.get("match") or [])
    require = list(rule.get("require") or [])
    if match and not any_term(text, match, boundary=True):
        return []
    if require and not any_term(text, require, boundary=False):
        return []
    if not _https(article):
        return []
    if kind == "milestone":
        return _milestone_hits(rule, article, text, period)
    if kind == "place":
        return _place_hits(rule, article, text, period)
    if kind == "site":
        return _site_hits(rule, article, text, period)
    if kind == "deal":
        return _deal_hits(rule, article, text, period)
    return _quantity_hits(rule, article, text, period)


def _quantity_hits(rule: dict, article: dict, text: str, period: str) -> list[dict]:
    units = list(rule.get("units") or [])
    max_value = rule.get("max_value")
    cap = float(max_value) if isinstance(max_value, (int, float)) and not isinstance(max_value, bool) else None
    hits = _numbers_near(text, list(rule.get("quantity") or []), units=units or None, max_value=cap)
    picked = _closest(hits, text, list(rule.get("quantity") or []))
    if picked is None:
        return []
    value, confidence = picked
    return [_hit(rule, article, period, value, confidence, note=ESTIMATE_NOTE)]


def _place_hits(rule: dict, article: dict, text: str, period: str) -> list[dict]:
    found = []
    for sentence in re.split(r"[.!?\n]", text):
        country = _country_in(sentence)
        if not country or not any_term(sentence, list(rule.get("match") or []), boundary=True):
            continue
        units = list(rule.get("units") or [])
        max_value = rule.get("max_value")
        cap = float(max_value) if isinstance(max_value, (int, float)) and not isinstance(max_value, bool) else None
        anchors = list(rule.get("quantity") or rule.get("match") or [])
        hits = _numbers_near(sentence, anchors, units=units or None, max_value=cap)
        picked = _closest(hits, sentence, list(rule.get("quantity") or []))
        if picked is None:
            continue
        value, confidence = picked
        found.append(
            _hit(
                rule,
                article,
                period,
                value,
                confidence,
                dimensions={"country": country, "place": country},
                note=ESTIMATE_NOTE,
            )
        )
    return found[:1]


def _milestone_hits(rule: dict, article: dict, text: str, period: str) -> list[dict]:
    hits = []
    day = str(article.get("published_at") or "")[:10]
    for spec in rule.get("milestones") or []:
        if not isinstance(spec, dict):
            continue
        if not any_term(text, list(spec.get("terms") or []), boundary=False):
            continue
        hits.append(
            _hit(
                rule,
                article,
                period,
                None,
                "low",
                dimensions={"milestone_id": str(spec.get("id") or ""), "date": day, "state": "News mention"},
                note=str(article.get("title") or "")[:200],
            )
        )
    return hits


def _site_hits(rule: dict, article: dict, text: str, period: str) -> list[dict]:
    site = _site_name(text)
    stage = ""
    stage_id = ""
    for spec in rule.get("milestones") or []:
        if isinstance(spec, dict) and any_term(text, list(spec.get("terms") or []), boundary=False):
            stage_id = str(spec.get("id") or "")
            stage = str(spec.get("label") or stage_id)
            break
    if not site and not stage_id:
        return []
    dimensions = {
        "site": site,
        "status": stage or "News mention",
        "note": str(article.get("title") or "")[:200],
    }
    if stage_id:
        dimensions["milestone_id"] = stage_id
        dimensions["date"] = str(article.get("published_at") or "")[:10]
        dimensions["state"] = stage or "News mention"
    return [_hit(rule, article, period, None, "low", dimensions=dimensions, note=str(article.get("title") or "")[:200])]


def _deal_hits(rule: dict, article: dict, text: str, period: str) -> list[dict]:
    segment = None
    for spec in rule.get("segments") or []:
        if isinstance(spec, dict) and any_term(text, list(spec.get("terms") or []), boundary=True):
            segment = spec
            break
    if segment is None:
        return []
    day = str(article.get("published_at") or "")[:10]
    label = str(segment.get("id") or "")
    register = _hit(
        rule,
        article,
        period,
        None,
        "low",
        dimensions={
            "partner": _partner_name(text),
            "segment": label,
            "size": _money(text),
            "date": day,
            "status": "News mention",
        },
        note=str(article.get("title") or "")[:200],
    )
    count = _hit(
        rule,
        article,
        period,
        1.0,
        "low",
        note=ESTIMATE_NOTE,
        metric_id=str(segment.get("metric_id") or ""),
    )
    count["unit"] = "deals"
    hits = [item for item in (register, count) if item.get("metric_id")]
    return hits


def _weaker(left: str, right: str) -> str:
    rank = {"low": 0, "medium": 1, "high": 2}
    if rank.get(left, 0) <= rank.get(right, 0):
        return left if left in rank else "low"
    return right if right in rank else "low"


def _dim_key(dimensions: dict) -> tuple:
    return tuple(sorted((str(key), str(dimensions[key])) for key in dimensions))


def series_from_hits(hits: list[dict]) -> list[dict]:
    """Group hits into pending estimate series. The same figure in a quarter is kept once."""
    grouped: dict[str, list[dict]] = {}
    for hit in hits:
        metric_id = str(hit.get("metric_id") or "")
        if metric_id:
            grouped.setdefault(metric_id, []).append(hit)
    series = []
    for metric_id, rows in grouped.items():
        use_max = bool(rows) and all(hit.get("aggregate") == "max" for hit in rows)
        deduped: dict[tuple, dict] = {}
        for hit in rows:
            dimensions = hit.get("dimensions") if isinstance(hit.get("dimensions"), dict) else {}
            key = (hit.get("period"), _dim_key(dimensions), hit.get("dedupe"))
            current = deduped.get(key)
            if current is None:
                deduped[key] = {
                    "period": hit.get("period"),
                    "value": hit.get("value"),
                    "confidence": hit.get("confidence") or "low",
                    "dimensions": dimensions,
                    "note": hit.get("note") or "",
                    "source_url": hit.get("source_url") or "",
                    "source_title": hit.get("source_title") or "",
                    "sources": [{"url": hit.get("source_url"), "title": hit.get("source_title") or ""}],
                }
                continue
            if hit.get("source_url") and len(current["sources"]) < 5:
                if all(item.get("url") != hit.get("source_url") for item in current["sources"]):
                    current["sources"].append({"url": hit.get("source_url"), "title": hit.get("source_title") or ""})
            current["confidence"] = _weaker(current["confidence"], hit.get("confidence") or "low")
        by_period: dict[tuple, list[dict]] = {}
        for item in deduped.values():
            by_period.setdefault((item["period"], _dim_key(item["dimensions"])), []).append(item)
        points = []
        numeric = False
        confidences = []
        for (period, _dim), items in by_period.items():
            values = [
                entry["value"] for entry in items
                if isinstance(entry["value"], (int, float)) and not isinstance(entry["value"], bool)
            ]
            sources = []
            for entry in items:
                for source in entry["sources"]:
                    if source.get("url") and all(kept.get("url") != source.get("url") for kept in sources):
                        sources.append(source)
            if values:
                distinct = list(dict.fromkeys(round(float(value), 4) for value in values))
                value = float(max(distinct) if use_max else sum(distinct))
                point_confidence = "low" if len(distinct) > 1 else (items[0]["confidence"] or "low")
                numeric = True
            else:
                value = None
                point_confidence = "low"
            if point_confidence not in {"low", "medium"}:
                point_confidence = "low"
            confidences.append(point_confidence)
            points.append(
                {
                    "period": period,
                    "value": value,
                    "reported": value is not None,
                    "estimate": True,
                    "confidence": point_confidence,
                    "dimensions": items[0]["dimensions"],
                    "note": items[0]["note"],
                    "source_url": items[0]["source_url"],
                    "source_title": items[0]["source_title"],
                    "sources": sources[:5],
                }
            )
        points.sort(key=lambda item: period_sort_key(str(item.get("period") or "")))
        if not points:
            continue
        confidence = confidences[0] if confidences else "low"
        for item in confidences[1:]:
            confidence = _weaker(confidence, item)
        latest = next((point for point in reversed(points) if point.get("source_url")), points[-1])
        series.append(
            {
                "metric_id": metric_id,
                "unit": rows[0].get("unit") or "",
                "cadence": "quarterly",
                "confidence": confidence,
                "approval_state": "proposed",
                "approved": False,
                "estimate": True,
                "source_kind": "news_estimate",
                "source_url": latest.get("source_url") or "",
                "source_title": latest.get("source_title") or "News estimate",
                "disclosure_status": "numeric" if numeric else "narrative",
                "points": points,
                "notes": [ESTIMATE_NOTE],
            }
        )
    return series


def estimates_from_articles(articles: list[dict], rules: list[dict]) -> list[dict]:
    hits = []
    seen_urls: set[str] = set()
    for article in articles or []:
        if not isinstance(article, dict):
            continue
        url = _https(article)
        text = article_text(article)
        period = quarter_of(str(article.get("published_at") or ""))
        if not url or not text.strip() or not period or url in seen_urls:
            continue
        seen_urls.add(url)
        for rule in rules or []:
            if not isinstance(rule, dict):
                continue
            for hit in apply_rule(rule, article, text, period):
                aggregate = str(rule.get("aggregate") or "sum")
                hit["aggregate"] = "max" if aggregate == "max" else "sum"
                hits.append(hit)
    return _force_estimate(series_from_hits(hits))


def _force_estimate(series_list: list[dict]) -> list[dict]:
    forced = []
    for series in series_list:
        series["approval_state"] = "proposed"
        series["approved"] = False
        series["estimate"] = True
        series["source_kind"] = "news_estimate"
        if series.get("confidence") == "high":
            series["confidence"] = "medium"
        forced.append(series)
    return forced


def topic_series(articles: list[dict], source: dict, generated_at: str) -> dict | None:
    metric_id = str(source.get("metric_id") or "")
    topics = [item for item in (source.get("topics") or []) if isinstance(item, dict)]
    if not metric_id or not topics:
        return None
    window_days = source.get("window_days")
    try:
        window = int(window_days) if window_days is not None else 90
    except (TypeError, ValueError):
        window = 90
    as_of = _parse_stamp(generated_at) or datetime.now(UTC)
    cutoff = as_of - timedelta(days=max(1, window))
    period = quarter_of(as_of.date().isoformat()) or quarter_of(generated_at)
    if not period:
        return None
    points = []
    for topic in topics:
        terms = list(topic.get("terms") or [])
        label = str(topic.get("label") or topic.get("id") or "")
        matched = []
        for article in articles or []:
            if not isinstance(article, dict):
                continue
            stamp = _parse_stamp(str(article.get("published_at") or ""))
            if stamp is None or stamp < cutoff or stamp > as_of + timedelta(days=1):
                continue
            if any_term(article_text(article), terms, boundary=True):
                matched.append(article)
        if not matched:
            continue
        scores = []
        for article in matched:
            score = article.get("sentiment_score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                scores.append(float(score))
        sentiment = round(sum(scores) / len(scores), 2) if scores else None
        points.append(
            {
                "period": period,
                "value": len(matched),
                "reported": True,
                "estimate": False,
                "dimensions": {
                    "topic": label,
                    "volume": len(matched),
                    "sentiment": "n/a" if sentiment is None else f"{sentiment:.2f}",
                },
            }
        )
    if not points:
        return None
    return _collected_series(
        metric_id,
        points,
        unit="articles",
        source_url="",
        source_title="News topics",
        source_kind="news_topics",
        confidence="medium",
        notes=[TOPIC_NOTE],
    )


def build_news_rollup(articles: list[dict], block: dict, generated_at: str) -> dict:
    """Compact per-ticker file the deep-dive page reads. Articles themselves are not copied."""
    topic_source = next(
        (
            source for source in (block or {}).get("sources") or []
            if isinstance(source, dict) and source.get("id") == "news_topics"
        ),
        None,
    )
    topics = topic_series(articles, topic_source, generated_at) if isinstance(topic_source, dict) else None
    estimates = estimates_from_articles(articles, estimate_rules(block if isinstance(block, dict) else {}))
    return {
        "generated_at": generated_at,
        "topics": [topics] if topics else [],
        "estimates": estimates,
    }


def series_from_rollup(rollup: dict | None) -> tuple[list[dict], list[dict]]:
    if not isinstance(rollup, dict) or not rollup.get("generated_at"):
        return [], []
    topics = [item for item in (rollup.get("topics") or []) if isinstance(item, dict) and item.get("metric_id")]
    estimates = _force_estimate(
        [item for item in (rollup.get("estimates") or []) if isinstance(item, dict) and item.get("metric_id")]
    )
    for series in topics:
        series["approval_state"] = "proposed"
        series["approved"] = False
        series["estimate"] = False
    return topics, estimates
