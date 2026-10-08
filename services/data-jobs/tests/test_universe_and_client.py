from datetime import date

import boto3
import httpx
import pytest
from moto import mock_aws

import http_client
import universe


def test_union_counts_and_projection(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        ddb = boto3.resource("dynamodb")
        t = ddb.create_table(
            TableName="prefs",
            KeySchema=[{"AttributeName": "user_sub", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "user_sub", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        t.put_item(Item={"user_sub": "a", "tickers": ["TSLA", "NVDA", "rivn"], "display": {"time_zone": "UTC"}})
        t.put_item(Item={"user_sub": "b", "tickers": ["NVDA", "bad ticker!"]})
        assert universe.user_ticker_union("prefs", ddb) == ["NVDA", "RIVN", "TSLA"]


def test_cap_keeps_base_and_reports_dropped():
    users = ["TSLA", "SPY"] + [f"T{i:02d}" for i in range(30)]
    u = universe.collection_universe(users, cap=25)
    assert u["equities"][:2] == ["TSLA", "SPCX"] and len(u["equities"]) == 27
    assert "SPY" not in u["equities"] and u["etfs"][0] == "SPY" and len(u["dropped"]) == 5


def test_watchlist_universe_keeps_user_etfs_and_reports_the_cap():
    users = ["NVDA", "SPY", "TSLA", "nvda"] + [f"T{i:02d}" for i in range(30)]
    watch = universe.watchlist_universe(users, cap=25)
    assert watch["tickers"][:2] == ["TSLA", "SPCX"]
    assert watch["tickers"].count("TSLA") == 1
    assert "NVDA" in watch["tickers"] and "SPY" in watch["tickers"]
    assert len(watch["tickers"]) == 27  # two defaults plus the 25-ticker cap
    assert "SPY" not in watch["dropped"] and "NVDA" not in watch["dropped"]
    assert watch["dropped"] == [f"T{i:02d}" for i in range(23, 30)]
    assert watch["over_cap"] == "over the ticker cap"
    assert universe.watchlist_universe(["SPY"])["over_cap"] is None


def test_sentiment_plan_respects_budget_and_rotates():
    eq = ["TSLA", "SPCX", "NVDA", "RIVN", "AAPL", "MSFT", "AMZN", "GOOG"]
    d1, d2 = universe.sentiment_plan(eq, date(2026, 10, 5)), universe.sentiment_plan(eq, date(2026, 10, 6))
    assert len(d1) == universe.SENTIMENT_CALLS_PER_DAY <= 16
    assert d1.count("TSLA") >= 6 and d1.count("SPCX") >= 6
    assert set(d1) - {"TSLA", "SPCX"} != set(d2) - {"TSLA", "SPCX"}  # rotation moves day to day
    assert universe.sentiment_plan(["TSLA", "SPCX"], date(2026, 10, 5)).count("TSLA") == 8


def test_allowlist_blocks_unknown_hosts():
    def ok(request):
        return httpx.Response(200, json={"ok": True})

    with http_client.get_client(transport=httpx.MockTransport(ok)) as c:
        assert c.get("https://data.alpaca.markets/v2/stocks/bars").status_code == 200
        assert c.get("https://external-api.kalshi.com/trade-api/v2/markets").status_code == 200
        with pytest.raises(http_client.HostNotAllowedError):
            c.get("https://evil.example.com/exfil")


def test_transient_http_failure_is_retried_with_retry_after(monkeypatch):
    calls, delays = [], []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(http_client.time, "sleep", delays.append)
    monkeypatch.setattr(http_client.random, "random", lambda: 0)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        response = http_client.request_with_retry(client, "GET", "https://data.alpaca.markets/test")

    assert response.status_code == 200 and len(calls) == 2
    assert delays == [2.0]


def test_non_transient_http_failure_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(http_client.time, "sleep", lambda _delay: pytest.fail("unexpected retry"))

    def respond(request):
        calls.append(request)
        return httpx.Response(400)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            http_client.request_with_retry(client, "GET", "https://data.alpaca.markets/test")
    assert len(calls) == 1


def test_get_client_merges_caller_headers_with_user_agent():
    # daily_prices and options_daily pass Alpaca auth headers; this used to raise TypeError.
    with http_client.get_client(headers={"APCA-API-KEY-ID": "k"}) as client:
        assert client.headers["APCA-API-KEY-ID"] == "k"
        assert client.headers["User-Agent"] == http_client.USER_AGENT
