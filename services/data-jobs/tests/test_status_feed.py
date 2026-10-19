import json
from datetime import UTC, datetime

import boto3
from moto import mock_aws

import backfill
import status_feed as sf


def test_every_job_has_a_cron_or_a_trigger():
    now = datetime(2026, 10, 8, 15, tzinfo=UTC)
    feed = sf.apply_event(None, {"job": "D4", "outcome": "success", "failed_sources": 0}, now)
    by_job = {row["job"]: row for row in feed["jobs"]}
    assert set(by_job) == set(sf.JOBS)
    for job, (name, cron, _calendar, trigger) in sf.JOBS.items():
        assert name
        row = by_job[job]
        assert row["trigger"] == trigger
        if job in sf.UNSCHEDULED:
            assert cron is None and trigger is None and row["next_run"] is None
            continue
        assert bool(cron) != bool(trigger), job
        if cron:
            assert row["next_run"]
            assert trigger is None
        else:
            assert row["next_run"] is None
            assert trigger
    assert by_job["M1"]["next_run"]
    assert by_job["Q1"]["next_run"]
    assert by_job["TREND"]["trigger"] == "After market close and release day"
    assert by_job["BACKFILL"]["trigger"] == "When a ticker is added"
    assert by_job["KALSHI"]["trigger"] is None


def test_partial_outcome_is_partial_and_failure_is_failed():
    now = datetime(2026, 10, 10, 12, tzinfo=UTC)
    feed = sf.apply_event(None, {"job": "Q2C", "outcome": "partial", "failed_sources": 45}, now)
    row = {job["job"]: job for job in feed["jobs"]}["Q2C"]
    assert row["status"] == "partial"
    assert row["last_outcome"] == "partial"
    assert row["failed_sources"] == 45
    feed = sf.apply_event(feed, {"job": "Q2C", "outcome": "failure", "failed_sources": 0}, now)
    row = {job["job"]: job for job in feed["jobs"]}["Q2C"]
    assert row["status"] == "failed"
    assert row["last_outcome"] == "failure"


def test_next_run_skips_weekend_and_holiday():
    # Friday 2026-11-20 after the close -> next H1 is Monday 10:05 ET (15:05 UTC)
    assert sf.next_run("H1", datetime(2026, 11, 20, 22, tzinfo=UTC)) == "2026-11-23T15:05+00:00"
    # Wednesday 2026-11-25 evening -> Thursday is Thanksgiving, so Friday 10:05 ET
    assert sf.next_run("H1", datetime(2026, 11, 25, 22, tzinfo=UTC)) == "2026-11-27T15:05+00:00"
    assert sf.next_run("TREND", datetime(2026, 11, 25, tzinfo=UTC)) is None


def test_links_run_records_last_links_run_without_moving_last_run():
    now = datetime(2026, 10, 8, 15, tzinfo=UTC)
    prior = "2026-10-01T12:00:00+00:00"
    feed = sf.apply_event(
        {"jobs": [{"job": "M1", "status": "ok", "last_run": prior, "last_outcome": "success", "failed_sources": 0}]},
        {"job": "M1", "outcome": "success", "mode": "links", "failed_sources": 0, "dropped": []},
        now,
    )
    m1 = {j["job"]: j for j in feed["jobs"]}["M1"]
    assert m1["last_run"] == prior and m1["status"] == "ok"
    assert m1["last_links_run"] == now.isoformat(timespec="seconds")
    assert "over_cap" not in m1
    feed = sf.apply_event(
        feed,
        {"job": "M1", "outcome": "success", "failed_sources": 0, "dropped": ["ZZZ"]},
        now,
    )
    m1 = {j["job"]: j for j in feed["jobs"]}["M1"]
    assert m1["last_run"] == now.isoformat(timespec="seconds")
    assert m1["last_links_run"] == now.isoformat(timespec="seconds")
    assert m1["over_cap"] == "over the ticker cap" and m1["dropped"] == ["ZZZ"]


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
