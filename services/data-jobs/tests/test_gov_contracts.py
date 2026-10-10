from datetime import date, timedelta

import pytest

from gov_contracts import (
    DOD_FEED_URL,
    JOB_ID,
    NASA_FEED_URL,
    SAM_CALLS_PER_DAY,
    SAM_CALLS_PER_RUN,
    UEI_TO_TICKER,
    USASPENDING_CONTRACT_FIELDS,
    ProviderStatus,
    feed_coverage,
    merge_awards,
    parse_sam,
    parse_usaspending,
    provider_failure,
    raise_for_provider,
    reconcile,
    rollup_contracts,
    sam_budget,
    sam_opportunity_params,
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


def test_sam_opportunity_window_is_one_call_inside_the_daily_budget():
    today = date(2026, 10, 10)
    params = sam_opportunity_params(today, "sam-key")
    assert params["postedFrom"] == "10/11/2025"
    assert params["postedTo"] == "10/10/2026"
    assert (today - (today - timedelta(days=364))).days == 364
    assert params["title"] == "SpaceX"
    assert params["limit"] == 10 and params["offset"] == 0
    assert params["api_key"] == "sam-key"
    assert SAM_CALLS_PER_RUN == 3 and SAM_CALLS_PER_DAY == 10
    assert sam_budget(0) == 3


DOD_SUMMARY = """<?xml version="1.0"?>
<rss><channel><item>
<title>Contracts for Oct. 9, 2026</title>
<description>Today's Department of War contracts valued at $7.5 million or more are now live.</description>
<link>https://www.war.gov/News/Contracts/Article/example/</link>
</item></channel></rss>
"""

DOD_AWARD = """<?xml version="1.0"?>
<rss><channel><item>
<title>Contracts for Oct. 9, 2026</title>
<description>SpaceX, Hawthorne, California, was awarded a $12,000,000 contract (FA8801-26-C-0001).</description>
<link>https://www.war.gov/News/Contracts/Article/example/</link>
</item></channel></rss>
"""


def test_dod_rss_keeps_summary_coverage_and_does_not_fetch_articles():
    assert "RSS.ashx" in DOD_FEED_URL and DOD_FEED_URL.startswith("https://www.defense.gov/")
    empty, coverage = feed_coverage(DOD_SUMMARY, "2026-10-09T16:00:00+00:00", date(2026, 10, 9))
    assert empty == []
    assert coverage == {"feed_items": 1, "award_rows": 0, "article_urls_fetched": 0}
    awards, award_coverage = feed_coverage(DOD_AWARD, "2026-10-09T16:00:00+00:00", date(2026, 10, 9))
    assert award_coverage["feed_items"] == 1
    assert award_coverage["award_rows"] == 1
    assert award_coverage["article_urls_fetched"] == 0
    assert awards[0]["ticker"] == "SPCX"
    assert awards[0]["source"] == "DoD"
    with pytest.raises(ValueError, match="not an RSS feed"):
        feed_coverage("<html>blocked</html>", "2026-10-09T16:00:00+00:00", date(2026, 10, 9))


def test_usaspending_fields_stay_on_the_contract_allowlist():
    body = usaspending_body("Space Exploration Technologies", "2021-10-10", "2026-10-10")
    assert body["subawards"] is False
    assert set(body["fields"]) <= USASPENDING_CONTRACT_FIELDS
    assert provider_failure(422) == "request_rejected"
    with pytest.raises(ProviderStatus, match="USAspending HTTP 422") as caught:
        raise_for_provider(422, "USAspending")
    assert "://" not in str(caught.value)


def test_nasa_429_is_throttle_before_any_parse():
    assert NASA_FEED_URL == "https://www.nasa.gov/news-release/feed/"
    assert provider_failure(429) == "rate_limited"
    with pytest.raises(ProviderStatus, match="throttled, not a parser failure") as caught:
        raise_for_provider(429, "NASA")
    assert "://" not in str(caught.value)
    with pytest.raises(ProviderStatus, match="access restricted, no rows synthesized"):
        raise_for_provider(403, "DoD contracts feed")


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
