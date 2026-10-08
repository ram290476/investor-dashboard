import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import release_day
from observability import emit_job_finished, job_handler

BEA = {
    "BEAAPI": {
        "Results": {
            "Data": [
                {
                    "TableName": "T20804",
                    "LineNumber": "1",
                    "LineDescription": "Personal consumption expenditures",
                    "TimePeriod": "2026M09",
                    "DataValue": "124.500",
                },
                {
                    "TableName": "T20804",
                    "LineNumber": "25",
                    "LineDescription": "Personal consumption expenditures excluding food and energy",
                    "TimePeriod": "2026M09",
                    "DataValue": "126.0",
                },
            ]
        }
    }
}

CENSUS = [["time", "IMPG", "EXPG"], ["2026-09", "280000", "170000"]]

ICS = """BEGIN:VEVENT
DTSTART;VALUE=DATE:20261015
SUMMARY:Consumer Price Index
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261113
SUMMARY:Producer Price Index
END:VEVENT
"""

FRED = {
    "release_dates": [
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-10-15"},
        {"release_id": 54, "release_name": "Personal Income and Outlays", "date": "2026-10-30"},
        {"release_id": 51, "release_name": "U.S. International Trade in Goods and Services", "date": "2026-11-05"},
        {"release_id": 53, "release_name": "Gross Domestic Product", "date": "2026-10-29"},
    ]
}

BLS = {
    "Results": {
        "series": [
            {
                "seriesID": "CUSR0000SA0",
                "data": [
                    {"year": "2026", "period": "M08", "value": "320.0"},
                    {"year": "2026", "period": "M09", "value": "321.2"},
                ],
            },
            {
                "seriesID": "CUSR0000SA0L1E",
                "data": [{"year": "2026", "period": "M09", "value": "325.0"}],
            },
        ]
    }
}


def test_bls_post_covers_headline_and_core_in_one_body():
    body = release_day.bls_body(2021, 2026, "bls-key")
    assert body["seriesid"] == ["CUSR0000SA0", "CUSR0000SA0L1E"]
    assert body["registrationkey"] == "bls-key"
    rows = release_day.parse_bls(BLS, "2026-10-15T12:35:00+00:00", {"CUSR0000SA0": 321.0})
    headline = next(row for row in rows if row["series_id"] == "CUSR0000SA0" and row["period"] == "2026-09")
    assert headline["actual"] == 321.2
    assert headline["surprise"] == release_day.surprise(321.2, 321.0)
    assert abs(headline["surprise"] - 0.2) < 1e-9


def test_revision_keeps_the_prior_actual_and_release_history():
    first = release_day.parse_bls(BLS, "2026-10-15T12:35:00+00:00")
    revised = release_day.parse_bls(
        {
            "Results": {
                "series": [
                    {
                        "seriesID": "CUSR0000SA0",
                        "data": [{"year": "2026", "period": "M09", "value": "321.5"}],
                    }
                ]
            }
        },
        "2026-11-13T13:35:00+00:00",
    )
    merged = release_day.merge_releases(first, revised)
    row = next(item for item in merged if item["series_id"] == "CUSR0000SA0" and item["period"] == "2026-09")
    assert row["actual"] == 321.5
    assert row["revised_prior"] == 321.2
    assert row["release_history"] == ["2026-10-15T12:35:00+00:00", "2026-11-13T13:35:00+00:00"]
    assert row["release_ts"] == "2026-10-15T12:35:00+00:00"


def test_calendar_at_schedule_converts_eastern_time_through_dst():
    winter = release_day.calendar_schedules(["2026-01-15"], "cpi")
    summer = release_day.calendar_schedules(["2026-07-15"], "cpi")
    assert winter[0]["ScheduleExpression"] == "at(2026-01-15T13:35:00)"
    assert summer[0]["ScheduleExpression"] == "at(2026-07-15T12:35:00)"
    assert winter[0]["Name"].startswith("invdash-m1-cpi-")
    when = datetime(2026, 1, 15, 8, 35, tzinfo=ZoneInfo("America/New_York"))
    assert release_day.at_schedule(when) == "at(2026-01-15T13:35:00)"


def test_no_new_period_retries_then_exits_cleanly():
    pauses = []
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"series_id": "CUSR0000SA0", "period": "2026-08", "actual": 1.0}]

    fresh = release_day.poll_for_new_period(
        fetch,
        {("CUSR0000SA0", "2026-08")},
        attempts=3,
        pause=pauses.append,
    )
    assert fresh == []
    assert calls["n"] == 3
    assert pauses == [120, 120]


def test_handler_emits_job_m1_and_trend_metrics_would_fire(monkeypatch):
    class FakeBus:
        def __init__(self):
            self.entries = []

        def put_events(self, Entries):
            self.entries.extend(Entries)
            return {"FailedEntryCount": 0}

    bus = FakeBus()
    order = []
    monkeypatch.setattr("observability._events", bus)

    @job_handler(release_day.JOB_ID)
    def run(event, context):
        order.append("macro")
        return {"status": "success"}

    class Context:
        function_name = "release-day"
        memory_limit_in_mb = 512
        invoked_function_arn = "arn:aws:lambda:us-east-1:1:function:release-day"
        aws_request_id = "req"

    assert run({}, Context())["status"] == "success"
    detail = json.loads(bus.entries[0]["Detail"])
    order.append("event")
    assert detail["job"] == "M1"
    assert release_day.trend_should_run(detail)
    assert order == ["macro", "event"]
    assert emit_job_finished.__name__ == "emit_job_finished"


def test_bea_and_census_parse_into_release_rows():
    pce = release_day.parse_bea(BEA, "2026-10-30T12:35:00+00:00")
    assert {row["series_id"] for row in pce} == {"PCE", "CORE_PCE"}
    assert next(row for row in pce if row["series_id"] == "PCE")["period"] == "2026-09"
    trade = release_day.parse_census(CENSUS, "2026-11-05T13:35:00+00:00")
    assert {row["series_id"] for row in trade} == {"CENSUS_IMPG", "CENSUS_EXPG"}
    assert next(row for row in trade if row["series_id"] == "CENSUS_IMPG")["actual"] == 280000


def test_stopgap_calendar_prefers_bls_ics_and_keeps_fred_pce_and_trade():
    rows = release_day.calendar_rows(release_day.parse_ics(ICS), release_day.parse_fred_dates(FRED))
    by_series = {row["series"]: row for row in rows}
    assert set(by_series) == {"cpi", "pce", "trade"}
    assert by_series["cpi"]["source"] == "bls"
    assert by_series["cpi"]["release_ts"].startswith("2026-10-15T08:35")
    assert by_series["pce"]["source"] == "fred"
    upcoming = release_day.upcoming_calendar(rows, date(2026, 10, 16))
    assert [row["series"] for row in upcoming] == ["pce", "trade"]


def test_nowcast_becomes_an_index_consensus_and_enrichment_is_derived():
    rows = release_day.parse_bls(BLS, "2026-10-15T12:35:00+00:00")
    merged = release_day.merge_releases([], rows)
    release_day.apply_nowcast(merged, [{"measure": "CPI", "value": 0.25}, {"measure": "Core CPI", "value": 0.1}])
    headline = next(row for row in merged if row["series_id"] == "CUSR0000SA0" and row["period"] == "2026-09")
    assert headline["consensus"] == release_day.consensus_index(320.0, 0.25)
    assert headline["surprise"] == release_day.surprise(321.2, headline["consensus"])
    release_day.enrich_releases(merged)
    assert abs(headline["mom"] - ((321.2 / 320.0 - 1) * 100)) < 1e-9
    core = next(row for row in merged if row["series_id"] == "CUSR0000SA0L1E")
    assert core["mom"] is None
    assert core["core_vs_headline"] is None
    history = [
        {"series_id": "CUSR0000SA0", "period": f"2026-0{month}", "actual": 100.0 + month, "surprise": month / 10}
        for month in range(1, 5)
    ]
    enriched = release_day.enrich_releases(history)
    assert enriched[-1]["surprise_z"] is not None


def test_follow_up_schedules_cover_the_window_and_skip_the_past():
    planned = release_day.follow_up_schedules(["2026-01-15"], "cpi")
    assert [item["ScheduleExpression"] for item in planned] == [
        "at(2026-01-15T13:35:00)",
        "at(2026-01-15T13:39:00)",
        "at(2026-01-15T13:43:00)",
        "at(2026-01-15T13:47:00)",
        "at(2026-01-15T13:51:00)",
    ]
    kept = release_day.future_schedules(planned, datetime(2026, 1, 15, 13, 40, tzinfo=ZoneInfo("UTC")))
    assert [item["ScheduleExpression"] for item in kept] == [
        "at(2026-01-15T13:43:00)",
        "at(2026-01-15T13:47:00)",
        "at(2026-01-15T13:51:00)",
    ]


def test_month_open_is_the_first_federal_business_day():
    planned = release_day.month_open_schedules(date(2026, 1, 1), months=1)
    assert planned[0]["ScheduleExpression"] == "at(2026-01-02T13:00:00)"
    assert planned[0]["Input"]["source"] == "calendar"
    assert planned[0]["Name"].startswith("invdash-m1-calendar-")


def test_ensure_schedules_is_idempotent_and_names_stay_on_the_m1_prefix():
    class ConflictException(Exception):
        pass

    class Client:
        def __init__(self):
            self.created = []

        def create_schedule(self, **kwargs):
            if kwargs["Name"] in self.created:
                raise ConflictException()
            assert kwargs["Name"].startswith("invdash-m1-")
            assert kwargs["ActionAfterCompletion"] == "DELETE"
            assert kwargs["Target"]["RoleArn"] == "role"
            self.created.append(kwargs["Name"])

    client = Client()
    planned = release_day.calendar_schedules(["2026-07-15"], "cpi")
    first = release_day.ensure_schedules(client, planned, "arn:function", "role")
    second = release_day.ensure_schedules(client, planned, "arn:function", "role")
    assert first == second
    assert len(client.created) == 1


def test_first_vintage_date_is_the_earliest_realtime_start():
    dates = release_day.first_release_dates(
        {
            "observations": [
                {"date": "2024-09-01", "realtime_start": "2024-11-13", "value": "2"},
                {"date": "2024-09-01", "realtime_start": "2024-10-10", "value": "1"},
                {"date": "2024-10-01", "realtime_start": "2024-11-13", "value": "3"},
            ]
        }
    )
    assert dates == {"2024-09": "2024-10-10", "2024-10": "2024-11-13"}


def test_vintage_date_replaces_a_backfill_stamp_and_does_not_move_forward():
    rows = [
        {
            "series_id": "CUSR0000SA0",
            "period": "2024-09",
            "release_ts": "2026-10-08T12:00:00+00:00",
            "release_history": ["2026-10-08T12:00:00+00:00"],
        }
    ]
    release_day.apply_vintage_dates(rows, {"CUSR0000SA0": {"2024-09": "2024-10-10"}})
    assert rows[0]["release_ts"].startswith("2024-10-10T08:35")
    assert rows[0]["release_history"] == [rows[0]["release_ts"]]
    release_day.apply_vintage_dates(rows, {"CUSR0000SA0": {"2024-09": "2024-11-13"}})
    assert rows[0]["release_ts"].startswith("2024-10-10T08:35")


def _index_history(series_id: str, start: tuple[int, int], n: int, base: float) -> list[dict]:
    rows = []
    year, month = start
    for i in range(n):
        period = f"{year:04d}-{month:02d}"
        rel_month, rel_year = month + 1, year
        if rel_month == 13:
            rel_month, rel_year = 1, year + 1
        rows.append(
            {
                "series_id": series_id,
                "period": period,
                "release_ts": f"{rel_year:04d}-{rel_month:02d}-15T13:30:00+00:00",
                "actual": base + i,
                "prior": None,
                "revised_prior": None,
                "consensus": None,
                "surprise": None,
                "units": "index",
                "source": "bls",
            }
        )
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return rows


def test_macro_rows_store_yoy_on_the_release_date():
    import polars as pl

    import trend_metrics as tm

    enriched = release_day.enrich_releases(_index_history("CUSR0000SA0", (2024, 1), 14, 100.0))
    macro = release_day.macro_rows(enriched)
    assert macro
    assert {row["series_id"] for row in macro} == {"CPI_YOY"}
    assert all(not row["obs_date"].endswith("-01") for row in macro)
    released = next(row for row in macro if row["obs_date"] == "2025-02-15")
    assert released["value"] == pytest.approx(12.0)
    assert released["available_date"] == "2025-02-15"
    assert released["source"] == "releases"
    assert "CUSR0000SA0" not in {row["series_id"] for row in macro}

    driver = pl.DataFrame(
        {
            "date": [date.fromisoformat(row["obs_date"]) for row in macro],
            "value": [row["value"] for row in macro],
        }
    )
    prices = pl.DataFrame({"date": [date(2025, 2, 14), date(2025, 2, 15)], "close": [100.0, 101.0]})
    out = {row["date"]: row["value"] for row in tm.driver_metrics(prices, driver, "CPI_YOY", "level").to_dicts()}
    assert date(2025, 2, 14) not in out
    assert out[date(2025, 2, 15)] == pytest.approx(12.0)

    trade = release_day.macro_rows(
        [
            {
                "series_id": "CENSUS_IMPG",
                "period": "2024-01",
                "release_ts": "2024-03-07T13:35:00+00:00",
                "yoy": 4.5,
                "actual": 10.0,
            }
        ]
    )
    assert trade == [
        {
            "series_id": "CENSUS_IMPG_YOY",
            "obs_date": "2024-03-07",
            "value": 4.5,
            "available_date": "2024-03-07",
            "source": "releases",
        }
    ]


def test_publish_macro_overwrites_a_revised_release_and_stays_in_its_folder(monkeypatch):
    import boto3
    from moto import mock_aws

    import lake

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", None)
        row = {
            "series_id": "CPI_YOY",
            "obs_date": "2025-02-15",
            "value": 12.0,
            "available_date": "2025-02-15",
            "source": "releases",
        }
        release_day.publish_macro([row], "2026-02-15T13:30:00+00:00")
        revised = {**row, "value": 12.4}
        keys = release_day.publish_macro([revised], "2026-03-12T13:30:00+00:00")
        assert keys == ["curated/macro_daily/source=releases/series_id=CPI_YOY/macro.parquet"]
        stored = lake.read_parquet_prefix("curated/macro_daily/source=releases/")
        assert stored.height == 1
        assert stored["value"].to_list() == [12.4]
        assert stored["obs_date"].to_list() == [date(2025, 2, 15)]
        listed = boto3.client("s3").list_objects_v2(Bucket="lake", Prefix="curated/macro_daily/")["Contents"]
        assert all("date=" not in item["Key"] for item in listed)


def test_handler_writes_release_dated_yoy_and_release_links(monkeypatch):
    import json
    from urllib.parse import parse_qs

    import boto3
    import httpx
    import polars as pl
    from moto import mock_aws

    import api_keys
    import http_client
    import lake
    import observability

    secret = "super-secret-fred-key"
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("SCHEDULER_ROLE_ARN", raising=False)
    monkeypatch.setattr(api_keys, "api_key", lambda name: secret)
    monkeypatch.setattr("universe.user_ticker_union", lambda *args, **kwargs: [])
    emitted = []
    monkeypatch.setattr(
        observability,
        "emit_job_finished",
        lambda job_id, run_id, outcome, detail=None: emitted.append(job_id),
    )

    months = []
    year, month = 2023, 1
    for i in range(20):
        period = f"{year:04d}-{month:02d}"
        rel_month, rel_year = month + 1, year
        if rel_month == 13:
            rel_month, rel_year = 1, year + 1
        months.append((period, f"{rel_year:04d}-{rel_month:02d}-15", 100.0 + i, 200.0 + i))
        month += 1
        if month == 13:
            month, year = 1, year + 1

    def respond(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(release_day.BLS_URL):
            return httpx.Response(
                200,
                json={
                    "Results": {
                        "series": [
                            {
                                "seriesID": "CUSR0000SA0",
                                "data": [
                                    {"year": p[:4], "period": f"M{p[5:7]}", "value": str(headline)}
                                    for p, _, headline, _core in months
                                ],
                            },
                            {
                                "seriesID": "CUSR0000SA0L1E",
                                "data": [
                                    {"year": p[:4], "period": f"M{p[5:7]}", "value": str(core)}
                                    for p, _, _headline, core in months
                                ],
                            },
                        ]
                    }
                },
            )
        if "apps.bea.gov" in url:
            return httpx.Response(200, json={"BEAAPI": {"Results": {"Data": []}}})
        if "api.census.gov" in url:
            return httpx.Response(200, json=[["time", "IMPG", "EXPG"]])
        if url.startswith(release_day.BLS_ICS_URL):
            return httpx.Response(200, text="")
        if "releases/dates" in url:
            return httpx.Response(200, json={"release_dates": []})
        if "series/observations" in url:
            series_id = parse_qs(request.url.query.decode())["series_id"][0]
            observations = []
            if series_id == "CPIAUCSL":
                observations = [
                    {"date": f"{period}-01", "realtime_start": release, "value": "1"}
                    for period, release, _headline, _core in months
                ]
            return httpx.Response(200, json={"observations": observations})
        if "clevelandfed.org" in url:
            return httpx.Response(200, text="<html></html>")
        raise AssertionError(url)

    real_client = http_client.get_client
    monkeypatch.setattr(
        http_client, "get_client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)
    )

    class Context:
        function_name = "release-day"
        memory_limit_in_mb = 512
        invoked_function_arn = "arn:aws:lambda:us-east-1:1:function:release-day"
        aws_request_id = "req-m1"

    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", None)
        monkeypatch.setattr(observability, "_events", None)
        result = release_day.handler({}, Context())
        assert result["status"] == "success"
        macro = lake.read_parquet_prefix("curated/macro_daily/source=releases/")
        assert set(macro["series_id"].unique()) == {"CPI_YOY", "CORE_CPI_YOY"}
        headline = macro.filter(pl.col("series_id") == "CPI_YOY").sort("obs_date")
        assert headline["obs_date"][0] == date(2024, 2, 15)
        assert headline["value"][0] == pytest.approx(12.0)
        assert date(2024, 1, 1) not in headline["obs_date"].to_list()
        links = lake.read_parquet_prefix("curated/release_links/")
        trend = links.filter((pl.col("row_kind") == "trend") & (pl.col("series_id") == "CPI_YOY"))
        assert trend.height == 1
        assert trend["trend_direction"][0] == "decelerating"
        assert trend["consecutive_releases"][0] >= 2
        listed = boto3.client("s3").list_objects_v2(Bucket="lake", Prefix="curated/")["Contents"]
        assert all("curated/macro_daily/date=" not in item["Key"] for item in listed)
        vintage_keys = [
            item["Key"]
            for item in boto3.client("s3").list_objects_v2(Bucket="lake", Prefix="raw/releases/fred-vintages/")[
                "Contents"
            ]
        ]
        assert vintage_keys
        for key in vintage_keys:
            assert secret not in json.dumps(lake.read_json(key))

    assert emitted == ["M1"]


def test_backfill_refresh_recognizes_a_finished_backfill_only():
    assert release_day.backfill_refresh({"detail": {"job": "BACKFILL", "outcome": "success"}})
    assert release_day.backfill_refresh({"detail": '{"job": "BACKFILL", "outcome": "success"}'})
    assert not release_day.backfill_refresh({"detail": {"job": "BACKFILL", "outcome": "failure"}})
    assert not release_day.backfill_refresh({"source": "schedule"})
    assert not release_day.backfill_refresh(None)


def test_backfill_event_rebuilds_links_for_a_new_ticker_without_fetching(monkeypatch):
    import boto3
    import polars as pl
    from moto import mock_aws

    import lake
    import observability

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr("universe.user_ticker_union", lambda *args, **kwargs: ["NVDA"])
    monkeypatch.setattr(
        observability,
        "emit_job_finished",
        lambda job_id, run_id, outcome, detail=None: None,
    )

    class Context:
        function_name = "release-day"
        memory_limit_in_mb = 512
        invoked_function_arn = "arn:aws:lambda:us-east-1:1:function:release-day"
        aws_request_id = "req-backfill"

    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", None)
        monkeypatch.setattr(observability, "_events", None)
        releases = pl.DataFrame(
            {
                "series_id": ["CUSR0000SA0", "CUSR0000SA0"],
                "period": ["2024-01", "2024-02"],
                "release_ts": ["2024-02-13T13:30:00+00:00", "2024-03-12T12:30:00+00:00"],
                "yoy": [2.0, 2.4],
                "actual": [100.0, 101.0],
                "consensus": [None, None],
            }
        )
        lake.write_parquet(releases, "curated/releases/series=CUSR0000SA0/year=2024/releases.parquet")
        prices = pl.DataFrame(
            {
                "ticker": ["NVDA", "NVDA"],
                "date": [date(2024, 3, 11), date(2024, 3, 12)],
                "close": [100.0, 101.0],
            }
        )
        lake.write_parquet(prices, "curated/prices_daily/ticker=NVDA/year=2024/prices.parquet")
        result = release_day.handler({"detail": {"job": "BACKFILL", "outcome": "success"}}, Context())
        assert result["links_only"] is True
        assert result["tickers"] == 1
        table = lake.read_parquet_prefix("curated/release_links/")
        summaries = table.filter(pl.col("row_kind") == "summary")
        assert set(summaries["ticker"].to_list()) == {"NVDA"}
        assert summaries["correlation_surprise"].null_count() == summaries.height
        assert "TSLA" not in summaries["ticker"].to_list()
