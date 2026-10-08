"""D1: daily FRED macro series the trend job reads.

07:00 ET on weekdays. Federal holidays and weekends are a no-op. The first run
backfills five years; later runs start ten days before the watermark so a
revised print overwrites the prior value. Rows land in raw/fred/ and
curated/macro_daily/source=fred/series_id=<S>/macro.parquet.

The API key is read at runtime and is never written to the lake or to a log line.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from trend_metrics import DRIVERS

ET = ZoneInfo("America/New_York")
FRED_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"
BACKFILL_YEARS = 5
REVISION_DAYS = 10
SOURCE = "fred"
STATE_KEY = "curated/macro_daily/source=fred/_state.json"
# Monthly YoY prints belong to M1. ETF proxies belong to the daily price job.
DAILY_FRED_SERIES: tuple[str, ...] = tuple(
    sid for sid in DRIVERS if not sid.startswith("ETF:") and not sid.endswith("_YOY")
)
_API_KEY_QUERY = re.compile(r"(api_key=)[^&\s\"]*")


def today_et() -> date:
    return datetime.now(ET).date()


def is_federal_business_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    import holidays

    return day not in holidays.US(years=[day.year])


def next_federal_business_day(day: date) -> date:
    """First federal business day strictly after `day` (the day the print is public)."""
    import holidays

    closed = holidays.US(years=[day.year, day.year + 1])
    nxt = day + timedelta(days=1)
    while nxt.weekday() >= 5 or nxt in closed:
        nxt += timedelta(days=1)
    return nxt


def observation_start(last_obs: date | None, today: date) -> date:
    """Five years on the first run; otherwise the watermark minus the revision window."""
    if last_obs is None:
        try:
            return today.replace(year=today.year - BACKFILL_YEARS)
        except ValueError:
            return today.replace(year=today.year - BACKFILL_YEARS, day=28)
    return last_obs - timedelta(days=REVISION_DAYS)


def parse_observations(payload: dict, series_id: str) -> list[dict]:
    """FRED '.' and any non-finite value are missing and are dropped."""
    rows = []
    for item in payload.get("observations") or []:
        raw = str(item.get("value") if item.get("value") is not None else "").strip()
        day = str(item.get("date") or "")[:10]
        if len(day) != 10 or raw in {"", "."}:
            continue
        try:
            value = float(raw)
            obs = date.fromisoformat(day)
        except ValueError:
            continue
        if value != value or value in {float("inf"), float("-inf")}:
            continue
        rows.append(
            {
                "series_id": series_id,
                "obs_date": obs,
                "value": value,
                "available_date": next_federal_business_day(obs),
                "source": SOURCE,
            }
        )
    return rows


def redact_secrets(payload: dict, secret: str) -> dict:
    """Drop the key from anything persisted. FRED receives it only as a query parameter."""
    text = json.dumps(payload, default=str)
    if secret:
        text = text.replace(secret, "")
    return json.loads(_API_KEY_QUERY.sub(r"\1", text))


def load_watermarks(state: dict | None) -> dict[str, date]:
    raw = (state or {}).get("series") or {}
    marks: dict[str, date] = {}
    if not isinstance(raw, dict):
        return marks
    for series_id, value in raw.items():
        text = str(value)[:10]
        if len(text) == 10:
            try:
                marks[str(series_id)] = date.fromisoformat(text)
            except ValueError:
                continue
    return marks


def curated_key(series_id: str) -> str:
    return f"curated/macro_daily/source=fred/series_id={series_id}/macro.parquet"


def curated_frame(rows: list[dict], ingested_at: str) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(
        pl.col("obs_date").cast(pl.Date),
        pl.col("available_date").cast(pl.Date),
        pl.col("value").cast(pl.Float64),
        pl.lit(SOURCE).alias("source"),
        pl.lit(ingested_at).alias("ingested_at"),
    )


def advance_watermark(previous: date | None, rows: list[dict]) -> date | None:
    if not rows:
        return previous
    latest = max(row["obs_date"] for row in rows)
    if previous is None or latest > previous:
        return latest
    return previous


def _fetch_observations(http, series_id: str, start: date, end: date, key: str) -> dict:
    """GET one series. Failures are re-raised without the request URL, which carries the key."""
    try:
        response = httpx_get(http, series_id, start, end, key)
        return response.json()
    except (httpx.HTTPError, RuntimeError, ValueError, TypeError):
        raise RuntimeError(f"FRED observations request failed for {series_id}") from None


def httpx_get(http, series_id: str, start: date, end: date, key: str):
    from http_client import request_with_retry

    return request_with_retry(
        http,
        "GET",
        FRED_OBSERVATIONS,
        params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
        },
    )


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from datetime import UTC

    from api_keys import api_key
    from http_client import get_client
    from lake import read_json, upsert_ranked, write_json
    from observability import job_handler, logger, source_run

    @job_handler("D1")
    def run(event, context):
        today = today_et()
        if not is_federal_business_day(today):
            logger.info("federal_calendar_skip", extra={"job": "D1", "date": today.isoformat()})
            return {"status": "skipped", "reason": "federal holiday or weekend", "date": today.isoformat()}

        key = api_key("fred")
        ingested_at = datetime.now(UTC).isoformat(timespec="seconds")
        run_key = getattr(context, "aws_request_id", None) or "run"
        state = read_json(STATE_KEY) or {}
        marks = load_watermarks(state if isinstance(state, dict) else None)
        failed = 0
        stored_rows = 0
        stored_series = 0

        with get_client() as http:
            for series_id in DAILY_FRED_SERIES:
                start = observation_start(marks.get(series_id), today)
                with source_run(f"fred:{series_id}") as rec:
                    payload = _fetch_observations(http, series_id, start, today, key)
                    safe = redact_secrets(payload, key)
                    write_json(safe, f"raw/fred/series_id={series_id}/date={today.isoformat()}/{run_key}.json")
                    rows = parse_observations(safe, series_id)
                    rec["rows"] = len(rows)
                    if rows:
                        upsert_ranked(
                            curated_frame(rows, ingested_at),
                            curated_key(series_id),
                            ["series_id", "obs_date"],
                            "ingested_at",
                        )
                        stored_rows += len(rows)
                        stored_series += 1
                    advanced = advance_watermark(marks.get(series_id), rows)
                    if advanced is not None:
                        marks[series_id] = advanced
                if rec["outcome"] == "failure":
                    failed += 1

        write_json({"series": {sid: day.isoformat() for sid, day in sorted(marks.items())}}, STATE_KEY)
        if failed and stored_series == 0:
            raise RuntimeError(f"FRED collection failed for {failed} series")
        return {
            "status": "success",
            "date": today.isoformat(),
            "series": stored_series,
            "rows_stored": stored_rows,
            "failed": failed,
        }

    return run(event, context)
