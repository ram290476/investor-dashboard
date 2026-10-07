from datetime import date

import pytest

from observability import emit_job_finished, job_handler
from regulatory_feeds import (
    JOB_ID,
    PLACEHOLDER_USER_AGENT,
    assert_prod_user_agent,
    classify_8k,
    classify_form4,
    dedupe_filings,
    insider_flows,
    parse_form4,
    parse_submissions,
    resolve_cik,
)

SUBMISSIONS = {
    "cik": "1318605",
    "filings": {
        "recent": {
            "accessionNumber": ["0001318605-26-000010", "0001318605-26-000011", "0001318605-24-000001"],
            "filingDate": ["2026-10-01", "2026-09-15", "2024-01-02"],
            "reportDate": ["2026-09-30", "2026-09-15", "2023-12-31"],
            "form": ["8-K", "4", "10-K"],
            "primaryDocument": ["tsla-8k.htm", "form4.xml", "tsla-10k.htm"],
            "primaryDocDescription": ["Results of operations", "Statement of changes", "Annual report"],
            "items": ["2.02,9.01", "", ""],
        }
    },
}

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Jane Doe</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><officerTitle>CFO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>40</value></transactionShares>
        <transactionPricePerShare><value>250</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_submissions_fixture_becomes_recent_filings():
    rows = parse_submissions(SUBMISSIONS, "TSLA", date(2026, 10, 6), "2026-10-06T12:00:00+00:00")
    by_form = {row["form"]: row for row in rows}
    assert set(by_form) == {"8-K", "4"}
    assert by_form["8-K"]["filing_class"] == "earnings"
    assert by_form["8-K"]["items"] == ["2.02", "9.01"]
    assert by_form["8-K"]["primary_doc_url"].endswith("/000131860526000010/tsla-8k.htm")
    assert "10-K" not in by_form  # 2024 is outside the 90-day backfill


def test_8k_and_form4_classification():
    assert classify_8k(["5.02"]) == "officer"
    assert classify_8k(["1.01", "8.01"]) == "agreement"
    assert classify_8k(["8.01"]) == "other"
    assert classify_8k(["2.02", "5.02"]) == "earnings"
    assert classify_form4("P") == "buy"
    assert classify_form4("S") == "sell"
    assert classify_form4("A") == "grant"
    parsed = parse_form4(FORM4)
    assert parsed["insider_name"] == "Jane Doe"
    assert parsed["insider_role"] == "CFO"
    assert parsed["filing_class"] == "sell"
    assert parsed["shares"] == 40
    assert parsed["price"] == 250
    flows = insider_flows(
        [{"form": "4", "filed_at": "2026-10-01", "txn_code": "S", "shares": 40, "price": 250}],
        date(2026, 10, 6),
    )
    assert flows == {"net_shares": -40.0, "net_value": -10000.0}


def test_accession_dedupe_keeps_the_later_copy():
    first = [{"accession_no": "A", "title": "first"}, {"accession_no": "B", "title": "kept"}]
    second = [{"accession_no": "A", "title": "revised"}]
    merged = {row["accession_no"]: row["title"] for row in dedupe_filings([*first, *second])}
    assert merged == {"A": "revised", "B": "kept"}


def test_prod_user_agent_rejects_the_placeholder():
    assert_prod_user_agent("investor-dashboard research@example.com")
    with pytest.raises(RuntimeError):
        assert_prod_user_agent(PLACEHOLDER_USER_AGENT)
    with pytest.raises(RuntimeError):
        assert_prod_user_agent("")
    assert resolve_cik({"0": {"cik_str": 123, "ticker": "SPCX"}}, "SPCX") == "0000000123"


def test_handler_emits_job_h3(monkeypatch):
    emitted = {}

    def capture(job_id, run_id, outcome, detail=None):
        emitted["job"] = job_id

    monkeypatch.setattr("observability.emit_job_finished", capture)

    @job_handler(JOB_ID)
    def run(event, context):
        return {"status": "success"}

    class Context:
        function_name = "regulatory-feeds"
        memory_limit_in_mb = 512
        invoked_function_arn = "arn:aws:lambda:us-east-1:1:function:regulatory-feeds"
        aws_request_id = "req"

    assert run({}, Context())["status"] == "success"
    assert emitted["job"] == "H3"
    assert emit_job_finished.__name__ == "emit_job_finished"
