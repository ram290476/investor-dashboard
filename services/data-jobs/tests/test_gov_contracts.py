from datetime import date

import pytest

from gov_contracts import (
    JOB_ID,
    UEI_TO_TICKER,
    merge_awards,
    parse_sam,
    parse_usaspending,
    reconcile,
    rollup_contracts,
    sam_budget,
    sam_status,
    ticker_for,
    usaspending_body,
)
from observability import emit_job_finished, job_handler

USASPENDING = {
    "results": [
        {
            "Award ID": "80NSSC26C0001",
            "Recipient Name": "Space Exploration Technologies Corp.",
            "Recipient UEI": "SPXUEI",
            "Award Amount": 1000,
            "Awarding Agency": "NASA",
            "Start Date": "2026-09-01",
            "Description": "Launch services",
        },
        {
            "Award ID": "80NSSC26C0001",
            "Recipient Name": "Space Exploration Technologies Corp.",
            "Award Amount": 250,
            "Awarding Agency": "NASA",
            "Start Date": "2026-10-01",
            "Description": "Modification",
        },
    ]
}

SAM = {
    "awards": [
        {
            "piid": "FA8801",
            "recipientName": "Tesla, Inc.",
            "uei": "TSLAUEI",
            "obligatedAmount": 500,
            "contractingAgency": "Department of Defense",
            "awardDate": "2026-10-02",
            "description": "Energy storage",
        }
    ]
}


def test_usaspending_body_has_required_award_type_codes_and_pages():
    body = usaspending_body("Tesla", "2021-10-07", "2026-10-07", page=3)
    # USAspending answers 422 when award_type_codes is missing.
    assert body["filters"]["award_type_codes"] == ["A", "B", "C", "D"]
    assert body["filters"]["recipient_search_text"] == ["Tesla"]
    assert body["filters"]["time_period"] == [{"start_date": "2021-10-07", "end_date": "2026-10-07"}]
    assert body["page"] == 3
    assert "Recipient UEI" in body["fields"]


def test_usaspending_and_sam_fixtures_map_recipients():
    spending = parse_usaspending(USASPENDING, "2026-10-06T21:45:00+00:00")
    assert spending[0]["ticker"] == "SPCX"
    assert spending[0]["source"] == "usaspending"
    sam = parse_sam(SAM, "2026-10-06T21:45:00+00:00")
    assert sam[0]["ticker"] == "TSLA"
    assert sam[0]["award_id"] == "FA8801"


def test_modifications_sum_and_keep_the_latest_action():
    merged = merge_awards(parse_usaspending(USASPENDING, "2026-10-06T21:45:00+00:00"))
    assert len(merged) == 1
    assert merged[0]["obligated_amount"] == 1250
    assert merged[0]["action_date"] == "2026-10-01"
    assert merged[0]["description"] == "Modification"


def test_contract_rollup_keeps_source_observation_and_does_not_invent_zero_coverage():
    rows = [
        {
            "ticker": "SPCX",
            "award_id": "NASA-1",
            "agency": "NASA",
            "action_date": "2026-10-01",
            "obligated_amount": 1250,
            "source": "usaspending",
            "ingested_at": "2026-10-02T12:00:00+00:00",
            "source_url": "https://www.usaspending.gov/award/1",
        },
        {
            "ticker": "TSLA",
            "award_id": "DOD-1",
            "agency": "DoD",
            "action_date": "2025-01-01",
            "obligated_amount": 25,
            "source": "DoD",
            "ingested_at": "2026-10-02T12:00:00+00:00",
        },
    ]

    spcx, tsla = rollup_contracts(rows, date(2026, 10, 6))

    assert spcx["ttm_obligated"] == 1250
    assert spcx["ttm_awards_count"] == 1
    assert spcx["source_ids"] == ["usaspending"]
    assert spcx["observed_at"] == "2026-10-02T12:00:00+00:00"
    assert spcx["recent"][0]["source_id"] == "usaspending"
    assert spcx["recent"][0]["observed_at"] == "2026-10-02T12:00:00+00:00"
    assert tsla["ttm_obligated"] is None
    assert tsla["ttm_awards_count"] == 0
    assert tsla["source_ids"] == []


def test_uei_map_wins_over_the_recipient_name():
    UEI_TO_TICKER["CUSTOMUEI"] = "SPCX"
    try:
        assert ticker_for("Some Other Vendor", "CUSTOMUEI") == "SPCX"
        assert ticker_for("Tesla Motors", None) == "TSLA"
        assert ticker_for("Boeing", "NOPE") is None
    finally:
        UEI_TO_TICKER.pop("CUSTOMUEI", None)


def test_sam_budget_stays_at_or_under_10_a_day():
    assert sam_budget(0) == 3
    assert sam_budget(8) == 2
    assert sam_budget(9) == 1
    assert sam_budget(10) == 0
    assert sam_budget(0, wanted=5) == 3


def test_expired_sam_key_is_partial_and_does_not_raise_as_a_job_failure():
    assert sam_status(401) == "partial"
    assert sam_status(403) == "partial"
    assert sam_status(200) == "ok"
    with pytest.raises(RuntimeError):
        sam_status(500)
    dod = {
        "award_id": "80NSSC26C0001",
        "source": "DoD",
        "action_date": "2026-10-01",
        "obligated_amount": 9999,
        "ticker": "SPCX",
    }
    provisional = {
        "award_id": "DoD-SPCX-2026-10-06-0",
        "source": "DoD",
        "action_date": "2026-10-06",
        "obligated_amount": 50,
        "ticker": "SPCX",
    }
    rows = reconcile([*parse_usaspending(USASPENDING, "2026-10-06T21:45:00+00:00"), dod, provisional])
    by_id = {row["award_id"]: row for row in rows}
    assert by_id["80NSSC26C0001"]["obligated_amount"] == 1250
    assert by_id["DoD-SPCX-2026-10-06-0"]["source"] == "DoD"


def test_handler_emits_job_d5(monkeypatch):
    emitted = {}

    def capture(job_id, run_id, outcome, detail=None):
        emitted["job"] = job_id

    monkeypatch.setattr("observability.emit_job_finished", capture)

    @job_handler(JOB_ID)
    def run(event, context):
        return {"status": "success"}

    class Context:
        function_name = "gov-contracts"
        memory_limit_in_mb = 512
        invoked_function_arn = "arn:aws:lambda:us-east-1:1:function:gov-contracts"
        aws_request_id = "req"

    assert run({}, Context())["status"] == "success"
    assert emitted["job"] == "D5"
    assert emit_job_finished.__name__ == "emit_job_finished"


# --- watchlist-driven name matching and rollup --------------------------------------

import gov_contracts  # noqa: E402

WATCH_NAMES = {"TSLA": "Tesla, Inc.", "SPCX": None, "PLTR": "Palantir Technologies Inc.", "AMZN": "AMAZON COM INC"}


def test_name_map_built_from_watchlist_entity_names():
    entries = gov_contracts.name_map_from_watchlist(WATCH_NAMES)
    assert ("palantir technologies", "PLTR") in entries
    assert ("amazon", "AMZN") in entries
    assert entries[:3] == list(gov_contracts.NAME_OVERRIDES)
    saved = list(gov_contracts.NAME_TO_TICKER)
    try:
        gov_contracts.use_watchlist_names(WATCH_NAMES)
        assert ticker_for("PALANTIR TECHNOLOGIES INC.", None) == "PLTR"
        assert ticker_for("Amazon Web Services, Inc.", None) == "AMZN"
        assert ticker_for("Space Exploration Technologies Corp", None) == "SPCX"
        assert ticker_for("Teslaco Holdings", None) is None  # word boundary, not substring
    finally:
        gov_contracts.NAME_TO_TICKER[:] = saved


def test_recipient_searches_and_sam_rotation_cover_the_watchlist():
    searches = gov_contracts.recipient_searches(["TSLA", "SPCX", "PLTR", "NONAME"], {**WATCH_NAMES, "NONAME": None})
    assert searches == [
        ("TSLA", "Tesla"),
        ("SPCX", "Space Exploration Technologies"),
        ("PLTR", "Palantir Technologies"),
    ]
    picked = {gov_contracts.sam_query_for(searches, date(2026, 10, d)) for d in range(1, 4)}
    assert picked == {"Tesla", "Space Exploration Technologies", "Palantir Technologies"}
    assert gov_contracts.sam_query_for([], date(2026, 10, 1)) == "SpaceX"


def test_rollup_covers_every_watchlist_ticker_with_awards():
    rows = [
        {"ticker": t, "award_id": f"A-{t}", "agency": "DoD", "action_date": "2026-09-30", "obligated_amount": 10,
         "source": "usaspending", "ingested_at": "2026-10-02T12:00:00+00:00"}
        for t in ("PLTR", "TSLA", "XYZ")
    ] + [{"ticker": None, "award_id": "N", "action_date": "2026-09-30", "obligated_amount": 5}]
    rolled = rollup_contracts(rows, date(2026, 10, 6), ["TSLA", "SPCX", "PLTR"])
    assert [row["ticker"] for row in rolled] == ["PLTR", "TSLA"]
    assert [row["ticker"] for row in rollup_contracts(rows, date(2026, 10, 6))] == ["PLTR", "TSLA", "XYZ"]


def test_sam_opportunities_params_include_mandatory_posted_window():
    from gov_contracts import sam_opportunities_params

    params = sam_opportunities_params("SpaceX", date(2026, 10, 14), "k")
    # Get Opportunities v2 answers HTTP 400 without postedFrom/postedTo (MM/dd/yyyy).
    assert params["postedFrom"] == "09/14/2026"
    assert params["postedTo"] == "10/14/2026"
    assert params["title"] == "SpaceX" and params["limit"] == 10


def test_sam_error_carries_the_body_with_the_key_redacted():
    body = '{"errorMessage":"PostedFrom and PostedTo are mandatory","url":"/search?api_key=SECRET123&limit=10"}'
    with pytest.raises(RuntimeError) as excinfo:
        sam_status(400, body)
    message = str(excinfo.value)
    assert "HTTP 400" in message and "PostedFrom and PostedTo are mandatory" in message
    assert "SECRET123" not in message and "api_key=REDACTED" in message


def test_dod_feed_points_at_war_gov_and_parses_items():
    from urllib.parse import urlparse

    from gov_contracts import DOD_CONTRACTS_FEED, parse_dod_feed
    from http_client import ALLOWED_HOSTS

    assert urlparse(DOD_CONTRACTS_FEED).hostname in ALLOWED_HOSTS
    xml = """<?xml version="1.0"?><rss version="2.0"><channel><title>Contracts</title>
      <item><title>Contracts for Oct. 14, 2026</title>
      <link>https://www.war.gov/News/Contracts/Contract/Article/4625668/contracts-for-oct-14-2026/</link>
      <pubDate>Wed, 14 Oct 2026 21:04:17 GMT</pubDate></item></channel></rss>"""
    items = parse_dod_feed(xml)
    assert items == [{
        "title": "Contracts for Oct. 14, 2026",
        "link": "https://www.war.gov/News/Contracts/Contract/Article/4625668/contracts-for-oct-14-2026/",
        "published": "Wed, 14 Oct 2026 21:04:17 GMT",
    }]


class _Resp:
    def __init__(self, status, retry_after=None):
        self.status_code = status
        self.headers = {"Retry-After": retry_after} if retry_after is not None else {}


def test_nasa_fetch_retries_429_honoring_retry_after_then_succeeds():
    responses = iter([_Resp(429, "7"), _Resp(429), _Resp(200)])
    waits = []
    got = gov_contracts.fetch_with_rate_limit(lambda: next(responses), waits.append)
    assert got.status_code == 200
    assert waits == [7.0, gov_contracts.NASA_BASE_DELAY_S * 2]


def test_nasa_fetch_returns_none_when_still_rate_limited():
    waits = []
    got = gov_contracts.fetch_with_rate_limit(lambda: _Resp(429, "999"), waits.append, attempts=3)
    assert got is None
    assert waits == [gov_contracts.NASA_MAX_DELAY_S] * 2  # capped, no sleep after the last try


def test_retry_after_delay_handles_bad_header():
    assert gov_contracts.retry_after_delay("soon", 0) == gov_contracts.NASA_BASE_DELAY_S
    assert gov_contracts.retry_after_delay(None, 1) == gov_contracts.NASA_BASE_DELAY_S * 2
    assert gov_contracts.retry_after_delay("Wed, 21 Oct 2015 07:28:00 GMT", 0) == 0.0
