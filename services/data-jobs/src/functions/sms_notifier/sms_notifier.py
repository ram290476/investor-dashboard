"""SNS subscriber that texts at most N times per issue per Pacific day.

Ops and security email subscriptions stay on their topics and are not capped.
This function is the only publisher to the SMS topic. A direct SMS subscription
on an alarm topic would bypass the cap.

Fail open: if the dedupe table errors, the text is still sent. If publishing
itself fails, the invocation raises so SNS retries, and the day's slot is left
unused.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from sms_cap import CapStoreError, deliver

_SMS_TYPE = {"DataType": "String", "StringValue": "Transactional"}


class DynamoCapStore:
    """One item per issue key. ``expires_at`` is the DynamoDB TTL."""

    def __init__(self, table_name: str, table: object | None = None) -> None:
        if table is None:
            table = boto3.resource("dynamodb").Table(table_name)
        self.table = table

    def get(self, issue_key: str) -> dict | None:
        try:
            resp = self.table.get_item(Key={"issue_key": issue_key}, ConsistentRead=True)
        except (ClientError, BotoCoreError) as exc:
            raise CapStoreError("dedupe store request failed") from exc
        item = resp.get("Item")
        if not item:
            return None
        return {
            "day": str(item["day"]),
            "sent": int(item.get("sent", 0)),
            "suppressed": int(item.get("suppressed", 0)),
        }

    def record_sent(self, issue_key: str, day: str, ttl: int) -> None:
        try:
            self.table.update_item(
                Key={"issue_key": issue_key},
                UpdateExpression="ADD sent :one",
                ConditionExpression="#d = :day",
                ExpressionAttributeNames={"#d": "day"},
                ExpressionAttributeValues={":one": 1, ":day": day},
            )
            return
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise CapStoreError("dedupe store request failed") from exc
        except BotoCoreError as exc:
            raise CapStoreError("dedupe store request failed") from exc
        try:
            self.table.update_item(
                Key={"issue_key": issue_key},
                UpdateExpression="SET #d = :day, sent = :one, suppressed = :zero, expires_at = :ttl",
                ExpressionAttributeNames={"#d": "day"},
                ExpressionAttributeValues={":day": day, ":one": 1, ":zero": 0, ":ttl": ttl},
            )
        except (ClientError, BotoCoreError) as exc:
            raise CapStoreError("dedupe store request failed") from exc

    def record_suppressed(self, issue_key: str, day: str) -> None:
        try:
            self.table.update_item(
                Key={"issue_key": issue_key},
                UpdateExpression="ADD suppressed :one",
                ConditionExpression="#d = :day",
                ExpressionAttributeNames={"#d": "day"},
                ExpressionAttributeValues={":one": 1, ":day": day},
            )
        except (ClientError, BotoCoreError) as exc:
            raise CapStoreError("dedupe store request failed") from exc


def _limit() -> int:
    raw = os.environ.get("SMS_DAILY_CAP", "1")
    try:
        value = int(raw)
    except ValueError:
        return 1
    return value if value >= 1 else 1


def _sns_publish(topic_arn: str, text: str) -> None:
    boto3.client("sns").publish(
        TopicArn=topic_arn,
        Message=text,
        MessageAttributes={"AWS.SNS.SMS.SMSType": _SMS_TYPE},
    )


def handler(event, _context, store=None, publish=None, now=None):
    """Lambda entry point. Tests pass ``store`` and ``publish``; SNS calls the first two args.

    A publish error propagates so SNS retries the invocation. The cap slot is
    recorded only after a successful publish, inside ``deliver``.
    """
    table = os.environ.get("CAP_TABLE_NAME", "")
    topic = os.environ.get("SMS_TOPIC_ARN", "")
    if store is None:
        store = DynamoCapStore(table)

    def publish_sms(text: str) -> None:
        _sns_publish(topic, text)

    if publish is None:
        publish = publish_sms
    moment = now or datetime.now(UTC)
    limit = _limit()
    for record in event.get("Records") or []:
        sns_rec = record.get("Sns") or {}
        deliver(
            sns_rec.get("Subject") or "",
            sns_rec.get("Message") or "",
            store,
            publish,
            limit=limit,
            now=moment,
        )
    return {"ok": True}
