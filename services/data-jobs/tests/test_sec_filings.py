"""SEC filing log: CIK map, accession dedupe, fiscal labels, no_sec_filer, and SEC backoff."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import company_ir
import dashboard_build
import sec_filings as sec
import status_feed

FIXTURES = Path(__file__).parent / "fixtures" / "company_ir"
CHECKED = "2026-10-13T10:00:00+00:00"
TODAY = date(2026, 10, 13)


class _Pace:
    def __init__(self):
        self.hosts = []

    def wait(self, host):
        self.hosts.append(host)


class _Response:
    def __init__(self, status, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = {} if payload is None else payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def _submissions(
    *,
    fiscal_end="1231",
    accession="0000320193-26-000010",
    form="10-Q",
    filed="2026-08-01",
    report="2026-06-27",
):
    return {
        "cik": "320193",
        "entityName": "Apple Inc.",
        "fiscalYearEnd": fiscal_end,
        "filings": {
            "recent": {
                "accessionNumber": [accession, accession],
                "filingDate": [filed, filed],
                "acceptanceDateTime": ["2026-08-01T20:00:00.000Z", "2026-08-01T20:00:00.000Z"],
                "reportDate": [report, report],
                "form": [form, form],
                "items": ["2.02,9.01", "2.02,9.01"],
                "primaryDocument": ["a.htm", "a.htm"],
                "primaryDocDescription": ["Quarterly report", "Quarterly report"],
                "size": [1200, 1200],
                "isXBRL": [1, 1],
                "isInlineXBRL": [0, 0],
            }
        },
    }


def test_cik_map_pads_and_matches_class_share_tickers():
    mapped = sec.cik_map_from_company_tickers(
        {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
            "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
            "3": {"cik_str": None, "ticker": "SKIP", "title": ""},
        }
    )
    assert mapped["AAPL"] == {"cik": "0000320193", "title": "Apple Inc."}
    assert mapped["NVDA"]["cik"] == "0001045810"
    assert sec.lookup_cik(mapped, "brk.b")["cik"] == "0001067983"
    assert sec.lookup_cik(mapped, "ARKK") is None
    assert "SKIP" not in mapped


def test_cik_cache_is_refreshed_weekly():
    fresh = {"fetched_at": "2026-10-10T00:00:00+00:00", "map": {"AAPL": {"cik": "0000320193", "title": "Apple"}}}
    assert sec.cik_cache_is_fresh(fresh, datetime(2026, 10, 12, tzinfo=UTC))
    assert not sec.cik_cache_is_fresh(fresh, datetime(2026, 10, 18, tzinfo=UTC))
    empty = {"fetched_at": "2026-10-12T00:00:00+00:00", "map": {}}
    assert not sec.cik_cache_is_fresh(empty, datetime(2026, 10, 12, tzinfo=UTC))


def test_accession_dedupe_keeps_the_first_seen_stamp():
    existing = [
        {
            "ticker": "NVDA",
            "accession_number": "0001045810-24-000001",
            "first_seen": "2024-02-22T00:00:00+00:00",
            "form": "10-K",
            "filed_at": "2024-02-21",
            "title": "old",
            "exhibits": [{"type": "EX-99.1", "description": "press", "url": "https://www.sec.gov/e"}],
        }
    ]
    incoming = [
        {
            "ticker": "nvda",
            "accession_number": "0001045810-24-000001",
            "first_seen": CHECKED,
            "form": "10-K",
            "filed_at": "2024-02-21",
            "title": "amended title",
            "exhibits": [],
        },
        {
            "ticker": "NVDA",
            "accession_number": "0001045810-26-000010",
            "form": "8-K",
            "filed_at": "2026-08-27",
            "title": "8-K",
        },
    ]
    merged, new_count = sec.merge_filings(existing, incoming, CHECKED)
    by_accession = {row["accession_number"]: row for row in merged}
    assert new_count == 1
    assert len(merged) == 2
    kept = by_accession["0001045810-24-000001"]
    assert kept["first_seen"] == "2024-02-22T00:00:00+00:00"
    assert kept["title"] == "amended title"
    assert kept["last_checked"] == CHECKED
    assert kept["exhibits"][0]["type"] == "EX-99.1"
    assert by_accession["0001045810-26-000010"]["first_seen"] == CHECKED


def test_duplicate_accessions_in_one_submission_collapse():
    result = sec.collect_ticker(
        "AAPL",
        cik_entry={"cik": "0000320193", "title": "Apple Inc."},
        submissions=_submissions(),
        extra_blocks=[],
        existing=[],
        backfill=False,
        today=TODAY,
        checked_at=CHECKED,
    )
    assert result["status"] == "ok"
    assert result["new_filings"] == 1
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["accession_number"] == "0000320193-26-000010"
    assert row["xbrl"] is True
    assert row["items"] == ["2.02", "9.01"]
    assert row["primary_doc_url"].endswith("/000032019326000010/a.htm")
    assert row["entity_name"] == "Apple Inc."
    raw = sec.raw_index_document(row)
    assert raw["primary_doc_url"] == row["primary_doc_url"]
    assert "body" not in raw
    assert sec.raw_accession_key("AAPL", row["accession_number"]).startswith("raw/sec_filings/ticker=AAPL/accession=")


def test_fiscal_labels_follow_the_filer_calendar_for_nvda_and_mrvl():
    nvda_month = company_ir.fiscal_end_month_from_submissions({"fiscalYearEnd": "0131"})
    mrvl_month = company_ir.fiscal_end_month_from_submissions({"fiscalYearEnd": "0201"})
    assert nvda_month == 1
    assert mrvl_month == 2
    nvda_year_end = sec.fiscal_labels("2026-01-31", nvda_month)
    nvda_q3 = sec.fiscal_labels("2025-10-26", nvda_month)
    mrvl_year_end = sec.fiscal_labels("2026-02-01", mrvl_month)
    mrvl_q2 = sec.fiscal_labels("2025-08-02", mrvl_month)
    assert nvda_year_end == {"fiscal_year": 2026, "fiscal_period": "2026Q4"}
    assert nvda_q3["fiscal_period"] == "2026Q3"
    assert mrvl_year_end["fiscal_period"] == "2026Q4"
    assert mrvl_q2 == {"fiscal_year": 2026, "fiscal_period": "2026Q2"}
    nvda = sec.collect_ticker(
        "NVDA",
        cik_entry={"cik": "0001045810", "title": "NVIDIA CORP"},
        submissions=_submissions(
            fiscal_end="0131",
            accession="0001045810-26-000004",
            form="10-Q",
            filed="2025-11-19",
            report="2025-10-26",
        ),
        extra_blocks=[],
        existing=[],
        backfill=True,
        today=TODAY,
        checked_at=CHECKED,
    )
    assert nvda["rows"][0]["fiscal_period"] == "2026Q3"
    assert nvda["rows"][0]["fiscal_year"] == 2026


def test_missing_cik_is_no_sec_filer_and_a_fetch_failure_keeps_a_reason():
    missing = sec.collect_ticker(
        "ARKK",
        cik_entry=None,
        submissions=None,
        extra_blocks=[],
        existing=[],
        backfill=False,
        today=TODAY,
        checked_at=CHECKED,
    )
    assert missing["status"] == "no_sec_filer"
    assert missing["reason"] == "no_sec_cik"
    assert missing["rows"] == []
    failed = sec.collect_ticker(
        "NVDA",
        cik_entry={"cik": "0001045810", "title": "NVIDIA CORP"},
        submissions=None,
        extra_blocks=[],
        existing=[],
        backfill=True,
        today=TODAY,
        checked_at=CHECKED,
        fetch_error="http_403",
    )
    assert failed["status"] == "failed"
    assert failed["reason"] == "http_403"
    event = sec._log_event("ARKK", "no_sec_filer", reason="no_sec_cik", filings=0, new_filings=0)
    assert event["event"] == "sec_filing_ticker"
    assert event["ticker"] == "ARKK"
    assert event["status"] == "no_sec_filer"


def test_rate_limit_backs_off_on_429_and_stops_after_repeated_403():
    assert company_ir.SEC_MIN_INTERVAL_S >= 0.1
    pace = _Pace()
    sleeps = []
    calls = {"n": 0}

    def fetch(_url):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Response(429, headers={"Retry-After": "2"})
        return _Response(200, {"ok": True})

    response, reason = sec.fetch_sec("https://data.sec.gov/submissions/CIK0000320193.json", fetch, pace, sleeps.append)
    assert reason is None
    assert response.json()["ok"] is True
    assert sleeps == [2.0]
    assert pace.hosts == ["data.sec.gov", "data.sec.gov"]

    blocked = []
    response, reason = sec.fetch_sec(
        "https://www.sec.gov/files/company_tickers.json",
        lambda _url: _Response(403),
        _Pace(),
        blocked.append,
        max_attempts=3,
    )
    assert response is None
    assert reason == "http_403"
    assert blocked == [1.0, 2.0]
    assert sec.sec_status_action(429, 0) == "backoff"
    assert sec.sec_status_action(403, 3) == "fail"


def test_targets_follow_the_watchlist_and_backfill_a_new_ticker():
    planned = sec.target_tickers(["NVDA", "MRVL", "CRM"], None)
    assert planned["tickers"] == ["NVDA", "MRVL", "CRM"]
    assert planned["reason"] == "schedule"
    added = sec.target_tickers(["NVDA"], {"detail-type": "TickerAdded", "detail": {"ticker": "crm"}})
    assert added["tickers"] == ["CRM"]
    assert added["backfill"] == {"CRM"}
    many = [f"T{index:02d}" for index in range(30)]
    capped = sec.target_tickers(many, {"source": "schedule"})
    assert len(capped["tickers"]) == 25
    assert capped["dropped"] == many[25:]
    assert sec.scheduled_backfill_tickers(["A", "B", "C"], set(), set()) == {"A", "B"}


def test_exhibits_use_the_filing_index_document_type():
    payload = json.loads((FIXTURES / "filing_index.json").read_text())
    exhibits = sec.exhibits_for_filing("0001318605", "0001628280-26-064366", None, payload)
    assert exhibits[0]["type"] == "EX-99.1"
    assert exhibits[0]["description"] == "Q3 2026 Production, Deliveries & Deployments"
    assert exhibits[0]["url"].endswith("/exhibit991.htm")


def test_serving_copy_on_the_dashboard_stays_a_short_list_for_any_ticker():
    rows = []
    for offset in range(sec.SERVING_RECENT + 10):
        filed = date(2026, 8, 1).fromordinal(date(2026, 8, 1).toordinal() + offset).isoformat()
        rows.append(
            {
                "accession_number": f"0001326801-26-{offset:06d}",
                "form": "8-K",
                "filed_at": filed,
                "title": f"Filing {offset}",
                "primary_doc_url": "https://www.sec.gov/Archives/example",
                "filing_class": "other",
                "exhibits": [{"type": "EX-99.1", "description": "release", "url": "https://www.sec.gov/ex"}],
            }
        )
    document = sec.serving_document(
        "CRM",
        status="ok",
        reason=None,
        cik="0001326801",
        entity_name="Salesforce, Inc.",
        filings=rows,
        as_of=CHECKED,
    )
    assert len(document["filings"]) == sec.SERVING_RECENT
    mapped, status = sec.dashboard_rows_from_serving(document)
    assert status == "ok"
    assert "exhibits" not in mapped[0]
    snapshot = dashboard_build.build_snapshot(
        tickers=["CRM"],
        prices={},
        trends={},
        fundamentals=[],
        status=None,
        generated_at="2026-10-06T18:00:00+00:00",
        filings={
            "CRM": [
                {
                    "accession_no": "0002",
                    "form": "4",
                    "filed_at": "2026-09-20",
                    "title": "Form 4",
                    "primary_doc_url": "https://www.sec.gov/c",
                    "filing_class": "buy",
                    "txn_code": "P",
                    "shares": 100,
                    "price": 10,
                }
            ]
        },
        served_filings={"CRM": {"rows": mapped, "status": status}},
    )
    filings = snapshot["tickers"]["CRM"]["filings"]
    assert len(filings) == 10
    assert snapshot["tickers"]["CRM"]["filings_status"] == "ok"
    assert "exhibits" not in json.dumps(filings)
    assert snapshot["tickers"]["CRM"]["insider_30d"] == {"net_shares": 100.0, "net_value": 1000.0}
    empty = sec.serving_document(
        "ARKK",
        status="no_sec_filer",
        reason="no_sec_cik",
        cik=None,
        entity_name=None,
        filings=[],
        as_of=CHECKED,
    )
    assert empty["filings"] == []
    assert empty["status"] == "no_sec_filer"


def test_status_feed_keeps_the_sec_run_summary():
    now = datetime(2026, 10, 13, 14, tzinfo=UTC)
    feed = status_feed.apply_event(
        None,
        {
            "job": "SEC",
            "outcome": "partial",
            "failed_sources": 1,
            "summary": {"tickers_processed": 4, "new_filings": 3, "failures": 1, "no_sec_filer": 1},
        },
        now,
    )
    row = next(item for item in feed["jobs"] if item["job"] == "SEC")
    assert row["name"] == "SEC filings"
    assert row["status"] == "partial"
    assert row["summary"] == {"tickers_processed": 4, "new_filings": 3, "failures": 1, "no_sec_filer": 1}
    assert row["next_run"]


def test_collector_source_is_not_limited_to_two_tickers():
    source = Path(sec.__file__).read_text()
    assert "TSLA" not in source
    assert "SPCX" not in source


_TEN_K = "".join(
    [
        "<html><body><p>Table of Contents</p><p>Item 1. Business</p><p>Item 1A. Risk Factors</p>",
        "<p>PART I</p><p>ITEM 1. BUSINESS</p><p>Overview</p>",
        "<p>This Annual Report on Form 10-K contains forward-looking statements under the securities laws.</p>",
        "<p>Acme Charging, Inc. operates one of the largest public fast-charging networks for electric",
        " vehicles in the United States. We own and operate stations in more than 40 states.</p>",
        "<p>Item 1A. Risk Factors</p>",
        "<p>Investing is risky and you could lose everything you put in, so read this section carefully.</p>",
        "</body></html>",
    ]
)


def test_description_parses_item_1_business_and_skips_table_of_contents():
    text = sec.description_from_annual_report(_TEN_K, "10-K")
    assert text.startswith("Acme Charging, Inc. operates one of the largest")
    assert "forward-looking" not in text and "Risk" not in text


def test_description_is_empty_without_business_section():
    assert sec.description_from_annual_report("<p>Nothing here.</p>", "10-K") == ""


def test_latest_annual_filing_and_cached_description_reuse():
    rows = [
        {"form": "10-K", "filed_at": "2025-02-20", "accession_number": "a1", "primary_doc_url": "https://www.sec.gov/a1.htm"},
        {"form": "10-K", "filed_at": "2026-02-20", "accession_number": "a2", "primary_doc_url": "https://www.sec.gov/a2.htm"},
        {"form": "8-K", "filed_at": "2026-05-01", "accession_number": "a3", "primary_doc_url": "https://www.sec.gov/a3.htm"},
    ]
    annual = sec.latest_annual_filing(rows)
    assert annual["accession_number"] == "a2"
    calls = []
    fetched = sec.resolve_description(annual, None, lambda url: calls.append(url) or _TEN_K)
    assert calls == ["https://www.sec.gov/a2.htm"] and fetched["accession_number"] == "a2"
    again = sec.resolve_description(annual, fetched, lambda url: calls.append(url) or "")
    assert again is fetched and len(calls) == 1


def test_serving_document_carries_description_only_when_present():
    with_text = sec.serving_document(
        "EVGO", status="ok", reason=None, cik="1", entity_name="EVgo Inc.", filings=[], as_of=CHECKED,
        description={"description": "Fast charging.", "form": "10-K", "filed_at": "2026-02-20",
                     "accession_number": "a2", "source_url": "https://www.sec.gov/a2.htm"},
    )
    assert with_text["description"] == "Fast charging." and with_text["description_source"]["form"] == "10-K"
    empty = sec.serving_document("SPY", status="no_sec_filer", reason=None, cik=None, entity_name=None,
                                 filings=[], as_of=CHECKED)
    assert empty["description"] is None and empty["description_source"] is None
