"""D1 FRED collector: parse, watermark, idempotent upsert, federal skip, job id."""

import json
from datetime import UTC, date, datetime
from urllib.parse import parse_qs

import boto3
import httpx
import polars as pl
import pytest
from moto import mock_aws

import api_keys
import http_client
import lake
import macro_daily
import observability
import status_feed as sf

SECRET = "super-secret-fred-key"


class _Context:
    function_name = "macro-daily"
    memory_limit_in_mb = 512
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:macro-daily"
    aws_request_id = "req-d1"


def test_daily_series_are_the_trend_job_fred_drivers():
    assert macro_daily.DAILY_FRED_SERIES == (
        "DGS2",
        "DGS10",
        "DGS30",
        "T10Y2Y",
        "DFII10",
        "T10YIE",
        "DFEDTARU",
        "EFFR",
        "SOFR",
        "VIXCLS",
        "DTWEXBGS",
        "DCOILWTICO",
        "USEPUINDXD",
        "DGS1MO",
        "DGS3MO",
        "DGS6MO",
        "DGS1",
        "DGS3",
        "DGS5",
        "DGS7",
        "DGS20",
    )


def test_parse_drops_missing_and_keeps_the_next_business_day():
    rows = macro_daily.parse_observations(
        {
            "observations": [
                {"date": "2026-10-02", "value": "4.25"},
                {"date": "2026-10-05", "value": "."},
                {"date": "2026-10-06", "value": "nan"},
                {"date": "2026-01-16", "value": "4.10"},
            ]
        },
        "DGS10",
    )
    assert [row["obs_date"] for row in rows] == [date(2026, 10, 2), date(2026, 1, 16)]
    assert rows[0]["value"] == 4.25
    # Friday 2026-10-02 is available Monday. Friday 2026-01-16 is before MLK Day (Monday the 19th).
    assert rows[0]["available_date"] == date(2026, 10, 5)
    assert rows[1]["available_date"] == date(2026, 1, 20)
    assert rows[0]["source"] == "fred"


def test_observation_window_backfills_five_years_then_revisits_ten_days():
    assert macro_daily.observation_start(None, date(2026, 10, 8)) == date(2021, 10, 8)
    assert macro_daily.observation_start(date(2026, 10, 1), date(2026, 10, 8)) == date(2026, 9, 21)
    assert macro_daily.observation_start(None, date(2024, 2, 29)) == date(2019, 2, 28)


def test_revision_overwrites_the_same_observation_and_a_replay_does_not_duplicate():
    first = macro_daily.curated_frame(
        macro_daily.parse_observations({"observations": [{"date": "2026-10-02", "value": "4.25"}]}, "DGS10"),
        "2026-10-03T11:00:00+00:00",
    )
    revised = macro_daily.curated_frame(
        macro_daily.parse_observations({"observations": [{"date": "2026-10-02", "value": "4.10"}]}, "DGS10"),
        "2026-10-06T11:00:00+00:00",
    )
    merged = lake.dedupe_ranked(pl.concat([first, revised]), ["series_id", "obs_date"], "ingested_at")
    assert merged.height == 1
    assert merged["value"].to_list() == [4.10]
    replay = lake.dedupe_ranked(pl.concat([merged, revised]), ["series_id", "obs_date"], "ingested_at")
    assert replay.height == 1
    assert replay["value"].to_list() == [4.10]


def test_watermark_advances_only_forward():
    rows = macro_daily.parse_observations(
        {"observations": [{"date": "2026-10-01", "value": "1"}, {"date": "2026-10-02", "value": "2"}]},
        "SOFR",
    )
    assert macro_daily.advance_watermark(None, rows) == date(2026, 10, 2)
    assert macro_daily.advance_watermark(date(2026, 10, 8), rows) == date(2026, 10, 8)
    assert macro_daily.advance_watermark(date(2026, 10, 2), []) == date(2026, 10, 2)


def test_redact_strips_the_key_from_anything_stored():
    cleaned = macro_daily.redact_secrets(
        {"notes": f"see https://api.stlouisfed.org/fred/series?api_key={SECRET}&file_type=json"},
        SECRET,
    )
    assert SECRET not in json.dumps(cleaned)
    assert "api_key=" in json.dumps(cleaned)


def test_federal_calendar_skips_weekends_and_holidays():
    assert not macro_daily.is_federal_business_day(date(2026, 10, 4))
    assert not macro_daily.is_federal_business_day(date(2026, 12, 25))
    assert not macro_daily.is_federal_business_day(date(2026, 7, 3))  # Independence Day observed
    assert macro_daily.is_federal_business_day(date(2026, 10, 5))


def test_d1_success_lights_the_status_row():
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    feed = sf.apply_event(None, {"job": "D1", "outcome": "success", "failed_sources": 0}, now)
    row = next(job for job in feed["jobs"] if job["job"] == "D1")
    assert row["status"] == "ok"
    assert row["name"] == "Morning macro & calendars"
    assert row["next_run"]


def _lake(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    boto3.client("s3").create_bucket(Bucket="lake")
    monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
    monkeypatch.setattr(lake, "_s3", None)
    monkeypatch.setattr(observability, "_events", None)


def test_handler_backfills_then_revises_without_leaking_the_key(monkeypatch):
    emitted = []

    def capture(job_id, run_id, outcome, detail=None):
        emitted.append({"job": job_id, "outcome": outcome, "detail": detail})

    monkeypatch.setattr(observability, "emit_job_finished", capture)
    monkeypatch.setattr(macro_daily, "DAILY_FRED_SERIES", ("DGS10", "SOFR"))
    monkeypatch.setattr(macro_daily, "today_et", lambda: date(2026, 10, 5))
    monkeypatch.setattr(api_keys, "api_key", lambda name: SECRET)
    values = {"DGS10": "4.25", "SOFR": "4.30"}
    requested: list[dict] = []

    def fred(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        requested.append({key: query[key][0] for key in query})
        series_id = query["series_id"][0]
        return httpx.Response(
            200,
            json={
                "observations": [
                    {"date": "2026-10-01", "value": "."},
                    {"date": "2026-10-02", "value": values[series_id]},
                ]
            },
        )

    real_get_client = http_client.get_client

    def client(**kwargs):
        return real_get_client(transport=httpx.MockTransport(fred), **kwargs)

    monkeypatch.setattr(http_client, "get_client", client)

    with mock_aws():
        _lake(monkeypatch)
        first = macro_daily.handler({}, _Context())
        assert first["status"] == "success"
        assert first["series"] == 2
        assert {call["observation_start"] for call in requested} == {"2021-10-05"}
        assert {call["series_id"] for call in requested} == {"DGS10", "SOFR"}

        body = lake.read_json("raw/fred/series_id=DGS10/date=2026-10-05/req-d1.json")
        assert SECRET not in json.dumps(body)
        frame = lake.read_parquet_prefix("curated/macro_daily/source=fred/")
        assert frame.height == 2
        assert set(frame["series_id"].to_list()) == {"DGS10", "SOFR"}
        assert frame.filter(pl.col("series_id") == "DGS10")["value"].to_list() == [4.25]
        assert frame["available_date"].to_list() == [date(2026, 10, 5), date(2026, 10, 5)]
        marks = lake.read_json(macro_daily.STATE_KEY)["series"]
        assert marks == {"DGS10": "2026-10-02", "SOFR": "2026-10-02"}

        values["DGS10"] = "4.10"
        requested.clear()
        second = macro_daily.handler({}, _Context())
        assert second["status"] == "success"
        assert {call["observation_start"] for call in requested} == {"2026-09-22"}
        revised = lake.read_parquet_prefix("curated/macro_daily/source=fred/series_id=DGS10/")
        assert revised.height == 1
        assert revised["value"].to_list() == [4.10]
        state_text = json.dumps(lake.read_json(macro_daily.STATE_KEY))
        assert SECRET not in state_text

    assert emitted[-1]["job"] == "D1"
    assert emitted[-1]["outcome"] == "success"
    assert all(item["job"] == "D1" for item in emitted)


def test_handler_skips_a_federal_holiday_and_still_finishes_as_d1(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        observability,
        "emit_job_finished",
        lambda job_id, run_id, outcome, detail=None: emitted.append((job_id, outcome)),
    )
    monkeypatch.setattr(macro_daily, "today_et", lambda: date(2026, 12, 25))

    def boom(**kwargs):
        raise AssertionError("holiday run must not call FRED")

    monkeypatch.setattr(http_client, "get_client", boom)
    result = macro_daily.handler({}, _Context())
    assert result["status"] == "skipped"
    assert emitted == [("D1", "success")]


def test_fred_application_error_is_not_a_successful_empty_collection(monkeypatch):
    monkeypatch.setattr(macro_daily, "httpx_get", lambda *args: httpx.Response(
        200, json={"error_code": 400, "error_message": "Invalid API key"},
    ))
    with pytest.raises(RuntimeError, match=r"DGS10.*ValueError"):
        macro_daily._fetch_observations(None, "DGS10", date(2026, 1, 1), date(2026, 10, 8), SECRET)
