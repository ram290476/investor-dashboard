"""#223 scope gaps: 8-period backfill (N-PORT and 13F) and the EDGAR company-search fallback."""

from __future__ import annotations

from test_fund_filings import ARKVX_XML, NOW, _Resp, _run, _subs

import fund_filings as ff

OLD = [("2026-04-30", "0000940400-26-020000", "2026-06-20"), ("2026-01-31", "0000940400-26-010000", "2026-03-25")]


def _older_xml(period):
    return ARKVX_XML.replace("2026-07-31", period)


def _history_routes(fail=None):
    forms = [("NPORT-P", "0000940400-26-036464", "2026-09-18")] + [("NPORT-P", acc, filed) for _p, acc, filed in OLD]
    routes = {ff.SUBMISSIONS_URL.format(cik="0001905088"): _Resp(payload=_subs("ARK Venture Fund", forms))}
    for period, acc, _filed in OLD:
        url = ff.ARCHIVE_URL.format(cik="1905088", acc=acc.replace("-", ""))
        routes[url] = _Resp(status=503) if fail == acc else _Resp(text=_older_xml(period))
    return routes


def test_first_run_backfills_older_nport_periods_once():
    out, _calls, _p = _run(["ARKVX"], extra=_history_routes())
    result = out["results"][0]
    assert [h["period_end"] for h in result["history"]] == ["2026-04-30", "2026-01-31"]
    assert out["state"]["backfilled"]["ARKVX"] == "0000940400-26-036464"
    written = {}
    ff.publish(out, write_text=lambda b, k: written.setdefault(k, b), write_json=lambda o, k: written.setdefault(k, o),
               write_parquet=lambda d, k: written.setdefault(k, d))
    base = "curated/fund_holdings/cik=1905088/fund=ARKVX/period="
    assert {base + p + "/holdings.parquet" for p in ("2026-07-31", "2026-04-30", "2026-01-31")} <= set(written)
    # Second run: already backfilled, no older documents fetched again.
    again, calls, _ = _run(["ARKVX"], fund_map=out["fund_map"], state=out["state"], extra=_history_routes())
    assert again["results"][0]["history"] == []
    assert not any("26020000" in url or "26010000" in url for url in calls)


def test_failed_backfill_document_is_retried_next_run():
    out, _c, _p = _run(["ARKVX"], extra=_history_routes(fail="0000940400-26-010000"))
    assert [h["period_end"] for h in out["results"][0]["history"]] == ["2026-04-30"]
    assert "ARKVX" not in out["state"]["backfilled"]


def test_nport_history_caps_at_eight_periods():
    rows = [{"accession": f"a-{i}", "report_date": "", "filed_at": "x", "form": "NPORT-P"} for i in range(20)]
    periods = iter(f"20{10 + i}-01-31" for i in range(30))

    def get_text(_url):
        return ARKVX_XML.replace("2026-07-31", next(periods))

    out, complete = ff.nport_history({"cik": "1905088"}, {}, get_text, rows,
                                     {"accession": "latest", "period_end": "2026-07-31"})
    assert complete and len(out) == ff.BACKFILL_PERIODS - 1


SINGLE = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><company-info>
<cik>0001905088</cik><conformed-name>ARK Venture Fund</conformed-name></company-info>
<entry><category term="NPORT-P"/></entry></feed>"""
LIST = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title></entry></feed>"""


def test_company_search_needs_one_nport_filer_with_an_agreeing_name():
    assert ff.company_search_match(SINGLE) == {"cik": "1905088", "name": "ARK Venture Fund"}
    assert ff.company_search_match(LIST) is None
    assert ff.company_search_match(SINGLE.replace("NPORT-P", "10-K")) is None
    assert ff.company_search_match("not xml") is None
    hit = ff.resolve_by_company_search("ARKVX", "ARK Venture Fund", lambda url: SINGLE)
    assert hit["method"] == "edgar_company_search" and hit["cik"] == "1905088"
    assert ff.resolve_by_company_search("ARKVX", "Totally Different Trust", lambda url: SINGLE) is None
    assert ff.resolve_by_company_search("ARKVX", None, lambda url: SINGLE) is None


def test_company_search_runs_only_after_fulltext_finds_nothing():
    calls = []

    def get_text(url):
        calls.append(url)
        return SINGLE

    def no_hits(url):
        return {"hits": {"hits": []}}

    found = ff.resolve_ticker("ZZZX", {}, {}, no_hits, known_name="ARK Venture Fund", get_text=get_text)
    assert found["status"] == "resolved" and found["method"] == "edgar_company_search"
    assert "company=ARK+Venture+Fund" in calls[0]
    down = ff.resolve_ticker("ZZZX", {}, {}, lambda url: None, known_name="ARK Venture Fund", get_text=get_text)
    assert down["reason"] == "efts_unavailable"  # retried next run, no guess while SEC search is down
    assert ff.resolve_ticker("ZZZX", {}, {}, no_hits, get_text=get_text)["reason"] == "no_fund_filer"


def test_known_names_come_from_the_dashboard_snapshot():
    snap = {"tickers": {"SYFY": {"company_name": None}, "ARKVX": {"company_name": "ARK Venture Fund"}}}
    assert ff.known_names_from_snapshot(snap, ["ARKVX", "SYFY", "NEW"]) == {
        "ARKVX": "ARK Venture Fund", "SYFY": None, "NEW": None}
    assert ff.known_names_from_snapshot(None, ["X"]) == {"X": None}


def test_first_run_backfills_older_13f_quarters_once(monkeypatch):
    quarters = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31",
                "2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31"]
    subs13 = {"name": "ARK Investment Management LLC", "filings": {"recent": {
        "form": ["13F-HR"] * len(quarters), "accessionNumber": [f"q-{q}" for q in quarters],
        "filingDate": quarters, "reportDate": quarters}}}
    fetched = []

    def fake_latest(cik, subs, get_json, get_text, max_docs=6, before=None):
        period = next((q for q in quarters if before is None or q < before), None)
        fetched.append(period)
        return None if period is None else {"accession": f"q-{period}", "amendments": [], "period_end": period,
                                            "holdings": [], "filed_at": period, "form": "13F-HR"}

    monkeypatch.setattr(ff, "latest_13f", fake_latest)
    entry = {"ticker": "ARKK", "status": "resolved", "kind": "fund", "cik": "1579982", "series_id": "S000042977",
             "resolved_at": NOW.isoformat(), "managers": [{"cik": "1697748"}], "managers_status": "resolved",
             "managers_resolved_at": NOW.isoformat()}
    extra = {ff.SUBMISSIONS_URL.format(cik="0001697748"): _Resp(payload=subs13)}
    out, _c, _p = _run(["ARKK"], fund_map={"funds": {"ARKK": entry}}, extra=extra)
    manager = out["managers"][0]
    assert manager["report"]["period_end"] == "2026-06-30" and manager["previous"]["period_end"] == "2026-03-31"
    assert [h["period_end"] for h in manager["history"]] == quarters[2:8]  # 8 quarters in total
    assert out["state"]["backfilled_13f"]["1697748"] == "q-2026-06-30"
    fetched.clear()
    again, _c, _p = _run(["ARKK"], fund_map=out["fund_map"], state=out["state"], extra=extra)
    assert again["managers"][0]["history"] == [] and len(fetched) == 1  # only the latest quarter
