"""Hourly SMS cap: one text per issue per fixed Pacific clock hour."""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import boto3
import pytest
from moto import mock_aws

from sms_cap import MemoryStore, deliver, format_sms, issue_key, window_bounds
from sms_notifier import DynamoCapStore, handler

PT = ZoneInfo("America/Los_Angeles")
SITE = 'ALARM: "invdash-site-api-5xx" in US West (N. California)'
SITE_OK = 'OK: "invdash-site-api-5xx" in US West (N. California)'
LAMBDA = 'ALARM: "invdash-lambda-errors" in US West (N. California)'


def _pt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=PT)


def test_same_issue_twice_within_the_hour_sends_one_sms():
    store = MemoryStore()
    sent: list[str] = []
    first = deliver(SITE, "alarm", store, sent.append, now=_pt(11, 14, 10))
    second = deliver(SITE_OK, "recovered", store, sent.append, now=_pt(11, 14, 50))
    assert first.send and first.reason == "sent"
    assert not second.send and second.reason == "suppressed"
    assert len(sent) == 1
    assert issue_key(SITE, "") == issue_key(SITE_OK, "") == "alarm:invdash-site-api-5xx"


def test_the_next_clock_hour_sends_again_and_reports_suppressed_repeats():
    store = MemoryStore()
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, now=_pt(11, 14, 10))
    deliver(SITE, "", store, sent.append, now=_pt(11, 14, 40))
    deliver(SITE, "", store, sent.append, now=_pt(11, 14, 55))
    assert len(sent) == 1
    # 15 minutes later is a new clock hour, not a continuation of a rolling hour.
    deliver(SITE, "", store, sent.append, now=_pt(11, 15, 5))
    assert len(sent) == 2
    assert "suppressed 2 repeats" in sent[1]


def test_different_issues_are_independent():
    store = MemoryStore()
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, now=_pt(11, 14, 10))
    deliver(LAMBDA, "", store, sent.append, now=_pt(11, 14, 10))
    deliver(SITE, "", store, sent.append, now=_pt(11, 14, 40))
    deliver(LAMBDA, "", store, sent.append, now=_pt(11, 14, 40))
    assert len(sent) == 2
    assert "invdash-site-api-5xx" in sent[0]
    assert "invdash-lambda-errors" in sent[1]


def test_windows_follow_pacific_clock_time_not_utc():
    # 06:10 and 06:40 UTC are both 23:xx Pacific (PDT, UTC-7): one clock hour.
    late = datetime(2026, 10, 12, 6, 10, tzinfo=UTC)
    still = datetime(2026, 10, 12, 6, 40, tzinfo=UTC)
    nxt = datetime(2026, 10, 12, 7, 5, tzinfo=UTC)
    assert window_bounds(late, 60)[0] == window_bounds(still, 60)[0] == "2026-10-11T23:00-0700"
    assert window_bounds(nxt, 60)[0] == "2026-10-12T00:00-0700"
    store = MemoryStore()
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, now=late)
    deliver(SITE, "", store, sent.append, now=still)
    deliver(SITE, "", store, sent.append, now=nxt)
    assert len(sent) == 2


def test_job_and_error_code_are_their_own_issue():
    prices = json.dumps({"job": "D4", "code": "ALPACA_429"})
    other = json.dumps({"detail-type": "Job Finished", "detail": {"job": "H1", "error_code": "TIMEOUT"}})
    assert issue_key("", prices) == "job:D4:ALPACA_429"
    assert issue_key("", other) == "job:H1:TIMEOUT"
    store = MemoryStore()
    sent: list[str] = []
    deliver("", prices, store, sent.append, now=_pt(11, 9, 5))
    deliver("", prices, store, sent.append, now=_pt(11, 9, 40))
    deliver("", other, store, sent.append, now=_pt(11, 9, 40))
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
    deliver(SITE, "", store, lambda _text: None, now=_pt(11, 9, 5))
    deliver(SITE, "", store, lambda _text: None, now=_pt(11, 9, 40))
    logged = capsys.readouterr().out
    assert '"event": "sms_suppressed"' in logged
    assert "alarm:invdash-site-api-5xx" in logged


def test_store_read_failure_still_sends():
    store = MemoryStore()
    store.fail_reads = True
    sent: list[str] = []
    decision = deliver(SITE, "", store, sent.append, now=_pt(11, 9, 5))
    assert decision.send and decision.reason == "fail_open"
    assert len(sent) == 1


def test_failed_suppress_write_still_sends():
    store = MemoryStore()
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, now=_pt(11, 9, 5))
    store.fail_suppress = True
    decision = deliver(SITE, "", store, sent.append, now=_pt(11, 9, 20))
    assert decision.reason == "fail_open"
    assert len(sent) == 2


def test_publish_failure_does_not_consume_the_slot():
    store = MemoryStore()

    def boom(_text: str) -> None:
        raise RuntimeError("sns down")

    with pytest.raises(RuntimeError, match="sns down"):
        deliver(SITE, "", store, boom, now=_pt(11, 9, 5))
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, now=_pt(11, 9, 5))
    assert len(sent) == 1


def test_handler_retries_a_failed_publish_without_using_up_the_hour():
    store = MemoryStore()
    event = {"Records": [{"Sns": {"Subject": SITE, "Message": "Threshold Crossed", "MessageId": "m1"}}]}
    now = _pt(11, 15, 10)

    def boom(_text: str) -> None:
        raise RuntimeError("sns down")

    with pytest.raises(RuntimeError, match="sns down"):
        handler(event, None, store=store, publish=boom, now=now)
    sent: list[str] = []
    handler(event, None, store=store, publish=sent.append, now=now)
    handler(event, None, store=store, publish=sent.append, now=now + timedelta(minutes=5))
    assert sent == [SITE]


def test_dynamodb_store_caps_one_issue_per_hour(monkeypatch):
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
        first = _pt(11, 9, 10)
        deliver(SITE, "", store, sent.append, now=first)
        deliver(SITE, "", store, sent.append, now=_pt(11, 9, 40))
        deliver(LAMBDA, "", store, sent.append, now=_pt(11, 9, 40))
        deliver(SITE, "", store, sent.append, now=_pt(11, 10, 5))
        assert len(sent) == 3
        assert "suppressed 1 repeat" in sent[2]
        item = boto3.resource("dynamodb").Table("invdash-sms-cap").get_item(
            Key={"issue_key": "alarm:invdash-site-api-5xx"}
        )["Item"]
        assert int(item["sent"]) == 1
        assert str(item["window"]) == "2026-10-11T10:00-0700"
        # TTL reaches the end of the next window (11:00 PT), not a multi-day hold.
        assert int(item["expires_at"]) == int((_pt(11, 12, 0)).timestamp())
