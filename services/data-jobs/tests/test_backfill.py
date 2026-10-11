from datetime import date

import boto3
import httpx
import pytest
from moto import mock_aws

import backfill
import http_client
import lake
import observability
from collectors import yahoo


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


def test_empty_history_is_only_treated_as_a_data_boundary_when_explicit():
    assert backfill._is_empty_historical_window({"chart": {"result": [{"timestamp": []}]}})
    assert backfill._is_empty_historical_window(
        {"chart": {"result": None, "error": {"description": "No data found, symbol may be delisted"}}}
    )
    assert backfill._is_empty_historical_window(
        {"chart": {"result": None, "error": {"description": "Data doesn't exist for startDate = 1, endDate = 2"}}}
    )
    assert not backfill._is_empty_historical_window(
        {"chart": {"result": None, "error": {"code": "Not Found", "description": "invalid ticker"}}}
    )


def test_yahoo_400_is_a_listing_boundary_and_404_is_an_invalid_symbol():
    missing = {
        "chart": {
            "result": None,
            "error": {"code": "Bad Request", "description": "Data doesn't exist for startDate = 1, endDate = 2"},
        }
    }
    unknown = {
        "chart": {
            "result": None,
            "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"},
        }
    }
    assert backfill.chart_window_disposition(400, missing) == "no_data"
    assert backfill.chart_window_disposition(404, unknown) == "invalid"
    assert backfill.chart_window_disposition(200, {"chart": {"result": [{"timestamp": [1]}]}}) == "rows"
    assert backfill.chart_window_disposition(500, {"chart": {"error": {"description": "unavailable"}}}) == "error"


def test_each_ticker_keeps_its_own_start_date():
    state = backfill.new_backfill_state(["TSLA"], date(2026, 10, 4))
    state["tickers"]["TSLA"].pop("earliest_date")
    state["tickers"]["TSLA"]["cursor"] = "2024-01-01"
    backfill._queue_requested_tickers(state, ["META"], False, date(2026, 10, 11))
    assert state["earliest_date"] == "2021-10-04"
    assert state["tickers"]["TSLA"]["cursor"] == "2024-01-01"
    assert state["tickers"]["TSLA"]["earliest_date"] == "2021-10-04"
    assert state["tickers"]["META"]["earliest_date"] == "2021-10-11"
    assert backfill.earliest_for(state, "TSLA") == date(2021, 10, 4)
    assert backfill.earliest_for(state, "META") == date(2021, 10, 11)


class _Context:
    function_name = "test"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test"
    aws_request_id = "test"


def _chart(close: float = 10.0) -> dict:
    return {
        "chart": {
            "result": [{
                "meta": {"gmtoffset": 0},
                "timestamp": [1_759_000_000],
                "indicators": {
                    "quote": [{"close": [close], "volume": [100]}],
                    "adjclose": [{"adjclose": [close]}],
                },
            }],
            "error": None,
        }
    }


def test_backfill_stops_at_a_yahoo_400_and_marks_an_unknown_symbol_invalid(monkeypatch):
    calls = {"SPCX": 0, "APPL": 0}

    def respond(request):
        ticker = request.url.path.rstrip("/").split("/")[-1]
        calls[ticker] = calls.get(ticker, 0) + 1
        if ticker == "APPL":
            return httpx.Response(404, json={
                "chart": {
                    "result": None,
                    "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"},
                },
            })
        if ticker == "SPCX" and calls["SPCX"] == 1:
            return httpx.Response(200, json=_chart())
        return httpx.Response(400, json={
            "chart": {
                "result": None,
                "error": {
                    "code": "Bad Request",
                    "description": "Data doesn't exist for startDate = 1, endDate = 2",
                },
            },
        })

    monkeypatch.setenv("BACKFILL_MAX_BATCHES", "3")
    monkeypatch.setenv("BACKFILL_BATCH_DAYS", "90")
    real_get_client = http_client.get_client
    monkeypatch.setattr(
        http_client, "get_client", lambda **kwargs: real_get_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(observability, "emit_job_finished", lambda *args, **kwargs: None)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", client)
        result = backfill.handler({"tickers": ["SPCX", "APPL"]}, _Context())
        state = lake.read_json(backfill.STATE_KEY)
        stored = lake.read_prices("SPCX")
        assert lake.read_prices("APPL").is_empty()
        again = backfill.handler({}, _Context())
    assert result["status"] == "complete"
    assert result["pending_tickers"] == []
    assert result["invalid_tickers"] == ["APPL"]
    assert result["no_data_tickers"] == ["SPCX"]
    assert result["tickers_completed"] == 1
    assert state["status"] == "complete"
    assert state["tickers"]["APPL"]["invalid"] is True
    assert "no data found" in state["tickers"]["APPL"]["invalid_reason"]
    assert state["tickers"]["SPCX"]["complete"] is True
    assert "no_data_before" in state["tickers"]["SPCX"]
    assert state["tickers"]["SPCX"].get("invalid") is not True
    assert stored.height == 1
    assert again["batches"] == 0
    assert calls["APPL"] == 1
