import json
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

import lake
import observability
import status_feed


def test_rejected_event_publish_is_not_silently_successful(monkeypatch):
    class Bus:
        def put_events(self, **kwargs):
            return {"FailedEntryCount": 1}

    monkeypatch.setattr(observability, "_events", Bus())
    with pytest.raises(RuntimeError, match="EventBridge rejected"):
        observability.emit_job_finished("TREND", "run", "success")
    # Preserve the original exception when reporting an already failed job.
    observability.emit_job_finished("TREND", "run", "failure")


def test_partial_source_ids_and_skipped_status_are_actionable():
    now = datetime(2026, 10, 8, tzinfo=UTC)
    feed = status_feed.apply_event(None, {
        "job": "D1", "outcome": "success", "failed_sources": 1, "failed_source_ids": ["fred:SOFR"],
    }, now)
    entry = next(row for row in feed["jobs"] if row["job"] == "D1")
    assert entry["status"] == "partial"
    assert entry["failed_source_ids"] == ["fred:SOFR"]
    feed = status_feed.apply_event(feed, {
        "job": "D1", "outcome": "success", "status": "skipped", "reason": "federal holiday",
    }, now)
    entry = next(row for row in feed["jobs"] if row["job"] == "D1")
    assert entry["status"] == "skipped"
    assert entry["failed_source_ids"] == []
    assert entry["reason"] == "federal holiday"
    assert not {"D2", "D3", "W1"} & set(status_feed.JOBS)


def test_source_failure_ids_are_emitted_without_error_payloads(monkeypatch):
    captured = []
    monkeypatch.setattr(observability, "emit_job_finished",
                        lambda job, run, outcome, detail=None: captured.append(detail))

    class Context:
        function_name = "test"
        memory_limit_in_mb = 128
        invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test"
        aws_request_id = "test"

    @observability.job_handler("D1")
    def run(event, context):
        with observability.source_run("fred:SOFR"):
            raise ValueError("private upstream payload")
        return {"status": "success"}

    run({}, Context())
    assert captured[0]["failed_source_ids"] == ["fred:SOFR"]
    assert captured[0]["failed_sources"] == 1
    assert "private upstream payload" not in json.dumps(captured)


def test_job_lease_excludes_concurrent_publishers_and_recovers_after_expiry(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", client)
        key = "serving/trend_metrics/_lease.json"
        with lake.job_lease(key):
            with pytest.raises(RuntimeError, match="already running"):
                with lake.job_lease(key):
                    pytest.fail("concurrent publisher entered")
        with lake.job_lease(key):
            pass
        client.put_object(Bucket="lake", Key=key, Body=json.dumps({"expires_at": 0}).encode())
        with lake.job_lease(key):
            pass


def test_job_lease_is_released_after_exception(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", client)
        with pytest.raises(ValueError):
            with lake.job_lease("lease"):
                raise ValueError("build failed")
        with lake.job_lease("lease"):
            pass
