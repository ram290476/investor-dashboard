"""M1: CPI, PCE and trade on release morning, plus a stopgap calendar until D1 exists.

The job has no cron. It writes one-off EventBridge Scheduler at() times in UTC from
America/New_York release instants, then polls until a new period shows up.
curated/macro_daily is written before the handler returns, so the Job Finished
event can start trend-metrics.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl

JOB_ID = "M1"
ET = ZoneInfo("America/New_York")
CPI_SERIES = ("CUSR0000SA0", "CUSR0000SA0L1E")
POLL_ATTEMPTS = 10
POLL_SECONDS = 120
BACKFILL_YEARS = 5
SCHEDULE_PREFIX = "invdash"
TREND_TRIGGERS = frozenset({"D4", "M1", "RECONCILE"})
# Four minutes apart: each run waits one extra two-minute poll, and 300s cannot hold a 20-minute loop.
FOLLOW_UP_MINUTES = (0, 4, 8, 12, 16)
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BEA_URL = "https://apps.bea.gov/api/data"
CENSUS_URL = "https://api.census.gov/data/timeseries/eits/ftd"
FRED_URL = "https://api.stlouisfed.org/fred/releases/dates"
BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/cpi.ics"
CLEVELAND_PAGE = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
MEASURE_SERIES = {"CPI": "CUSR0000SA0", "Core CPI": "CUSR0000SA0L1E", "PCE": "PCE"}
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
    return detail.get("outcome") == "success" and detail.get("job") in TREND_TRIGGERS


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


def parse_census(payload, release_ts: str) -> list[dict]:
    """Census foreign-trade goods: imports and exports, one series each."""
    if isinstance(payload, dict):
        raise ValueError(str(payload.get("error") or payload)[:200])
    if not isinstance(payload, list) or len(payload) < 2:
        return []
    header = [str(name) for name in payload[0]]
    rows = []
    for raw in payload[1:]:
        record = dict(zip(header, raw, strict=False))
        period = str(record.get("time") or "")[:7]
        if len(period) != 7:
            continue
        for column, series_id in (("IMPG", "CENSUS_IMPG"), ("EXPG", "CENSUS_EXPG")):
            actual = _number(record.get(column))
            if actual is None:
                continue
            rows.append(_row(series_id, period, release_ts, actual, "census", "usd"))
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


def apply_nowcast(rows: list[dict], nowcast_rows: list[dict]) -> list[dict]:
    latest_pct: dict[str, float] = {}
    for item in nowcast_rows:
        series_id = MEASURE_SERIES.get(str(item.get("measure") or ""))
        if series_id and isinstance(item.get("value"), (int, float)):
            latest_pct[series_id] = float(item["value"])
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("series_id")), []).append(row)
    for series_id, group in grouped.items():
        pct = latest_pct.get(series_id)
        if pct is None:
            continue
        ordered = sorted(group, key=lambda item: str(item.get("period") or ""))
        latest = ordered[-1]
        if latest.get("consensus") is not None:
            continue
        prior = latest.get("prior")
        if prior is None and len(ordered) >= 2:
            prior = ordered[-2].get("actual")
        agreed = consensus_index(prior, pct)
        if agreed is None:
            continue
        latest["consensus"] = agreed
        latest["surprise"] = surprise(latest.get("actual"), agreed)
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


def macro_rows(releases: list[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    for row in releases:
        series_id = row.get("series_id")
        period = str(row.get("period") or "")
        if not series_id or not isinstance(row.get("actual"), (int, float)):
            continue
        current = latest.get(series_id)
        if current is None or period > current["period"]:
            latest[series_id] = row
    return [
        {"series_id": row["series_id"], "obs_date": f"{row['period']}-01", "value": float(row["actual"])}
        for row in latest.values()
    ]


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

    @job_handler(JOB_ID)
    def run(event, context):
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
                payload = request_with_retry(
                    http,
                    "GET",
                    CENSUS_URL,
                    params={
                        "get": "time,IMPG,EXPG",
                        "time": f"from {start_year}-01",
                        "key": api_key("census"),
                    },
                ).json()
                write_json(payload, f"raw/releases/census/date={day}/{run_key}.json")
                parsed = parse_census(payload, release_ts)
                rec["rows"] = len(parsed)
                found.extend(parsed)
            latest_rows["rows"] = found
            return found

        with source_run("bls-ics") as rec, get_client() as http:
            text = request_with_retry(http, "GET", BLS_ICS_URL).text
            write_json({"ics": text}, f"raw/releases/bls-ics/date={day}/{run_key}.json")
            ics_dates = parse_ics(text)
            rec["rows"] = len(ics_dates)
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
        attempts = 1 if (event or {}).get("source") == "calendar" else 2
        fresh = poll_for_new_period(fetch_prints, known, attempts=attempts, pause=time.sleep)
        merged = merge_releases(existing, latest_rows["rows"] or fresh)
        merged = enrich_releases(apply_nowcast(merged, nowcast))
        if merged:
            frame = release_frame(merged).with_columns(pl.col("period").str.slice(0, 4).alias("_year"))
            grouped = frame.partition_by(["series_id", "_year"], as_dict=True)
            for (series_id, year), part in grouped.items():
                key = f"curated/releases/series={series_id}/year={int(year)}/releases.parquet"
                write_parquet(part.drop("_year"), key)
        macro = macro_rows(merged)
        if macro:
            write_parquet(pl.DataFrame(macro), f"curated/macro_daily/date={day}/macro.parquet")
        upcoming = upcoming_calendar(calendar_rows(ics_dates, fred_rows), today)
        stored = read_json("curated/release_calendar/upcoming.json")
        if not upcoming and isinstance(stored, list):
            upcoming = stored
        write_json(upcoming, "curated/release_calendar/upcoming.json")
        role = os.getenv("SCHEDULER_ROLE_ARN")
        if role:
            import boto3

            planned = follow_ups_for(upcoming) + month_open_schedules(today)
            names = ensure_schedules(
                boto3.client("scheduler"),
                future_schedules(planned, now),
                getattr(context, "invoked_function_arn", ""),
                role,
            )
            logger.info("release_schedules_armed", extra={"count": len(names)})
        return {"status": "success" if fresh else "skipped", "new_periods": len(fresh)}

    return run(event, context)


def follow_ups_for(upcoming: list[dict]) -> list[dict]:
    planned = []
    for row in upcoming:
        day = str(row.get("release_ts") or "")[:10]
        series = str(row.get("series") or "")
        if len(day) == 10 and series:
            planned.extend(follow_up_schedules([day], series))
    return planned
