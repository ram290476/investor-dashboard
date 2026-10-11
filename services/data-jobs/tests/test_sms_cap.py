"""Daily SMS cap: one text per issue per Pacific day, then a reset."""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import boto3
import pytest
from moto import mock_aws

from sms_cap import MemoryStore, calendar_day, deliver, format_sms, issue_key
from sms_notifier import DynamoCapStore, handler

PT = ZoneInfo("America/Los_Angeles")
SITE = 'ALARM: "invdash-site-api-5xx" in US West (N. California)'
SITE_OK = 'OK: "invdash-site-api-5xx" in US West (N. California)'
LAMBDA = 'ALARM: "invdash-lambda-errors" in US West (N. California)'


def _at(hour: int, day: int = 11) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=PT)


def test_same_issue_twice_in_a_day_sends_one_sms():
    store = MemoryStore()
    sent: list[str] = []
    first = deliver(SITE, "alarm", store, sent.append, limit=1, now=_at(9))
    second = deliver(SITE_OK, "recovered", store, sent.append, limit=1, now=_at(10))
    assert first.send and first.reason == "sent"
    assert not second.send and second.reason == "suppressed"
    assert len(sent) == 1
    assert issue_key(SITE, "") == issue_key(SITE_OK, "") == "alarm:invdash-site-api-5xx"


def test_new_pacific_day_resets_and_reports_suppressed_repeats():
    store = MemoryStore()
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, limit=1, now=_at(9))
    deliver(SITE, "", store, sent.append, limit=1, now=_at(12))
    deliver(SITE, "", store, sent.append, limit=1, now=_at(18))
    assert len(sent) == 1
    deliver(SITE, "", store, sent.append, limit=1, now=_at(9, day=12))
    assert len(sent) == 2
    assert "suppressed 2 repeats" in sent[1]


def test_different_issues_are_independent():
    store = MemoryStore()
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, limit=1, now=_at(9))
    deliver(LAMBDA, "", store, sent.append, limit=1, now=_at(9))
    deliver(SITE, "", store, sent.append, limit=1, now=_at(11))
    deliver(LAMBDA, "", store, sent.append, limit=1, now=_at(11))
    assert len(sent) == 2
    assert "invdash-site-api-5xx" in sent[0]
    assert "invdash-lambda-errors" in sent[1]


def test_utc_evening_and_next_morning_can_be_different_pacific_days():
    # 06:30 UTC is still the previous Pacific date while DST is in effect (UTC-7).
    same_utc_date_late = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)
    same_utc_date_early = datetime(2026, 10, 12, 7, 30, tzinfo=UTC)
    assert calendar_day(same_utc_date_late) == "2026-10-11"
    assert calendar_day(same_utc_date_early) == "2026-10-12"
    store = MemoryStore()
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, limit=1, now=same_utc_date_late)
    deliver(SITE, "", store, sent.append, limit=1, now=same_utc_date_late + timedelta(minutes=20))
    deliver(SITE, "", store, sent.append, limit=1, now=same_utc_date_early)
    assert len(sent) == 2


def test_job_and_error_code_are_their_own_issue():
    prices = json.dumps({"job": "D4", "code": "ALPACA_429"})
    other = json.dumps({"detail-type": "Job Finished", "detail": {"job": "H1", "error_code": "TIMEOUT"}})
    assert issue_key("", prices) == "job:D4:ALPACA_429"
    assert issue_key("", other) == "job:H1:TIMEOUT"
    store = MemoryStore()
    sent: list[str] = []
    deliver("", prices, store, sent.append, limit=1, now=_at(9))
    deliver("", prices, store, sent.append, limit=1, now=_at(10))
    deliver("", other, store, sent.append, limit=1, now=_at(10))
    assert len(sent) == 2


def test_security_hub_repeats_of_one_cve_share_a_key_and_a_short_text():
    body = json.dumps(
        {
            "detail-type": "Security Hub Findings - Imported",
            "detail": {"findings": [{"Title": "CVE-2026-84233 - rpm, rpm-libs", "GeneratorId": "AWSInspector"}]},
        }
    )
    assert issue_key("AWS Notification Message", body) == "securityhub:CVE-2026-84233 - rpm, rpm-libs"
    text = format_sms("AWS Notification Message", body + "\n\n--\nunsubscribe", 0)
    assert text.startswith("CVE-2026-84233")
    assert "unsubscribe" not in text


def test_suppressed_repeat_is_logged(capsys):
    store = MemoryStore()
    deliver(SITE, "", store, lambda _text: None, limit=1, now=_at(9))
    deliver(SITE, "", store, lambda _text: None, limit=1, now=_at(10))
    logged = capsys.readouterr().out
    assert '"event": "sms_suppressed"' in logged
    assert "alarm:invdash-site-api-5xx" in logged


def test_store_read_failure_still_sends():
    store = MemoryStore()
    store.fail_reads = True
    sent: list[str] = []
    decision = deliver(SITE, "", store, sent.append, limit=1, now=_at(9))
    assert decision.send and decision.reason == "fail_open"
    assert len(sent) == 1


def test_failed_suppress_write_still_sends():
    store = MemoryStore()
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, limit=1, now=_at(9))
    store.fail_suppress = True
    decision = deliver(SITE, "", store, sent.append, limit=1, now=_at(10))
    assert decision.reason == "fail_open"
    assert len(sent) == 2


def test_publish_failure_does_not_consume_the_slot():
    store = MemoryStore()

    def boom(_text: str) -> None:
        raise RuntimeError("sns down")

    with pytest.raises(RuntimeError, match="sns down"):
        deliver(SITE, "", store, boom, limit=1, now=_at(9))
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, limit=1, now=_at(9))
    assert len(sent) == 1


def test_handler_retries_a_failed_publish_without_using_up_the_day():
    store = MemoryStore()
    event = {"Records": [{"Sns": {"Subject": SITE, "Message": "Threshold Crossed", "MessageId": "m1"}}]}
    now = _at(15)

    def boom(_text: str) -> None:
        raise RuntimeError("sns down")

    with pytest.raises(RuntimeError, match="sns down"):
        handler(event, None, store=store, publish=boom, now=now)
    sent: list[str] = []
    handler(event, None, store=store, publish=sent.append, now=now)
    handler(event, None, store=store, publish=sent.append, now=now + timedelta(minutes=5))
    assert sent == [SITE]


def test_dynamodb_store_caps_one_issue_and_resets_the_next_day(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName="invdash-sms-cap",
            KeySchema=[{"AttributeName": "issue_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "issue_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        store = DynamoCapStore("invdash-sms-cap")
        sent: list[str] = []
        deliver(SITE, "", store, sent.append, limit=1, now=_at(9))
        deliver(SITE, "", store, sent.append, limit=1, now=_at(11))
        deliver(LAMBDA, "", store, sent.append, limit=1, now=_at(11))
        deliver(SITE, "", store, sent.append, limit=1, now=_at(9, day=12))
        assert len(sent) == 3
        assert "suppressed 1 repeat" in sent[2]
        item = boto3.resource("dynamodb").Table("invdash-sms-cap").get_item(
            Key={"issue_key": "alarm:invdash-site-api-5xx"}
        )["Item"]
        assert int(item["sent"]) == 1
        assert str(item["day"]) == "2026-10-12"
        assert int(item["expires_at"]) > 0
