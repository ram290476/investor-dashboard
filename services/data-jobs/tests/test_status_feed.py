import json
from datetime import UTC, datetime

import boto3
from moto import mock_aws

import backfill
import status_feed as sf


def test_next_run_skips_weekend_and_holiday():
    # Friday 2026-11-20 after the close -> next H1 is Monday 10:05 ET (15:05 UTC)
    assert sf.next_run("H1", datetime(2026, 11, 20, 22, tzinfo=UTC)) == "2026-11-23T15:05+00:00"
    # Wednesday 2026-11-25 evening -> Thursday is Thanksgiving, so Friday 10:05 ET
    assert sf.next_run("H1", datetime(2026, 11, 25, 22, tzinfo=UTC)) == "2026-11-27T15:05+00:00"
    assert sf.next_run("TREND", datetime(2026, 11, 25, tzinfo=UTC)) is None


def test_apply_event_statuses():
    now = datetime(2026, 10, 5, 21, tzinfo=UTC)
    feed = sf.apply_event(None, {"job": "D4", "outcome": "success", "failed_sources": 1}, now)
    jobs = {j["job"]: j for j in feed["jobs"]}
    assert jobs["D4"]["status"] == "partial" and jobs["H1"]["status"] == "never_run"
    feed = sf.apply_event(feed, {"job": "D4", "outcome": "failure"}, now)
    assert {j["job"]: j for j in feed["jobs"]}["D4"]["status"] == "failed"
    assert set(jobs) == set(sf.JOBS)


def test_update_feed_conditional_write(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="lake")
        sf.update_feed(s3, "lake", {"job": "H1", "outcome": "success"})
        sf.update_feed(s3, "lake", {"job": "H2", "outcome": "success"})
        feed = json.loads(s3.get_object(Bucket="lake", Key="serving/status.json")["Body"].read())
        statuses = {j["job"]: j["status"] for j in feed["jobs"]}
        assert statuses["H1"] == "ok" and statuses["H2"] == "ok"


def test_backfill_event_parsing():
    assert backfill.tickers_from_event({"detail-type": "TickerAdded", "detail": {"ticker": "rivn"}}) == (
        ["RIVN"],
        False,
    )
    assert backfill.tickers_from_event({"tickers": ["tsla"], "force": True}) == (["TSLA"], True)
