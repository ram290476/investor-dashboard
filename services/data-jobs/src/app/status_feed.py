"""Read-only 'data refresh' status feed for the site's account menu.

serving/status.json, rebuilt on every 'Job Finished' event:
    {"generated_at": ISO, "jobs": [{"job", "name", "status", "last_run", "last_outcome",
                                     "failed_sources", "next_run", "trigger", "last_links_run"}]}
A links-only M1 run (detail.mode == "links") sets last_links_run and leaves last_run.
Tickers past the cap are stored as over_cap "over the ticker cap".
status: "ok" | "partial" (some sources failed) | "failed" | "skipped" | "never_run".
next_run: next scheduled start (ISO, UTC) from the job's schedule, skipping weekends
and NYSE / US federal holidays per the job's calendar; null when the job has no cron.
trigger: why an event-driven job runs, or null when it has a cron or no schedule yet.

Updates use S3 conditional writes (If-Match on the ETag), so two jobs finishing at
once can't overwrite each other's entries.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from croniter import croniter

ET = ZoneInfo("America/New_York")

# job -> (display name, cron in ET or None, holiday calendar, trigger or None)
# A job has a cron or a trigger, except KALSHI, which stays unset until its terms are accepted (#89).
JOBS: dict[str, tuple[str, str | None, str, str | None]] = {
    "H1": ("Market prices", "5 10-16 * * 1-5", "nyse", None),
    "H2": ("News & sentiment", "15 * * * 1-5", "nyse", None),
    "H3": ("Regulatory & company feeds", "25 6-22 * * 1-5", "federal", None),
    "D1": ("Morning macro & calendars", "0 7 * * 1-5", "federal", None),
    "D4": ("Market close", "45 16 * * 1-5", "nyse", None),
    "D5": ("Government contracts", "45 17 * * 1-5", "federal", None),
    "M1": ("Release-day data & calendar", "35 6 * * 1-5", "federal", None),
    "Q1": ("Quarterly fundamentals", "30 8 * * 1", "nyse", None),
    # 07:15 ET matches the EventBridge schedule (America/New_York), not 07:15 UTC.
    "Q2C": ("Company IR collect", "15 7 * * 1-5", "federal", None),
    "Q2X": ("Company IR extract", None, "federal", "After company IR collect"),
    "Q2S": ("Company metrics serve", None, "federal", "After company IR extract"),
    "SHORT": ("Short interest", "30 18 * * 1-5", "nyse", None),
    "OPTIONS": ("Options put/call and IV", "50 16 * * 1-5", "nyse", None),
    "TREND": ("Trend metrics", None, "nyse", "After market close and release day"),
    "BACKFILL": ("Ticker history backfill", None, "nyse", "When a ticker is added"),
    "RECONCILE": ("Split & dividend adjustment", "15 19 * * 1-5", "nyse", None),
    # No cron until Ram accepts the Kalshi terms (issue #89). A next_run would advertise a schedule that is not enabled.
    "KALSHI": ("FOMC odds (Kalshi)", None, "federal", None),
}

# Jobs with neither a cron nor a trigger. KALSHI is waiting on Ram (#89).
UNSCHEDULED = frozenset({"KALSHI"})


def _holidays(calendar: str, years: range) -> set[date]:
    try:
        import holidays as hol
    except ImportError:  # library missing: weekends only
        return set()
    if calendar == "nyse":
        return set(hol.financial_holidays("NYSE", years=list(years)).keys())
    return set(hol.US(years=list(years)).keys())


def next_run(job: str, now: datetime) -> str | None:
    _name, cron, cal, _trigger = JOBS[job]
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
        previous = by_job.get(job, {})
        if detail.get("mode") == "links":
            # A links rebuild is not a release-day collection, so last_run stays put.
            entry = {
                "status": "never_run",
                "last_run": None,
                "last_outcome": None,
                "failed_sources": 0,
                **previous,
                "job": job,
                "last_links_run": now.isoformat(timespec="seconds"),
            }
        else:
            failed = int(detail.get("failed_sources") or 0)
            outcome = detail.get("outcome", "success")
            status = (
                "failed" if outcome in {"failure", "failed"}
                else "partial" if outcome == "partial" or failed
                else "skipped" if detail.get("status") == "skipped" else "ok"
            )
            entry = {
                **previous,
                "job": job,
                "last_run": now.isoformat(timespec="seconds"),
                "last_outcome": outcome,
                "status": status,
                "failed_sources": failed,
                "failed_source_ids": list(detail.get("failed_source_ids") or []),
                "reason": detail.get("reason"),
            }
        if "dropped" in detail:
            dropped = list(detail.get("dropped") or [])
            if dropped:
                entry["dropped"] = dropped
                entry["over_cap"] = "over the ticker cap"
            else:
                entry.pop("dropped", None)
                entry.pop("over_cap", None)
        by_job[job] = entry
    jobs = []
    for j, (name, _cron, _cal, trigger) in JOBS.items():
        entry = by_job.get(j, {"job": j, "status": "never_run", "last_run": None, "last_outcome": None})
        entry.update({"name": name, "next_run": next_run(j, now), "trigger": trigger})
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
