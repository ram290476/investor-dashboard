"""Read-only 'data refresh' status feed for the site's account menu.

serving/status.json, rebuilt on every 'Job Finished' event:
    {"generated_at": ISO, "jobs": [{"job", "name", "status", "last_run", "last_outcome",
                                     "failed_sources", "next_run"}]}
status: "ok" | "partial" (some sources failed) | "failed" | "never_run".
next_run: next scheduled start (ISO, UTC) from the job's schedule, skipping weekends
and NYSE / US federal holidays per the job's calendar; null for event-driven jobs.

Updates use S3 conditional writes (If-Match on the ETag), so two jobs finishing at
once can't overwrite each other's entries.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from croniter import croniter

ET = ZoneInfo("America/New_York")

# job -> (display name, cron in ET or None for event-driven, holiday calendar)
JOBS: dict[str, tuple[str, str | None, str]] = {
    "H1": ("Market prices", "5 10-16 * * 1-5", "nyse"),
    "H2": ("News & sentiment", "15 * * * 1-5", "nyse"),
    "H3": ("Regulatory & company feeds", "25 6-22 * * 1-5", "federal"),
    "D1": ("Morning macro & calendars", "0 7 * * 1-5", "federal"),
    "D2": ("Regulatory & operations sweep", "30 7 * * 1-5", "federal"),
    "D3": ("Overnight rates", "15 9 * * 1-5", "federal"),
    "D4": ("Market close", "45 16 * * 1-5", "federal"),
    "D5": ("Government contracts", "45 17 * * 1-5", "federal"),
    "W1": ("Weekly sweep", "0 8 * * 1", "federal"),
    "M1": ("Release-day data", None, "federal"),
    "Q1": ("Quarterly fundamentals", None, "nyse"),
    "SHORT": ("Short interest", "30 18 * * 1-5", "nyse"),
    "OPTIONS": ("Options put/call and IV", "50 16 * * 1-5", "nyse"),
    "TREND": ("Trend metrics", None, "nyse"),
    "BACKFILL": ("Ticker history backfill", None, "nyse"),
}


def _holidays(calendar: str, years: range) -> set[date]:
    try:
        import holidays as hol
    except ImportError:  # library missing: weekends only
        return set()
    if calendar == "nyse":
        return set(hol.financial_holidays("NYSE", years=list(years)).keys())
    return set(hol.US(years=list(years)).keys())


def next_run(job: str, now: datetime) -> str | None:
    _name, cron, cal = JOBS[job]
    if not cron:
        return None
    off = _holidays(cal, range(now.year, now.year + 2))
    it = croniter(cron, now.astimezone(ET))
    for _ in range(500):
        nxt = it.get_next(datetime)
        if nxt.date() not in off:
            return nxt.astimezone(UTC).isoformat(timespec="minutes")
    return None


def apply_event(feed: dict | None, detail: dict, now: datetime) -> dict:
    """Merge one 'Job Finished' event detail into the feed and refresh every next_run."""
    feed = feed or {"jobs": []}
    by_job = {j["job"]: j for j in feed.get("jobs", [])}
    job = detail.get("job")
    if job in JOBS:
        failed = int(detail.get("failed_sources") or 0)
        outcome = detail.get("outcome", "success")
        status = "failed" if outcome != "success" else ("partial" if failed else "ok")
        by_job[job] = {
            **by_job.get(job, {}),
            "job": job,
            "last_run": now.isoformat(timespec="seconds"),
            "last_outcome": outcome,
            "status": status,
            "failed_sources": failed,
        }
    jobs = []
    for j, (name, _, _) in JOBS.items():
        entry = by_job.get(j, {"job": j, "status": "never_run", "last_run": None, "last_outcome": None})
        entry.update({"name": name, "next_run": next_run(j, now)})
        entry.setdefault("failed_sources", 0)
        jobs.append(entry)
    return {"generated_at": now.isoformat(timespec="seconds"), "jobs": jobs}


def update_feed(s3, bucket: str, detail: dict, key: str = "serving/status.json", attempts: int = 5) -> dict:
    """Read-modify-write with If-Match / If-None-Match so concurrent updates retry instead of clobbering."""
    for _ in range(attempts):
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            current, etag = json.loads(obj["Body"].read()), obj["ETag"]
        except s3.exceptions.NoSuchKey:
            current, etag = None, None
        feed = apply_event(current, detail, datetime.now(UTC))
        body = json.dumps(feed, separators=(",", ":")).encode()
        cond = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
        try:
            s3.put_object(
                Bucket=bucket, Key=key, Body=body, ContentType="application/json", CacheControl="max-age=30", **cond
            )
            return feed
        except s3.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] not in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise
    raise RuntimeError("status.json kept changing; gave up after retries")
