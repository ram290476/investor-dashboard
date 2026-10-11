"""Hourly email and SMS caps: one message per issue per channel per fixed Pacific clock window."""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
import pytest
from moto import mock_aws

from sms_cap import (
    MemoryStore,
    deliver,
    format_email,
    format_sms,
    is_vulnerability_key,
    issue_key,
    window_bounds,
)
from sms_notifier import DynamoCapStore, email_topic_arn, handler

PT = ZoneInfo("America/Los_Angeles")
SITE = 'ALARM: "invdash-site-api-5xx" in US West (N. California)'
SITE_OK = 'OK: "invdash-site-api-5xx" in US West (N. California)'
LAMBDA = 'ALARM: "invdash-lambda-errors" in US West (N. California)'


def _pt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=PT)


def _event(topic: str = "arn:aws:sns:us-west-1:1:invdash-ops-alarms") -> dict:
    return {"Records": [{"Sns": {"TopicArn": topic, "Subject": SITE, "Message": "Threshold Crossed"}}]}


@pytest.mark.parametrize("channel", ["email", "sms"])
def test_same_issue_twice_within_the_hour_sends_one(channel):
    store = MemoryStore()
    sent: list[str] = []
    first = deliver(SITE, "alarm", store, sent.append, now=_pt(11, 14, 10), channel=channel)
    second = deliver(SITE_OK, "recovered", store, sent.append, now=_pt(11, 14, 50), channel=channel)
    assert first.send and first.reason == "sent" and first.channel == channel
    assert not second.send and second.reason == "suppressed"
    assert len(sent) == 1
    assert issue_key(SITE, "") == issue_key(SITE_OK, "") == "alarm:invdash-site-api-5xx"


@pytest.mark.parametrize("channel", ["email", "sms"])
def test_the_next_clock_hour_sends_again_and_reports_suppressed_repeats(channel):
    store = MemoryStore()
    sent: list[str] = []
    deliver(SITE, "body", store, sent.append, now=_pt(11, 14, 10), channel=channel)
    deliver(SITE, "body", store, sent.append, now=_pt(11, 14, 40), channel=channel)
    deliver(SITE, "body", store, sent.append, now=_pt(11, 14, 55), channel=channel)
    assert len(sent) == 1
    # 15 minutes later is a new clock hour, not a continuation of a rolling hour.
    deliver(SITE, "body", store, sent.append, now=_pt(11, 15, 5), channel=channel)
    assert len(sent) == 2
    assert "suppressed 2 repeats" in sent[1]


def test_different_issues_are_independent():
    store = MemoryStore()
    emails: list[str] = []
    texts: list[str] = []
    for subject, minute in ((SITE, 10), (LAMBDA, 10), (SITE, 40), (LAMBDA, 40)):
        deliver(subject, "", store, emails.append, now=_pt(11, 14, minute), channel="email")
        deliver(subject, "", store, texts.append, now=_pt(11, 14, minute), channel="sms")
    assert len(emails) == 2
    assert len(texts) == 2
    assert "invdash-site-api-5xx" in emails[0]
    assert "invdash-lambda-errors" in emails[1]
    assert "invdash-site-api-5xx" in texts[0]
    assert "invdash-lambda-errors" in texts[1]


def test_email_and_sms_counters_do_not_share_a_slot():
    store = MemoryStore()
    emails: list[str] = []
    texts: list[str] = []
    deliver(SITE, "body", store, emails.append, now=_pt(11, 14, 10), channel="email", limit=1)
    deliver(SITE, "body", store, texts.append, now=_pt(11, 14, 10), channel="sms", limit=2)
    deliver(SITE, "body", store, emails.append, now=_pt(11, 14, 20), channel="email", limit=1)
    deliver(SITE, "body", store, texts.append, now=_pt(11, 14, 20), channel="sms", limit=2)
    deliver(SITE, "body", store, texts.append, now=_pt(11, 14, 30), channel="sms", limit=2)
    assert len(emails) == 1
    assert len(texts) == 2


def test_each_channel_uses_its_own_window():
    store = MemoryStore()
    emails: list[str] = []
    texts: list[str] = []
    # 14:10 and 14:40 are one 60-minute email window, and two 30-minute SMS windows.
    deliver(SITE, "body", store, emails.append, now=_pt(11, 14, 10), channel="email", window_minutes=60)
    deliver(SITE, "body", store, texts.append, now=_pt(11, 14, 10), channel="sms", window_minutes=30)
    deliver(SITE, "body", store, emails.append, now=_pt(11, 14, 40), channel="email", window_minutes=60)
    deliver(SITE, "body", store, texts.append, now=_pt(11, 14, 40), channel="sms", window_minutes=30)
    assert len(emails) == 1
    assert len(texts) == 2
    deliver(SITE, "body", store, emails.append, now=_pt(11, 15, 5), channel="email", window_minutes=60)
    deliver(SITE, "body", store, texts.append, now=_pt(11, 15, 5), channel="sms", window_minutes=30)
    assert "suppressed 1 repeat" in emails[-1]
    assert "suppressed" not in texts[-1]


def test_windows_follow_pacific_clock_time_not_utc():
    # 06:10 and 06:40 UTC are both 23:xx Pacific (PDT, UTC-7): one clock hour.
    late = datetime(2026, 10, 12, 6, 10, tzinfo=UTC)
    still = datetime(2026, 10, 12, 6, 40, tzinfo=UTC)
    nxt = datetime(2026, 10, 12, 7, 5, tzinfo=UTC)
    assert window_bounds(late, 60)[0] == window_bounds(still, 60)[0] == "2026-10-11T23:00-0700"
    assert window_bounds(nxt, 60)[0] == "2026-10-12T00:00-0700"
    store = MemoryStore()
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, now=late, channel="email")
    deliver(SITE, "", store, sent.append, now=still, channel="email")
    deliver(SITE, "", store, sent.append, now=nxt, channel="email")
    assert len(sent) == 2


def test_job_and_error_code_are_their_own_issue():
    prices = json.dumps({"job": "D4", "code": "ALPACA_429"})
    other = json.dumps({"detail-type": "Job Finished", "detail": {"job": "H1", "error_code": "TIMEOUT"}})
    assert issue_key("", prices) == "job:D4:ALPACA_429"
    assert issue_key("", other) == "job:H1:TIMEOUT"
    store = MemoryStore()
    sent: list[str] = []
    deliver("", prices, store, sent.append, now=_pt(11, 9, 5), channel="email")
    deliver("", prices, store, sent.append, now=_pt(11, 9, 40), channel="email")
    deliver("", other, store, sent.append, now=_pt(11, 9, 40), channel="sms")
    assert len(sent) == 2


def test_security_hub_repeats_of_one_cve_share_a_key_and_a_short_text():
    body = json.dumps(
        {
            "detail-type": "Security Hub Findings - Imported",
            "detail": {"findings": [{"Title": "CVE-2026-84233 - rpm, rpm-libs", "GeneratorId": "AWSInspector"}]},
        }
    )
    assert issue_key("AWS Notification Message", body) == "vuln:CVE-2026-84233"
    text = format_sms("AWS Notification Message", body + "\n\n--\nunsubscribe", 0)
    assert text.startswith("CVE-2026-84233")
    assert "unsubscribe" not in text
    mailed = format_email("AWS Notification Message", body, 2)
    subject, _, mailed_body = mailed.partition("\n")
    assert subject.startswith("AWS Notification Message")
    assert "suppressed 2 repeats" in subject
    assert len(subject) <= 100
    assert "Suppressed 2 repeats" in mailed_body
    assert body in mailed_body


@pytest.mark.parametrize("channel", ["email", "sms"])
def test_suppressed_repeat_is_logged_with_channel_and_count(capsys, channel):
    store = MemoryStore()
    deliver(SITE, "", store, lambda _text: None, now=_pt(11, 9, 5), channel=channel)
    deliver(SITE, "", store, lambda _text: None, now=_pt(11, 9, 40), channel=channel)
    logged = capsys.readouterr().out
    assert '"event": "alert_suppressed"' in logged
    assert "alarm:invdash-site-api-5xx" in logged
    assert f'"channel": "{channel}"' in logged
    assert '"suppressed": 1' in logged


@pytest.mark.parametrize("channel", ["email", "sms"])
def test_store_read_failure_still_sends(channel):
    store = MemoryStore()
    store.fail_reads = True
    sent: list[str] = []
    decision = deliver(SITE, "", store, sent.append, now=_pt(11, 9, 5), channel=channel)
    assert decision.send and decision.reason == "fail_open"
    assert decision.channel == channel
    assert len(sent) == 1


@pytest.mark.parametrize("channel", ["email", "sms"])
def test_failed_suppress_write_still_sends(channel):
    store = MemoryStore()
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, now=_pt(11, 9, 5), channel=channel)
    store.fail_suppress = True
    decision = deliver(SITE, "", store, sent.append, now=_pt(11, 9, 20), channel=channel)
    assert decision.reason == "fail_open"
    assert len(sent) == 2


@pytest.mark.parametrize("channel", ["email", "sms"])
def test_publish_failure_does_not_consume_the_slot(channel):
    store = MemoryStore()

    def boom(_text: str) -> None:
        raise RuntimeError("sns down")

    with pytest.raises(RuntimeError, match="sns down"):
        deliver(SITE, "", store, boom, now=_pt(11, 9, 5), channel=channel)
    sent: list[str] = []
    deliver(SITE, "", store, sent.append, now=_pt(11, 9, 5), channel=channel)
    assert len(sent) == 1


def test_handler_retries_a_failed_publish_without_using_up_the_hour():
    store = MemoryStore()
    event = _event()
    now = _pt(11, 15, 10)

    def boom(_text: str) -> None:
        raise RuntimeError("sns down")

    with pytest.raises(RuntimeError, match="sns down"):
        handler(event, None, store=store, publish=boom, publish_email=boom, now=now)
    emails: list[str] = []
    texts: list[str] = []
    handler(event, None, store=store, publish=texts.append, publish_email=emails.append, now=now)
    handler(
        event,
        None,
        store=store,
        publish=texts.append,
        publish_email=emails.append,
        now=now + timedelta(minutes=5),
    )
    assert texts == [SITE]
    assert len(emails) == 1
    assert emails[0].startswith(SITE)


def test_handler_caps_each_channel_for_the_hour_and_resets_after():
    store = MemoryStore()
    emails: list[str] = []
    texts: list[str] = []
    event = _event("arn:aws:sns:us-west-1:1:invdash-security-alarms")
    handler(event, None, store=store, publish=texts.append, publish_email=emails.append, now=_pt(11, 14, 10))
    handler(event, None, store=store, publish=texts.append, publish_email=emails.append, now=_pt(11, 14, 50))
    assert len(emails) == 1
    assert len(texts) == 1
    handler(event, None, store=store, publish=texts.append, publish_email=emails.append, now=_pt(11, 15, 1))
    assert len(emails) == 2
    assert len(texts) == 2
    assert "suppressed 1 repeat" in emails[1]
    assert "suppressed 1 repeat" in texts[1]


def test_handler_reads_separate_limits_per_channel(monkeypatch):
    monkeypatch.setenv("EMAIL_MAX_PER_WINDOW", "1")
    monkeypatch.setenv("SMS_MAX_PER_WINDOW", "2")
    monkeypatch.setenv("EMAIL_WINDOW_MINUTES", "60")
    monkeypatch.setenv("SMS_WINDOW_MINUTES", "60")
    store = MemoryStore()
    emails: list[str] = []
    texts: list[str] = []
    event = _event()
    for minute in (5, 15, 25):
        handler(
            event,
            None,
            store=store,
            publish=texts.append,
            publish_email=emails.append,
            now=_pt(11, 14, minute),
        )
    assert len(emails) == 1
    assert len(texts) == 2


def test_email_topic_follows_the_source_alarm_topic():
    assert email_topic_arn("arn:aws:sns:us-west-1:1:invdash-ops-alarms", "ops", "sec") == "ops"
    assert email_topic_arn("arn:aws:sns:us-east-1:1:invdash-ops-alerts-us-east-1", "ops", "sec") == "ops"
    assert email_topic_arn("arn:aws:sns:us-west-1:1:invdash-security-alarms", "ops", "sec") == "sec"
    # Legacy source names still route by topic.
    assert email_topic_arn("arn:aws:sns:us-west-1:1:invdash-ops-alerts", "ops", "sec") == "ops"
    assert email_topic_arn("arn:aws:sns:us-west-1:1:invdash-security-alerts", "ops", "sec") == "sec"
    # Only the topic name counts, not a substring elsewhere in the ARN.
    assert email_topic_arn("arn:aws:sns:us-west-1:1:security-alarms-ops", "ops", "sec") == "ops"
    assert email_topic_arn("", "ops", "sec") == "ops"


def test_handler_skips_sms_when_no_handset_is_configured():
    store = MemoryStore()
    emails: list[str] = []
    handler(_event(), None, store=store, publish_email=emails.append, now=_pt(11, 14, 10))
    handler(_event(), None, store=store, publish_email=emails.append, now=_pt(11, 14, 20))
    assert len(emails) == 1


def test_dynamodb_store_caps_each_channel_per_hour(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName="invdash-alert-cap",
            KeySchema=[
                {"AttributeName": "issue_key", "KeyType": "HASH"},
                {"AttributeName": "channel", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "issue_key", "AttributeType": "S"},
                {"AttributeName": "channel", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        store = DynamoCapStore("invdash-alert-cap")
        emails: list[str] = []
        texts: list[str] = []
        first = _pt(11, 9, 10)
        for channel, dest in (("email", emails), ("sms", texts)):
            deliver(SITE, "body", store, dest.append, now=first, channel=channel)
            deliver(SITE, "body", store, dest.append, now=_pt(11, 9, 40), channel=channel)
            deliver(LAMBDA, "body", store, dest.append, now=_pt(11, 9, 40), channel=channel)
            deliver(SITE, "body", store, dest.append, now=_pt(11, 10, 5), channel=channel)
        assert len(emails) == 3
        assert len(texts) == 3
        assert "suppressed 1 repeat" in emails[2]
        assert "suppressed 1 repeat" in texts[2]
        table = boto3.resource("dynamodb").Table("invdash-alert-cap")
        for channel in ("email", "sms"):
            item = table.get_item(Key={"issue_key": "alarm:invdash-site-api-5xx", "channel": channel})["Item"]
            assert int(item["sent"]) == 1
            assert str(item["window"]) == "2026-10-11T10:00-0700"
            # TTL reaches the end of the next window (12:00 PT), not a multi-day hold.
            assert int(item["expires_at"]) == int(_pt(11, 12, 0).timestamp())


# ---------------------------------------------------------------------------
# Vulnerability findings: Inspector and its Security Hub copy are one issue.
# Fixtures are the 2026-10-11 10:46 PT events for CVE-2026-56857 on the jobs
# image (deploy #200), with the account id redacted.
# ---------------------------------------------------------------------------
FIXTURES = Path(__file__).parent / "fixtures" / "notifier"
SECURITY_ALARMS = "arn:aws:sns:us-west-1:123456789012:invdash-security-alarms"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _inspector() -> dict:
    return _fixture("inspector2_finding_cve.json")


def _security_hub() -> dict:
    return _fixture("securityhub_imported_cve.json")


def _sns(payload: dict, subject: str | None = None) -> dict:
    sns = {"TopicArn": SECURITY_ALARMS, "Message": json.dumps(payload)}
    if subject is not None:
        sns["Subject"] = subject
    return {"Records": [{"Sns": sns}]}


def test_inspector_and_security_hub_copy_of_one_cve_share_a_key():
    inspector = issue_key("", json.dumps(_inspector()))
    hub = issue_key("AWS Notification Message", json.dumps(_security_hub()))
    assert inspector == hub == "vuln:CVE-2026-56857:ecr:invdash/jobs"
    assert is_vulnerability_key(inspector)
    assert "inspector" not in inspector and "securityhub" not in inspector


def test_new_image_digest_with_the_same_cve_keeps_the_key():
    later = _inspector()
    digest = "sha256:" + "1" * 64
    later["detail"]["resources"][0]["id"] = later["detail"]["resources"][0]["id"].rsplit("sha256:", 1)[0] + digest
    later["detail"]["resources"][0]["details"]["awsEcrContainerImage"]["imageHash"] = digest
    assert issue_key("", json.dumps(later)) == issue_key("", json.dumps(_inspector()))


def test_other_cve_or_other_repository_is_a_different_issue():
    other_cve = _inspector()
    other_cve["detail"]["packageVulnerabilityDetails"]["vulnerabilityId"] = "CVE-2026-78367"
    other_repo = _security_hub()
    other_repo["detail"]["findings"][0]["Resources"][0]["Details"]["AwsEcrContainerImage"]["RepositoryName"] = "x/api"
    base = issue_key("", json.dumps(_inspector()))
    assert issue_key("", json.dumps(other_cve)) == "vuln:CVE-2026-78367:ecr:invdash/jobs"
    assert issue_key("", json.dumps(other_repo)) == "vuln:CVE-2026-56857:ecr:x/api"
    assert len({base, issue_key("", json.dumps(other_cve)), issue_key("", json.dumps(other_repo))}) == 3


def test_ghsa_advisories_are_keyed_the_same_way():
    inspector = _inspector()
    inspector["detail"]["packageVulnerabilityDetails"]["vulnerabilityId"] = "GHSA-9g5q-2w5x-hmxf"
    inspector["detail"]["title"] = "GHSA-9g5q-2w5x-hmxf - github.com/go-chi/chi/v5"
    hub = _security_hub()
    hub["detail"]["findings"][0]["Vulnerabilities"][0]["Id"] = "GHSA-9g5q-2w5x-hmxf"
    hub["detail"]["findings"][0]["Title"] = "GHSA-9g5q-2w5x-hmxf - github.com/go-chi/chi/v5"
    key = issue_key("", json.dumps(inspector))
    assert key == issue_key("", json.dumps(hub)) == "vuln:GHSA-9G5Q-2W5X-HMXF:ecr:invdash/jobs"


def test_lambda_function_resource_is_keyed_by_function_name():
    inspector = _inspector()
    inspector["detail"]["resources"] = [
        {
            "type": "AWS_LAMBDA_FUNCTION",
            "id": "arn:aws:lambda:us-west-1:123456789012:function:invdash-prefs-api:$LATEST",
            "details": {"awsLambdaFunction": {"functionName": "invdash-prefs-api"}},
        }
    ]
    hub = _security_hub()
    hub["detail"]["findings"][0]["Resources"] = [
        {
            "Type": "AwsLambdaFunction",
            "Id": "arn:aws:lambda:us-west-1:123456789012:function:invdash-prefs-api:$LATEST",
            "Details": {"AwsLambdaFunction": {"FunctionName": "invdash-prefs-api"}},
        }
    ]
    assert issue_key("", json.dumps(inspector)) == issue_key("", json.dumps(hub))
    assert issue_key("", json.dumps(hub)) == "vuln:CVE-2026-56857:lambda:invdash-prefs-api"


def test_non_cve_security_findings_keep_their_product_keys():
    control = _security_hub()
    finding = control["detail"]["findings"][0]
    finding.pop("Vulnerabilities")
    finding["Types"] = ["Software and Configuration Checks/Industry and Regulatory Standards"]
    finding["Title"] = "IAM.6 Hardware MFA should be enabled for the root user"
    finding["GeneratorId"] = "security-control/IAM.6"
    assert issue_key("", json.dumps(control)) == "securityhub:IAM.6 Hardware MFA should be enabled for the root user"

    network = _inspector()
    network["detail"]["type"] = "NETWORK_REACHABILITY"
    network["detail"].pop("packageVulnerabilityDetails")
    network["detail"]["title"] = "Port 22 is reachable from an Internet Gateway"
    assert issue_key("", json.dumps(network)) == "inspector:Port 22 is reachable from an Internet Gateway"

    probe = "Recon:EC2/PortProbeUnprotectedPort"
    guardduty = json.dumps({"detail-type": "GuardDuty Finding", "detail": {"type": probe}})
    assert issue_key("", guardduty) == "guardduty:Recon:EC2/PortProbeUnprotectedPort"
    assert not is_vulnerability_key(issue_key("", json.dumps(control)))


def test_handler_mails_one_cve_once_for_inspector_and_its_security_hub_copy(monkeypatch):
    monkeypatch.setenv("VULN_WINDOW_MINUTES", "1440")
    store = MemoryStore()
    emails: list[str] = []
    texts: list[str] = []
    at = _pt(11, 10, 46)
    handler(_sns(_inspector()), None, store=store, publish=texts.append, publish_email=emails.append, now=at)
    handler(
        _sns(_security_hub(), "AWS Notification Message"),
        None,
        store=store,
        publish=texts.append,
        publish_email=emails.append,
        now=at + timedelta(seconds=11),
    )
    assert len(emails) == 1 and len(texts) == 1
    assert "CVE-2026-56857" in emails[0]
    item = store.items[("email", "vuln:CVE-2026-56857:ecr:invdash/jobs")]
    assert item["sent"] == 1 and item["suppressed"] == 1


def test_cve_rescans_wait_for_the_long_window_but_alarms_keep_the_hour(monkeypatch):
    monkeypatch.setenv("VULN_WINDOW_MINUTES", "1440")
    store = MemoryStore()
    emails: list[str] = []
    for hour in (10, 13, 23):  # rescans and image pushes later the same Pacific day
        handler(_sns(_inspector()), None, store=store, publish_email=emails.append, now=_pt(11, hour, 46))
    assert len(emails) == 1
    handler(_sns(_inspector()), None, store=store, publish_email=emails.append, now=_pt(12, 0, 5))
    assert len(emails) == 2
    assert "suppressed 2 repeats" in emails[1].partition("\n")[0]

    alarm_store = MemoryStore()
    alarm_mail: list[str] = []
    for hour in (10, 11):
        handler(_event(), None, store=alarm_store, publish_email=alarm_mail.append, now=_pt(11, hour, 5))
    assert len(alarm_mail) == 2


def test_vuln_window_defaults_to_one_pacific_day(monkeypatch):
    monkeypatch.delenv("VULN_WINDOW_MINUTES", raising=False)
    store = MemoryStore()
    emails: list[str] = []
    handler(_sns(_inspector()), None, store=store, publish_email=emails.append, now=_pt(11, 1, 0))
    handler(_sns(copy.deepcopy(_security_hub())), None, store=store, publish_email=emails.append, now=_pt(11, 22, 0))
    assert len(emails) == 1


def test_deliver_without_vuln_window_uses_the_channel_window():
    store = MemoryStore()
    sent: list[str] = []
    body = json.dumps(_inspector())
    deliver("", body, store, sent.append, now=_pt(11, 10, 46), channel="email")
    deliver("", body, store, sent.append, now=_pt(11, 11, 46), channel="email")
    assert len(sent) == 2
