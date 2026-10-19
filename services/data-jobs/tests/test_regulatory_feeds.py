from datetime import date

import pytest

from observability import emit_job_finished, job_handler
from regulatory_feeds import (
    JOB_ID,
    PLACEHOLDER_USER_AGENT,
    assert_prod_user_agent,
    attach_form4,
    ciks_for_tickers,
    classify_8k,
    classify_form4,
    combine_filings,
    dedupe_filings,
    enrich_form4_rows,
    form4_xml_urls,
    insider_flows,
    parse_form4,
    parse_launches,
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


def test_launches_keep_provider_and_collection_timestamp_for_provenance():
    ingested_at = "2026-10-08T18:00:00+00:00"
    events = parse_launches(
        {"results": [{"name": "Starship test", "net": "2026-10-15T12:00:00Z", "url": "https://provider.test/1"}]},
        ingested_at,
    )

    assert events[0]["source"] == "launch-library"
    assert events[0]["source_url"] == "https://provider.test/1"
    assert events[0]["event_ts"] == "2026-10-15T12:00:00Z"
    assert events[0]["ingested_at"] == ingested_at


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
    tickers = {
        "0": {"cik_str": 1045810, "ticker": "NVDA"},
        "1": {"cik_str": 1067983, "ticker": "BRK-B"},
        "2": {"ticker": "SKIP"},
    }
    mapped = ciks_for_tickers(tickers, ["NVDA", "brk.b", "ARKK", "NVDA"])
    assert mapped == {"NVDA": "0001045810", "BRK.B": "0001067983"}
    assert "TSLA" not in mapped and "SPCX" not in mapped


def test_form4_share_counts_survive_a_later_submissions_copy():
    parsed = parse_submissions(SUBMISSIONS, "NVDA", date(2026, 10, 6), "2026-10-06T12:00:00+00:00")
    form4 = next(row for row in parsed if row["form"] == "4")
    stored = attach_form4(form4, FORM4)
    assert stored["shares"] == 40
    assert stored["filing_class"] == "sell"
    refreshed = parse_submissions(SUBMISSIONS, "NVDA", date(2026, 10, 6), "2026-10-06T18:00:00+00:00")
    merged = combine_filings([stored], refreshed)
    kept = next(row for row in merged if row["form"] == "4")
    assert kept["shares"] == 40
    assert kept["price"] == 250
    assert kept["txn_code"] == "S"
    assert kept["insider_name"] == "Jane Doe"
    assert insider_flows(merged, date(2026, 10, 6)) == {"net_shares": -40.0, "net_value": -10000.0}
    urls = form4_xml_urls(
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000004/xslF345X05/wf-form4.xml"
    )
    assert urls[1].endswith("/000104581026000004/wf-form4.xml")
    fetched = []

    def fetch_xml(url):
        fetched.append(url)
        return FORM4 if url.endswith(".xml") else "<html></html>"

    filled = enrich_form4_rows(refreshed, set(), fetch_xml)
    again = enrich_form4_rows(filled, {form4["accession_no"]}, fetch_xml)
    assert filing_shares(filled) == 40
    assert fetched
    assert filing_shares(again) == 40
    assert len(fetched) == 1


def filing_shares(rows):
    return next(row["shares"] for row in rows if row["form"] == "4")


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


@pytest.mark.parametrize("name", ["Liu Teyin M", "Stat Lauren Elaina Friedman"])
def test_insider_name_after_many_null_rows_builds_a_frame(name):
    """#182: >100 rows with insider_name=None, then a Form 4 name, raised polars ComputeError."""
    import polars as pl

    import regulatory_feeds

    base = {"ticker": "TSLA", "filed_at": "2026-10-13", "accession_no": "", "insider_name": None,
            "insider_role": None, "shares": None, "price": None, "txn_code": None, "filing_class": None}
    rows = [{**base, "accession_no": f"a{i}"} for i in range(150)]
    rows.append({**base, "accession_no": "form4", "insider_name": name, "insider_role": "Director",
                 "shares": 1000.0, "price": 250.5, "txn_code": "S", "filing_class": "sell"})
    with pytest.raises(Exception, match="could not append value"):
        pl.DataFrame(rows)  # the old behavior, so this test proves the regression
    frame = regulatory_feeds.rows_frame(rows)
    assert frame.schema["insider_name"] == pl.Utf8
    assert frame.filter(pl.col("accession_no") == "form4")["insider_name"].to_list() == [name]


def test_rows_frame_all_null_name_column_is_still_utf8():
    import polars as pl

    import regulatory_feeds

    frame = regulatory_feeds.rows_frame([{"ticker": "TSLA", "insider_name": None}])
    assert frame.schema["insider_name"] == pl.Utf8
