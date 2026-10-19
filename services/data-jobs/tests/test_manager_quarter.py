"""13F summary data (#223): previous quarter, issuer sectors, QoQ diff in serving/managers/<cik>.json."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import issuer_sectors as isec

import fund_serving as fs
import manager_holdings as mh

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def row(cusip, name, shares, value, option=None):
    return {"cusip": cusip, "name": name, "shares": shares, "value_usd": value, "put_call": option,
            "is_option": bool(option), "title": "COM", "period_end": "2026-06-30"}


CURRENT = [row("A", "ALPHA INC", 100, 1000), row("A", "ALPHA INC", 50, 500), row("B", "BETA CORP", 10, 300),
           row("C", "GAMMA LTD", 40, 400), row("D", "DELTA", 5, 50, option="Put")]
PREVIOUS = [row("A", "ALPHA INC", 100, 900), row("B", "BETA CORP", 20, 500), row("X", "EXITED CO", 7, 700)]


def test_quarter_changes_classifies_buys_sells_and_size_changes():
    ch = fs.quarter_changes(CURRENT, PREVIOUS, {"A": "ALP"})
    assert [r["cusip"] for r in ch["new_buys"]] == ["C"]
    assert [r["cusip"] for r in ch["full_sells"]] == ["X"] and ch["full_sells"][0]["value_prev_usd"] == 700
    assert ch["increases"][0]["cusip"] == "A" and ch["increases"][0]["shares_change_pct"] == 50
    assert ch["increases"][0]["ticker"] == "ALP"
    assert ch["decreases"][0]["cusip"] == "B" and ch["decreases"][0]["trade_value_usd"] == -300
    assert ch["counts"] == {"new_buys": 1, "full_sells": 1, "increased": 1, "decreased": 1, "unchanged": 0}
    assert fs.quarter_changes(CURRENT, None, {}) is None  # no previous quarter in the lake


def test_manager_document_has_totals_top_and_sectors_with_other_fallback():
    sectors = {"A": {"sector": "Technology", "industry": "Software", "ticker": "ALP"},
               "B": {"sector": "Technology", "industry": "Semis"}}
    doc = fs.build_manager_document({"manager_cik": "1", "period_end": "2026-06-30"}, CURRENT, {}, "g",
                                    previous_rows=PREVIOUS, previous_header={"period_end": "2026-03-31"},
                                    sectors=sectors)
    assert doc["positions"] == 4 and doc["option_rows"] == 1
    assert doc["value_total_usd"] == 2200
    assert doc["top"][0]["name"] == "ALPHA INC" and doc["top"][0]["ticker"] == "ALP"
    assert [s["sector"] for s in doc["sectors"]] == ["Technology", "Other"]
    assert doc["sectors"][1]["value_usd"] == 400
    assert doc["previous_period_end"] == "2026-03-31" and doc["changes"]["counts"]["new_buys"] == 1


def test_sector_from_submissions_strips_office_number():
    s = isec.sector_from_submissions({"ownerOrg": "06 Technology", "sicDescription": "Services-Prepackaged Software",
                                      "sic": "7372", "name": "X"})
    assert s["sector"] == "Technology" and s["industry"] == "Services-Prepackaged Software"
    assert isec.sector_from_submissions({})["sector"] == isec.OTHER


def test_resolve_issuers_by_cusip_ticker_then_name_and_caches():
    operating = {"TSLA": {"cik": 1318605, "title": "Tesla, Inc."}, "ROKU": {"cik": 1428439, "title": "Roku, Inc."},
                 "DUP1": {"cik": 1, "title": "Same Co"}, "DUP2": {"cik": 2, "title": "Same Co"}}
    urls = []

    def get_json(url):
        urls.append(url)
        return {"ownerOrg": "05 Energy & Transportation", "sicDescription": "Motor Vehicles"}

    rows = [{"cusip": "88160R101", "name": "TESLA INC"}, {"cusip": "77543R102", "name": "ROKU INC"},
            {"cusip": "Z", "name": "SAME CO"}]
    out = isec.resolve_issuers(rows, cusip_tickers={"88160R101": "TSLA"}, operating_map=operating, cache={},
                               get_json=get_json, submissions_url="https://data.sec.gov/{cik}.json", now=NOW)
    assert out["88160R101"]["method"] == "nport_cusip_ticker"
    assert out["88160R101"]["sector"] == "Energy & Transportation"
    assert out["77543R102"]["method"] == "issuer_name" and out["77543R102"]["ticker"] == "ROKU"
    assert out["Z"]["sector"] == "Other" and out["Z"]["method"] == "unmatched"  # clashing name is not guessed
    n = len(urls)
    isec.resolve_issuers(rows, cusip_tickers={"88160R101": "TSLA"}, operating_map=operating, cache=out,
                         get_json=get_json, submissions_url="https://data.sec.gov/{cik}.json",
                         now=NOW + timedelta(days=1))
    assert len(urls) == n  # cached


def test_latest_13f_before_skips_current_quarter():
    subs = {"filings": {"recent": {"form": ["13F-HR", "13F-HR"], "accessionNumber": ["0001-26-2", "0001-26-1"],
                                   "filingDate": ["2026-08-14", "2026-05-15"],
                                   "reportDate": ["2026-06-30", "2026-03-31"]}}}
    seen = []

    def get_text(url):
        seen.append(url)
        return None

    mh.latest_13f("1697748", subs, get_json=lambda u: None, get_text=get_text, before="2026-06-30")
    assert seen and all("000126" + "1" in u.replace("-", "") for u in seen)


def test_fund_document_embeds_the_adviser_13f_summary():
    rows13 = [{**r, "period_end": "2026-06-30"} for r in CURRENT]
    rows13 += [{**r, "period_end": "2026-03-31"} for r in PREVIOUS]
    store = {
        fs.FUND_MAP_KEY: {"funds": {"ARKK": {"kind": "fund", "status": "resolved", "cik": "9", "name": "Fund",
                                             "managers": [{"cik": "1", "name": "Mgr"}]}}},
        "curated/manager_holdings/cik=1/period=2026-06-30/_manager.json": {"name": "Mgr", "period_end": "2026-06-30"},
        fs.ISSUER_SECTORS_KEY: {"A": {"sector": "Technology"}},
    }
    built = fs.build_fund_documents(
        ["ARKK"], read_json=store.get, now=NOW, generated_at="g",
        read_rows=lambda p: rows13 if p.startswith("curated/manager_holdings/cik=1/") else [])
    block = built["funds"]["ARKK"]["managers"][0]["summary_13f"]
    assert block["previous_period_end"] == "2026-03-31" and block["changes"]["counts"]["full_sells"] == 1
    assert block["sectors"][0]["sector"] == "Technology" and "holdings" not in block
    assert built["managers"]["1"]["holdings"]
