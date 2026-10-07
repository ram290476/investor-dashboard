from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws

import lake
from collectors.alpaca import parse_bars
from hourly_prices import (
    JOB_ID,
    alpha_vantage_quote_bar,
    dedupe_hourly,
    fetch_start,
    finnhub_quote_bar,
    intraday_rollup,
    is_early_close,
    is_market_day,
    partition_key,
    select_bars,
    should_collect,
    store_hourly,
    to_hourly_rows,
)
from observability import emit_job_finished, job_handler


def test_parse_bars_fixture_becomes_an_hourly_row():
    parsed = parse_bars(
        {
            "bars": {
                "tsla": [
                    {
                        "t": "2026-10-06T14:00:00Z",
                        "o": 368.0,
                        "h": 371.0,
                        "l": 367.5,
                        "c": 370.84,
                        "v": 1000,
                        "vw": 369.2,
                        "n": 40,
                    }
                ]
            }
        }
    )
    rows = to_hourly_rows(parsed, "DS-02", "2026-10-06T14:05:00+00:00")
    assert rows[0]["ticker"] == "TSLA"
    assert rows[0]["ts_utc"] == "2026-10-06T14:00:00+00:00"
    assert rows[0]["close"] == 370.84
    assert rows[0]["vwap"] == 369.2
    assert rows[0]["trade_count"] == 40
    assert rows[0]["source"] == "DS-02"
    assert "prices_daily" not in partition_key("TSLA", 2026)


def test_early_close_stops_after_1305_and_holidays_are_skipped():
    assert is_early_close(date(2025, 7, 3))
    assert is_early_close(date(2025, 11, 28))
    assert is_early_close(date(2026, 11, 27))
    assert is_early_close(date(2026, 12, 24))
    assert not is_early_close(date(2026, 7, 3))  # full holiday: July 4 is a Saturday
    assert not is_early_close(date(2026, 10, 6))
    assert not is_market_day(date(2026, 12, 25))
    assert not should_collect(datetime(2026, 12, 25, 15, 5, tzinfo=UTC))[0]
    # 2026-11-27 is EST (UTC-5): 18:05 UTC is 13:05 ET and still runs; 18:06 UTC is past the cutoff.
    assert should_collect(datetime(2026, 11, 27, 18, 5, tzinfo=UTC))[0]
    assert not should_collect(datetime(2026, 11, 27, 18, 6, tzinfo=UTC))[0]
    assert should_collect(datetime(2026, 10, 6, 20, 5, tzinfo=UTC))[0]


def test_upsert_dedupe_keeps_alpaca_over_finnhub_on_the_same_bar():
    ingested = "2026-10-06T14:05:00+00:00"
    bar = {
        "ticker": "TSLA",
        "bar_ts": "2026-10-06T14:00:00Z",
        "open": 1,
        "high": 2,
        "low": 1,
        "vwap": None,
        "trade_count": None,
    }
    alpaca = to_hourly_rows([{**bar, "close": 10, "volume": 5}], "DS-02", ingested)
    finnhub = to_hourly_rows(
        [{**bar, "close": 9, "volume": 1}],
        "DS-04",
        "2026-10-06T14:06:00+00:00",
    )
    kept = dedupe_hourly([*finnhub, *alpaca])
    assert len(kept) == 1
    assert kept[0]["source"] == "DS-02"
    assert kept[0]["close"] == 10


def test_fallback_path_is_used_only_when_alpaca_fails():
    ingested = "2026-10-06T14:05:00+00:00"
    alpaca = to_hourly_rows(
        [
            {
                "ticker": "TSLA",
                "bar_ts": "2026-10-06T14:00:00Z",
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 10,
                "volume": 1,
                "vwap": None,
                "trade_count": None,
            }
        ],
        "DS-02",
        ingested,
    )
    quote = finnhub_quote_bar("TSLA", {"c": 11.0, "h": 12, "l": 10, "o": 10.5, "t": 1_759_780_800}, ingested)
    assert select_bars(alpaca, alpaca_failed=False, fallback_rows=[quote]) == alpaca
    chosen = select_bars([], alpaca_failed=True, fallback_rows=[quote])
    assert chosen[0]["source"] == "DS-04" and chosen[0]["close"] == 11.0
    av = alpha_vantage_quote_bar(
        {
            "Global Quote": {
                "01. symbol": "SPCX",
                "02. open": "18",
                "03. high": "19",
                "04. low": "17",
                "05. price": "18.5",
                "06. volume": "100",
                "07. latest trading day": "2026-10-06",
            }
        },
        ingested,
    )
    assert av["ticker"] == "SPCX" and av["source"] == "DS-03"


def test_first_run_backfills_thirty_days_and_a_watermark_resumes():
    assert fetch_start(None, date(2026, 10, 6)) == "2026-09-06"
    assert fetch_start("2026-10-06T14:00:00+00:00", date(2026, 10, 6)) == "2026-10-06"


def test_intraday_rollup_uses_the_prior_daily_close():
    bars = to_hourly_rows(
        [
            {
                "ticker": "TSLA",
                "bar_ts": "2026-10-06T14:00:00Z",
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 105,
                "volume": 10,
                "vwap": None,
                "trade_count": None,
            },
            {
                "ticker": "TSLA",
                "bar_ts": "2026-10-06T15:00:00Z",
                "open": 105,
                "high": 120,
                "low": 100,
                "close": 115,
                "volume": 5,
                "vwap": None,
                "trade_count": None,
            },
        ],
        "DS-02",
        "2026-10-06T15:05:00+00:00",
    )
    rollup = intraday_rollup(bars, prior_close=100)
    assert rollup["last"] == 115
    assert rollup["change_pct"] == pytest.approx(0.15)
    assert rollup["session_high"] == 120
    assert rollup["session_low"] == 90
    assert rollup["volume"] == 15


def test_handler_emits_job_h1(monkeypatch):
    emitted = {}

    def capture(job_id, run_id, outcome, detail=None):
        emitted["job"] = job_id
        emitted["outcome"] = outcome

    monkeypatch.setattr("observability.emit_job_finished", capture)

    @job_handler(JOB_ID)
    def run(event, context):
        return {"status": "success"}

    class Context:
        function_name = "hourly-prices"
        memory_limit_in_mb = 512
        invoked_function_arn = "arn:aws:lambda:us-east-1:1:function:hourly-prices"
        aws_request_id = "req"

    assert run({}, Context())["status"] == "success"
    assert emitted["job"] == "H1"
    assert emit_job_finished.__name__ == "emit_job_finished"


def test_store_hourly_upsert_keeps_alpaca_over_a_later_finnhub_row(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", None)
        ingested = "2026-10-06T14:05:00+00:00"
        bar = {
            "ticker": "TSLA",
            "bar_ts": "2026-10-06T14:00:00Z",
            "open": 1,
            "high": 2,
            "low": 1,
            "vwap": None,
            "trade_count": None,
        }
        store_hourly(to_hourly_rows([{**bar, "close": 9, "volume": 1}], "DS-04", ingested))
        store_hourly(to_hourly_rows([{**bar, "close": 10, "volume": 5}], "DS-02", ingested))
        keys = [
            item["Key"]
            for page in boto3.client("s3").get_paginator("list_objects_v2").paginate(Bucket="lake")
            for item in page.get("Contents", [])
        ]
        assert keys == ["curated/prices_hourly/ticker=TSLA/year=2026/prices.parquet"]
        frame = lake.read_parquet_prefix("curated/prices_hourly/")
        assert frame.height == 1
        assert frame["source"][0] == "DS-02"
        assert frame["close"][0] == 10
