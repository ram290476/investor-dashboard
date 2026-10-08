"""Yearly price partitions: upsert semantics, S3 read counts, legacy-layout equivalence and migration."""

import io
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import boto3
import numpy as np
import polars as pl
import pytest
from moto import mock_aws

import dashboard_build
import lake
import observability
import trend_metrics
import universe

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compact_price_partitions as compact


class _Context:
    function_name = "test"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test"
    aws_request_id = "req-1"


def _business_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _frame(ticker: str, days: list[date], close, source_id: str) -> pl.DataFrame:
    close = np.asarray(close, dtype=float)
    return pl.DataFrame(
        {
            "ticker": ticker,
            "date": days,
            "close": close,
            "adj_close": close,
            "volume": np.arange(len(days), dtype=np.int64),
            "source_id": source_id,
        }
    )


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", None)
        monkeypatch.setattr(observability, "_events", None)
        monkeypatch.setattr(universe, "user_ticker_union", lambda *a, **k: [])
        yield client


def _count_gets(client) -> dict:
    counter = {"GetObject": 0}

    def hook(**kwargs):
        counter["GetObject"] += 1

    client.meta.events.register("before-call.s3.GetObject", hook)
    return counter


def _keys(client, prefix="curated/prices_daily/") -> list[str]:
    pages = client.get_paginator("list_objects_v2").paginate(Bucket="lake", Prefix=prefix)
    return sorted(o["Key"] for p in pages for o in p.get("Contents", []))


# --- upsert semantics -------------------------------------------------------------------------


def test_price_partition_key_layout():
    assert lake.price_partition_key("TSLA", 2026) == "curated/prices_daily/ticker=TSLA/year=2026/prices.parquet"


def test_upsert_prices_splits_by_year_and_merges(s3):
    days = [date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 2)]
    keys = lake.upsert_prices(_frame("TSLA", days, [1, 2, 3], "DS-05"))
    assert sorted(keys) == [lake.price_partition_key("TSLA", 2025), lake.price_partition_key("TSLA", 2026)]
    lake.upsert_prices(_frame("TSLA", [date(2026, 1, 5)], [4], "DS-02"))
    out = lake.read_prices("TSLA")
    assert out["date"].to_list() == [*days, date(2026, 1, 5)]
    assert out["close"].to_list() == [1.0, 2.0, 3.0, 4.0]
    assert _keys(s3) == [lake.price_partition_key("TSLA", 2025), lake.price_partition_key("TSLA", 2026)]


def test_upsert_keeps_daily_prices_row_over_later_backfill(s3):
    day = [date(2026, 10, 5)]
    lake.upsert_prices(_frame("TSLA", day, [450.0], "DS-02"))
    lake.upsert_prices(_frame("TSLA", day, [449.0], "DS-05"))  # backfill rerun must not clobber D4
    assert lake.read_prices("TSLA").select("close", "source_id").row(0) == (450.0, "DS-02")
    lake.upsert_prices(_frame("TSLA", day, [451.0], "DS-02"))  # a D4 rerun replaces the earlier D4 row
    assert lake.read_prices("TSLA").select("close", "source_id").row(0) == (451.0, "DS-02")


def test_upsert_retries_on_concurrent_write(s3, monkeypatch):
    key = lake.price_partition_key("TSLA", 2026)
    lake.upsert_prices(_frame("TSLA", [date(2026, 1, 2)], [1.0], "DS-05"))
    real_put = lake.s3().put_object
    calls = {"n": 0}

    def racing_put(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # another job writes between our read and our conditional put
            real_put(Bucket="lake", Key=key, Body=_parquet(_frame("TSLA", [date(2026, 1, 5)], [2.0], "DS-02")))
        return real_put(**kwargs)

    monkeypatch.setattr(lake.s3(), "put_object", racing_put)
    lake.upsert_prices(_frame("TSLA", [date(2026, 1, 6)], [3.0], "DS-02"))
    assert calls["n"] == 2  # 1st: racing write, then our conditional put fails; 2nd: re-read + retry succeeds
    assert lake.read_prices("TSLA")["date"].to_list() == [date(2026, 1, 5), date(2026, 1, 6)]


def _parquet(df: pl.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.write_parquet(buf)
    return buf.getvalue()


# --- read counts ------------------------------------------------------------------------------


def test_read_count_is_one_per_ticker_year_not_one_per_day(s3):
    days = _business_days(date(2021, 10, 6), date(2026, 10, 5))  # five years of weekdays
    close = np.linspace(100, 200, len(days))
    for i, d in enumerate(days):  # old D4 layout: one object per ticker-day
        lake.write_parquet(
            _frame("TSLA", [d], [close[i]], "DS-02"), f"curated/prices_daily/ticker=TSLA/date={d}/daily.parquet"
        )
    counter = _count_gets(lake.s3())
    legacy = lake.read_prices("TSLA")
    legacy_gets = counter["GetObject"]

    compact.compact(bucket="lake", delete_legacy=True)
    counter["GetObject"] = 0
    yearly = lake.read_prices("TSLA")
    yearly_gets = counter["GetObject"]

    assert legacy_gets == len(days) == 1304
    assert yearly_gets == 6  # 2021..2026
    assert yearly.equals(legacy)


# --- legacy-layout equivalence and migration -------------------------------------------------


def _seed_legacy_layout():
    """Backfill batches (DS-05) for TSLA and SPY plus overlapping D4 days (DS-02) with different closes."""
    rng = np.random.default_rng(11)
    days = _business_days(date(2025, 3, 3), date(2026, 10, 2))
    spy_ret = rng.normal(0, 0.01, len(days))
    series = {
        "SPY": 500 * np.cumprod(1 + spy_ret),
        "TSLA": 250 * np.cumprod(1 + 1.5 * spy_ret + rng.normal(0, 0.01, len(days))),
    }
    for ticker, close in series.items():
        for start in range(0, len(days), 63):  # ~90-calendar-day backfill batches
            chunk = days[start : start + 63]
            lake.write_parquet(
                _frame(ticker, chunk, close[start : start + 63], "DS-05"),
                f"curated/prices_daily/ticker={ticker}/batch_start={chunk[0]}/batch_end={chunk[-1]}/prices_daily.parquet",
            )
        for i in range(len(days) - 10, len(days)):  # last 10 days also collected by D4, slightly different
            lake.write_parquet(
                _frame(ticker, [days[i]], [close[i] * 1.001], "DS-02"),
                f"curated/prices_daily/ticker={ticker}/date={days[i]}/daily.parquet",
            )
    return days


def _outputs(client) -> dict:
    trend_metrics.handler({}, _Context())  # production order: D4 -> TREND -> DASHBOARD
    dashboard_build.handler({}, _Context())
    dash = json.loads(client.get_object(Bucket="lake", Key="serving/dashboard.json")["Body"].read())
    dash.pop("generated_at")
    latest = json.loads(client.get_object(Bucket="lake", Key="serving/trend_metrics/latest/TSLA.json")["Body"].read())
    trend = pl.read_parquet(
        io.BytesIO(
            client.get_object(Bucket="lake", Key="serving/trend_metrics/ticker=TSLA/trend_metrics.parquet")[
                "Body"
            ].read()
        )
    )
    return {"dashboard": dash, "latest": latest, "trend": trend}


def test_outputs_unchanged_after_migrating_legacy_layout(s3):
    days = _seed_legacy_layout()
    before = _outputs(s3)
    tsla = before["dashboard"]["tickers"]["TSLA"]["price_history"]
    assert len(tsla) == len(days)
    assert tsla[-1]["date"] == str(days[-1])

    summary = compact.compact(bucket="lake", delete_legacy=True)
    assert summary["legacy_objects"] == 2 * (len(range(0, len(days), 63)) + 10)
    assert summary["deleted"] == summary["legacy_objects"]
    assert _keys(s3) == [lake.price_partition_key(t, y) for t in ("SPY", "TSLA") for y in (2025, 2026)]

    after = _outputs(s3)
    assert after["dashboard"] == before["dashboard"]
    assert after["latest"] == before["latest"]
    assert after["trend"].equals(before["trend"])
    # D4 wins over backfill for the overlapping days, in both layouts.
    tsla_rows = lake.read_prices("TSLA").filter(pl.col("date") >= days[-10])
    assert set(tsla_rows["source_id"]) == {"DS-02"}


def test_migration_is_idempotent_dry_run_and_keeps_newer_yearly_rows(s3):
    d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
    lake.write_parquet(
        _frame("TSLA", [d1, d2], [1.0, 2.0], "DS-05"),
        "curated/prices_daily/ticker=TSLA/batch_start=2026-09-01/batch_end=2026-10-03/prices_daily.parquet",
    )
    # New code already wrote a newer backfill row for d2 into the yearly partition before migration ran.
    lake.upsert_prices(_frame("TSLA", [d2], [2.5], "DS-05"))

    dry = compact.compact(bucket="lake", dry_run=True)
    assert dry["legacy_objects"] == 1 and dry["written"] == 0 and dry["deleted"] == 0
    assert len(_keys(s3)) == 2  # nothing written or removed

    first = compact.compact(bucket="lake")  # keep legacy objects by default
    assert first["written"] == 1 and first["deleted"] == 0
    assert lake.read_prices("TSLA")["close"].to_list() == [1.0, 2.5]  # yearly row was newer, so it stays

    second = compact.compact(bucket="lake", delete_legacy=True)
    assert second["deleted"] == 1
    assert _keys(s3) == [lake.price_partition_key("TSLA", 2026)]
    assert compact.compact(bucket="lake", delete_legacy=True)["legacy_objects"] == 0


def test_migration_cli_parses_arguments():
    args = compact.parse_args(["--bucket", "invdash-lake", "--ticker", "tsla", "--delete-legacy"])
    assert args.bucket == "invdash-lake" and args.ticker == ["TSLA"] and args.delete_legacy and not args.dry_run


# --- backfill writes through upsert -----------------------------------------------------------


def test_backfill_handler_upserts_yearly_partitions_without_clobbering_d4(s3, monkeypatch):
    from datetime import UTC, datetime

    import httpx

    import backfill
    import http_client

    def yahoo(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        start = datetime.fromtimestamp(int(q["period1"]), tz=UTC).date()
        end = datetime.fromtimestamp(int(q["period2"]), tz=UTC).date() - timedelta(days=1)
        days = _business_days(start, end)
        stamps = [int(datetime(d.year, d.month, d.day, 14, 30, tzinfo=UTC).timestamp()) for d in days]
        closes = [100.0 + i for i in range(len(days))]
        result = {
            "meta": {"gmtoffset": -14400},
            "timestamp": stamps,
            "indicators": {"quote": [{"close": closes, "volume": [1] * len(days)}], "adjclose": [{"adjclose": closes}]},
        }
        return httpx.Response(200, json={"chart": {"result": [result], "error": None}})

    real_get_client = http_client.get_client
    monkeypatch.setattr(
        http_client, "get_client", lambda **kw: real_get_client(transport=httpx.MockTransport(yahoo), **kw)
    )
    monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
    monkeypatch.setenv("BACKFILL_MAX_BATCHES", "3")

    d4_day = _business_days(datetime.now(UTC).date() - timedelta(days=10), datetime.now(UTC).date())[-3]
    lake.upsert_prices(_frame("TSLA", [d4_day], [999.0], "DS-02"))

    backfill.handler({"tickers": ["TSLA"]}, _Context())
    first = lake.read_prices("TSLA")
    backfill.handler({"tickers": ["TSLA"], "force": True}, _Context())  # full restart rewrites the same windows
    second = lake.read_prices("TSLA")

    price_keys = [k for k in _keys(s3) if not k.endswith("state.json")]
    assert price_keys and all("/year=" in k and k.endswith("/prices.parquet") for k in price_keys)
    assert {int(k.split("year=")[1][:4]) for k in price_keys} == set(first["date"].dt.year().unique().to_list())
    assert first.height > 150 and first.select("ticker", "date").is_duplicated().sum() == 0
    assert second.height == first.height
    assert first.filter(pl.col("date") == d4_day).select("close", "source_id").row(0) == (999.0, "DS-02")


def test_upsert_new_partition_never_gets_a_missing_key(s3, monkeypatch):
    # Job roles only have prefix-conditioned s3:ListBucket, so S3 answers GET on a missing key with 403.
    from botocore.exceptions import ClientError

    real_get = lake.s3().get_object

    def iam_like_get(**kwargs):
        if kwargs["Key"] not in _keys(s3):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "GetObject")
        return real_get(**kwargs)

    monkeypatch.setattr(lake.s3(), "get_object", iam_like_get)
    lake.upsert_prices(_frame("TSLA", [date(2026, 1, 2)], [1.0], "DS-02"))
    lake.upsert_prices(_frame("TSLA", [date(2026, 1, 5)], [2.0], "DS-02"))
    assert lake.read_prices("TSLA")["close"].to_list() == [1.0, 2.0]
