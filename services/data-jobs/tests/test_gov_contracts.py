import pytest

from gov_contracts import (
    JOB_ID,
    UEI_TO_TICKER,
    merge_awards,
    parse_sam,
    parse_usaspending,
    reconcile,
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
