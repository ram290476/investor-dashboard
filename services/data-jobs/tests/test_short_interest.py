import json

import boto3
import httpx
import polars as pl
import pytest
from moto import mock_aws

import api_keys
import http_client
import lake
import observability
import short_interest
import universe


class Context:
    function_name = "test"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test"
    aws_request_id = "test"


@pytest.mark.parametrize("failure", ["http", "json", "missing_token", "data"])
def test_finra_failure_is_explicit_and_does_not_update_watermark(monkeypatch, failure):
    def respond(request):
        if "access_token" in request.url.path:
            if failure == "http":
                return httpx.Response(401, text="private provider response")
            if failure == "json":
                return httpx.Response(200, text="<html>not JSON</html>")
            if failure == "missing_token":
                return httpx.Response(200, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "test-token"})
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(403, text="private provider response")

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(http_client, "get_client", lambda: client)
    monkeypatch.setattr(api_keys, "api_key", lambda name: "test")
    monkeypatch.setattr(universe, "user_ticker_union", lambda: [])
    monkeypatch.setattr(lake, "read_json", lambda key: None)
    writes = []
    monkeypatch.setattr(lake, "write_json", lambda *args: writes.append(args))
    monkeypatch.setattr(lake, "upsert_ranked", lambda *args, **kwargs: writes.append(args))
    emitted = []
    monkeypatch.setattr(observability, "emit_job_finished",
                        lambda job, run, outcome, detail: emitted.append((outcome, detail)))
    with pytest.raises(RuntimeError, match="FINRA collection failed"):
        short_interest.handler({}, Context())
    assert not writes
    assert emitted[0][0] == "failure"
    assert emitted[0][1]["failed_source_ids"] == ["DS-91"]
    assert "private provider response" not in json.dumps(emitted)


@pytest.mark.parametrize("known", [[], ["2026-09-30"]])
def test_finra_stores_only_new_settlement_dates(monkeypatch, known):
    def respond(request):
        if "access_token" in request.url.path:
            return httpx.Response(200, json={"access_token": "test-token"})
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json=[{
            "settlementDate": "2026-09-30", "symbolCode": "TSLA",
            "currentShortPositionQuantity": 100, "previousShortPositionQuantity": 90,
            "averageDailyVolumeQuantity": 25, "daysToCoverQuantity": 4,
        }])

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(http_client, "get_client", lambda: client)
    monkeypatch.setattr(api_keys, "api_key", lambda name: "test")
    monkeypatch.setattr(universe, "user_ticker_union", lambda: [])
    monkeypatch.setattr(
        lake, "read_json",
        lambda key: {"settlement_dates": known, "history_complete": ["TSLA", "SPCX"]},
    )
    partitions, states = [], []
    monkeypatch.setattr(lake, "upsert_ranked", lambda *args, **kwargs: partitions.append(args) or 0)
    monkeypatch.setattr(lake, "write_json", lambda *args: states.append(args))
    monkeypatch.setattr(observability, "emit_job_finished", lambda *args: None)
    result = short_interest.handler({}, Context())
    assert result["new_settlement_dates"] == ([] if known else ["2026-09-30"])
    assert result["history_catchup"] == []
    assert len(partitions) == len(states) == (0 if known else 1)


def test_merge_keeps_one_row_per_ticker_and_settlement():
    existing = [{
        "ticker": "TSLA", "settlement_date": "2026-09-30", "short_interest": 10, "source_id": "DS-91",
    }]
    incoming = [
        {"ticker": "tsla", "settlement_date": "2026-09-30", "short_interest": 10, "source_id": "DS-91"},
        {"ticker": "META", "settlement_date": "2026-09-30", "short_interest": 4, "source_id": "DS-91"},
        {"ticker": "META", "settlement_date": "2018-01-12", "short_interest": 1, "source_id": "DS-91"},
    ]
    merged = short_interest.merge_settlement_rows(existing, incoming)
    assert [(row["ticker"], row["settlement_date"]) for row in merged] == [
        ("META", "2018-01-12"),
        ("META", "2026-09-30"),
        ("TSLA", "2026-09-30"),
    ]
    assert short_interest.tickers_needing_history(["TSLA", "META", "EVGO"], {"history_complete": ["TSLA"]}) == [
        "META", "EVGO",
    ]


def _finra_row(day: str, symbol: str, shares: int) -> dict:
    return {
        "settlementDate": day,
        "symbolCode": symbol,
        "currentShortPositionQuantity": shares,
        "previousShortPositionQuantity": shares - 1,
        "averageDailyVolumeQuantity": 25,
        "daysToCoverQuantity": 1.5,
    }


def test_catchup_merges_full_history_without_duplicate_partitions(monkeypatch):
    bodies: list[dict] = []

    def respond(request):
        if "access_token" in request.url.path:
            return httpx.Response(200, json={"access_token": "test-token"})
        body = json.loads(request.content)
        bodies.append(body)
        symbols = body["domainFilters"][0]["values"]
        if body.get("compareFilters"):
            return httpx.Response(200, json=[])
        if symbols == ["META"]:
            return httpx.Response(200, json=[
                _finra_row("2018-01-12", "META", 3),
                _finra_row("2026-09-30", "META", 9),
                _finra_row("2026-09-30", "META", 9),
            ])
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        http_client, "get_client", lambda: httpx.Client(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(api_keys, "api_key", lambda name: "test")
    monkeypatch.setattr(universe, "user_ticker_union", lambda: ["META"])
    monkeypatch.setattr(observability, "emit_job_finished", lambda *args, **kwargs: None)
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", s3)
        lake.write_parquet(
            pl.DataFrame([{
                "ticker": "TSLA",
                "settlement_date": "2026-09-30",
                "short_interest": 100,
                "previous_short_interest": 90,
                "avg_daily_volume": 25,
                "days_to_cover": 4.0,
                "source_id": "DS-91",
            }]),
            "curated/short_interest/settlement_date=2026-09-30/short_interest.parquet",
        )
        lake.write_json(
            {"settlement_dates": ["2026-09-30"], "history_complete": ["TSLA", "SPCX"]},
            short_interest.STATE_KEY,
        )
        added = {"detail-type": "TickerAdded", "detail": {"ticker": "META"}}
        first = short_interest.handler(added, Context())
        stored = lake.read_parquet_prefix("curated/short_interest/settlement_date=2026-09-30/")
        older = lake.read_parquet_prefix("curated/short_interest/settlement_date=2018-01-12/")
        state = lake.read_json(short_interest.STATE_KEY)
        bodies.clear()
        second = short_interest.handler({}, Context())
    assert first["history_catchup"] == ["META"]
    assert "2018-01-12" in first["new_settlement_dates"]
    keys = {(row["ticker"], row["settlement_date"]) for row in stored.to_dicts()}
    assert keys == {("TSLA", "2026-09-30"), ("META", "2026-09-30")}
    assert [(row["ticker"], row["short_interest"]) for row in older.to_dicts()] == [("META", 3)]
    assert "META" in state["history_complete"]
    assert second["history_catchup"] == []
    assert bodies
    assert all(body.get("compareFilters") for body in bodies)
    assert all("META" in body["domainFilters"][0]["values"] or "TSLA" in body["domainFilters"][0]["values"]
               for body in bodies)
