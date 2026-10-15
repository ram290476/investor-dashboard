"""13F manager holdings via the N-CEN adviser (#223). Fixtures are real SEC documents, trimmed."""

from __future__ import annotations

from pathlib import Path

import pytest

import fund_filings as ff
import manager_holdings as mh

FIXTURES = Path(__file__).parent / "fixtures" / "fund_filings"
NCEN = (FIXTURES / "ncen_ark_etf_trust_trimmed.xml").read_text()
COVER = (FIXTURES / "13f_ark_primary_doc.xml").read_text()
TABLE = (FIXTURES / "13f_ark_infotable_trimmed.xml").read_text()
SUBS_URL = ff.SUBMISSIONS_URL
OPTION_ROW = """<infoTable><nameOfIssuer>ARK ETF TR</nameOfIssuer><titleOfClass>CALL</titleOfClass>
<cusip>00214Q104</cusip><value>5000000</value><shrsOrPrnAmt><sshPrnamt>100000</sshPrnamt>
<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><putCall>Call</putCall>
<investmentDiscretion>SOLE</investmentDiscretion></infoTable>"""


def _subs(name, forms):
    return {"name": name, "filings": {"recent": {
        "form": [f[0] for f in forms], "accessionNumber": [f[1] for f in forms],
        "filingDate": [f[2] for f in forms], "reportDate": [f[3] if len(f) > 3 else "" for f in forms]}}}


def _efts(entries):
    return {"hits": {"hits": [{"_source": {"ciks": [c], "display_names": [f"{n}  (CIK {c})"]}} for c, n in entries]}}


def test_advisers_for_series_excludes_sub_advisers():
    assert [a["name"] for a in mh.advisers_from_ncen(NCEN, "S000042977")] == ["ARK Investment Management LLC"]
    buffer = mh.advisers_from_ncen(NCEN, "S000095346")
    assert [a["name"] for a in buffer] == ["ARK Investment Management LLC"]  # Milliman is a sub-adviser
    assert buffer[0]["crd"] == "000169525"
    assert mh.advisers_from_ncen(NCEN, "S999999999") == []


def test_resolve_manager_needs_name_match_and_13f_filings():
    adviser = {"name": "ARK Investment Management LLC", "crd": "000169525"}
    payload = _efts([("0001579984", "ARK Investment Management LLC"), ("0001697748", "ARK INVESTMENT MANAGEMENT LLC"),
                     ("0001697748", "ARK Investment Management LLC"), ("0002052510", "Whipplewood Advisors, LLC")])
    docs = {
        mh.EFTS_13F_URL.format(name="ARK%20Investment%20Management%20LLC"): payload,
        SUBS_URL.format(cik="0001697748"): _subs("ARK Investment Management LLC", [("13F-HR", "a", "2026-08-14")]),
        SUBS_URL.format(cik="0001579984"): _subs("ARK Investment Management LLC", []),
    }
    found = mh.resolve_manager(adviser, docs.get, SUBS_URL)
    assert found["cik"] == "1697748" and found["method"] == "n-cen_adviser"


def test_resolve_manager_search_outage_is_retryable():
    with pytest.raises(mh.LookupUnavailable):
        mh.resolve_manager({"name": "X"}, lambda url: {"errorType": "ConnectionError"}, SUBS_URL)


def test_parse_13f_cover_and_table():
    cover = mh.parse_13f_cover(COVER)
    assert cover["period_end"] == "2026-06-30" and cover["is_amendment"] is False and cover["entry_total"] == 191
    rows = mh.parse_13f_table(TABLE)
    assert len(rows) == 3 and rows[0]["cusip"] == "008073108" and rows[0]["value_usd"] == 100280355
    assert rows[0]["shares"] == 607502 and rows[0]["is_option"] is False


def test_option_rows_are_kept_separate_with_no_pct():
    table = TABLE.replace("</informationTable>", OPTION_ROW + "</informationTable>")
    rows = mh.with_portfolio_pct(mh.parse_13f_table(table))
    assert rows[-1]["is_option"] and rows[-1]["pct_portfolio"] is None
    assert abs(sum(r["pct_portfolio"] for r in rows if not r["is_option"]) - 100) < 1e-9


def test_latest_13f_restatement_and_new_holdings_amendments():
    def cover(amend, kind=None):
        text = COVER.replace("<isAmendment>false</isAmendment>", f"<isAmendment>{amend}</isAmendment>")
        if kind:
            text = text.replace("</isAmendment>", f"</isAmendment><amendmentInfo><amendmentType>{kind}"
                                "</amendmentType></amendmentInfo>")
        return text

    index = {"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "infotable.xml"}]}}
    subs = _subs("ARK", [("13F-HR/A", "0000000000-26-000003", "2026-09-01", "2026-06-30"),
                         ("13F-HR", "0000000000-26-000002", "2026-08-14", "2026-06-30"),
                         ("13F-HR", "0000000000-26-000001", "2026-05-14", "2026-03-31")])

    def run(kind):
        texts = {}
        for acc, cov in (("000000000026000003", cover("true", kind)), ("000000000026000002", cover("false"))):
            texts[mh.FILING_DOC_URL.format(cik=1, acc=acc, name="primary_doc.xml")] = cov
            texts[mh.FILING_DOC_URL.format(cik=1, acc=acc, name="infotable.xml")] = TABLE
        jsons = {mh.FILING_INDEX_URL.format(cik=1, acc=a): index for a in ("000000000026000003", "000000000026000002")}
        return mh.latest_13f("1", subs, jsons.get, texts.get)

    restated = run("RESTATEMENT")
    assert restated["accession"] == "0000000000-26-000003" and len(restated["holdings"]) == 3
    added = run("NEW HOLDINGS")
    assert added["accession"] == "0000000000-26-000002" and added["amendments"] == ["0000000000-26-000003"]
    assert len(added["holdings"]) == 6 and added["period_end"] == "2026-06-30"


def test_info_table_name_from_index():
    index = {"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "50240.xml"}]}}
    assert mh.info_table_name(index) == "50240.xml"
    assert mh.info_table_name(None) is None


def test_norm_name():
    assert mh.norm_name("ARK INVESTMENT MANAGEMENT LLC") == mh.norm_name("ARK Investment Management, L.L.C.")
