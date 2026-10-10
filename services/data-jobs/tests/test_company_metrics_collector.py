"""Collector tests. Fixtures stand in for EDGAR; nothing here opens a socket."""

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import company_ir as ir
import fundamentals as fq
from regulatory_feeds import earnings_filing_detail

FIXTURES = Path(__file__).parent / "fixtures" / "company_ir"
EXTRACTED = datetime(2026, 10, 8, 12, tzinfo=UTC)
SEC_URL = "https://www.sec.gov/Archives/edgar/data/1318605/000162828026064366/exhibit991.htm"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _meta(**extra):
    base = {
        "ticker": "TSLA",
        "source_url": SEC_URL,
        "source_doc_hash": "b" * 64,
        "extracted_at": EXTRACTED,
        "published_date": "2026-10-02",
        "source_kind": "press_release",
        "fiscal_period": "2026Q3",
    }
    base.update(extra)
    return base


class _Response:
    def __init__(self, status: int, body: str | bytes):
        self.status_code = status
        self.content = body.encode() if isinstance(body, str) else body
        self.text = self.content.decode()
        self.headers = {}

    def json(self):
        return json.loads(self.content)


def test_catalog_corrections_stay_proposed_and_schema_valid():
    catalog = ir.load_catalog("TSLA")
    errors = ir.validate_catalog(catalog)
    assert errors == []
    by_id = {item["metric_id"]: item for item in catalog["metrics"]}
    assert by_id["store_service_locations"]["disclosure_status"] == "discontinued"
    assert by_id["mobile_service_fleet"]["disclosure_status"] == "discontinued"
    assert by_id["solar_deployed"]["disclosure_status"] == "not_disclosed"
    assert by_id["robotaxi_fleet"]["disclosure_status"] == "not_disclosed"
    assert by_id["fsd_miles"]["disclosure_status"] == "chart_only"
    assert by_id["robotaxi_paid_miles"]["disclosure_status"] == "chart_only"
    assert by_id["tesla_semi"]["disclosure_status"] == "narrative"
    assert by_id["optimus"]["disclosure_status"] == "narrative"
    assert by_id["fsd_subscriptions"]["scale"] == 1_000_000
    assert "total_deliveries" in by_id and "deferred_revenue" in by_id
    assert all(item["status"] == "proposed" and item["approved_by"] is None for item in catalog["metrics"])
    broken = json.loads(json.dumps(catalog))
    broken["metrics"][0]["status"] = "approved"
    broken["metrics"][0]["approved_by"] = None
    assert any("approved_by" in error for error in ir.validate_catalog(broken))


def test_press_release_fixture_uses_q3_2026_deliveries_not_the_prior_year():
    catalog = ir.load_catalog("TSLA")
    parsed = ir.parse_company_document(_load("tsla_q3_2026_deliveries.html"), catalog, **_meta())
    rows = {(row["metric_id"], row["fiscal_period"]): row["value"] for row in parsed["rows"]}
    assert rows[("total_deliveries", "2026Q3")] == 486_532
    assert rows[("deliveries_model_3y", "2026Q3")] == 478_237
    assert rows[("deliveries_other_models", "2026Q3")] == 8_295
    assert rows[("total_production", "2026Q3")] == 464_391
    assert rows[("energy_storage_deployed", "2026Q3")] == pytest.approx(13.7)
    assert 497_099 not in rows.values()
    assert ir.classify_exhibit_title("Q3 2026 Production, Deliveries & Deployments") == "press_release"
    assert ir.classify_exhibit_title("Q2 2026 Update") == "deck"


def test_operational_summary_scales_units_flags_bounds_and_proposes_new_labels():
    catalog = ir.load_catalog("TSLA")
    parsed = ir.parse_company_document(
        _load("tsla_q2_2026_operational.html"),
        catalog,
        **_meta(source_kind="deck", fiscal_period="2026Q2", published_date="2026-07-22"),
    )
    fsd = next(
        row for row in parsed["rows"]
        if row["metric_id"] == "fsd_subscriptions" and row["fiscal_period"] == "2026Q2"
    )
    assert fsd["value"] == pytest.approx(1_480_000)
    assert fsd["approved"] is False
    q3_2025 = next(
        row for row in parsed["rows"]
        if row["metric_id"] == "total_deliveries" and row["fiscal_period"] == "2025Q3"
    )
    assert q3_2025["value"] == 497_099
    q2_2026 = next(
        row for row in parsed["rows"]
        if row["metric_id"] == "total_deliveries" and row["fiscal_period"] == "2026Q2"
    )
    assert q2_2026["value"] == 480_126
    capacity = next(row for row in parsed["proposals"] if "california" in row["metric_id"])
    assert capacity["status"] == "proposed" and capacity["approved_by"] is None and capacity["lower_bound"] is True
    assert all(row["fiscal_period"] != "YoY" for row in parsed["rows"])
    assert not any(row["metric_id"] == "robotaxi_fleet" and row["value"] == 0 for row in parsed["rows"])


def test_financial_summary_scales_millions_and_keeps_parenthesized_negatives():
    catalog = ir.load_catalog("TSLA")
    parsed = ir.parse_company_document(
        _load("tsla_q2_2026_financial.html"),
        catalog,
        **_meta(source_kind="deck", fiscal_period="2026Q2", published_date="2026-07-22"),
    )
    revenue = next(
        row for row in parsed["rows"]
        if row["metric_id"] == "revenue_gaap" and row["fiscal_period"] == "2026Q2"
    )
    assert revenue["value"] == pytest.approx(28_236_000_000)
    cash = next(
        row for row in parsed["rows"]
        if row["metric_id"] == "free_cash_flow" and row["fiscal_period"] == "2026Q2"
    )
    assert cash["value"] == pytest.approx(-1_092_000_000)


def test_xbrl_drops_scale_errors_and_beats_the_deck_for_gaap():
    facts = json.loads(_load("companyfacts_scale_error.json"))
    series = ir.parse_xbrl_instants(
        facts["facts"]["us-gaap"]["ContractWithCustomerLiability"]["units"]["USD"],
    )
    assert "2022Q1" not in series
    assert series["2025Q4"] == 3_867_000_000
    flags = ir.xbrl_scale_flags(facts["facts"]["us-gaap"]["ContractWithCustomerLiability"]["units"]["USD"])
    assert flags and flags[0]["reason"] == "scale_error"
    catalog = ir.load_catalog("TSLA")
    deck = ir.parse_company_document(
        _load("tsla_q2_2026_financial.html"),
        catalog,
        **_meta(source_kind="deck", fiscal_period="2026Q2", published_date="2026-07-22", source_doc_hash="d" * 64),
    )["rows"]
    # A different deck figure must be flagged, and the curated winner stays on XBRL.
    for row in deck:
        if row["metric_id"] == "revenue_gaap" and row["fiscal_period"] == "2026Q2":
            row["value"] = 20_000_000_000
    xbrl_rows, _flags = ir.companyfacts_observations(
        facts, catalog, ticker="TSLA", source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json",
        source_doc_hash="e" * 64, extracted_at=EXTRACTED, published_date="2026-07-23",
    )
    mismatches = ir.reconcile_rows(
        [row for row in deck if row["metric_id"] == "revenue_gaap"],
        ir.xbrl_value_map(xbrl_rows),
    )
    assert mismatches[0]["status"] == "mismatch"
    winners, revisions = ir.dedupe_observations(deck + xbrl_rows)
    assert revisions
    revenue = next(
        row for row in winners
        if row["metric_id"] == "revenue_gaap" and row["fiscal_period"] == "2026Q2"
    )
    assert revenue["value"] == 28_236_000_000
    assert revenue["source_kind"] == "xbrl"
    assert revenue["supersedes"] == "d" * 64


def test_operating_dedupe_prefers_the_latest_deck_and_news_never_wins():
    common = {
        "ticker": "TSLA", "metric_id": "total_deliveries", "fiscal_period": "2026Q2",
        "period_end": date(2026, 6, 30), "unit": "vehicles", "extracted_at": EXTRACTED,
        "method": "table", "catalog_status": "approved", "category": "operating", "approved": True,
    }
    rows = [
        {
            **common, "value": 480_000, "source_kind": "press_release",
            "published_date": "2026-07-02", "source_doc_hash": "1" * 64, "source_url": SEC_URL,
        },
        {
            **common, "value": 480_126, "source_kind": "deck",
            "published_date": "2026-07-22", "source_doc_hash": "2" * 64, "source_url": SEC_URL,
        },
        {
            **common, "value": 1, "source_kind": "news", "published_date": "2026-07-23",
            "source_doc_hash": "3" * 64, "source_url": "https://news.example/tsla", "method": "llm",
        },
        {
            **common, "value": 470_000, "source_kind": "deck",
            "published_date": "2026-04-22", "source_doc_hash": "4" * 64, "source_url": SEC_URL,
        },
    ]
    winners, revisions = ir.dedupe_observations(rows)
    assert len(winners) == 1
    assert winners[0]["value"] == 480_126
    assert winners[0]["source_kind"] == "deck"
    assert winners[0]["supersedes"] == "4" * 64
    assert revisions[0]["previous_value"] == 470_000


def test_narrative_candidates_stay_unapproved_and_off_without_a_secret():
    text = _load("tsla_q2_2026_operational.html")
    assert ir.narrative_candidates(text, enabled=False) == []
    found = ir.narrative_candidates(text, enabled=True)
    assert found and all(item["confidence"] == ir.CONFIDENCE["llm"] for item in found)
    catalog = ir.load_catalog("TSLA")
    parsed = ir.parse_company_document(
        text, catalog, **_meta(source_kind="deck", fiscal_period="2026Q2", llm_enabled=True),
    )
    llm_rows = [row for row in parsed["rows"] if row["method"] == "llm"]
    assert llm_rows and all(row["approved"] is False for row in llm_rows)


def test_ir_fetch_policy_blocks_tesla_html_and_stops_on_429():
    robots = _load("robots_ir.txt")
    agent = ir.crawler_user_agent("investor-dashboard (contact: test@example.com)")
    assert ir.decide_ir_fetch("https://www.tesla.com/fsd/safety", robots, agent)["reason"] == "blocked_host"
    assert ir.decide_ir_fetch("https://ir.tesla.com/press", robots, agent)["reason"] == "html_not_a_fallback"
    assert ir.decide_ir_fetch("https://ir.tesla.com/robots.txt", None, agent)["action"] == "fetch"
    denied = ir.decide_ir_fetch("https://ir.tesla.com/private/deck.pdf", robots, agent)
    assert denied["reason"] == "robots" and denied["store"] is False
    allowed = ir.decide_ir_fetch("https://ir.tesla.com/_flysystem/s3/sec/deck.pdf", robots, agent, 429)
    assert allowed["retry"] is False and allowed["reason"] == "provider_policy"


def test_sec_pace_stays_under_ten_requests_per_second():
    slept = []
    clock = {"now": 0.0}

    def sleep(seconds):
        slept.append(seconds)
        clock["now"] += seconds

    pace = ir.Pace(0.12, sleep=sleep, clock=lambda: clock["now"])
    pace.wait("data.sec.gov")
    pace.wait("www.sec.gov")
    pace.wait("ir.tesla.com")
    assert slept == [pytest.approx(0.12)]


def test_collection_plan_skips_an_hourly_h3_without_a_new_exhibit():
    quiet = ir.collection_plan(
        {"detail-type": "Job Finished", "detail": {"job": "H3", "outcome": "success"}},
        date(2026, 10, 7),
    )
    assert quiet["collect"] is False
    triggered = ir.collection_plan(
        {
            "detail-type": "Job Finished",
            "detail": {
                "job": "H3",
                "filing": {"form": "8-K", "exhibits": ["EX-99.1"], "ticker": "TSLA"},
            },
        },
        date(2026, 10, 7),
    )
    assert triggered["collect"] is True and triggered["reason"] == "edgar-8k"
    added = ir.collection_plan({"detail-type": "TickerAdded", "detail": {"ticker": "rivn"}}, date(2026, 10, 7))
    assert added["reason"] == "ticker-added" and added["tickers"] == ["RIVN"]
    assert len(ir.company_ir_tickers([f"T{i}" for i in range(40)])) == 25


def test_h3_emits_a_filing_only_for_todays_item_202():
    rows = [{
        "form": "8-K", "items": ["2.02", "9.01"], "filed_at": "2026-10-02",
        "ticker": "TSLA", "accession_no": "0001628280-26-064366",
    }]
    detail = earnings_filing_detail(rows, date(2026, 10, 2))
    assert detail["exhibits"] == ["EX-99.1"] and detail["ticker"] == "TSLA"
    assert earnings_filing_detail(rows, date(2026, 10, 3)) is None


def _edgar_routes(exhibit_status=200):
    accession = "0001628280-26-064366"
    index_url = ir.filing_index_url("0001318605", accession)
    exhibit_url = ir.archive_url("0001318605", accession, "exhibit991.htm")
    facts_url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json"
    routes = {
        "https://www.sec.gov/files/company_tickers.json": _Response(200, json.dumps({
            "0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla"},
        })),
        "https://data.sec.gov/submissions/CIK0001318605.json": _Response(200, _load("submissions_tsla.json")),
        index_url: _Response(200, _load("filing_index.json")),
        exhibit_url: _Response(exhibit_status, _load("tsla_q3_2026_deliveries.html") if exhibit_status == 200 else ""),
        facts_url: _Response(200, _load("companyfacts_scale_error.json")),
    }
    return routes, exhibit_url


def test_collect_stores_edgar_once_and_stops_on_429_without_retry(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "investor-dashboard (contact: test@example.com)")
    routes, exhibit_url = _edgar_routes()
    calls = []
    stored = {}
    manifests = {}

    def fetch(url, headers):
        calls.append((url, headers["User-Agent"]))
        if url not in routes:
            return _Response(404, "")
        return routes[url]

    def run_once(exists):
        return ir.run_collect(
            {"source": "schedule"},
            date(2026, 10, 5),
            fetch=fetch,
            exists=exists,
            put_bytes=lambda key, body, content_type: stored.__setitem__(key, body),
            put_json=lambda key, obj: manifests.__setitem__(key, obj),
            read_json=lambda key: manifests.get(key),
            tickers=["TSLA", "SPCX"],
            pace=ir.Pace(0),
        )

    first = run_once(lambda key: key in stored)
    assert first["status"] == "ok"
    assert first["collected"] >= 1
    assert calls[0][1].startswith("InvestorDashboardIR/1.0")
    assert any(key.endswith(".htm") for key in stored)
    requested = [url for url, _agent in calls]
    assert any(url.endswith("/000162828026064366/index.json") for url in requested)
    assert not any(url.endswith("-index.json") for url in requested)
    again = run_once(lambda key: key in stored)
    assert again["collected"] == 0
    assert again["documents"] >= 1

    routes_429, exhibit_url = _edgar_routes(429)
    seen = []

    def fetch_429(url, headers):
        seen.append(url)
        return routes_429.get(url, _Response(404, ""))

    stopped = ir.run_collect(
        {
            "source": "edgar-8k",
            "watchlist": ["TSLA"],
            "filing": {"form": "8-K", "exhibits": ["EX-99.1"], "ticker": "TSLA"},
        },
        date(2026, 10, 7),
        fetch=fetch_429,
        exists=lambda key: False,
        put_bytes=lambda key, body, content_type: None,
        put_json=lambda key, obj: None,
        read_json=lambda key: None,
        tickers=["TSLA"],
        pace=ir.Pace(0),
    )
    assert seen.count(exhibit_url) == 1
    assert any("ex-99.1" in source for source in stopped["failed_source_ids"])
    assert "www.tesla.com" not in " ".join(seen)


def test_robots_denied_and_tesla_html_are_not_fetched(monkeypatch):
    monkeypatch.setattr(ir, "load_catalog", lambda ticker: {
        "ticker": ticker,
        "metrics": [],
        "ir_sources": [{
            "id": "tesla-ir-pdf",
            "robots_url": "https://ir.tesla.com/robots.txt",
            "urls": [
                "https://www.tesla.com/fsd/safety",
                "https://ir.tesla.com/press",
                "https://ir.tesla.com/private/deck.pdf",
                "https://ir.tesla.com/_flysystem/s3/sec/deck.pdf",
            ],
        }],
    })
    calls = []

    def fetch(url, headers):
        calls.append(url)
        if url == "https://www.sec.gov/files/company_tickers.json":
            return _Response(200, json.dumps({"0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla"}}))
        if url.endswith("CIK0001318605.json") and "submissions" in url:
            return _Response(200, json.dumps({"cik": "1318605", "filings": {"recent": {}}}))
        if "companyfacts" in url:
            return _Response(200, "{}")
        if url.endswith("/robots.txt"):
            return _Response(200, _load("robots_ir.txt"))
        if url.endswith("deck.pdf"):
            return _Response(200, b"%PDF-1.4")
        return _Response(200, "nope")

    result = ir.run_collect(
        {"source": "schedule"},
        date(2026, 10, 5),
        fetch=fetch,
        exists=lambda key: False,
        put_bytes=lambda key, body, content_type: None,
        put_json=lambda key, obj: None,
        read_json=lambda key: None,
        tickers=["TSLA"],
        pace=ir.Pace(0),
    )
    assert not any("tesla.com/fsd" in url or url.endswith("/press") or "/private/" in url for url in calls)
    assert any(url.endswith("/_flysystem/s3/sec/deck.pdf") for url in calls)
    assert any(reason.get("reason") == "robots" for reason in result["reasons"])


def test_extract_and_serve_round_trip_filters_proposed_and_keeps_last_good(tmp_path):
    catalog = ir.load_catalog("TSLA")
    bodies = {
        "deliveries": _load("tsla_q3_2026_deliveries.html").encode(),
        "deck": _load("tsla_q2_2026_operational.html").encode(),
        "facts": _load("companyfacts_scale_error.json").encode(),
    }
    manifests = [
        {
            "ticker": "TSLA", "key": "deliveries", "source_kind": "press_release", "period": "2026Q3",
            "source_url": SEC_URL, "sha256": "a" * 64, "published_date": "2026-10-02",
            "accession": "0001628280-26-064366",
        },
        {
            "ticker": "TSLA", "key": "deck", "source_kind": "deck", "period": "2026Q2",
            "source_url": SEC_URL, "sha256": "b" * 64, "published_date": "2026-07-22",
            "accession": "0001628280-26-049213",
        },
        {"ticker": "TSLA", "key": "facts", "source_kind": "xbrl", "period": "companyfacts",
         "source_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json",
         "sha256": "c" * 64, "published_date": "2026-07-23"},
    ]
    written = {}

    def write_parquet(frame, key):
        frame.write_parquet(tmp_path / "metrics.parquet", compression="zstd")
        written[key] = frame

    def write_json(key, obj):
        written[key] = obj

    result = ir.run_extract(
        {"manifests": manifests, "status": "ok"},
        read_bytes=lambda key: bodies[key],
        read_json=lambda key: None,
        write_parquet=write_parquet,
        write_json=write_json,
        now=EXTRACTED,
    )
    assert result["extracted"] > 0
    assert any(flag.get("status") == "scale_error" for flag in result["mismatches"])
    observations = written[ir.curated_observations_key("TSLA")]["rows"]
    deliveries = next(
        row for row in observations
        if row["metric_id"] == "total_deliveries" and row["fiscal_period"] == "2026Q3"
    )
    assert deliveries["value"] == 486_532
    assert deliveries["approved"] is False
    proposals = written[ir.curated_proposals_key("TSLA")]["metrics"]
    assert proposals and all(item["status"] == "proposed" and item["approved_by"] is None for item in proposals)

    approved = ir.set_catalog_status(catalog, "total_deliveries", "approved", "ram", "2026-10-09T00:00:00Z")
    for row in observations:
        if row["metric_id"] == "total_deliveries":
            row["approved"] = True
            row["catalog_status"] = "approved"
    store = {ir.curated_observations_key("TSLA"): {"rows": observations}}

    def read_json(key):
        return store.get(key)

    def write_serving(key, obj):
        store[key] = obj

    import company_ir

    original = company_ir.load_catalog
    company_ir.load_catalog = lambda ticker: approved if ticker == "TSLA" else original(ticker)
    try:
        served = ir.run_serve(
            {"tickers": ["TSLA"], "run_status": "ok", "mismatches": result["mismatches"]},
            read_json=read_json,
            write_json=write_serving,
            now="2026-10-08T12:00:00Z",
        )
    finally:
        company_ir.load_catalog = original
    assert served["served"] == 1
    document = store[ir.serving_stock_key("TSLA")]
    assert [item["metric_id"] for item in document["metrics"]] == ["total_deliveries"]
    assert document["metrics"][0]["latest"]["value"] == 486_532
    assert document["metrics"][0]["provenance"]["source_url"].startswith("https://www.sec.gov/")

    partial = ir.run_serve(
        {"tickers": ["TSLA"], "run_status": "partial", "status": "partial"},
        read_json=read_json,
        write_json=write_serving,
        now="2026-10-09T00:00:00Z",
    )
    assert partial["alert"] is True
    kept = store[ir.serving_stock_key("TSLA")]
    assert kept["metrics"][0]["latest"]["value"] == 486_532
    assert "previous approved values kept" in kept["freshness_label"]
    skipped = ir.run_serve({"status": "skipped", "tickers": ["TSLA"]}, read_json=read_json, write_json=write_serving)
    assert skipped["served"] == 0


def test_q1_uses_approved_curated_counts_and_warns_when_the_csv_is_missing():
    manual = [{
        "ticker": "TSLA", "metric": "deliveries", "fiscal_quarter": "2026Q3",
        "release_date": date(2026, 10, 2), "value": 497099.0, "unit": "vehicles", "source_id": "DS-12",
    }]
    curated = [
        {"ticker": "TSLA", "metric_id": "total_deliveries", "fiscal_period": "2026Q3", "value": 486532,
         "approved": True, "published_date": "2026-10-02"},
        {"ticker": "TSLA", "metric_id": "fsd_subscriptions", "fiscal_period": "2026Q2", "value": 1_480_000,
         "approved": True, "published_date": "2026-07-22"},
        {"ticker": "TSLA", "metric_id": "total_deliveries", "fiscal_period": "2026Q2", "value": 1,
         "approved": False, "published_date": "2026-07-02"},
    ]
    approved = {("TSLA", "total_deliveries"), ("TSLA", "fsd_subscriptions")}
    rows = fq.rows_from_company_metrics(curated, approved)
    table = fq.build_table([], manual + rows)
    got = {(row["metric"], row["fiscal_quarter"]): row["value"] for row in table.to_dicts()}
    assert got[("deliveries", "2026Q3")] == 486532
    assert got[("fsd_subscribers", "2026Q2")] == 1_480_000
    assert ("deliveries", "2026Q2") not in got
    summed = fq.rows_from_company_metrics(
        [
            {
                "ticker": "TSLA", "metric_id": "deliveries_model_3y", "fiscal_period": "2026Q1",
                "value": 10, "approved": True, "published_date": "2026-04-02",
            },
            {
                "ticker": "TSLA", "metric_id": "deliveries_other_models", "fiscal_period": "2026Q1",
                "value": 3, "approved": True, "published_date": "2026-04-02",
            },
        ],
        {("TSLA", "deliveries_model_3y"), ("TSLA", "deliveries_other_models")},
    )
    assert summed[0]["metric"] == "deliveries" and summed[0]["value"] == 13
    assert fq.manual_csv_warning(None)
    assert fq.manual_csv_warning("ticker,metric\n") is None
    script = Path(__file__).parents[1].joinpath("scripts", "add_fundamental.py").read_text(encoding="utf-8")
    assert "2026-10-02 486532" in script
    assert "2026-10-02 497099" not in script


def test_propose_and_approve_helpers_never_invent_an_approval(tmp_path):
    scripts = Path(__file__).parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    import propose_catalog
    from approve_metric import apply_status

    catalog = propose_catalog.build_proposed_catalog("SPCX", [{
        "text": _load("spcx_exhibit.html"),
        "source_kind": "deck",
        "fiscal_period": "2026Q3",
        "source_url": "https://www.sec.gov/Archives/edgar/data/fixture/spcx.htm",
    }])
    assert catalog["metrics"]
    assert all(item["status"] == "proposed" and item["approved_by"] is None for item in catalog["metrics"])
    with pytest.raises(ValueError, match="--by"):
        apply_status(ir.load_catalog("TSLA"), "total_deliveries", "approved", "", "2026-10-10T00:00:00Z")
    approved = apply_status(ir.load_catalog("TSLA"), "total_deliveries", "approved", "ram", "2026-10-10T00:00:00Z")
    entry = ir.catalog_entry(approved, "total_deliveries")
    assert entry["status"] == "approved" and entry["approved_by"] == "ram"
    assert ir.validate_catalog(approved) == []


def test_filing_index_url_is_the_directory_index():
    url = ir.filing_index_url("0001318605", "0001628280-26-064366")
    assert url == "https://www.sec.gov/Archives/edgar/data/1318605/000162828026064366/index.json"


def _instant(end: str, value: float, filed: str, form: str = "10-Q") -> dict:
    return {"end": end, "val": value, "filed": filed, "form": form, "accn": "000", "fy": 2026, "fp": "Q2"}


def test_cash_and_investments_uses_current_concepts_then_older_fallbacks():
    cash_2018 = 2_967_000_000
    cash_2026 = 15_219_000_000
    short_2026 = 28_305_000_000
    cash_2022 = 19_532_000_000
    marketable_2022 = 1_575_000_000
    cash_2021 = 17_576_000_000
    short_2021 = 131_000_000
    gaap = {
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": {"units": {"USD": [
            _instant("2018-09-30", 3_522_966_000, "2018-11-02"),
            _instant("2017-09-30", 3_000_000_000, "2017-11-03"),
        ]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
            _instant("2018-09-30", cash_2018, "2018-11-02"),
            _instant("2021-12-31", cash_2021, "2022-02-07", "10-K"),
            _instant("2022-09-30", cash_2022, "2022-10-24"),
            _instant("2026-06-30", cash_2026, "2026-07-23"),
        ]}},
        "ShortTermInvestments": {"units": {"USD": [
            _instant("2021-12-31", short_2021, "2022-02-07", "10-K"),
            _instant("2024-12-31", 20_424_000_000, "2025-01-30", "10-K"),
            _instant("2026-06-30", short_2026, "2026-07-23"),
        ]}},
        "MarketableSecuritiesCurrent": {"units": {"USD": [
            _instant("2021-12-31", short_2021, "2022-02-07", "10-K"),
            _instant("2022-09-30", marketable_2022, "2022-10-24"),
        ]}},
        "CashCashEquivalentsAndShortTermInvestments": {"units": {"USD": [
            _instant("2012-03-31", 900_000_000, "2012-05-10"),
        ]}},
    }
    rows, _flags = ir.companyfacts_observations(
        {"facts": {"us-gaap": gaap}},
        ir.load_catalog("TSLA"),
        ticker="TSLA",
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json",
        source_doc_hash="e" * 64,
        extracted_at=EXTRACTED,
    )
    series = {
        row["fiscal_period"]: row["value"]
        for row in rows if row["metric_id"] == "cash_and_investments"
    }
    assert series["2018Q3"] == cash_2018
    assert series["2026Q2"] == cash_2026 + short_2026
    assert series["2022Q3"] == cash_2022 + marketable_2022
    assert series["2021Q4"] == cash_2021 + short_2021
    assert series["2017Q3"] == 3_000_000_000
    assert series["2012Q1"] == 900_000_000


def test_extract_still_parses_a_partial_collect():
    written = {}
    result = ir.run_extract(
        {
            "job": "Q2C",
            "outcome": "partial",
            "status": "partial",
            "run_status": "partial",
            "manifests": [{
                "ticker": "TSLA",
                "key": "deliveries",
                "source_kind": "press_release",
                "period": "2026Q3",
                "source_url": SEC_URL,
                "sha256": "a" * 64,
                "published_date": "2026-10-02",
            }],
        },
        read_bytes=lambda key: _load("tsla_q3_2026_deliveries.html").encode(),
        read_json=lambda key: None,
        write_parquet=lambda frame, key: written.__setitem__(key, frame.height),
        write_json=lambda key, obj: written.__setitem__(key, obj),
        now=EXTRACTED,
    )
    assert result["run_status"] == "ok"
    assert result["extracted"] > 0
    assert written[ir.curated_parquet_key("TSLA")] > 0
