import copy
from datetime import UTC, date, datetime

import httpx
import polars as pl
import pytest

import backfill
import http_client
import lake
import observability
from collectors import yahoo


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 10, 15, 0, tzinfo=tz or UTC)


class _Context:
    function_name = "backfill"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:backfill"
    aws_request_id = "req-backfill"


def test_backfill_batches_move_from_newest_to_oldest_without_overlap():
    earliest = date(2021, 10, 4)
    first = backfill.batch_bounds(date(2026, 10, 4), earliest, batch_days=90)
    second = backfill.batch_bounds(first[0], earliest, batch_days=90)
    assert first == (date(2026, 7, 6), date(2026, 10, 4))
    assert second == (date(2026, 4, 7), date(2026, 7, 6))
    assert second[1] == first[0]


def test_backfill_clamps_last_batch_and_handles_leap_day():
    assert backfill.batch_bounds(date(2021, 11, 1), date(2021, 10, 4), 90) == (
        date(2021, 10, 4),
        date(2021, 11, 1),
    )
    assert backfill.years_ago(date(2028, 2, 29), 5) == date(2023, 2, 28)
    with pytest.raises(ValueError, match="positive"):
        backfill.batch_bounds(date(2026, 1, 1), date(2025, 1, 1), 0)


def test_checkpoint_contains_resume_cursor_and_deduplicated_tickers():
    state = backfill.new_backfill_state(["TSLA", "SPCX"], date(2026, 10, 4))
    assert state["earliest_date"] == "2021-10-04"
    assert state["tickers"]["TSLA"]["cursor"] == "2026-10-04"
    assert state["tickers"]["SPCX"]["complete"] is False
    assert backfill.tickers_from_event({"tickers": ["tsla", "TSLA", " spcx "]}) == (
        ["TSLA", "SPCX"],
        False,
    )


def test_backfill_queues_existing_recent_price_history_and_ignores_duplicate_events():
    state = backfill.new_backfill_state([], date(2026, 10, 4))
    backfill._queue_requested_tickers(state, ["TSLA"], False, date(2026, 10, 4))
    assert state["tickers"]["TSLA"]["complete"] is False
    state["tickers"]["TSLA"]["cursor"] = "2026-07-06"
    backfill._queue_requested_tickers(state, ["TSLA"], False, date(2026, 10, 5))
    assert state["tickers"]["TSLA"]["cursor"] == "2026-07-06"


def test_yahoo_batch_parameters_use_exclusive_utc_end():
    params = yahoo.chart_params(start=date(2026, 7, 6), end=date(2026, 10, 4))
    assert params["period2"] - params["period1"] == 90 * 24 * 60 * 60
    assert "range" not in params
    assert yahoo.chart_params(5)["range"] == "5y"
    with pytest.raises(ValueError, match="half-open"):
        yahoo.chart_params(start=date(2026, 1, 2), end=date(2026, 1, 2))


def test_yahoo_404_is_the_requested_symbol_and_400_needs_a_stored_inception():
    missing = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}
    assert yahoo.classify_chart(404, missing) == "unknown_symbol"
    assert "AAPL" not in yahoo.CHART_URL.format(ticker="APPL")
    window = {
        "chart": {
            "result": None,
            "error": {"code": "Bad Request", "description": "Data doesn't exist for startDate = 1, endDate = 2"},
        }
    }
    assert yahoo.classify_chart(400, window) == "pre_inception_candidate"
    other = {"chart": {"result": None, "error": {"code": "Bad Request", "description": "Invalid crumb"}}}
    assert yahoo.classify_chart(400, other) == "client_error"
    assert yahoo.classify_chart(400, {"chart": {"error": {"description": "Not Found"}}}) == "client_error"
    assert yahoo.classify_chart(429, window) == "rate_limited"
    inception = date(2026, 6, 1)
    assert backfill.confirm_pre_inception("pre_inception_candidate", date(2026, 4, 9), inception)
    assert not backfill.confirm_pre_inception("pre_inception_candidate", date(2026, 4, 9), None)
    assert not backfill.confirm_pre_inception("pre_inception_candidate", date(2026, 7, 1), inception)
    assert not backfill.confirm_pre_inception("client_error", date(2026, 4, 9), inception)


def test_backfill_keeps_appl_and_stops_the_run_on_yahoo_429(monkeypatch):
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        ticker = request.url.path.rsplit("/", 1)[-1]
        calls.append(ticker)
        if ticker == "APPL":
            body = {"chart": {"result": None, "error": {"description": "No data found"}}}
            return httpx.Response(404, json=body)
        if ticker == "SPCX":
            return httpx.Response(429, json={"chart": {"error": {"description": "Too Many Requests"}}})
        raise AssertionError(ticker)

    real_client = http_client.get_client

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(backfill, "datetime", _Clock)
    monkeypatch.setattr(http_client, "get_client", client)
    monkeypatch.setenv("BACKFILL_MAX_BATCHES", "5")
    monkeypatch.setenv("LAKE_BUCKET", "lake")
    monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
    held: dict = {}
    monkeypatch.setattr(lake, "read_json", lambda *args, **kwargs: held.get("state"))
    monkeypatch.setattr(lake, "write_json", lambda payload, *args, **kwargs: held.update(state=payload))
    monkeypatch.setattr(lake, "read_prices", lambda *args, **kwargs: pl.DataFrame())
    monkeypatch.setattr(observability, "emit_job_finished", lambda *args, **kwargs: None)

    result = backfill.handler({"tickers": ["APPL", "SPCX", "AAPL"]}, _Context())
    progress = held["state"]["tickers"]
    assert calls == ["APPL", "SPCX"]
    assert "AAPL" not in calls
    assert progress["APPL"]["complete"] is True
    assert progress["APPL"]["stopped"] == "unknown_symbol"
    assert progress["APPL"]["invalid_symbol"] == "APPL"
    assert progress["SPCX"]["complete"] is False
    assert progress["SPCX"]["cursor"] == "2026-10-10"
    assert progress["AAPL"]["complete"] is False
    assert result["status"] == "rate_limited"
    assert result["rate_limited"] is True
    assert result["invalid_symbols"] == ["APPL"]
    assert result["rows_stored"] == 0


def test_backfill_400_is_terminal_only_before_a_stored_inception(monkeypatch):
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "chart": {
                    "result": None,
                    "error": {"description": "Data doesn't exist for startDate = 1, endDate = 2"},
                }
            },
        )

    real_client = http_client.get_client

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(backfill, "datetime", _Clock)
    monkeypatch.setattr(http_client, "get_client", client)
    monkeypatch.setenv("LAKE_BUCKET", "lake")
    monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
    monkeypatch.setattr(observability, "emit_job_finished", lambda *args, **kwargs: None)
    monkeypatch.setattr(lake, "upsert_prices", lambda *args, **kwargs: pytest.fail("no rows to store"))
    state = {
        "version": 1,
        "years": 5,
        "earliest_date": "2021-10-10",
        "next_index": 0,
        "tickers": {"SPCX": {"cursor": "2026-04-09", "complete": False, "batches": 4, "rows_stored": 10}},
    }
    frames = {"rows": pl.DataFrame({"date": [date(2026, 6, 1)]})}
    monkeypatch.setattr(lake, "read_json", lambda *args, **kwargs: state)
    monkeypatch.setattr(lake, "write_json", lambda payload, *args, **kwargs: None)
    monkeypatch.setattr(lake, "read_prices", lambda *args, **kwargs: frames["rows"])

    confirmed = backfill.handler({"tickers": ["SPCX"]}, _Context())
    assert confirmed["status"] == "complete"
    assert confirmed["rows_stored"] == 0
    assert confirmed["no_data_tickers"] == ["SPCX"]
    assert state["tickers"]["SPCX"]["stopped"] == "pre_inception"
    assert state["tickers"]["SPCX"]["inception"] == "2026-06-01"
    assert state["tickers"]["SPCX"]["rows_stored"] == 10

    rejected_state = copy.deepcopy(state)
    rejected_state["tickers"]["SPCX"]["complete"] = False
    rejected_state["tickers"]["SPCX"]["cursor"] = "2026-04-09"
    rejected_state["status"] = "running"
    frames["rows"] = pl.DataFrame()
    monkeypatch.setattr(lake, "read_json", lambda *args, **kwargs: rejected_state)
    rejected = backfill.handler({"tickers": ["SPCX"]}, _Context())
    assert rejected["status"] == "running"
    assert rejected["failed_tickers"] == ["SPCX"]
    assert rejected_state["tickers"]["SPCX"]["cursor"] == "2026-04-09"
    assert rejected_state["tickers"]["SPCX"]["complete"] is False


def test_empty_history_is_only_treated_as_a_data_boundary_when_explicit():
    assert backfill._is_empty_historical_window({"chart": {"result": [{"timestamp": []}]}})
    assert backfill._is_empty_historical_window(
        {"chart": {"result": None, "error": {"description": "No data found, symbol may be delisted"}}}
    )
    assert not backfill._is_empty_historical_window(
        {"chart": {"result": None, "error": {"code": "Not Found", "description": "invalid ticker"}}}
    )
