"""Per-issue hourly caps for email and SMS alerts.

Windows are fixed clock buckets in America/Los_Angeles, aligned to local
midnight. They are not a rolling interval from the first send. With the
default of 60 minutes, 14:00-14:59 Pacific is one window and 15:00 starts the
next, so an alert at 14:50 and another at 15:05 are different windows.

Email and SMS are counted separately. Each channel has its own window length
and its own maximum (defaults: 60 minutes and 1). One issue can therefore text
again inside an hour while mail for that issue is still held, when the SMS
window is shorter or its maximum is higher.

The dedupe item for a channel expires at the end of the following window for
that channel, long enough for the next window's first message to report how
many repeats were held back. If the store cannot be read or updated, that
channel is sent anyway (fail open). A failed publish does not consume the
slot, so a retry can still send.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")

_ALARM_SUBJECT = re.compile(r'(?:ALARM|OK|INSUFFICIENT_DATA):\s*"([^"]+)"')
_ALARM_NAME = re.compile(r"(?m)^-\s*Name:\s+(\S+)")
_GENERIC_SUBJECTS = {"AWS Notification Message", "AWS Notification"}
_CHANNELS = frozenset({"email", "sms"})


class CapStoreError(Exception):
    """The dedupe store could not be read or updated. Callers fail open and send."""


@dataclass(frozen=True)
class Decision:
    send: bool
    reason: str
    issue_key: str
    channel: str = "sms"
    window: str = ""
    suppressed_prior: int = 0
    suppressed_in_window: int = 0
    ttl: int = 0


class MemoryStore:
    """In-memory stand-in for the DynamoDB counter. Tests use the failure flags."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.fail_reads = False
        self.fail_record_sent = False
        self.fail_suppress = False

    def get(self, issue_key: str, channel: str) -> dict[str, Any] | None:
        if self.fail_reads:
            raise CapStoreError("read failed")
        item = self.items.get((channel, issue_key))
        return None if item is None else dict(item)

    def record_sent(self, issue_key: str, channel: str, window: str, ttl: int) -> None:
        if self.fail_record_sent:
            raise CapStoreError("write failed")
        key = (channel, issue_key)
        current = self.items.get(key)
        if current is None or current["window"] != window:
            self.items[key] = {"window": window, "sent": 1, "suppressed": 0, "expires_at": ttl}
            return
        current["sent"] = int(current["sent"]) + 1

    def record_suppressed(self, issue_key: str, channel: str, window: str) -> None:
        if self.fail_suppress:
            raise CapStoreError("suppress failed")
        current = self.items.get((channel, issue_key))
        if current is None or current["window"] != window:
            raise CapStoreError("missing cap item")
        current["suppressed"] = int(current.get("suppressed", 0)) + 1


def window_bounds(now: datetime, window_minutes: int) -> tuple[str, int]:
    """Fixed Pacific clock window containing ``now``, plus the DynamoDB TTL epoch.

    Buckets start at local midnight and are ``window_minutes`` long (the last
    bucket of the day may be shorter). The id is the window start with its UTC
    offset, so the repeated hour on a fall-back day is two windows. The TTL is
    the end of the next window.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    minutes = window_minutes if window_minutes >= 1 else 60
    local = now.astimezone(PACIFIC)
    midnight = datetime.combine(local.date(), time.min, tzinfo=PACIFIC)
    elapsed = int((local - midnight).total_seconds() // 60)
    if elapsed < 0:
        elapsed = 0
    start = midnight + timedelta(minutes=(elapsed // minutes) * minutes)
    label = start.strftime("%Y-%m-%dT%H:%M%z")
    ttl = int((start + timedelta(minutes=minutes * 2)).timestamp())
    return label, ttl


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
    """One short text. The next window's first text names how many repeats were held back."""
    subject = (subject or "").strip()
    if subject and subject not in _GENERIC_SUBJECTS:
        line = subject
    else:
        line = _summary(message) or subject or "alert"
    line = re.sub(r"\s+", " ", line).strip()[:140]
    return (line + _repeat_note(suppressed_prior))[:300]


def format_email(subject: str, message: str, suppressed_prior: int) -> str:
    """Original subject and body. The subject stays within SNS's 100-character limit.

    The suppressed count is on the subject and, when any repeats were held, a
    one-line trailer on the body. The returned string is ``subject\\nbody``.
    """
    note = _repeat_note(suppressed_prior)
    base = re.sub(r"\s+", " ", (subject or "alert")).strip() or "alert"
    room = 100 - len(note)
    subj = (base[:room] + note) if room > 0 else note[:100]
    body = message or ""
    if suppressed_prior > 0:
        word = "repeat" if suppressed_prior == 1 else "repeats"
        body = f"{body}\n\nSuppressed {suppressed_prior} {word} in the previous window."
    return subj + "\n" + body


def plan(
    store: MemoryStore,
    issue_key_value: str,
    now: datetime,
    *,
    channel: str,
    window_minutes: int,
    limit: int,
) -> Decision:
    """Read one channel's counter. Does not write, so a failed publish does not consume the slot."""
    _check_channel(channel)
    window, ttl = window_bounds(now, window_minutes)
    cap = limit if limit >= 1 else 1
    item = store.get(issue_key_value, channel)
    if item is None or str(item.get("window")) != window:
        prior = 0 if item is None else int(item.get("suppressed") or 0)
        return Decision(
            send=True,
            reason="sent",
            issue_key=issue_key_value,
            channel=channel,
            window=window,
            suppressed_prior=prior,
            ttl=ttl,
        )
    sent = int(item.get("sent") or 0)
    if sent < cap:
        return Decision(
            send=True, reason="sent", issue_key=issue_key_value, channel=channel, window=window, ttl=ttl
        )
    suppressed_in_window = int(item.get("suppressed") or 0) + 1
    return Decision(
        send=False,
        reason="suppressed",
        issue_key=issue_key_value,
        channel=channel,
        window=window,
        suppressed_in_window=suppressed_in_window,
        ttl=ttl,
    )


def deliver(
    subject: str,
    message: str,
    store: MemoryStore,
    publish: Callable[[str], None],
    *,
    now: datetime,
    channel: str = "sms",
    window_minutes: int = 60,
    limit: int = 1,
) -> Decision:
    """Send or log one channel. Store errors publish that channel and return fail_open."""
    _check_channel(channel)
    key = issue_key(subject, message)
    try:
        decision = plan(
            store, key, now, channel=channel, window_minutes=window_minutes, limit=limit
        )
    except CapStoreError:
        _log("alert_cap_failed_open", issue_key=key, channel=channel, error="store_read_failed")
        publish(_render(channel, subject, message, 0))
        return Decision(send=True, reason="fail_open", issue_key=key, channel=channel)

    if not decision.send:
        try:
            store.record_suppressed(key, channel, decision.window)
        except CapStoreError:
            _log("alert_cap_failed_open", issue_key=key, channel=channel, error="store_suppress_failed")
            publish(_render(channel, subject, message, 0))
            return Decision(send=True, reason="fail_open", issue_key=key, channel=channel)
        _log(
            "alert_suppressed",
            issue_key=key,
            channel=channel,
            suppressed=decision.suppressed_in_window,
            window=decision.window,
        )
        return decision

    publish(_render(channel, subject, message, decision.suppressed_prior))
    try:
        store.record_sent(key, channel, decision.window, decision.ttl)
    except CapStoreError:
        _log("alert_cap_record_failed", issue_key=key, channel=channel, window=decision.window)
    else:
        _log(
            "alert_sent",
            issue_key=key,
            channel=channel,
            window=decision.window,
            suppressed_prior=decision.suppressed_prior,
            reason=decision.reason,
        )
    return decision


def _check_channel(channel: str) -> None:
    if channel not in _CHANNELS:
        raise ValueError(f"unknown channel {channel}")


def _render(channel: str, subject: str, message: str, suppressed_prior: int) -> str:
    if channel == "email":
        return format_email(subject, message, suppressed_prior)
    return format_sms(subject, message, suppressed_prior)


def _repeat_note(count: int) -> str:
    if count == 1:
        return " (suppressed 1 repeat)"
    if count > 1:
        return f" (suppressed {count} repeats)"
    return ""


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
