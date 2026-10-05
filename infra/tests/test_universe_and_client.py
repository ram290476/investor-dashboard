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
