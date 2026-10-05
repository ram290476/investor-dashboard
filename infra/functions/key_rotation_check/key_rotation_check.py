"""Daily API-key rotation check (IA-5 authenticator management).

Reads every SecureString under KEY_PATH (default /invdash/api-keys/), works out when
each key is due from its last change date and its RotationDays tag, logs one JSON
line per key (audit evidence, AU-2/AU-3), and emails the security topic when a key:

- is overdue (every day until it is rotated),
- is due in exactly one of REMIND_DAYS days (default 14, 7, 3, 1, 0), or
- still holds the Terraform placeholder (Mondays only).

The secret values are never read; only parameter metadata and tags.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import boto3

ssm = boto3.client("ssm")
sns = boto3.client("sns")

KEY_PATH = os.environ.get("KEY_PATH", "/invdash/api-keys").rstrip("/")
TOPIC_ARN = os.environ["TOPIC_ARN"]
PROJECT = os.environ.get("PROJECT", "invdash")
REMIND_DAYS = sorted({int(d) for d in os.environ.get("REMIND_DAYS", "14,7,3,1,0").split(",")}, reverse=True)
DEFAULT_ROTATION_DAYS = 365


def _tags(name: str) -> dict[str, str]:
    resp = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=name)
    return {t["Key"]: t["Value"] for t in resp.get("TagList", [])}


def key_statuses(now: datetime) -> list[dict]:
    rows = []
    pages = ssm.get_paginator("describe_parameters").paginate(
        ParameterFilters=[{"Key": "Path", "Option": "Recursive", "Values": [KEY_PATH]}]
    )
    for page in pages:
        for p in page["Parameters"]:
            tags = _tags(p["Name"])
            rotation_days = int(tags.get("RotationDays", DEFAULT_ROTATION_DAYS))
            last = p["LastModifiedDate"]
            due = last + timedelta(days=rotation_days)
            days_left = (due.date() - now.date()).days
            never_set = p.get("Version", 1) == 1  # version 1 is Terraform's placeholder
            if never_set:
                status = "NOT_SET"
            elif days_left < 0:
                status = "OVERDUE"
            elif days_left <= REMIND_DAYS[0]:
                status = "DUE_SOON"
            else:
                status = "OK"
            rows.append(
                {
                    "key": p["Name"].rsplit("/", 1)[-1],
                    "parameter": p["Name"],
                    "provider": tags.get("Provider", "?"),
                    "sources": tags.get("Sources", ""),
                    "rotation_days": rotation_days,
                    "last_rotated": None if never_set else last.date().isoformat(),
                    "due": None if never_set else due.date().isoformat(),
                    "days_left": None if never_set else days_left,
                    "status": status,
                    "regenerate_url": tags.get("RegenerateUrl", ""),
                }
            )
    return sorted(rows, key=lambda r: (r["days_left"] is None, r["days_left"] if r["days_left"] is not None else 0))


def needs_email(row: dict, now: datetime) -> bool:
    if row["status"] == "OVERDUE":
        return True
    if row["status"] == "NOT_SET":
        return now.weekday() == 0  # Monday nag until the key is stored
    return row["days_left"] in REMIND_DAYS


def format_message(rows: list[dict]) -> tuple[str, str]:
    counts = {s: sum(r["status"] == s for r in rows) for s in ("OVERDUE", "DUE_SOON", "NOT_SET")}
    parts = [
        f"{n} {label}"
        for label, n in (
            ("overdue", counts["OVERDUE"]),
            ("due soon", counts["DUE_SOON"]),
            ("not stored", counts["NOT_SET"]),
        )
        if n
    ]
    subject = f"[{PROJECT}] API key rotation: " + ", ".join(parts or ["reminder"])
    lines = ["These provider API keys need attention:", ""]
    for r in rows:
        when = (
            "never stored"
            if r["status"] == "NOT_SET"
            else (
                f"{-r['days_left']} days OVERDUE (was due {r['due']})"
                if r["status"] == "OVERDUE"
                else f"due {r['due']} ({r['days_left']} days)"
            )
        )
        first = r["status"] == "NOT_SET"
        lines += [
            f"- {r['key']} ({r['provider']}; sources {r['sources']}): {when}",
            f"    1. {'Get a key' if first else 'Regenerate'} at {r['regenerate_url'] or 'the provider dashboard'}",
            f"    2. Store it: scripts/rotate-key.sh {r['key']}",
        ]
        if not first:
            lines.append("    3. Revoke the old key after the next job run succeeds")
    lines += ["", "Rotation schedule and runbook: Investor Dashboard architecture doc, 'API key rotation'."]
    return subject[:100], "\n".join(lines)


def handler(event, context):
    now = datetime.now(UTC)
    rows = key_statuses(now)
    for r in rows:
        print(json.dumps({"event": "key_rotation_status", **r}))  # 400-day log retention = rotation evidence
    to_send = [r for r in rows if needs_email(r, now)]
    if to_send:
        subject, body = format_message(to_send)
        sns.publish(TopicArn=TOPIC_ARN, Subject=subject, Message=body)
    summary = {s: sum(r["status"] == s for r in rows) for s in ("OK", "DUE_SOON", "OVERDUE", "NOT_SET")}
    print(json.dumps({"event": "key_rotation_summary", "emailed": len(to_send), **summary}))
    return summary
