import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

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
