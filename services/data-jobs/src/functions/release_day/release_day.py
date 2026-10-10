"""M1: CPI, PCE and trade on release morning, plus the release calendar.

The morning cron bootstraps the calendar. It writes one-off EventBridge Scheduler at() times in UTC from
America/New_York release instants, then polls until a new period shows up.
Year-over-year history is written to curated/macro_daily/source=releases/ before the
handler returns, dated on the release date, so the Job Finished event can start
trend-metrics without a look-ahead. Release trends and window links for every
watchlist ticker go to curated/release_links/. A backfill that wrote rows rebuilds
those links without fetching the agencies or reading API keys.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import polars as pl

JOB_ID = "M1"
ET = ZoneInfo("America/New_York")
CPI_SERIES = ("CUSR0000SA0", "CUSR0000SA0L1E")
POLL_ATTEMPTS = 10
POLL_SECONDS = 120
BACKFILL_YEARS = 5
SCHEDULE_PREFIX = "invdash"
TREND_TRIGGERS = frozenset({"D4", "D1", "M1", "RECONCILE"})
# Four minutes apart: each run waits one extra two-minute poll, and 300s cannot hold a 20-minute loop.
FOLLOW_UP_MINUTES = (0, 4, 8, 12, 16)
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BEA_URL = "https://apps.bea.gov/api/data"
CENSUS_URL = "https://api.census.gov/data/timeseries/eits/ftd"
# Official variables for timeseries/eits/ftd (variables.json, checked 2026-10-10).
# time, for and in are predicate-only. IMPG and EXPG are not variables.
CENSUS_GET_VARIABLES = frozenset(
    {
        "category_code",
        "cell_value",
        "data_type_code",
        "error_data",
        "geo_level_code",
        "program_code",
        "seasonally_adj",
        "time_slot_date",
        "time_slot_id",
        "time_slot_name",
    }
)
CENSUS_PREDICATE_ONLY = frozenset({"for", "in", "time"})
CENSUS_REQUIRED_GET = frozenset(
    {"cell_value", "time_slot_id", "category_code", "data_type_code", "seasonally_adj"}
)
# FTD-mf codebook (Census econ download, 2026-10-06): BOPG is goods; IMP and EXP are millions of dollars.
CENSUS_GOODS_CATEGORY = "BOPG"
CENSUS_GOODS_TYPES = {"IMP": "CENSUS_IMPG", "EXP": "CENSUS_EXPG"}
FRED_URL = "https://api.stlouisfed.org/fred/releases/dates"
FRED_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"
# FRED vintage series -> the M1 series released that morning. The earliest realtime_start
# is the publication date, so a backfill is not stamped with the day the job first ran.
VINTAGE_SERIES = (
    ("CPIAUCSL", ("CUSR0000SA0", "CUSR0000SA0L1E")),
    ("PCEPI", ("PCE", "CORE_PCE")),
    ("IMPGS", ("CENSUS_IMPG", "CENSUS_EXPG")),
)
# Names trend_metrics reads. Census goods are part of M1 but are not daily drivers.
YOY_SERIES = {
    "CUSR0000SA0": "CPI_YOY",
    "CUSR0000SA0L1E": "CORE_CPI_YOY",
    "PCE": "PCE_YOY",
    "CORE_PCE": "CORE_PCE_YOY",
    "CENSUS_IMPG": "CENSUS_IMPG_YOY",
    "CENSUS_EXPG": "CENSUS_EXPG_YOY",
}
RELEASE_LINKS_KEY = "curated/release_links/release_links.parquet"
_API_KEY_QUERY = re.compile(r"(api_key=)[^&\s\"]*")
# Official calendar subscription on the CPI schedule page and bls.gov/help/hlpical.htm.
# cpi.ics is not that endpoint (Lambda received HTTP 404). A 403 is an access denial.
BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
CLEVELAND_PAGE = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
MEASURE_SERIES = {
    "CPI": "CUSR0000SA0",
    "Core CPI": "CUSR0000SA0L1E",
    "PCE": "PCE",
    "Core PCE": "CORE_PCE",
}
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
RELEASE_FIELDS = (
    "series_id",
    "period",
    "release_ts",
    "actual",
    "prior",
    "revised_prior",
    "consensus",
    "surprise",
    "units",
    "source",
    "mom",
    "yoy",
    "surprise_z",
    "core_vs_headline",
    "release_history",
)


def surprise(actual: float | None, consensus: float | None) -> float | None:
    if not isinstance(actual, (int, float)) or not isinstance(consensus, (int, float)):
        return None
    return float(actual) - float(consensus)


def at_schedule(when: datetime) -> str:
    """EventBridge at() is UTC. DST is handled by converting a New York instant."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=ET)
    stamp = when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"at({stamp})"


def schedule_name(series: str, when: datetime) -> str:
    stamp = when.astimezone(UTC).strftime("%Y%m%dT%H%M")
    return f"{SCHEDULE_PREFIX}-m1-{series}-{stamp}"[:64]


def trend_should_run(detail: dict) -> bool:
    return detail.get("outcome") == "success" and (
        detail.get("job") in TREND_TRIGGERS
        or (detail.get("job") == "BACKFILL" and detail.get("batches", 0) > 0)
    )


def bls_body(start_year: int, end_year: int, api_key: str) -> dict:
    return {
        "seriesid": list(CPI_SERIES),
        "startyear": str(start_year),
        "endyear": str(end_year),
        "registrationkey": api_key,
    }


def _row(series_id: str, period: str, release_ts: str, actual: float, source: str, units: str) -> dict:
    return {
        "series_id": series_id,
        "period": period,
        "release_ts": release_ts,
        "actual": actual,
        "prior": None,
        "revised_prior": None,
        "consensus": None,
        "surprise": None,
        "units": units,
        "source": source,
    }


def parse_bls(payload: dict, release_ts: str, consensus: dict[str, float] | None = None) -> list[dict]:
    if payload.get("status") not in (None, "REQUEST_SUCCEEDED"):
        raise ValueError("BLS rejected the time-series request")
    rows = []
    for series in (payload.get("Results") or {}).get("series") or []:
        series_id = series.get("seriesID")
        if not series_id:
            continue
        agreed = None if not consensus else consensus.get(series_id)
        for point in series.get("data") or []:
            period_code = str(point.get("period") or "")
            if not period_code.startswith("M") or period_code == "M13":
                continue
            period = f"{point.get('year')}-{period_code[1:]}"
            try:
                actual = float(point.get("value"))
            except (TypeError, ValueError):
                continue
            row = _row(series_id, period, release_ts, actual, "bls", "index")
            row["consensus"] = agreed
            row["surprise"] = surprise(actual, agreed)
            rows.append(row)
    return rows


def parse_bea(payload: dict, release_ts: str) -> list[dict]:
    """NIPA T20804 monthly price index. Line 1 is headline PCE."""
    results = ((payload or {}).get("BEAAPI") or {}).get("Results") or {}
    if results.get("Error"):
        raise ValueError(str(results["Error"])[:200])
    rows = []
    for item in results.get("Data") or []:
        if str(item.get("TableName") or "T20804") not in {"T20804", ""}:
            continue
        series_id = _bea_series(str(item.get("LineDescription") or ""), str(item.get("LineNumber") or ""))
        period = _bea_period(str(item.get("TimePeriod") or ""))
        actual = _number(item.get("DataValue"))
        if not series_id or not period or actual is None:
            continue
        rows.append(_row(series_id, period, release_ts, actual, "bea", "index"))
    return rows


def _bea_series(description: str, line: str) -> str | None:
    text = " ".join(description.lower().split())
    if "personal consumption expenditures" not in text and line != "1":
        return None
    if "excluding food and energy" in text:
        return "CORE_PCE"
    if line == "1" or text.startswith("personal consumption expenditures"):
        return "PCE"
    return None


def _bea_period(token: str) -> str | None:
    if "M" not in token:
        return None
    year, month = token.split("M", 1)
    if len(year) == 4 and month.isdigit():
        return f"{int(year):04d}-{int(month):02d}"
    return None


def census_request_problems(params: dict) -> list[str]:
    """Reject a foreign-trade request that does not match the published variable list."""
    problems = []
    requested = {name.strip() for name in str(params.get("get") or "").split(",") if name.strip()}
    missing = CENSUS_REQUIRED_GET - requested
    if missing:
        problems.append("missing required variables: " + ", ".join(sorted(missing)))
    unknown = requested - CENSUS_GET_VARIABLES
    if unknown:
        problems.append("unknown variables: " + ", ".join(sorted(unknown)))
    if requested & CENSUS_PREDICATE_ONLY:
        problems.append("predicate-only variable in get")
    if params.get("category_code") != CENSUS_GOODS_CATEGORY:
        problems.append("category_code must be BOPG (goods)")
    if str(params.get("seasonally_adj") or "").lower() != "yes":
        problems.append("seasonally_adj must be yes")
    window = str(params.get("time") or "")
    if not re.fullmatch(r"from \d{4}-\d{2} to \d{4}-\d{2}", window):
        problems.append("time must be a month range: from YYYY-MM to YYYY-MM")
    return problems


def census_params(start_year: int, end: date, api_key: str) -> dict:
    """Goods imports and exports. Variables and the time predicate match Census metadata."""
    params = {
        "get": ",".join(sorted(CENSUS_REQUIRED_GET)),
        "time": f"from {start_year}-01 to {end.year}-{end.month:02d}",
        "category_code": CENSUS_GOODS_CATEGORY,
        "seasonally_adj": "yes",
        "key": api_key,
    }
    problems = census_request_problems(params)
    if problems:
        raise ValueError("Census request does not match official metadata: " + "; ".join(problems))
    return params


def _census_period(record: dict) -> str | None:
    for key in ("time", "time_slot_id"):
        text = str(record.get(key) or "")
        if len(text) >= 7 and text[4] == "-" and text[:4].isdigit() and text[5:7].isdigit():
            return text[:7]
    return None


def parse_census(payload, release_ts: str) -> list[dict]:
    """Seasonally adjusted BOP goods imports and exports, in the published millions of dollars."""
    if isinstance(payload, dict):
        raise ValueError(str(payload.get("error") or payload)[:200])
    if not isinstance(payload, list) or len(payload) < 2:
        return []
    header = [str(name) for name in payload[0]]
    rows = []
    for raw in payload[1:]:
        record = dict(zip(header, raw, strict=False))
        if record.get("category_code") not in (None, "", CENSUS_GOODS_CATEGORY):
            continue
        if str(record.get("seasonally_adj") or "yes").lower() not in {"yes", "y", "1"}:
            continue
        series_id = CENSUS_GOODS_TYPES.get(str(record.get("data_type_code") or ""))
        period = _census_period(record)
        actual = _number(record.get("cell_value"))
        if not series_id or not period or actual is None:
            continue
        rows.append(_row(series_id, period, release_ts, actual, "census", "usd_millions"))
    return rows


def _number(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"NA", "(NA)", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_ics(text: str) -> list[str]:
    """CPI dates from the BLS news-release calendar."""
    dates: list[str] = []
    summary, day = "", None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("BEGIN:VEVENT"):
            summary, day = "", None
        elif line.startswith("SUMMARY"):
            summary = line.split(":", 1)[-1]
        elif line.startswith("DTSTART"):
            token = line.split(":", 1)[-1]
            if len(token) >= 8 and token[:8].isdigit():
                day = f"{token[:4]}-{token[4:6]}-{token[6:8]}"
        elif line.startswith("END:VEVENT") and day and "consumer price index" in summary.lower():
            dates.append(day)
    return dates


def classify_release(name: str) -> str | None:
    text = name.lower()
    if "consumer price" in text:
        return "cpi"
    if "personal income" in text:
        return "pce"
    if "international trade" in text:
        return "trade"
    return None


def parse_fred_dates(payload: dict) -> list[dict]:
    rows = []
    for item in payload.get("release_dates") or []:
        series = classify_release(str(item.get("release_name") or ""))
        day = str(item.get("date") or "")[:10]
        if not series or len(day) != 10:
            continue
        when = datetime.fromisoformat(day).replace(hour=8, minute=35, tzinfo=ET)
        rows.append({"series": series, "release_ts": when.isoformat(timespec="minutes"), "source": "fred"})
    return rows


def calendar_rows(ics_dates: list[str], fred_rows: list[dict]) -> list[dict]:
    """BLS ICS wins for CPI. FRED fills PCE, trade, and any CPI date the ICS feed missed."""
    seen: set[tuple[str, str]] = set()
    rows = []
    for day in ics_dates:
        key = ("cpi", day)
        if key in seen:
            continue
        seen.add(key)
        when = datetime.fromisoformat(day).replace(hour=8, minute=35, tzinfo=ET)
        rows.append({"series": "cpi", "release_ts": when.isoformat(timespec="minutes"), "source": "bls"})
    for row in fred_rows:
        key = (row["series"], str(row.get("release_ts") or "")[:10])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def upcoming_calendar(rows: list[dict], today: date) -> list[dict]:
    future = [row for row in rows if str(row.get("release_ts") or "")[:10] >= today.isoformat()]
    return sorted(future, key=lambda row: str(row.get("release_ts") or ""))


def merge_releases(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """(series_id, period) is the key. A changed actual records the old one as revised_prior."""
    by_key = {(row.get("series_id"), row.get("period")): dict(row) for row in existing}
    for row in sorted(incoming, key=lambda item: str(item.get("period") or "")):
        key = (row.get("series_id"), row.get("period"))
        current = by_key.get(key)
        if current is None:
            previous = sorted(
                (str(period), item["actual"])
                for (series_id, period), item in by_key.items()
                if series_id == key[0] and str(period) < str(key[1]) and isinstance(item.get("actual"), (int, float))
            )
            by_key[key] = {
                **row,
                "prior": previous[-1][1] if previous else row.get("prior"),
                "revised_prior": None,
                "release_history": [row.get("release_ts")],
            }
            continue
        if current.get("actual") == row.get("actual"):
            continue
        history = list(current.get("release_history") or [current.get("release_ts")])
        history.append(row.get("release_ts"))
        by_key[key] = {
            **current,
            "actual": row.get("actual"),
            "revised_prior": current.get("actual"),
            "consensus": row.get("consensus", current.get("consensus")),
            "surprise": surprise(row.get("actual"), row.get("consensus", current.get("consensus"))),
            "release_history": history,
        }
    return list(by_key.values())


def consensus_index(prior: float | None, nowcast_pct: float | None) -> float | None:
    """Cleveland's nowcast is a percent. Turn it into an index level so surprise = actual - consensus."""
    if not isinstance(prior, (int, float)) or not isinstance(nowcast_pct, (int, float)):
        return None
    return float(prior) * (1 + float(nowcast_pct) / 100)


def nowcast_month(period: str) -> str | None:
    """Month label from the nowcast table. Quarterly labels are not monthly periods."""
    text = " ".join(str(period or "").replace(",", " ").split())
    if len(text) >= 7 and text[4] == "-" and text[:4].isdigit() and text[5:7].isdigit():
        return text[:7]
    parts = text.split()
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 4 and parts[0].lower() in _MONTHS:
        return f"{int(parts[1]):04d}-{_MONTHS[parts[0].lower()]:02d}"
    return None


def apply_nowcast(rows: list[dict], nowcast_rows: list[dict], known: set | None = None) -> list[dict]:
    """Attach a MoM nowcast only to a new release for that same month.

    Year-over-year and annualized quarterly forecasts are not index consensus.
    A period already in `known` keeps whatever consensus it has, including none.
    """
    stored = known or set()
    mom: dict[tuple[str, str], float] = {}
    for item in nowcast_rows:
        if item.get("basis") != "mom":
            continue
        series_id = MEASURE_SERIES.get(str(item.get("measure") or ""))
        period = nowcast_month(str(item.get("period") or ""))
        if series_id and period and isinstance(item.get("value"), (int, float)):
            mom[(series_id, period)] = float(item["value"])
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("series_id")), []).append(row)
    for group in grouped.values():
        ordered = sorted(group, key=lambda item: str(item.get("period") or ""))
        by_period = {item.get("period"): item for item in ordered}
        for row in ordered:
            series_id = str(row.get("series_id") or "")
            period = str(row.get("period") or "")
            if (series_id, period) in stored or row.get("consensus") is not None:
                continue
            pct = mom.get((series_id, period))
            if pct is None:
                continue
            prior = row.get("prior")
            if prior is None:
                previous = by_period.get(shift_period(period, 1) or "")
                prior = None if previous is None else previous.get("actual")
            agreed = consensus_index(prior, pct)
            if agreed is None:
                continue
            row["consensus"] = agreed
            row["surprise"] = surprise(row.get("actual"), agreed)
    return rows


def shift_period(period: str, months: int) -> str | None:
    if len(period) < 7 or period[4] != "-":
        return None
    try:
        year, month = int(period[:4]), int(period[5:7])
    except ValueError:
        return None
    index = year * 12 + (month - 1) - months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def percent_change(actual, base) -> float | None:
    if not isinstance(actual, (int, float)) or not isinstance(base, (int, float)) or base == 0:
        return None
    return (float(actual) / float(base) - 1) * 100


def zscore(current, history: list[float]) -> float | None:
    if not isinstance(current, (int, float)) or len(history) < 2:
        return None
    mean = sum(history) / len(history)
    var = sum((value - mean) ** 2 for value in history) / (len(history) - 1)
    if var <= 0:
        return 0.0
    return (float(current) - mean) / var**0.5


def enrich_releases(rows: list[dict]) -> list[dict]:
    """MoM and YoY percent changes, a surprise z-score against the prior 24 prints, and core minus headline."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("series_id")), []).append(row)
    headline_mom: dict[str, float] = {}
    for series_id, group in grouped.items():
        ordered = sorted(group, key=lambda item: str(item.get("period") or ""))
        by_period = {item.get("period"): item for item in ordered}
        surprises: list[float | None] = []
        for row in ordered:
            period = str(row.get("period") or "")
            prior = by_period.get(shift_period(period, 1) or "")
            year_ago = by_period.get(shift_period(period, 12) or "")
            row["mom"] = percent_change(row.get("actual"), None if prior is None else prior.get("actual"))
            row["yoy"] = percent_change(row.get("actual"), None if year_ago is None else year_ago.get("actual"))
            surprises.append(row.get("surprise") if isinstance(row.get("surprise"), (int, float)) else None)
            history = [value for value in surprises[:-1] if value is not None][-24:]
            row["surprise_z"] = zscore(surprises[-1], history)
            if series_id == "CUSR0000SA0" and isinstance(row.get("mom"), (int, float)):
                headline_mom[period] = float(row["mom"])
    for row in grouped.get("CUSR0000SA0L1E", []):
        headline = headline_mom.get(str(row.get("period") or ""))
        row["core_vs_headline"] = (
            None if headline is None or not isinstance(row.get("mom"), (int, float)) else float(row["mom"]) - headline
        )
    return rows


def poll_for_new_period(fetch, known: set[tuple], attempts: int = POLL_ATTEMPTS, pause=None) -> list[dict]:
    """Retry every two minutes, up to 20 minutes, then exit with no rows if nothing new arrived."""
    last: list[dict] = []
    for attempt in range(attempts):
        last = fetch() or []
        fresh = [row for row in last if (row.get("series_id"), row.get("period")) not in known]
        if fresh:
            return fresh
        if attempt + 1 < attempts and pause:
            pause(POLL_SECONDS)
    return []


def _schedules(dates: list[str], series: str, hour: int, minute: int, offsets: tuple[int, ...]) -> list[dict]:
    planned = []
    for day in dates:
        year, month, dom = (int(part) for part in day.split("-"))
        base = datetime(year, month, dom, hour, minute, tzinfo=ET)
        for offset in offsets:
            when = base + timedelta(minutes=offset)
            planned.append(
                {
                    "Name": schedule_name(series, when),
                    "ScheduleExpression": at_schedule(when),
                    "ScheduleExpressionTimezone": "UTC",
                }
            )
    return planned


def calendar_schedules(dates: list[str], series: str, hour: int = 8, minute: int = 35) -> list[dict]:
    """One-off schedules for release mornings. The expression is UTC so DST is in the timestamp."""
    return _schedules(dates, series, hour, minute, (0,))


def follow_up_schedules(dates: list[str], series: str, hour: int = 8, minute: int = 35) -> list[dict]:
    """Cover the 20-minute release window with one-off runs the 300s timeout can finish."""
    return _schedules(dates, series, hour, minute, FOLLOW_UP_MINUTES)


def month_open_schedules(today: date, months: int = 3) -> list[dict]:
    """08:00 ET on the first federal business day, so the calendar is refreshed even with no D1."""
    import holidays

    planned = []
    year, month = today.year, today.month
    closed = holidays.US(years=[year, year + 1])
    for _ in range(months):
        day = date(year, month, 1)
        while day.weekday() >= 5 or day in closed:
            day += timedelta(days=1)
        if day >= today:
            for item in calendar_schedules([day.isoformat()], "calendar", hour=8, minute=0):
                planned.append({**item, "Input": {"source": "calendar", "schedule": item["Name"]}})
        month += 1
        if month == 13:
            month = 1
            year += 1
            closed = holidays.US(years=[year, year + 1])
    return planned


def future_schedules(planned: list[dict], now: datetime) -> list[dict]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    kept = []
    for item in planned:
        stamp = item["ScheduleExpression"].removeprefix("at(").removesuffix(")")
        when = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
        if when > now:
            kept.append(item)
    return kept


def _schedule_exists(exc: Exception) -> bool:
    code = None
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
    return exc.__class__.__name__ == "ConflictException" or code == "ConflictException"


def ensure_schedules(client, planned: list[dict], function_arn: str, role_arn: str) -> list[str]:
    """Create invdash-m1-* one-off schedules. An existing name is left in place."""
    names = []
    for item in planned:
        payload = item.get("Input") or {"source": "release", "schedule": item["Name"]}
        try:
            client.create_schedule(
                Name=item["Name"],
                ScheduleExpression=item["ScheduleExpression"],
                ScheduleExpressionTimezone=item.get("ScheduleExpressionTimezone", "UTC"),
                FlexibleTimeWindow={"Mode": "OFF"},
                Target={"Arn": function_arn, "RoleArn": role_arn, "Input": json.dumps(payload)},
                ActionAfterCompletion="DELETE",
            )
        except Exception as exc:
            if not _schedule_exists(exc):
                raise
        names.append(item["Name"])
    return names


def fred_vintage_params(series_id: str, api_key: str, start: date) -> dict:
    return {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start.isoformat(),
        "realtime_start": start.isoformat(),
        "realtime_end": "9999-12-31",
    }


def first_release_dates(payload: dict) -> dict[str, str]:
    """Reference period -> earliest vintage date. That date is when the print was public."""
    first: dict[str, str] = {}
    for item in payload.get("observations") or []:
        period = str(item.get("date") or "")[:7]
        stamp = str(item.get("realtime_start") or "")[:10]
        if len(period) != 7 or period[4] != "-" or len(stamp) != 10 or stamp[4] != "-":
            continue
        if period not in first or stamp < first[period]:
            first[period] = stamp
    return first


def apply_vintage_dates(rows: list[dict], vintages: dict[str, dict[str, str]]) -> list[dict]:
    """Move a backfill stamp back to the first publication date. Never move a date forward."""
    for row in rows:
        stamp = (vintages.get(str(row.get("series_id") or "")) or {}).get(str(row.get("period") or ""))
        if not stamp:
            continue
        current = str(row.get("release_ts") or "")
        if len(current) >= 10 and stamp[:10] >= current[:10]:
            continue
        year, month, day = (int(part) for part in stamp[:10].split("-"))
        new_ts = datetime(year, month, day, 8, 35, tzinfo=ET).isoformat(timespec="minutes")
        old = row.get("release_ts")
        row["release_ts"] = new_ts
        history = row.get("release_history")
        if isinstance(history, list) and history and history[0] == old:
            history[0] = new_ts
    return rows


class ProviderStatus(RuntimeError):
    """Source failure whose message is safe to log. It does not include a URL or key."""


def raise_without_url(exc: httpx.HTTPStatusError, source: str) -> None:
    status = exc.response.status_code
    if status == 429:
        raise ProviderStatus(f"{source} HTTP 429; throttled, not a parser failure") from None
    if status in (401, 403):
        raise ProviderStatus(f"{source} HTTP {status}; access denied, no dates or rows synthesized") from None
    raise ProviderStatus(f"{source} HTTP {status}") from None


def redact_api_key(payload: dict, secret: str) -> dict:
    text = json.dumps(payload, default=str)
    if secret:
        text = text.replace(secret, "")
    return json.loads(_API_KEY_QUERY.sub(r"\1", text))


def _fred_json(http, url: str, params: dict, label: str) -> dict:
    """GET FRED. A failure is re-raised without the URL, which carries the API key."""
    from http_client import request_with_retry

    try:
        return request_with_retry(http, "GET", url, params=params).json()
    except (httpx.HTTPError, RuntimeError, ValueError, TypeError):
        raise RuntimeError(f"FRED request failed for {label}") from None


def macro_rows(releases: list[dict]) -> list[dict]:
    """Full YoY history under the names trend_metrics reads, dated by the release date."""
    chosen: dict[tuple[str, str], dict] = {}
    for row in releases:
        series_id = YOY_SERIES.get(str(row.get("series_id") or ""))
        yoy = row.get("yoy")
        obs = str(row.get("release_ts") or "")[:10]
        if series_id is None or not isinstance(yoy, (int, float)) or isinstance(yoy, bool) or len(obs) != 10:
            continue
        key = (series_id, obs)
        period = str(row.get("period") or "")
        current = chosen.get(key)
        if current is not None and period <= current["period"]:
            continue
        chosen[key] = {
            "series_id": series_id,
            "obs_date": obs,
            "value": float(yoy),
            "available_date": obs,
            "source": "releases",
            "period": period,
        }
    return [{field: row[field] for field in row if field != "period"} for row in chosen.values()]


def event_detail(event) -> dict | None:
    """Job Finished detail, whether EventBridge delivered it as an object or a JSON string."""
    if not isinstance(event, dict):
        return None
    detail = event.get("detail") or {}
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            return None
    return detail if isinstance(detail, dict) else None


def backfill_refresh(event) -> bool:
    """True when a backfill that wrote rows (batches > 0) finished successfully."""
    detail = event_detail(event)
    if not detail or detail.get("job") != "BACKFILL" or detail.get("outcome", "success") != "success":
        return False
    try:
        return int(detail.get("batches") or 0) > 0
    except (TypeError, ValueError):
        return False


def watchlist_for_links() -> dict[str, list[str] | str | None]:
    """Defaults plus every prefs ticker, including an index ETF a user added."""
    from universe import user_ticker_union, watchlist_universe

    return watchlist_universe(user_ticker_union())


def publish_release_links(releases: list[dict]) -> dict[str, list[str] | str | int | None]:
    """Rewrite the links table for every watchlist ticker that already has closes."""
    from lake import read_prices, write_parquet
    from observability import logger
    from release_links import build_release_links

    uni = watchlist_for_links()
    dropped = list(uni["dropped"] or [])
    if dropped:
        logger.warning("over the ticker cap", extra={"dropped": dropped, "dropped_count": len(dropped)})
    links = build_release_links(releases, read_prices(), YOY_SERIES, list(uni["tickers"] or []))
    linked = 0
    if not links.is_empty():
        write_parquet(links, RELEASE_LINKS_KEY)
        summaries = links.filter(pl.col("row_kind") == "summary")
        if not summaries.is_empty() and "ticker" in summaries.columns:
            linked = summaries["ticker"].drop_nulls().n_unique()
    return {"tickers": linked, "dropped": dropped, "over_cap": uni["over_cap"]}


def publish_macro(rows: list[dict], ingested_at: str) -> list[str]:
    """Upsert each series into its own curated folder. A later ingest overwrites the same release date."""
    from lake import upsert_ranked

    keys = []
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["series_id"], []).append({**row, "ingested_at": ingested_at})
    for series_id, group in grouped.items():
        frame = pl.DataFrame(group).with_columns(
            pl.col("obs_date").str.to_date(),
            pl.col("available_date").str.to_date(),
            pl.col("value").cast(pl.Float64),
        )
        key = f"curated/macro_daily/source=releases/series_id={series_id}/macro.parquet"
        upsert_ranked(frame, key, ["series_id", "obs_date"], "ingested_at")
        keys.append(key)
    return keys


def release_frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame([{field: row.get(field) for field in RELEASE_FIELDS} for row in rows])


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    import os
    import time

    from api_keys import api_key
    from collectors.fed_sources import parse_cleveland_html
    from http_client import get_client, request_with_retry
    from lake import read_json, read_parquet_prefix, write_json, write_parquet
    from observability import job_handler, logger, source_run

    incoming = event_detail(event)
    idle = incoming and incoming.get("job") == "BACKFILL" and not backfill_refresh(event)
    if (event or {}).get("source") == "schedule":
        from macro_daily import is_federal_business_day

        idle = not is_federal_business_day(datetime.now(ET).date())

    @job_handler(JOB_ID, lease_key=None if idle else "curated/releases/_lease.json")
    def run(event, context):
        if (event or {}).get("source") == "schedule":
            from macro_daily import is_federal_business_day

            if not is_federal_business_day(datetime.now(ET).date()):
                return {"status": "skipped", "reason": "federal holiday or weekend"}
        detail = event_detail(event)
        if detail and detail.get("job") in {"BACKFILL", "D4", "RECONCILE"}:
            # Links only: no BLS, BEA, Census, or FRED calls, and no API keys.
            if detail.get("outcome") != "success" or (
                detail.get("job") == "BACKFILL" and not backfill_refresh(event)
            ):
                return {"status": "skipped", "mode": "links", "batches": 0}
            frame = read_parquet_prefix("curated/releases/")
            stored = frame.to_dicts() if not frame.is_empty() else []
            publish_macro(macro_rows(stored), datetime.now(UTC).isoformat(timespec="seconds"))
            return {"status": "success", "mode": "links", **publish_release_links(stored)}

        now = datetime.now(UTC)
        release_ts = now.isoformat(timespec="seconds")
        end_year = now.year
        start_year = end_year - BACKFILL_YEARS
        day = now.date().isoformat()
        run_key = getattr(context, "aws_request_id", None) or "run"
        today = now.astimezone(ET).date()
        latest_rows: dict[str, list] = {"rows": []}
        nowcast: list[dict] = []
        ics_dates: list[str] = []
        fred_rows: list[dict] = []

        def fetch_prints():
            found: list[dict] = []
            with source_run("bls") as rec, get_client() as http:
                payload = request_with_retry(
                    http, "POST", BLS_URL, json=bls_body(start_year, end_year, api_key("bls"))
                ).json()
                write_json(payload, f"raw/releases/bls/date={day}/{run_key}.json")
                parsed = parse_bls(payload, release_ts)
                rec["rows"] = len(parsed)
                found.extend(parsed)
            years = ",".join(str(year) for year in range(start_year, end_year + 1))
            with source_run("bea") as rec, get_client() as http:
                payload = request_with_retry(
                    http,
                    "GET",
                    BEA_URL,
                    params={
                        "UserID": api_key("bea"),
                        "method": "GetData",
                        "DataSetName": "NIPA",
                        "TableName": "T20804",
                        "Frequency": "M",
                        "Year": years,
                        "ResultFormat": "JSON",
                    },
                ).json()
                write_json(payload, f"raw/releases/bea/date={day}/{run_key}.json")
                parsed = parse_bea(payload, release_ts)
                rec["rows"] = len(parsed)
                found.extend(parsed)
            with source_run("census") as rec, get_client() as http:
                try:
                    payload = request_with_retry(
                        http,
                        "GET",
                        CENSUS_URL,
                        params=census_params(start_year, today, api_key("census")),
                    ).json()
                except httpx.HTTPStatusError as exc:
                    raise_without_url(exc, "Census")
                if isinstance(payload, dict):
                    payload = redact_api_key(payload, api_key("census"))
                write_json(payload, f"raw/releases/census/date={day}/{run_key}.json")
                parsed = parse_census(payload, release_ts)
                rec["rows"] = len(parsed)
                rec["coverage"] = f"goods_rows={len(parsed)}"
                found.extend(parsed)
            latest_rows["rows"] = found
            return found

        with source_run("bls-ics") as rec, get_client() as http:
            try:
                text = request_with_retry(http, "GET", BLS_ICS_URL).text
            except httpx.HTTPStatusError as exc:
                raise_without_url(exc, "BLS calendar")
            write_json({"ics": text}, f"raw/releases/bls-ics/date={day}/{run_key}.json")
            ics_dates = parse_ics(text)
            rec["rows"] = len(ics_dates)
            rec["coverage"] = f"cpi_dates={len(ics_dates)}"
        with source_run("fred") as rec, get_client() as http:
            payload = request_with_retry(
                http,
                "GET",
                FRED_URL,
                params={
                    "api_key": api_key("fred"),
                    "file_type": "json",
                    "include_release_dates_with_no_data": "true",
                    "limit": 1000,
                    "sort_order": "asc",
                },
            ).json()
            write_json(payload, f"raw/releases/fred/date={day}/{run_key}.json")
            fred_rows = parse_fred_dates(payload)
            rec["rows"] = len(fred_rows)
        vintages: dict[str, dict[str, str]] = {}
        vintage_start = date(start_year, 1, 1)
        fred_key = api_key("fred")
        with get_client() as http:
            for fred_id, series_ids in VINTAGE_SERIES:
                with source_run(f"fred-vintage-{fred_id}") as rec:
                    payload = _fred_json(
                        http,
                        FRED_OBSERVATIONS,
                        fred_vintage_params(fred_id, fred_key, vintage_start),
                        fred_id,
                    )
                    safe = redact_api_key(payload, fred_key)
                    write_json(safe, f"raw/releases/fred-vintages/series={fred_id}/date={day}/{run_key}.json")
                    periods = first_release_dates(safe)
                    rec["rows"] = len(periods)
                    for series_id in series_ids:
                        vintages[series_id] = periods
        with source_run("cleveland") as rec, get_client() as http:
            html = request_with_retry(http, "GET", CLEVELAND_PAGE).text
            write_json({"html": html}, f"raw/releases/cleveland/date={day}/{run_key}.json")
            nowcast = parse_cleveland_html(html, today)
            rec["rows"] = len(nowcast)

        existing_frame = read_parquet_prefix("curated/releases/")
        existing = existing_frame.to_dicts() if not existing_frame.is_empty() else []
        known = {(row.get("series_id"), row.get("period")) for row in existing}
        # One invocation waits a single extra two minutes. Follow-up at() schedules cover the rest
        # of the 20-minute window. A calendar refresh only needs one pass.
        attempts = 1 if (event or {}).get("source") in {"calendar", "schedule"} else 2
        fresh = poll_for_new_period(fetch_prints, known, attempts=attempts, pause=time.sleep)
        merged = merge_releases(existing, latest_rows["rows"] or fresh)
        merged = apply_vintage_dates(merged, vintages)
        merged = enrich_releases(apply_nowcast(merged, nowcast, known))
        if merged:
            frame = release_frame(merged).with_columns(pl.col("period").str.slice(0, 4).alias("_year"))
            grouped = frame.partition_by(["series_id", "_year"], as_dict=True)
            for (series_id, year), part in grouped.items():
                key = f"curated/releases/series={series_id}/year={int(year)}/releases.parquet"
                write_parquet(part.drop("_year"), key)
        macro = macro_rows(merged)
        if macro:
            publish_macro(macro, now.isoformat(timespec="seconds"))
        published = publish_release_links(merged)
        upcoming = upcoming_calendar(calendar_rows(ics_dates, fred_rows), today)
        stored = read_json("curated/release_calendar/upcoming.json")
        if not upcoming and isinstance(stored, list):
            upcoming = stored
        write_json(upcoming, "curated/release_calendar/upcoming.json")
        role = os.getenv("SCHEDULER_ROLE_ARN")
        if role:
            import boto3

            # Bulkhead: the prints above are already stored, so a scheduler error marks M1 partial.
            with source_run("scheduler") as rec:
                planned = follow_ups_for(upcoming) + month_open_schedules(today)
                names = ensure_schedules(
                    boto3.client("scheduler"),
                    future_schedules(planned, now),
                    getattr(context, "invoked_function_arn", ""),
                    role,
                )
                rec["rows"] = len(names)
                logger.info("release_schedules_armed", extra={"count": len(names)})
        return {"status": "success" if fresh else "skipped", "new_periods": len(fresh), **published}

    return run(event, context)


def follow_ups_for(upcoming: list[dict]) -> list[dict]:
    planned = []
    for row in upcoming:
        day = str(row.get("release_ts") or "")[:10]
        series = str(row.get("series") or "")
        if len(day) == 10 and series:
            planned.extend(follow_up_schedules([day], series))
    return planned
