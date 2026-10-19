"""serving/funds/<T>.json merge and the dashboard-build fund step (#223)."""

from __future__ import annotations

from datetime import UTC, datetime

import dashboard_build
import fund_serving as fs

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
GEN = "2026-10-15T12:00:00+00:00"
FUND_MAP = {"funds": {
    "ARKK": {"ticker": "ARKK", "kind": "fund", "status": "resolved", "cik": "1579982", "series_id": "S000042977",
             "method": "company_tickers_mf", "managers": [{"cik": "1697748", "name": "ARK Investment Management LLC"}]},
    "XOVR": {"ticker": "XOVR", "kind": "fund", "status": "resolved", "cik": "1495922", "series_id": "S000059420",
             "method": "company_tickers_mf", "managers": []},
    "AKF": {"ticker": "AKF", "kind": "unknown", "status": "unresolved"},
    "TSLA": {"ticker": "TSLA", "kind": "operating", "status": "resolved", "cik": "1318605"},
}}


def _h(period, name, cusip, pct, value, **extra):
    return {"period_end": period, "name": name, "title": extra.pop("title", None), "cusip": cusip,
            "ticker": extra.pop("ticker", None), "isin": None, "other_id": extra.pop("other_id", None),
            "balance": 10.0, "units": "NS", "value_usd": value, "pct_nav": pct, "payoff": "Long",
            "asset_cat": "EC", "fair_val_level": "1", "restricted": False, **extra}


ARKK_ROWS = [
    _h("2026-07-31", "TESLA INC", "88160R101", 9.7, 541e6, ticker="TSLA"),
    _h("2026-07-31", "N/A", None, 2.0, 100e6, title="SpaceX SPV Class A", other_id="PPSPACEX"),
    _h("2026-07-31", "N/A", None, 0.5, 20e6, other_id="PPX"),
    _h("2026-04-30", "TESLA INC", "88160R101", 8.0, 400e6, ticker="TSLA"),
]
MANAGER_ROWS = [
    {"period_end": "2026-06-30", "name": "Tesla Inc", "title": "Common Stock", "cusip": "88160R101",
     "value_usd": 1.16e9, "shares": 1.0, "share_type": "SH", "put_call": None, "is_option": False,
     "pct_portfolio": 90.0},
    {"period_end": "2026-06-30", "name": "N/A", "title": "Common Stock", "cusip": "000000001",
     "value_usd": 1.0e8, "shares": 1.0, "share_type": "SH", "put_call": None, "is_option": False,
     "pct_portfolio": 10.0},
    {"period_end": "2026-06-30", "name": "ARK ETF TR", "title": "CALL", "cusip": "00214Q104", "value_usd": 5e6,
     "shares": 1.0, "share_type": "SH", "put_call": "Call", "is_option": True, "pct_portfolio": None},
]
JSONS = {
    fs.FUND_MAP_KEY: FUND_MAP,
    "curated/fund_holdings/cik=1579982/fund=ARKK/period=2026-07-31/_fund.json": {
        "series_id": "S000042977", "series_name": "ARK Innovation ETF", "period_end": "2026-07-31",
        "net_assets": 5.56e9, "accession": "0000940400-26-037955", "filed_at": "2026-09-22",
        "submission_type": "NPORT-P"},
    "curated/fund_profile/ARKK.json": {"source": "yahoo", "fetched_at": "2026-10-13T08:00:00+00:00",
                                       "expense_ratio": 0.0075, "total_assets": 7.92e9, "category": "Mid-Cap Growth",
                                       "top_holdings": []},
    "curated/fund_profile/XOVR.json": {"source": "yahoo", "fetched_at": "2026-09-01T08:00:00+00:00",
                                       "total_assets": 1.5e9, "top_holdings": [
                                           {"symbol": "NVDA", "name": "Nvidia Corp", "pct": 0.095},
                                           {"symbol": None, "name": "N/A", "pct": 0.01}]},
    "curated/manager_holdings/cik=1697748/period=2026-06-30/_manager.json": {
        "name": "ARK Investment Management LLC", "period_end": "2026-06-30", "filed_at": "2026-08-14",
        "accession": "0001104659-26-096910", "amendments": [], "form": "13F-HR"},
}
ROWS = {"curated/fund_holdings/cik=1579982/fund=ARKK/": ARKK_ROWS,
        "curated/manager_holdings/cik=1697748/": MANAGER_ROWS}


def _build(tickers=("TSLA", "ARKK", "XOVR", "AKF")):
    return fs.build_fund_documents(list(tickers), read_json=JSONS.get, read_rows=lambda p: ROWS.get(p, []),
                                   now=NOW, generated_at=GEN)


def test_display_name_falls_back_to_issuer_then_title_then_unnamed():
    assert fs.display_name({"name": "N/A", "issuer": "Space Exploration Technologies"}) == \
        "Space Exploration Technologies"
    assert fs.display_name({"name": " n/a ", "title": "SpaceX SPV Class A"}) == "SpaceX SPV Class A"
    assert fs.display_name({"name": None, "title": "N/A"}) == fs.UNNAMED
    assert fs.display_name({"name": "TESLA INC"}) == "TESLA INC"


def test_fund_document_contract_from_nport():
    doc = _build()["funds"]["ARKK"]
    assert doc["schema_version"] == 1 and doc["status"] == "ok" and doc["holdings_source"] == "sec_nport"
    assert doc["holdings_as_of"] == "2026-07-31" and doc["filed_at"] == "2026-09-22" and doc["form"] == "NPORT-P"
    assert doc["filing_url"] == "https://www.sec.gov/Archives/edgar/data/1579982/000094040026037955/"
    assert doc["holdings_count"] == 3 and [h["pct_nav"] for h in doc["holdings"]] == [9.7, 2.0, 0.5]
    assert doc["holdings"][0]["prev_pct_nav"] == 8.0
    names = [h["name"] for h in doc["holdings"]]
    assert names == ["TESLA INC", "SpaceX SPV Class A", fs.UNNAMED]
    assert doc["holdings"][1]["name_filed"] == "N/A" and doc["holdings"][1]["is_private"] is True
    # Recent Yahoo profile wins for net assets and is labelled.
    assert doc["net_assets_usd"] == 7.92e9 and doc["net_assets_source"] == "yahoo"
    assert doc["profile"]["expense_ratio"] == 0.0075


def test_manager_block_and_document():
    built = _build()
    block = built["funds"]["ARKK"]["managers"][0]
    assert block["cik"] == "1697748" and block["positions"] == 2 and block["option_rows"] == 1
    assert block["overlap_positions"] == 1 and block["source"] == "sec_13f"
    manager = built["managers"]["1697748"]
    assert manager["holdings"][0]["ticker"] == "TSLA"  # CUSIP filled from the fund's N-PORT row
    assert manager["holdings"][1]["name"] == "Common Stock"  # 'N/A' issuer falls back to the title
    assert manager["holdings"][-1]["is_option"] is True and manager["positions"] == 2


def test_yahoo_top_holdings_fallback_when_no_nport():
    doc = _build()["funds"]["XOVR"]
    assert doc["holdings_source"] == "yahoo_top_holdings" and doc["holdings_as_of"] == "2026-09-01"
    assert doc["holdings"][0]["pct_nav"] == 9.5 and doc["holdings"][1]["name"] == fs.UNNAMED
    assert doc["holdings_note"] and doc["accession"] is None and doc["filing_url"] is None
    assert doc["net_assets_source"] is None  # a 6-week-old Yahoo profile is not used for net assets


def test_only_funds_get_documents():
    built = _build()
    assert set(built["funds"]) == {"ARKK", "XOVR"} and built["kinds"] == {"ARKK": "fund", "XOVR": "fund"}
    assert _build(("TSLA",))["funds"] == {}


def test_empty_state_for_fund_with_no_data():
    fund_map = {"funds": {"NEWF": {"kind": "fund", "status": "resolved", "cik": "1", "managers": []}}}
    built = fs.build_fund_documents(["NEWF"], read_json={fs.FUND_MAP_KEY: fund_map}.get, read_rows=lambda p: [],
                                    now=NOW, generated_at=GEN)
    doc = built["funds"]["NEWF"]
    assert doc["status"] == "not_published" and doc["holdings"] == [] and doc["holdings_source"] is None


def test_dashboard_build_publishes_and_marks_instrument_kind():
    written = {}
    fields = dashboard_build.publish_fund_documents(
        ["TSLA", "ARKK", "XOVR"], read_json=JSONS.get, read_rows=lambda p: ROWS.get(p, []),
        write=lambda doc, key: written.setdefault(key, doc), now=NOW, generated_at=GEN)
    assert set(written) == {"serving/funds/ARKK.json", "serving/funds/XOVR.json", "serving/managers/1697748.json"}
    snapshot = {"tickers": {"TSLA": {}, "ARKK": {}, "XOVR": {}}}
    dashboard_build.apply_fund_fields(snapshot, fields)
    assert snapshot["tickers"]["ARKK"]["instrument_kind"] == "fund"
    assert snapshot["tickers"]["ARKK"]["fund"]["holdings_count"] == 3
    assert "instrument_kind" not in snapshot["tickers"]["TSLA"]
