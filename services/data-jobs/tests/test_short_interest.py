import json

import httpx
import pytest

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
    emitted = []
    monkeypatch.setattr(observability, "emit_job_finished",
                        lambda job, run, outcome, detail: emitted.append((outcome, detail)))
    with pytest.raises(RuntimeError, match="FINRA collection failed"):
        short_interest.handler({}, Context())
    assert not writes
    assert emitted[0][0] == "failure"
    assert emitted[0][1]["failed_source_ids"] == ["DS-91"]
    assert "private provider response" not in json.dumps(emitted)
    if failure in {"http", "data"}:
        assert observability._runs[-1]["error"] == "FINRA authentication failed"
        assert "private provider response" not in observability._runs[-1]["error"]


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
    monkeypatch.setattr(lake, "read_json", lambda key: {"settlement_dates": known})
    partitions, states = [], []
    monkeypatch.setattr(lake, "write_parquet", lambda *args: partitions.append(args))
    monkeypatch.setattr(lake, "write_json", lambda *args: states.append(args))
    monkeypatch.setattr(observability, "emit_job_finished", lambda *args: None)
    result = short_interest.handler({}, Context())
    assert result["new_settlement_dates"] == ([] if known else ["2026-09-30"])
    assert len(partitions) == len(states) == (0 if known else 1)
