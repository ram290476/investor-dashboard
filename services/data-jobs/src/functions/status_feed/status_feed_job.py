"""status_feed job: rebuild serving/status.json on every 'Job Finished' event (see app/status_feed.py)."""

from __future__ import annotations

import os


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from lake import s3
    from status_feed import update_feed

    detail = event.get("detail") or {}
    if detail.get("job") == "STATUS":  # never react to our own events
        return {"skipped": True}
    feed = update_feed(s3(), os.environ["LAKE_BUCKET"], detail)
    return {"jobs": len(feed["jobs"])}
