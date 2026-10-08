"""Resumable five-year Yahoo daily-price history load (DS-05).

An initial or TickerAdded event seeds S3 state. Scheduled invocations then
retrieve bounded batches from newest to oldest until every ticker is complete.
Each batch is upserted into the ticker's yearly partitions (lake.upsert_prices), so
retrying a request replaces the same rows instead of creating duplicates, and never
overwrites a daily_prices (DS-02) close for the same day.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

STATE_KEY = "curated/prices_daily/_backfill/state.json"
DEFAULT_YEARS = 5
DEFAULT_BATCH_DAYS = 90
DEFAULT_MAX_BATCHES = 5


def tickers_from_event(event: dict) -> tuple[list[str], bool]:
    if event.get("detail-type") == "TickerAdded":
        ticker = str((event.get("detail") or {}).get("ticker", "")).strip().upper()
        return ([ticker] if ticker else []), False
    tickers = event.get("tickers", [])
    if not isinstance(tickers, list):
        raise ValueError("tickers must be a list")
    clean = list(dict.fromkeys(str(t).strip().upper() for t in tickers if str(t).strip()))
    return clean, bool(event.get("force"))


def years_ago(today: date, years: int = DEFAULT_YEARS) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def batch_bounds(cursor: date, earliest: date, batch_days: int = DEFAULT_BATCH_DAYS) -> tuple[date, date]:
    if batch_days < 1:
        raise ValueError("batch_days must be positive")
    end = cursor
    start = max(earliest, end - timedelta(days=batch_days))
    if start >= end:
        raise ValueError("cursor must be later than the historical start date")
    return start, end


def new_backfill_state(tickers: list[str], today: date, years: int = DEFAULT_YEARS) -> dict:
    earliest = years_ago(today, years)
    return {
        "version": 1,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "years": years,
        "earliest_date": earliest.isoformat(),
        "next_index": 0,
        "tickers": {
            ticker: {
                "cursor": today.isoformat(),
                "complete": False,
                "batches": 0,
                "rows_stored": 0,
            }
            for ticker in tickers
        },
    }


def _is_empty_historical_window(payload: dict) -> bool:
    chart = payload.get("chart") or {}
    result = chart.get("result") or []
    if result:
        return not (result[0].get("timestamp") or [])
    error = chart.get("error") or {}
    message = str(error.get("description") or error.get("message") or "").lower()
    return "no data found" in message


def _queue_requested_tickers(state: dict, tickers: list[str], force: bool, today: date) -> None:
    if not tickers:
        return
    existing = state.get("tickers", {})
    for ticker in tickers:
        if not ticker:
            continue
        if not force and ticker in existing:
            continue
        reset = new_backfill_state([ticker], today, int(state.get("years", DEFAULT_YEARS)))
        existing[ticker] = reset["tickers"][ticker]
        state["earliest_date"] = reset["earliest_date"]
    state["tickers"] = existing
    state.setdefault("started_at", datetime.now(UTC).isoformat(timespec="seconds"))


def handler(event, context):  # pragma: no cover - thin AWS wrapper over collectors.yahoo
    import polars as pl
    from aws_lambda_powertools.metrics import MetricUnit

    from lake import LAKE_BUCKET, read_json, upsert_prices, write_json
    from observability import job_handler, logger, metrics, source_run

    @job_handler("BACKFILL")
    def run(event, context):
        bucket = LAKE_BUCKET
        if not bucket:
            raise RuntimeError("LAKE_BUCKET is not configured")

        today = datetime.now(UTC).date()
        years = int(os.getenv("BACKFILL_YEARS", str(DEFAULT_YEARS)))
        batch_days = int(os.getenv("BACKFILL_BATCH_DAYS", str(DEFAULT_BATCH_DAYS)))
        max_batches = int(os.getenv("BACKFILL_MAX_BATCHES", str(DEFAULT_MAX_BATCHES)))
        if years < 1 or batch_days < 1 or max_batches < 1:
            raise ValueError("BACKFILL_YEARS, BACKFILL_BATCH_DAYS and BACKFILL_MAX_BATCHES must be positive")

        state = read_json(STATE_KEY, bucket) or new_backfill_state([], today, years)
        if int(state.get("years", years)) != years and not event.get("force"):
            raise ValueError("BACKFILL_YEARS changed while a load is active; use force to start a new range")
        state["years"] = years
        tickers, force = tickers_from_event(event)
        if force:
            state = new_backfill_state([], today, years)
        _queue_requested_tickers(state, tickers, force, today)
        state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        write_json(state, STATE_KEY, bucket, cache_seconds=0)

        from collectors import yahoo
        from http_client import get_client, request_with_retry

        ticker_order = list(state.get("tickers", {}))
        if not ticker_order:
            return {"status": "idle", "batches": 0, "pending_tickers": []}

        earliest = date.fromisoformat(state["earliest_date"])
        pending_before = [t for t in ticker_order if not state["tickers"][t].get("complete")]
        batches_done = 0
        stored_rows = 0
        no_data_tickers: list[str] = []
        failed_tickers: list[str] = []
        index = int(state.get("next_index", 0)) % len(ticker_order)

        with get_client() as http:
            while batches_done < max_batches:
                pending = [t for t in ticker_order if not state["tickers"][t].get("complete")]
                if not pending:
                    break
                if set(pending) <= set(failed_tickers):
                    break
                ticker = ticker_order[index % len(ticker_order)]
                index = (index + 1) % len(ticker_order)
                progress = state["tickers"][ticker]
                if progress.get("complete") or ticker in failed_tickers:
                    continue

                cursor = date.fromisoformat(progress["cursor"])
                start, end = batch_bounds(cursor, earliest, batch_days)
                params = yahoo.chart_params(start=start, end=end)
                with source_run("DS-05") as record:
                    response = request_with_retry(
                        http,
                        "GET",
                        yahoo.CHART_URL.format(ticker=ticker),
                        params=params,
                    )
                    payload = response.json()
                    empty_window = _is_empty_historical_window(payload)
                    rows = [] if empty_window else yahoo.parse_chart(payload, ticker)
                    received = len((payload.get("chart", {}).get("result") or [{}])[0].get("timestamp") or [])
                    if rows:
                        frame = pl.DataFrame(rows).sort("date")
                        upsert_prices(frame, bucket)
                        record["rows"] = frame.height
                        stored_rows += frame.height

                    progress["batches"] = int(progress.get("batches", 0)) + 1
                    progress["rows_stored"] = int(progress.get("rows_stored", 0)) + len(rows)
                    progress["last_batch"] = {
                        "start": start.isoformat(),
                        "end_exclusive": end.isoformat(),
                        "rows_received": received,
                        "rows_processed": len(rows),
                        "rows_rejected": max(0, received - len(rows)),
                        "rows_stored": len(rows),
                    }
                    metrics.add_metric(name="BackfillDaysRequested", unit=MetricUnit.Count, value=(end - start).days)
                    metrics.add_metric(name="BackfillRowsReceived", unit=MetricUnit.Count, value=received)
                    metrics.add_metric(name="BackfillRowsProcessed", unit=MetricUnit.Count, value=len(rows))
                    metrics.add_metric(
                        name="BackfillRowsRejected", unit=MetricUnit.Count, value=max(0, received - len(rows))
                    )
                    logger.info(
                        "backfill_batch",
                        extra={
                            "ticker": ticker,
                            "requested_start": start.isoformat(),
                            "requested_end_exclusive": end.isoformat(),
                            "rows_received": received,
                            "rows_processed": len(rows),
                            "rows_rejected": max(0, received - len(rows)),
                            "rows_stored": len(rows),
                        },
                    )
                    if not rows and empty_window:
                        progress["complete"] = True
                        progress["no_data_before"] = end.isoformat()
                        no_data_tickers.append(ticker)
                    else:
                        progress["cursor"] = start.isoformat()
                        if start <= earliest:
                            progress["complete"] = True
                            progress["cursor"] = earliest.isoformat()

                if record["outcome"] == "failure":
                    failed_tickers.append(ticker)
                    continue

                batches_done += 1
                state["next_index"] = index
                state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
                write_json(state, STATE_KEY, bucket, cache_seconds=0)

        pending_after = [t for t in ticker_order if not state["tickers"][t].get("complete")]
        state["status"] = "running" if pending_after else "complete"
        state["completed_at"] = None if pending_after else datetime.now(UTC).isoformat(timespec="seconds")
        state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        write_json(state, STATE_KEY, bucket, cache_seconds=0)

        logger.info(
            "backfill_progress",
            extra={
                "status": state["status"],
                "batches_this_run": batches_done,
                "rows_stored_this_run": stored_rows,
                "tickers_pending": pending_after,
                "no_data_tickers": no_data_tickers,
                "failed_tickers": failed_tickers,
                "tickers_pending_before": pending_before,
            },
        )
        return {
            "status": state["status"],
            "batches": batches_done,
            "rows_stored": stored_rows,
            "rows_written": stored_rows,
            "pending_tickers": pending_after,
            "no_data_tickers": no_data_tickers,
            "failed_tickers": failed_tickers,
        }

    return run(event, context)
