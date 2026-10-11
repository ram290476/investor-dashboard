"""SNS subscriber that caps email and SMS independently per issue.

Windows are fixed buckets in America/Los_Angeles, aligned to local midnight
(default 60 minutes, so one clock hour). Email and SMS each have their own
window length and maximum. Alarms publish to the intake topics
(``<name>-ops-alarms``, ``<name>-security-alarms``, and the us-east-1 CloudFront
topic), which invoke this function. It is the only publisher to the delivery
topics (``<name>-ops-alerts``, ``<name>-security-alerts``) where the inboxes
subscribe and, when a handset is configured, the SMS topic. A direct
subscription on an intake topic would bypass the cap.

Fail open: if the dedupe table errors, that channel is still sent. If
publishing itself fails, the invocation raises so SNS retries, and that
channel's slot is left unused. Email is attempted before SMS; a failed email
publish retries the whole event, and a channel already recorded is not sent
again on that retry.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from sms_cap import CapStoreError, deliver

_SMS_TYPE = {"DataType": "String", "StringValue": "Transactional"}


class DynamoCapStore:
    """One item per issue key and channel. ``expires_at`` is the DynamoDB TTL."""

    def __init__(self, table_name: str, table: object | None = None) -> None:
        if table is None:
            table = boto3.resource("dynamodb").Table(table_name)
        self.table = table

    def get(self, issue_key: str, channel: str) -> dict | None:
        try:
            resp = self.table.get_item(
                Key={"issue_key": issue_key, "channel": channel}, ConsistentRead=True
            )
        except (ClientError, BotoCoreError) as exc:
            raise CapStoreError("dedupe store request failed") from exc
        item = resp.get("Item")
        if not item:
            return None
        return {
            "window": str(item["window"]),
            "sent": int(item.get("sent", 0)),
            "suppressed": int(item.get("suppressed", 0)),
        }

    def record_sent(self, issue_key: str, channel: str, window: str, ttl: int) -> None:
        key = {"issue_key": issue_key, "channel": channel}
        try:
            self.table.update_item(
                Key=key,
                UpdateExpression="ADD sent :one",
                ConditionExpression="#w = :window",
                ExpressionAttributeNames={"#w": "window"},
                ExpressionAttributeValues={":one": 1, ":window": window},
            )
            return
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise CapStoreError("dedupe store request failed") from exc
        except BotoCoreError as exc:
            raise CapStoreError("dedupe store request failed") from exc
        try:
            self.table.update_item(
                Key=key,
                UpdateExpression="SET #w = :window, sent = :one, suppressed = :zero, expires_at = :ttl",
                ExpressionAttributeNames={"#w": "window"},
                ExpressionAttributeValues={":window": window, ":one": 1, ":zero": 0, ":ttl": ttl},
            )
        except (ClientError, BotoCoreError) as exc:
            raise CapStoreError("dedupe store request failed") from exc

    def record_suppressed(self, issue_key: str, channel: str, window: str) -> None:
        try:
            self.table.update_item(
                Key={"issue_key": issue_key, "channel": channel},
                UpdateExpression="ADD suppressed :one",
                ConditionExpression="#w = :window",
                ExpressionAttributeNames={"#w": "window"},
                ExpressionAttributeValues={":one": 1, ":window": window},
            )
        except (ClientError, BotoCoreError) as exc:
            raise CapStoreError("dedupe store request failed") from exc


def email_topic_arn(source_arn: str, ops_arn: str, security_arn: str) -> str:
    """Pick the delivery topic for the intake topic that invoked us.

    Security intake (``<name>-security-alarms``) mails the security delivery
    topic. Everything else, including the us-east-1 CloudFront topic, mails ops.
    ``-security-alerts`` is accepted too so a legacy source still routes to security.
    """
    topic_name = (source_arn or "").rsplit(":", 1)[-1]
    if topic_name.endswith(("-security-alarms", "-security-alerts")):
        return security_arn
    return ops_arn


def _positive(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def _sns_publish_sms(topic_arn: str, text: str) -> None:
    boto3.client("sns").publish(
        TopicArn=topic_arn,
        Message=text,
        MessageAttributes={"AWS.SNS.SMS.SMSType": _SMS_TYPE},
    )


def _sns_publish_email(topic_arn: str, rendered: str) -> None:
    subject, sep, body = rendered.partition("\n")
    boto3.client("sns").publish(
        TopicArn=topic_arn,
        Subject=subject[:100],
        Message=body if sep else rendered,
    )


def handler(event, _context, store=None, publish=None, publish_email=None, now=None):
    """Lambda entry point. Tests pass ``store`` and the publishers; SNS calls the first two args.

    A publish error propagates so SNS retries the invocation. The cap slot is
    recorded only after a successful publish, inside ``deliver``.
    """
    if store is None:
        store = DynamoCapStore(os.environ.get("CAP_TABLE_NAME", ""))
    moment = now or datetime.now(UTC)
    email_window = _positive("EMAIL_WINDOW_MINUTES", 60)
    email_limit = _positive("EMAIL_MAX_PER_WINDOW", 1)
    sms_window = _positive("SMS_WINDOW_MINUTES", 60)
    sms_limit = _positive("SMS_MAX_PER_WINDOW", 1)
    vuln_window = _positive("VULN_WINDOW_MINUTES", 1440)
    ops_email = os.environ.get("OPS_EMAIL_TOPIC_ARN", "")
    security_email = os.environ.get("SECURITY_EMAIL_TOPIC_ARN", "")
    sms_topic = os.environ.get("SMS_TOPIC_ARN", "")

    for record in event.get("Records") or []:
        sns_rec = record.get("Sns") or {}
        subject = sns_rec.get("Subject") or ""
        message = sns_rec.get("Message") or ""
        source = sns_rec.get("TopicArn") or ""

        if publish_email is not None or ops_email or security_email:
            topic = email_topic_arn(source, ops_email, security_email)

            def publish_one_email(text: str, topic_arn: str = topic) -> None:
                if publish_email is not None:
                    publish_email(text)
                    return
                _sns_publish_email(topic_arn, text)

            deliver(
                subject,
                message,
                store,
                publish_one_email,
                now=moment,
                channel="email",
                window_minutes=email_window,
                limit=email_limit,
                vuln_window_minutes=vuln_window,
            )

        if publish is not None or sms_topic:

            def publish_one_sms(text: str) -> None:
                if publish is not None:
                    publish(text)
                    return
                _sns_publish_sms(sms_topic, text)

            deliver(
                subject,
                message,
                store,
                publish_one_sms,
                now=moment,
                channel="sms",
                window_minutes=sms_window,
                limit=sms_limit,
                vuln_window_minutes=vuln_window,
            )
    return {"ok": True}
