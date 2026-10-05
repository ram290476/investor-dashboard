"""Observability helpers for the Investor Dashboard job Lambdas.

Gives every collector and build job the same structured logs, traces and metrics:

- Logs (AU-2, AU-3, AU-8): JSON, UTC timestamps, and on every line the job,
  run_id, source_id, outcome and X-Ray trace id, so one run can be followed end to end.
- Traces (SI-4): X-Ray segments per job and per source call.
- Metrics (SI-4, CA-7): six custom metrics with one fixed dimension
  (service=<project>). Never add per-job or per-source dimensions: each new
  dimension value is a new billable metric. Per-source detail belongs in the
  ingestion_runs table and the logs.

Usage in a collector:

    from observability import job_handler, source_run, record_rate_headroom

    @job_handler("H1")
    def handler(event, context):
        with source_run("DS-02") as run:
            bars = fetch_alpaca_bars()
            write_curated(bars)
            run["rows"] = len(bars)
        record_rate_headroom(provider="alpaca", used=7, limit=288_000)

Lambda environment (Terraform output `lambda_environment`):
    POWERTOOLS_SERVICE_NAME, POWERTOOLS_METRICS_NAMESPACE, AWS_USE_FIPS_ENDPOINT=true
Requires: aws-lambda-powertools[tracer] >= 3.0
"""

from __future__ import annotations

import functools
import json
import os
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

SERVICE = os.getenv("POWERTOOLS_SERVICE_NAME", "invdash")

logger = Logger(service=SERVICE, utc=True)
tracer = Tracer(service=SERVICE)
metrics = Metrics(namespace=os.getenv("POWERTOOLS_METRICS_NAMESPACE", "InvestorDashboard"), service=SERVICE)

# Max age before a source counts as stale, by refresh tier (matches the doc's freshness rules).
MAX_AGE_HOURS = {"hourly": 2, "daily": 30, "weekly": 24 * 8, "monthly": 24 * 35, "quarterly": 24 * 100}

_runs: list[dict[str, Any]] = []

JOB_EVENT_SOURCE = f"{SERVICE}.jobs"
_events = None


def emit_job_finished(job_id: str, run_id: str, outcome: str, detail: dict[str, Any] | None = None) -> None:
    """Publish a 'Job Finished' event on the default EventBridge bus.

    EventBridge rules use it to chain work: trend_metrics after D4 and M1 succeed,
    the status feed after every job, and ticker backfill after a TickerAdded event.
    Never raises: a failed publish is logged and the job result stands.
    """
    global _events
    try:
        import boto3

        _events = _events or boto3.client("events")
        _events.put_events(
            Entries=[
                {
                    "Source": JOB_EVENT_SOURCE,
                    "DetailType": "Job Finished",
                    "Detail": json.dumps({"job": job_id, "run_id": run_id, "outcome": outcome, **(detail or {})}),
                }
            ]
        )
    except Exception:  # observability must not fail the job
        logger.exception("emit_job_finished_failed")


def job_handler(
    job_id: str,
    persist_runs: Callable[[list[dict[str, Any]]], None] | None = None,
    emit_event: bool = True,
):
    """Wrap a Lambda handler: correlation ids, tracing, metric flush, run records, job event.

    persist_runs receives one dict per source run (the ingestion_runs rows) and
    should append them to the lake, e.g. curated/ingestion_runs/year=/month=/<run_id>.parquet.
    emit_event publishes 'Job Finished' (see emit_job_finished) when the handler returns or raises.
    """

    def decorator(fn):
        @functools.wraps(fn)
        @logger.inject_lambda_context(clear_state=True)
        @tracer.capture_lambda_handler(capture_response=False)
        @metrics.log_metrics(capture_cold_start_metric=False)  # cold-start metric would add one metric per function
        def wrapper(event, context):
            run_id = f"{job_id}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
            logger.append_keys(job=job_id, run_id=run_id)  # Powertools adds xray_trace_id itself
            tracer.put_annotation("job", job_id)
            tracer.put_annotation("run_id", run_id)
            _runs.clear()
            started = time.monotonic()
            outcome = "success"
            try:
                return fn(event, context)
            except Exception:
                outcome = "failure"
                logger.exception("job_failed", extra={"event": "job_run", "outcome": outcome})
                raise  # Lambda retries twice, then sends the event to the dead-letter queue.
            finally:
                logger.info(
                    "job_finished",
                    extra={
                        "event": "job_run",
                        "outcome": outcome,
                        "sources": len(_runs),
                        "failed_sources": sum(r["outcome"] == "failure" for r in _runs),
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    },
                )
                if persist_runs and _runs:
                    try:
                        persist_runs([dict(r, run_id=run_id, job=job_id) for r in _runs])
                    except Exception:  # never let bookkeeping hide the real result
                        logger.exception("persist_runs_failed")
                if emit_event:
                    emit_job_finished(
                        job_id,
                        run_id,
                        outcome,
                        {
                            "sources": len(_runs),
                            "failed_sources": sum(r["outcome"] == "failure" for r in _runs),
                        },
                    )

        return wrapper

    return decorator


@contextmanager
def source_run(source_id: str) -> Iterator[dict[str, Any]]:
    """Time one source fetch; log, trace and count its outcome.

    A failing source is logged and counted but does not stop the other sources
    in the job (bulkhead). Set run["rows"] inside the block.
    """
    run: dict[str, Any] = {
        "source_id": source_id,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": 0,
        "outcome": "success",
        "error_type": None,
        "error": None,
    }
    started = time.monotonic()
    with tracer.provider.in_subsegment(f"source:{source_id}") as subsegment:
        subsegment.put_annotation("source_id", source_id)
        try:
            yield run
        except Exception as exc:  # noqa: BLE001 - bulkhead: record and continue with the next source
            run.update(outcome="failure", error_type=type(exc).__name__, error=str(exc)[:500])
            subsegment.add_exception(exc, stack=traceback.extract_tb(exc.__traceback__))
            metrics.add_metric(name="FailedRuns", unit=MetricUnit.Count, value=1)
        finally:
            run["duration_ms"] = round((time.monotonic() - started) * 1000)
            metrics.add_metric(name="RowsWritten", unit=MetricUnit.Count, value=run["rows"])
            log = logger.error if run["outcome"] == "failure" else logger.info
            log("source_run", extra={"event": "source_run", **run})
            _runs.append(run)


def record_rate_headroom(provider: str, used: int, limit: int) -> None:
    """Report how much of a provider's free quota is left (alarm under 20%)."""
    headroom = max(0.0, 100.0 * (1 - used / limit)) if limit else 100.0
    logger.info("rate_headroom", extra={"event": "rate_headroom", "provider": provider, "used": used, "limit": limit})
    metrics.add_metric(name="RateLimitHeadroomPct", unit=MetricUnit.Percent, value=round(headroom, 1))


def publish_freshness(latest_success: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """Build job: compute freshness from the latest successful run per source.

    latest_success rows: {"source_id", "priority" ("P1"/"P2"/"P3"), "tier", "last_success" (aware datetime),
    "due" (bool: False on weekends/holidays when the source is not expected to update)}.
    Emits StaleP1Sources, StaleSources and FreshP1Ratio, and returns the public health.json body.
    """
    now = now or datetime.now(UTC)
    stale, stale_p1, p1_due = [], 0, 0
    for row in latest_success:
        if not row.get("due", True):
            continue
        age_h = (now - row["last_success"]).total_seconds() / 3600
        if age_h > MAX_AGE_HOURS[row["tier"]]:
            stale.append(row["source_id"])
            stale_p1 += row["priority"] == "P1"
        p1_due += row["priority"] == "P1"

    metrics.add_metric(name="StaleSources", unit=MetricUnit.Count, value=len(stale))
    metrics.add_metric(name="StaleP1Sources", unit=MetricUnit.Count, value=stale_p1)
    if p1_due:
        metrics.add_metric(name="FreshP1Ratio", unit=MetricUnit.Count, value=round(1 - stale_p1 / p1_due, 4))
    logger.info("freshness", extra={"event": "freshness", "stale": stale, "stale_p1": stale_p1, "p1_due": p1_due})

    # Public, non-sensitive health document read by the external canary.
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stale_p1": stale_p1,
        "stale_total": len(stale),
    }
