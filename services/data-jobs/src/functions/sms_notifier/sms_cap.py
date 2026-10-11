"""Per-issue daily cap for SMS alerts.

One issue sends at most ``limit`` texts per calendar day in America/Los_Angeles.
The issue key is the CloudWatch alarm name, or a job plus its error code, so a
flapping alarm does not text on every state change. Email is not capped here.

The dedupe store is consulted before a text is sent. If the store cannot be
read or updated, the text is sent anyway (fail open) and the failure is logged.
A failed publish does not consume the day's slot, so a retry can still send.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")

_ALARM_SUBJECT = re.compile(r'(?:ALARM|OK|INSUFFICIENT_DATA):\s*"([^"]+)"')
_ALARM_NAME = re.compile(r"(?m)^-\s*Name:\s+(\S+)")
_GENERIC_SUBJECTS = {"AWS Notification Message", "AWS Notification"}


class CapStoreError(Exception):
    """The dedupe store could not be read or updated. Callers fail open and send."""


@dataclass(frozen=True)
class Decision:
    send: bool
    reason: str
    issue_key: str
    day: str = ""
    suppressed_prior: int = 0
    suppressed_today: int = 0
    ttl: int = 0


class MemoryStore:
    """In-memory stand-in for the DynamoDB counter. Tests use the failure flags."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.fail_reads = False
        self.fail_record_sent = False
        self.fail_suppress = False

    def get(self, issue_key: str) -> dict[str, Any] | None:
        if self.fail_reads:
            raise CapStoreError("read failed")
        item = self.items.get(issue_key)
        return None if item is None else dict(item)

    def record_sent(self, issue_key: str, day: str, ttl: int) -> None:
        if self.fail_record_sent:
            raise CapStoreError("write failed")
        current = self.items.get(issue_key)
        if current is None or current["day"] != day:
            self.items[issue_key] = {"day": day, "sent": 1, "suppressed": 0, "expires_at": ttl}
            return
        current["sent"] = int(current["sent"]) + 1

    def record_suppressed(self, issue_key: str, day: str) -> None:
        if self.fail_suppress:
            raise CapStoreError("suppress failed")
        current = self.items.get(issue_key)
        if current is None or current["day"] != day:
            raise CapStoreError("missing cap item")
        current["suppressed"] = int(current.get("suppressed", 0)) + 1


def calendar_day(now: datetime) -> str:
    """Pacific calendar date. A naive timestamp is rejected so UTC is not assumed."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(PACIFIC).date().isoformat()


def ttl_epoch(day: str) -> int:
    """Keep the counter through the next two Pacific midnights so the next text can report it."""
    start = datetime.combine(date.fromisoformat(day), time.min, tzinfo=PACIFIC)
    return int((start + timedelta(days=3)).timestamp())


def issue_key(subject: str, message: str) -> str:
    """Stable id for one problem. Alarm state (ALARM vs OK) is not part of the key."""
    subject = subject or ""
    message = message or ""
    match = _ALARM_SUBJECT.search(subject)
    if match:
        return _clip(f"alarm:{match.group(1)}")

    payload = _parse_payload(message)
    if payload is not None:
        alarm = payload.get("AlarmName")
        if alarm:
            return _clip(f"alarm:{alarm}")
        job = _job_key(payload)
        if job:
            return job
        routed = _routed_event_key(payload)
        if routed:
            return routed

    if "State Change" in message or "Alarm Description" in message:
        named = _ALARM_NAME.search(message)
        if named:
            return _clip(f"alarm:{named.group(1)}")

    if "API key rotation" in subject:
        return "key-rotation"

    basis = (subject.strip() + "\n" + message.split("\n--\n", 1)[0][:180]).encode()
    return "msg:" + hashlib.sha256(basis).hexdigest()[:16]


def format_sms(subject: str, message: str, suppressed_prior: int) -> str:
    """One short text. The next day's first text can name how many repeats were held back."""
    subject = (subject or "").strip()
    if subject and subject not in _GENERIC_SUBJECTS:
        line = subject
    else:
        line = _summary(message) or subject or "alert"
    line = re.sub(r"\s+", " ", line).strip()[:140]
    if suppressed_prior == 1:
        line += " (suppressed 1 repeat)"
    elif suppressed_prior > 1:
        line += f" (suppressed {suppressed_prior} repeats)"
    return line[:300]


def plan(store: MemoryStore, issue_key_value: str, now: datetime, limit: int) -> Decision:
    """Read the counter. Does not write, so a failed publish does not consume the slot."""
    day = calendar_day(now)
    ttl = ttl_epoch(day)
    cap = limit if limit >= 1 else 1
    item = store.get(issue_key_value)
    if item is None or str(item.get("day")) != day:
        prior = 0 if item is None else int(item.get("suppressed") or 0)
        return Decision(
            send=True,
            reason="sent",
            issue_key=issue_key_value,
            day=day,
            suppressed_prior=prior,
            ttl=ttl,
        )
    sent = int(item.get("sent") or 0)
    if sent < cap:
        return Decision(send=True, reason="sent", issue_key=issue_key_value, day=day, ttl=ttl)
    suppressed_today = int(item.get("suppressed") or 0) + 1
    return Decision(
        send=False,
        reason="suppressed",
        issue_key=issue_key_value,
        day=day,
        suppressed_today=suppressed_today,
        ttl=ttl,
    )


def deliver(
    subject: str,
    message: str,
    store: MemoryStore,
    publish: Callable[[str], None],
    *,
    limit: int,
    now: datetime,
) -> Decision:
    """Send or log one notification. Store errors publish the text and return fail_open."""
    key = issue_key(subject, message)
    try:
        decision = plan(store, key, now, limit)
    except CapStoreError:
        _log("sms_cap_failed_open", issue_key=key, error="store_read_failed")
        publish(format_sms(subject, message, 0))
        return Decision(send=True, reason="fail_open", issue_key=key)

    if not decision.send:
        try:
            store.record_suppressed(key, decision.day)
        except CapStoreError:
            _log("sms_cap_failed_open", issue_key=key, error="store_suppress_failed")
            publish(format_sms(subject, message, 0))
            return Decision(send=True, reason="fail_open", issue_key=key)
        _log(
            "sms_suppressed",
            issue_key=key,
            day=decision.day,
            suppressed=decision.suppressed_today,
        )
        return decision

    publish(format_sms(subject, message, decision.suppressed_prior))
    try:
        store.record_sent(key, decision.day, decision.ttl)
    except CapStoreError:
        _log("sms_cap_record_failed", issue_key=key, day=decision.day)
    else:
        _log(
            "sms_sent",
            issue_key=key,
            day=decision.day,
            suppressed_prior=decision.suppressed_prior,
            reason=decision.reason,
        )
    return decision


def _clip(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:240]


def _parse_payload(message: str) -> dict[str, Any] | None:
    text = (message or "").strip()
    if not text:
        return None
    first = text.splitlines()[0].strip()
    for candidate in (first, text):
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _job_key(payload: dict[str, Any]) -> str | None:
    candidates: list[dict[str, Any]] = [payload]
    detail = payload.get("detail")
    if isinstance(detail, dict):
        candidates.append(detail)
    for obj in candidates:
        job = obj.get("job")
        code = obj.get("code") or obj.get("error_code") or obj.get("error")
        if job and code:
            return _clip(f"job:{job}:{code}")
    return None


def _routed_event_key(payload: dict[str, Any]) -> str | None:
    detail_type = str(payload.get("detail-type") or "")
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    if detail_type == "GuardDuty Finding":
        return _clip(f"guardduty:{detail.get('type') or 'finding'}")
    if "Security Hub" in detail_type:
        findings = detail.get("findings") or []
        title = "finding"
        if findings and isinstance(findings[0], dict):
            title = str(findings[0].get("Title") or findings[0].get("GeneratorId") or title)
        return _clip(f"securityhub:{title}")
    if "Inspector" in detail_type:
        return _clip(f"inspector:{detail.get('title') or detail.get('findingArn') or 'finding'}")
    if detail.get("eventName"):
        params = detail.get("requestParameters") if isinstance(detail.get("requestParameters"), dict) else {}
        name = ""
        if isinstance(params, dict):
            name = str(params.get("name") or params.get("userName") or "")
        return _clip(f"event:{detail.get('eventName')}:{name}")
    event_code = detail.get("eventTypeCode")
    if event_code or payload.get("source") == "aws.health":
        return _clip(f"health:{event_code or detail.get('service') or 'event'}")
    return None


def _summary(message: str) -> str:
    payload = _parse_payload(message)
    if payload is None:
        first = (message or "").split("\n--\n", 1)[0].strip().splitlines()
        return first[0] if first else ""
    detail_type = str(payload.get("detail-type") or "")
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    if "Security Hub" in detail_type:
        findings = detail.get("findings") or []
        if findings and isinstance(findings[0], dict):
            return str(findings[0].get("Title") or detail_type)
    if detail_type == "GuardDuty Finding":
        return f"GuardDuty {detail.get('type') or 'finding'}"
    if "Inspector" in detail_type:
        return str(detail.get("title") or detail_type)
    if detail.get("eventName"):
        return str(detail.get("eventName"))
    alarm = payload.get("AlarmName")
    if alarm:
        state = str(payload.get("NewStateValue") or "").strip()
        return f"{state} {alarm}".strip()
    job = _job_key(payload)
    if job:
        return job.removeprefix("job:").replace(":", " ")
    return detail_type


def _log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, default=str))
