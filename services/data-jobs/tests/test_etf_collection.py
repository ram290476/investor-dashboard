"""D4 collects the index ETF proxies so trend_metrics has ETF drivers (moto end-to-end)."""

import io
import json
from datetime import date, timedelta
from urllib.parse import parse_qs

import boto3
import httpx
import numpy as np
import polars as pl
import pytest
from moto import mock_aws

import api_keys
import daily_prices
import dashboard_build
import http_client
import lake
import observability
import trend_metrics
import universe

D4_DAY = date(2026, 10, 5)


class _Context:
    function_name = "test"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test"
    aws_request_id = "req-1"


def _history_days(n, last):
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def test_collection_symbols_include_etf_proxies():
    symbols = daily_prices.collection_symbols(universe.collection_universe(["NVDA", "SPY"]))
    assert symbols[:3] == ["TSLA", "SPCX", "NVDA"]
    assert set(universe.INDEX_PROXIES) <= set(symbols)
    assert len(symbols) == len(set(symbols))  # SPY as a user ticker is not requested twice


@pytest.fixture
def lake_bucket(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", None)
        monkeypatch.setattr(observability, "_events", None)
        monkeypatch.setattr(universe, "user_ticker_union", lambda *a, **k: [])
        yield boto3.client("s3")


def _seed_backfill(n=400, seed=3):
    """Yahoo-style backfill batches (DS-05) for TSLA and SPY, ending the trading day before D4_DAY."""
    rng = np.random.default_rng(seed)
    days = _history_days(n, D4_DAY - timedelta(days=3))
    spy_ret = rng.normal(0, 0.01, n)
    series = {
        "SPY": 500 * np.cumprod(1 + spy_ret),
        "TSLA": 250 * np.cumprod(1 + 1.5 * spy_ret + rng.normal(0, 0.01, n)),
    }
    for ticker, close in series.items():
        frame = pl.DataFrame(
            {"ticker": ticker, "date": days, "close": close, "adj_close": close, "volume": 1, "source_id": "DS-05"}
        )
        lake.write_parquet(
            frame, f"curated/prices_daily/ticker={ticker}/batch_start=x/batch_end=y/prices_daily.parquet"
        )
    return {t: float(c[-1]) for t, c in series.items()}


def test_daily_prices_collects_etfs_and_trend_metrics_has_etf_drivers(lake_bucket, monkeypatch):
    last = _seed_backfill()
    requested: list[list[str]] = []
    adjustments: list[str] = []

    def alpaca(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        symbols = query["symbols"][0].split(",")
        requested.append(symbols)
        adjustments.append(query["adjustment"][0])
        stamp = f"{D4_DAY.isoformat()}T04:00:00Z"
        bars = {s: [{"t": stamp, "c": last.get(s, 100.0) * 1.01, "v": 10}] for s in symbols}
        return httpx.Response(200, json={"bars": bars, "next_page_token": None})

    real_get_client = http_client.get_client
    monkeypatch.setattr(
        http_client, "get_client", lambda **kw: real_get_client(transport=httpx.MockTransport(alpaca), **kw)
    )
    monkeypatch.setattr(api_keys, "api_key", lambda name: "test")
    monkeypatch.setattr(daily_prices, "is_market_day", lambda day: True)

    result = daily_prices.handler({}, _Context())
    assert result["status"] == "success"
    assert sorted(adjustments) == ["all", "raw", "split"]  # close_raw, close, adj_close
    assert all(set(r) == {"TSLA", "SPCX", *universe.INDEX_PROXIES} for r in requested)
    for etf in universe.INDEX_PROXIES:
        lake_bucket.head_object(Bucket="lake", Key=lake.price_partition_key(etf, D4_DAY.year))
    assert lake.read_prices("SPY")["date"].max() == D4_DAY  # legacy backfill batch + new yearly D4 row

    trend_metrics.handler({}, _Context())  # no curated/macro_daily/ yet: ETF drivers only
    latest = json.loads(
        lake_bucket.get_object(Bucket="lake", Key="serving/trend_metrics/latest/TSLA.json")["Body"].read()
    )
    assert latest["date"] == D4_DAY.isoformat()
    rows = {row["series_id"]: row for row in latest["rows"]}
    assert set(rows) == {f"ETF:{etf}" for etf in universe.INDEX_PROXIES} | {"PX:TSLA"}
    assert rows["PX:TSLA"]["ticker"] == "TSLA"
    assert rows["PX:TSLA"]["trend_state"] in {"up", "down", "flat"}
    assert rows["PX:TSLA"]["since"]
    assert rows["PX:TSLA"]["days_in_state"] >= 1
    full = pl.read_parquet(
        io.BytesIO(
            lake_bucket.get_object(Bucket="lake", Key="serving/trend_metrics/ticker=TSLA/trend_metrics.parquet")[
                "Body"
            ].read()
        )
    )
    assert full.select("series_id", "date").is_duplicated().sum() == 0
    assert rows["ETF:SPY"]["corr_90d"] > 0.5  # TSLA was generated to move with SPY
    assert rows["ETF:SPY"]["z_1m"] is not None  # SPY has enough history for z-scores
    dashboard_build.handler({}, _Context())
    dashboard = lake.read_json("serving/dashboard.json")
    assert set(universe.INDEX_PROXIES) <= set(dashboard["tickers"])
    assert dashboard["tickers"]["SPY"]["price_history"][-1]["date"] == D4_DAY.isoformat()


def test_watchlist_etf_has_its_own_price_trend_without_self_driver(lake_bucket, monkeypatch):
    _seed_backfill()
    monkeypatch.setattr(universe, "user_ticker_union", lambda: ["SPY"])
    trend_metrics.handler({}, _Context())
    latest = lake.read_json("serving/trend_metrics/latest/SPY.json")
    assert latest is not None
    rows = {row["series_id"]: row for row in latest["rows"]}
    assert rows["PX:SPY"]["trend_state"] in {"up", "down", "flat"}
    assert "ETF:SPY" not in rows
