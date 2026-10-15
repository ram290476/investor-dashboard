"""Fund N-PORT collector (#223): universe mapping, EDGAR fallback, N-PORT parsing, lake keys.

Fixtures are real SEC documents fetched 2026-10-14 and trimmed to a few holdings."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import fund_filings as ff
import http_client

FIXTURES = Path(__file__).parent / "fixtures" / "fund_filings"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
ARKK_XML = (FIXTURES / "nport_arkk_trimmed.xml").read_text()
ARKVX_XML = (FIXTURES / "nport_arkvx_trimmed.xml").read_text()
EFTS = json.loads((FIXTURES / "efts_arkvx.json").read_text())
FEED = (FIXTURES / "series_feed_arkk.atom").read_text()

MF = {
    "fields": ["cik", "seriesId", "classId", "symbol"],
    "data": [[1579982, "S000042977", "C000133121", "ARKK"], [1579982, "S000064752", "C000209702", "ARKF"]],
}
OPERATING = {"0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."}}


def _subs(name, forms):
    return {
        "name": name,
        "filings": {"recent": {
            "form": [f for f, _a, _d in forms], "accessionNumber": [a for _f, a, _d in forms],
            "filingDate": [d for _f, _a, d in forms], "reportDate": ["" for _ in forms],
        }},
    }


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._payload, self.text, self.headers = status, payload, text, {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Pace:
    def __init__(self):
        self.hosts = []

    def wait(self, host):
        self.hosts.append(host)


def _fake_sec(extra=None):
    routes = {
        ff.MF_URL: _Resp(payload=MF),
        ff.OPERATING_URL: _Resp(payload=OPERATING),
        ff.EFTS_URL.format(ticker="ARKVX"): _Resp(payload=EFTS),
        ff.SUBMISSIONS_URL.format(cik="0001496362"): _Resp(
            payload=_subs("ZACK ROBERT G.", [("4", "x-1", "2025-09-26")])
        ),
        ff.SUBMISSIONS_URL.format(cik="0002145987"): _Resp(payload=_subs("Someone", [("3", "x-2", "2025-01-01")])),
        ff.SUBMISSIONS_URL.format(cik="0001697748"): _Resp(payload=_subs("ARK Investment Mgmt", [("13F-HR", "x", "")])),
        ff.SUBMISSIONS_URL.format(cik="0001905088"): _Resp(payload=_subs(
            "ARK Venture Fund", [("N-23C3A", "0000940400-26-040000", "2026-10-01"),
                                 ("NPORT-P", "0000940400-26-036464", "2026-09-18")])),
        ff.SUBMISSIONS_URL.format(cik="0001579982"): _Resp(payload=_subs(
            "ARK ETF Trust", [("NPORT-P", "0000940400-26-037955", "2026-09-22")])),
        ff.SERIES_FEED_URL.format(series_id="S000042977"): _Resp(text=FEED),
        ff.SERIES_FEED_URL.format(series_id="S000064752"): _Resp(text=""),
        ff.ARCHIVE_URL.format(cik="1905088", acc="000094040026036464"): _Resp(text=ARKVX_XML),
        ff.ARCHIVE_URL.format(cik="1579982", acc="000094040026037955"): _Resp(text=ARKK_XML),
    }
    routes.update(extra or {})
    calls = []

    def fetch(url):
        calls.append(url)
        return routes.get(url, _Resp(status=404))

    return fetch, calls


def _run(tickers, fund_map=None, state=None, extra=None):
    fetch, calls = _fake_sec(extra)
    pace = _Pace()
    out = ff.run_collection(tickers, fetch=fetch, pace=pace, sleep=lambda s: None,
                            fund_map=fund_map, state=state or {}, now=NOW)
    return out, calls, pace


def test_mf_map_parses_fields_and_data():
    assert ff.mf_map_from_payload(MF)["ARKK"] == {"cik": "1579982", "series_id": "S000042977", "class_id": "C000133121"}


def test_efts_candidates_rank_by_hits():
    ranked = ff.efts_candidates(EFTS)
    assert ranked[0][0] == "1905088" and ranked[0][1] == 6
    assert ranked[0][2] == "ARK Venture Fund"


def test_fallback_skips_person_filers_and_needs_fund_forms():
    out, _calls, _ = _run(["ARKVX"])
    entry = out["fund_map"]["funds"]["ARKVX"]
    assert entry["kind"] == "fund" and entry["cik"] == "1905088"
    assert entry["method"] == "efts_fulltext" and entry["series_id"] is None and entry["confidence"] == 1.0


def test_fallback_without_fund_filer_is_unresolved():
    empty = {"hits": {"hits": [{"_source": {"ciks": ["0001496362"], "display_names": ["ZACK"]}}]}}
    out, _c, _p = _run(["ZZZZ"], extra={ff.EFTS_URL.format(ticker="ZZZZ"): _Resp(payload=empty)})
    assert out["fund_map"]["funds"]["ZZZZ"]["status"] == "unresolved"
    assert out["results"] == []


def test_operating_company_is_cached_not_fetched():
    out, calls, _ = _run(["TSLA"])
    assert out["fund_map"]["funds"]["TSLA"]["kind"] == "operating"
    assert not any("submissions" in url for url in calls)


def test_parse_nport_arkk_header_and_rows():
    parsed = ff.parse_nport(ARKK_XML)
    head = parsed["header"]
    assert head["series_id"] == "S000042977" and head["period_end"] == "2026-07-31"
    assert head["reg_cik"] == "1579982" and head["net_assets"] > 5e9
    top = parsed["holdings"][0]
    assert top["ticker"] == "NTLA" and top["cusip"] == "45826J105"
    assert top["isin"] == "US45826J1051" and top["units"] == "NS"
    values = [row["value_usd"] for row in parsed["holdings"]]
    assert values == sorted(values, reverse=True)


def test_parse_nport_private_holding_has_no_cusip_or_ticker():
    rows = ff.parse_nport(ARKVX_XML)["holdings"]
    private = [row for row in rows if row["other_id_desc"] == "INTERNAL"]
    assert private and private[0]["cusip"] is None and private[0]["ticker"] is None
    assert private[0]["fair_val_level"] == "3"
    assert ff.parse_nport(ARKVX_XML)["header"]["series_id"] is None


def test_series_feed_lists_only_nport():
    rows = ff.series_feed_filings(FEED)
    assert rows and rows[0]["accession"] == "0000940400-26-037955"
    assert all(row["form"] in ff.NPORT_FORMS for row in rows)
    assert ff.series_feed_filings("not xml") == []


def test_trust_series_mismatch_is_not_taken():
    # ARKF shares ARK ETF Trust's CIK; the only NPORT-P on file is ARKK's series, so ARKF must not get it.
    out, _c, _p = _run(["ARKF"])
    assert out["results"][0]["status"] == "failed" and out["results"][0]["reason"] == "series_not_found"


def test_run_collection_end_to_end_and_pace_used_for_every_sec_call():
    out, calls, pace = _run(["TSLA", "ARKK", "ARKVX"])
    by = {row["ticker"]: row for row in out["results"]}
    assert by["ARKK"]["filing"]["accession"] == "0000940400-26-037955"
    assert by["ARKVX"]["filing"]["period_end"] == "2026-07-31" and by["ARKVX"]["new"] is True
    assert len(pace.hosts) == len(calls)
    assert set(pace.hosts) <= {"www.sec.gov", "data.sec.gov", "efts.sec.gov"}


def test_cached_map_skips_resolution_and_seen_accession_is_not_new():
    first, _c, _p = _run(["ARKVX"])
    second, calls, _ = _run(["ARKVX"], fund_map=first["fund_map"], state=first["state"])
    assert ff.MF_URL not in calls and not any("efts" in url for url in calls)
    assert second["results"][0]["new"] is False


def test_publish_writes_keys_by_cik_and_fund_ticker():
    out, _c, _p = _run(["ARKVX"])
    written = {}
    ff.publish(out, write_text=lambda body, key: written.setdefault(key, body),
               write_json=lambda obj, key: written.setdefault(key, obj),
               write_parquet=lambda df, key: written.setdefault(key, df))
    period = "curated/fund_holdings/cik=1905088/fund=ARKVX/period=2026-07-31/"
    assert set(written) == {
        "raw/fund_filings/nport/cik=1905088/0000940400-26-036464.xml",
        period + "holdings.parquet", period + "_fund.json", ff.FUND_MAP_KEY, ff.STATE_KEY,
    }
    frame = written[period + "holdings.parquet"]
    assert frame.height == 3 and set(frame["fund_ticker"]) == {"ARKVX"} and set(frame["cik"]) == {"1905088"}


def test_stale_map_entry_is_refreshed():
    assert ff.map_entry_fresh({"resolved_at": "2026-10-01T00:00:00+00:00"}, NOW)
    assert not ff.map_entry_fresh({"resolved_at": "2026-08-01T00:00:00+00:00"}, NOW)
    assert not ff.map_entry_fresh(None, NOW)


def test_efts_host_is_allowed():
    assert "efts.sec.gov" in http_client.ALLOWED_HOSTS
