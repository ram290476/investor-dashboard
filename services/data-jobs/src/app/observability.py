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

    from observability import job_handler, source_run

    @job_handler("H1")
    def handler(event, context):
        with source_run("DS-02") as run:
            bars = fetch_alpaca_bars()
            write_curated(bars)
            run["rows"] = len(bars)

Lambda environment (Terraform output `lambda_environment`):
    POWERTOOLS_SERVICE_NAME, POWERTOOLS_METRICS_NAMESPACE, AWS_USE_FIPS_ENDPOINT=true
Requires: aws-lambda-powertools[tracer] >= 3.0
"""

from __future__ import annotations

import functools
import json
import os
import re
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit, single_metric

SERVICE = os.getenv("POWERTOOLS_SERVICE_NAME", "invdash")

logger = Logger(service=SERVICE, utc=True)
tracer = Tracer(service=SERVICE)
metrics = Metrics(namespace=os.getenv("POWERTOOLS_METRICS_NAMESPACE", "InvestorDashboard"), service=SERVICE)

# Query-string credentials (Finnhub token=, Massive apiKey=, ...) appear in httpx error text because it
# quotes the full request URL. Scrub them before anything reaches CloudWatch Logs (#232).
_SECRET_PARAM = re.compile(
    r"(?i)\b(token|access_token|api_?key|apikey|key|secret|password|signature)=([^&\s'\"]+)"
)


def redact_secrets(text: str) -> str:
    """Replace credential values in URL query strings with [REDACTED]."""
    return _SECRET_PARAM.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)


_runs: list[dict[str, Any]] = []

JOB_EVENT_SOURCE = f"{SERVICE}.jobs"
_events = None
# Keys a handler return value may add to the Job Finished detail. rows_stored is published as rows_written.
_EVENT_FIELDS = (
    "batches",
    "rows_written",
    "mode",
    "dropped",
    "over_cap",
    "status",
    "reason",
    # Company IR chain: collect hands manifests to extract, extract hands tickers to serve.
    "collected",
    "documents",
    "skipped",
    "tickers",
    "manifests",
    "extracted",
    "mismatches",
    "served",
    "run_status",
    "filing",
    # Backfill: how many tickers reached a terminal success in this run. Dashboard rebuilds on > 0.
    "tickers_completed",
    # SEC filings run: tickers processed, new filings, failures, no_sec_filer.
    "summary",
)


def _finished_detail(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    detail = {key: result[key] for key in _EVENT_FIELDS if result.get(key) is not None}
    if "rows_written" not in detail and result.get("rows_stored") is not None:
        detail["rows_written"] = result["rows_stored"]
    return detail


def chain_outcome(result: Any, default: str = "success") -> str:
    """Publish partial and failed collector runs as those outcomes.

    Only `run_status` opts in. Other jobs keep outcome success when some sources fail
    and the status feed still marks them partial from failed_sources. An exception
    already set default to failure and must stay a failure.
    """
    if default != "success" or not isinstance(result, dict):
        return default
    reported = result.get("run_status")
    if reported == "partial":
        return "partial"
    if reported in {"failed", "failure"}:
        return "failure"
    return default


def emit_job_finished(job_id: str, run_id: str, outcome: str, detail: dict[str, Any] | None = None) -> None:
    """Publish a 'Job Finished' event on the default EventBridge bus.

    EventBridge rules use it to chain work: trend_metrics after D4 and M1 succeed,
    the status feed after every job, and ticker backfill after a TickerAdded event.
    A rejected publish fails the successful job so Lambda can retry its idempotent work.
    Failure reporting must not mask the original collector exception.
    """
    global _events
    try:
        import boto3

        _events = _events or boto3.client("events")
        response = _events.put_events(
            Entries=[
                {
                    "Source": JOB_EVENT_SOURCE,
                    "DetailType": "Job Finished",
                    "Detail": json.dumps({"job": job_id, "run_id": run_id, "outcome": outcome, **(detail or {})}),
                }
            ]
        )
        if response.get("FailedEntryCount"):
            raise RuntimeError("EventBridge rejected the job-finished event")
    except Exception:
        logger.exception("emit_job_finished_failed")
        # A dropped success or partial event would skip the downstream job, so retry it.
        if outcome in {"success", "partial"}:
            raise


def job_handler(
    job_id: str,
    persist_runs: Callable[[list[dict[str, Any]]], None] | None = None,
    emit_event: bool = True,
    lease_key: str | None = None,
    skip_when_leased: bool = True,
):
    """Wrap a Lambda handler: correlation ids, tracing, metric flush, run records, job event.

    persist_runs receives one dict per source run (the ingestion_runs rows) and
    should append them to the lake, e.g. curated/ingestion_runs/year=/month=/<run_id>.parquet.
    emit_event publishes 'Job Finished' (see emit_job_finished) when the handler returns or raises.
    skip_when_leased (default True) returns a skipped result instead of failing when lease_key is
    still held, so an overlapping invoke logs ``job_skipped`` and exits cleanly rather than raising
    LeaseHeld, which counted as a Lambda error, got retried twice and landed on the dead-letter
    queue. Pass False only for a job whose event must not be dropped while another run is active.
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
            result = None
            try:
                from lake import LeaseHeld, job_lease

                with job_lease(lease_key) if lease_key else nullcontext():
                    result = fn(event, context)
                return result
            except LeaseHeld:
                if skip_when_leased:
                    outcome = "skipped"
                    result = {"status": "skipped", "reason": "lease held"}
                    logger.info(
                        "job_skipped",
                        extra={"event": "job_run", "outcome": outcome, "reason": "lease held"},
                    )
                    return result
                outcome = "failure"
                _log_job_failed(outcome)
                raise
            except Exception:
                outcome = "failure"
                _log_job_failed(outcome)
                raise  # Lambda retries twice, then sends the event to the dead-letter queue.
            finally:
                outcome = chain_outcome(result, outcome)
                failed_ids = sorted({r["source_id"] for r in _runs if r["outcome"] == "failure"})
                logger.info(
                    "job_finished",
                    extra={
                        "event": "job_run",
                        "outcome": outcome,
                        "sources": len(_runs),
                        "failed_sources": sum(r["outcome"] == "failure" for r in _runs),
                        "failed_source_ids": failed_ids,
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
                            "failed_source_ids": failed_ids,
                            **_finished_detail(result),
                        },
                    )

        return wrapper

    return decorator


FAILED_SOURCES_METRIC = "FailedSources"
# Seconds into the clock hour at which every FailedSources datapoint is stamped (#233).
FAILED_SOURCES_STAMP_OFFSET_S = 30 * 60
_failed_source_buckets: set[tuple[str, int]] = set()


def failed_source_bucket(now: datetime) -> datetime:
    """The timestamp for a FailedSources datapoint: HH:30:00 UTC of the failure's clock hour.

    Every failure in one clock hour lands on the same minute, and consecutive hours are exactly 3600 s
    apart. A 3600 s alarm window (start-inclusive, end-exclusive) therefore holds exactly one bucket,
    so a source that fails once an hour counts 1 no matter whether it logged at :18 or :19. HH:30 keeps
    the stamp within 30 minutes of the real time (CloudWatch accepts 2 h ahead / 14 days back).
    """
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return hour + timedelta(seconds=FAILED_SOURCES_STAMP_OFFSET_S)


def _emit_failed_source(source_id: str, now: datetime | None = None) -> bool:
    """Emit FailedSources=1 at most once per source per clock hour from this container (#233).

    Dimensions are only {service}, so the alarm is a plain metric alarm (no SEARCH, no metric math).
    Returns True when a datapoint was emitted.
    """
    stamp = failed_source_bucket(now or datetime.now(UTC))
    key = (source_id, int(stamp.timestamp()))
    if key in _failed_source_buckets:
        return False
    try:
        with single_metric(
            name=FAILED_SOURCES_METRIC,
            unit=MetricUnit.Count,
            value=1,
            namespace=metrics.namespace,
            default_dimensions={"service": SERVICE},
        ) as metric:
            metric.set_timestamp(stamp)
        _failed_source_buckets.add(key)
        return True
    except Exception:
        logger.exception("failed_source_metric_failed")
        return False


def _log_job_failed(outcome: str) -> None:
    """job_failed with the traceback scrubbed of query-string credentials (#232)."""
    logger.error(
        "job_failed",
        extra={"event": "job_run", "outcome": outcome, "exception": redact_secrets(traceback.format_exc())},
    )


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
            run.update(outcome="failure", error_type=type(exc).__name__, error=redact_secrets(str(exc))[:500])
            subsegment.add_exception(exc, stack=traceback.extract_tb(exc.__traceback__))
            metrics.add_metric(name="FailedRuns", unit=MetricUnit.Count, value=1)
            _emit_failed_source(source_id)
        finally:
            run["duration_ms"] = round((time.monotonic() - started) * 1000)
            metrics.add_metric(name="RowsWritten", unit=MetricUnit.Count, value=run["rows"])
            log = logger.error if run["outcome"] == "failure" else logger.info
            log("source_run", extra={"event": "source_run", **run})
            _runs.append(run)
