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
        row for row in parsed["rows"] if row["metric_id"] == "fsd_subscriptions" and row["fiscal_period"] == "2026Q2"
    )
    assert fsd["value"] == pytest.approx(1_480_000)
    assert fsd["approved"] is False
    q3_2025 = next(
        row for row in parsed["rows"] if row["metric_id"] == "total_deliveries" and row["fiscal_period"] == "2025Q3"
    )
    assert q3_2025["value"] == 497_099
    q2_2026 = next(
        row for row in parsed["rows"] if row["metric_id"] == "total_deliveries" and row["fiscal_period"] == "2026Q2"
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
        row for row in parsed["rows"] if row["metric_id"] == "revenue_gaap" and row["fiscal_period"] == "2026Q2"
    )
    assert revenue["value"] == pytest.approx(28_236_000_000)
    cash = next(
        row for row in parsed["rows"] if row["metric_id"] == "free_cash_flow" and row["fiscal_period"] == "2026Q2"
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
        facts,
        catalog,
        ticker="TSLA",
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json",
        source_doc_hash="e" * 64,
        extracted_at=EXTRACTED,
        published_date="2026-07-23",
    )
    mismatches = ir.reconcile_rows(
        [row for row in deck if row["metric_id"] == "revenue_gaap"],
        ir.xbrl_value_map(xbrl_rows),
    )
    assert mismatches[0]["status"] == "mismatch"
    winners, revisions = ir.dedupe_observations(deck + xbrl_rows)
    assert revisions
    revenue = next(row for row in winners if row["metric_id"] == "revenue_gaap" and row["fiscal_period"] == "2026Q2")
    assert revenue["value"] == 28_236_000_000
    assert revenue["source_kind"] == "xbrl"
    assert revenue["supersedes"] == "d" * 64


def test_operating_dedupe_prefers_the_latest_deck_and_news_never_wins():
    common = {
        "ticker": "TSLA",
        "metric_id": "total_deliveries",
        "fiscal_period": "2026Q2",
        "period_end": date(2026, 6, 30),
        "unit": "vehicles",
        "extracted_at": EXTRACTED,
        "method": "table",
        "catalog_status": "approved",
        "category": "operating",
        "approved": True,
    }
    rows = [
        {
            **common,
            "value": 480_000,
            "source_kind": "press_release",
            "published_date": "2026-07-02",
            "source_doc_hash": "1" * 64,
            "source_url": SEC_URL,
        },
        {
            **common,
            "value": 480_126,
            "source_kind": "deck",
            "published_date": "2026-07-22",
            "source_doc_hash": "2" * 64,
            "source_url": SEC_URL,
        },
        {
            **common,
            "value": 1,
            "source_kind": "news",
            "published_date": "2026-07-23",
            "source_doc_hash": "3" * 64,
            "source_url": "https://news.example/tsla",
            "method": "llm",
        },
        {
            **common,
            "value": 470_000,
            "source_kind": "deck",
            "published_date": "2026-04-22",
            "source_doc_hash": "4" * 64,
            "source_url": SEC_URL,
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
        text,
        catalog,
        **_meta(source_kind="deck", fiscal_period="2026Q2", llm_enabled=True),
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
    rows = [
        {
            "form": "8-K",
            "items": ["2.02", "9.01"],
            "filed_at": "2026-10-02",
            "ticker": "TSLA",
            "accession_no": "0001628280-26-064366",
        }
    ]
    detail = earnings_filing_detail(rows, date(2026, 10, 2))
    assert detail["exhibits"] == ["EX-99.1"] and detail["ticker"] == "TSLA"
    assert earnings_filing_detail(rows, date(2026, 10, 3)) is None


def _edgar_routes(exhibit_status=200):
    accession = "0001628280-26-064366"
    index_url = ir.filing_index_url("0001318605", accession)
    exhibit_url = ir.archive_url("0001318605", accession, "exhibit991.htm")
    facts_url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json"
    routes = {
        "https://www.sec.gov/files/company_tickers.json": _Response(
            200,
            json.dumps(
                {
                    "0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla"},
                }
            ),
        ),
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
    assert not any(url.endswith(".txt") for url in requested)
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
    monkeypatch.setattr(
        ir,
        "load_catalog",
        lambda ticker: {
            "ticker": ticker,
            "metrics": [],
            "ir_sources": [
                {
                    "id": "tesla-ir-pdf",
                    "robots_url": "https://ir.tesla.com/robots.txt",
                    "urls": [
                        "https://www.tesla.com/fsd/safety",
                        "https://ir.tesla.com/press",
                        "https://ir.tesla.com/private/deck.pdf",
                        "https://ir.tesla.com/_flysystem/s3/sec/deck.pdf",
                    ],
                }
            ],
        },
    )
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
            "ticker": "TSLA",
            "key": "deliveries",
            "source_kind": "press_release",
            "period": "2026Q3",
            "source_url": SEC_URL,
            "sha256": "a" * 64,
            "published_date": "2026-10-02",
            "accession": "0001628280-26-064366",
        },
        {
            "ticker": "TSLA",
            "key": "deck",
            "source_kind": "deck",
            "period": "2026Q2",
            "source_url": SEC_URL,
            "sha256": "b" * 64,
            "published_date": "2026-07-22",
            "accession": "0001628280-26-049213",
        },
        {
            "ticker": "TSLA",
            "key": "facts",
            "source_kind": "xbrl",
            "period": "companyfacts",
            "source_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json",
            "sha256": "c" * 64,
            "published_date": "2026-07-23",
        },
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
        row for row in observations if row["metric_id"] == "total_deliveries" and row["fiscal_period"] == "2026Q3"
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
    approved = [item for item in document["metrics"] if item["approval_state"] == "approved"]
    proposed = [item for item in document["metrics"] if item["approval_state"] == "proposed"]
    assert [item["metric_id"] for item in approved] == ["total_deliveries"]
    assert approved[0]["latest"]["value"] == 486_532
    assert approved[0]["provenance"]["source_url"].startswith("https://www.sec.gov/")
    assert proposed
    assert all(item["approved"] is False for item in proposed)
    assert all(item["provenance"]["confidence"] is not None for item in document["metrics"])
    assert all(str(item["provenance"]["source_url"]).startswith("https://") for item in document["metrics"])
    assert document["metrics"][0]["metric_id"] == "total_deliveries"

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
    manual = [
        {
            "ticker": "TSLA",
            "metric": "deliveries",
            "fiscal_quarter": "2026Q3",
            "release_date": date(2026, 10, 2),
            "value": 497099.0,
            "unit": "vehicles",
            "source_id": "DS-12",
        }
    ]
    curated = [
        {
            "ticker": "TSLA",
            "metric_id": "total_deliveries",
            "fiscal_period": "2026Q3",
            "value": 486532,
            "approved": True,
            "published_date": "2026-10-02",
        },
        {
            "ticker": "TSLA",
            "metric_id": "fsd_subscriptions",
            "fiscal_period": "2026Q2",
            "value": 1_480_000,
            "approved": True,
            "published_date": "2026-07-22",
        },
        {
            "ticker": "TSLA",
            "metric_id": "total_deliveries",
            "fiscal_period": "2026Q2",
            "value": 1,
            "approved": False,
            "published_date": "2026-07-02",
        },
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
                "ticker": "TSLA",
                "metric_id": "deliveries_model_3y",
                "fiscal_period": "2026Q1",
                "value": 10,
                "approved": True,
                "published_date": "2026-04-02",
            },
            {
                "ticker": "TSLA",
                "metric_id": "deliveries_other_models",
                "fiscal_period": "2026Q1",
                "value": 3,
                "approved": True,
                "published_date": "2026-04-02",
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

    catalog = propose_catalog.build_proposed_catalog(
        "SPCX",
        [
            {
                "text": _load("spcx_exhibit.html"),
                "source_kind": "deck",
                "fiscal_period": "2026Q3",
                "source_url": "https://www.sec.gov/Archives/edgar/data/fixture/spcx.htm",
            }
        ],
    )
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
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": {
            "units": {
                "USD": [
                    _instant("2018-09-30", 3_522_966_000, "2018-11-02"),
                    _instant("2017-09-30", 3_000_000_000, "2017-11-03"),
                ]
            }
        },
        "CashAndCashEquivalentsAtCarryingValue": {
            "units": {
                "USD": [
                    _instant("2018-09-30", cash_2018, "2018-11-02"),
                    _instant("2021-12-31", cash_2021, "2022-02-07", "10-K"),
                    _instant("2022-09-30", cash_2022, "2022-10-24"),
                    _instant("2026-06-30", cash_2026, "2026-07-23"),
                ]
            }
        },
        "ShortTermInvestments": {
            "units": {
                "USD": [
                    _instant("2021-12-31", short_2021, "2022-02-07", "10-K"),
                    _instant("2024-12-31", 20_424_000_000, "2025-01-30", "10-K"),
                    _instant("2026-06-30", short_2026, "2026-07-23"),
                ]
            }
        },
        "MarketableSecuritiesCurrent": {
            "units": {
                "USD": [
                    _instant("2021-12-31", short_2021, "2022-02-07", "10-K"),
                    _instant("2022-09-30", marketable_2022, "2022-10-24"),
                ]
            }
        },
        "CashCashEquivalentsAndShortTermInvestments": {
            "units": {
                "USD": [
                    _instant("2012-03-31", 900_000_000, "2012-05-10"),
                ]
            }
        },
    }
    rows, _flags = ir.companyfacts_observations(
        {"facts": {"us-gaap": gaap}},
        ir.load_catalog("TSLA"),
        ticker="TSLA",
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0001318605.json",
        source_doc_hash="e" * 64,
        extracted_at=EXTRACTED,
    )
    series = {row["fiscal_period"]: row["value"] for row in rows if row["metric_id"] == "cash_and_investments"}
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
            "manifests": [
                {
                    "ticker": "TSLA",
                    "key": "deliveries",
                    "source_kind": "press_release",
                    "period": "2026Q3",
                    "source_url": SEC_URL,
                    "sha256": "a" * 64,
                    "published_date": "2026-10-02",
                }
            ],
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


REAL = FIXTURES / "real"


def _real(name: str) -> str:
    return (REAL / name).read_text(encoding="utf-8")


def _value(rows, metric_id, fiscal_period):
    return next(row["value"] for row in rows if row["metric_id"] == metric_id and row["fiscal_period"] == fiscal_period)


def test_exhibits_come_from_the_index_table_or_submission_type_not_the_filename():
    gif_index = {
        "directory": {
            "item": [
                {"name": "exhibit991.htm", "type": "text.gif"},
                {"name": "q2fy27pr.htm", "type": "text.gif"},
                {"name": "earningsreleaseq22608042.htm", "type": "text.gif"},
            ]
        }
    }
    assert ir.exhibits_from_index(gif_index) == []
    tesla = ir.exhibits_from_index_page(_real("tsla_q3_2026_index.html"))
    assert tesla == [{"name": "exhibit991111111.htm", "type": "EX-99.1", "description": "EX-99.1"}]
    nvidia = ir.exhibits_from_index_page(_real("nvda_q2_fy27_index.html"))
    assert [item["name"] for item in nvidia] == ["q2fy27pr.htm", "q2fy27cfocommentary.htm"]
    assert [item["type"] for item in nvidia] == ["EX-99.1", "EX-99.2"]
    spacex = ir.exhibits_from_index_page(_real("spcx_q2_2026_index.html"))
    assert spacex[0]["name"] == "earningsreleaseq22608042.htm"
    assert spacex[0]["type"] == "EX-99.1"
    submitted = ir.exhibits_from_submission(_real("tsla_q3_2026_submission.txt"))
    assert submitted == [{"name": "exhibit991111111.htm", "type": "EX-99.1", "description": "EX-99.1"}]


def test_deliveries_release_is_classified_from_body_text_and_not_the_filing_date():
    text = _real("tsla_q3_2026_deliveries.htm")
    assert ir.title_from_html(text) == "Document"
    assert ir.classify_release("Document", text) == "press_release"
    assert ir.fiscal_period_for_end(date(2026, 10, 2)) == "2026Q3"
    assert ir.fiscal_period_for_end(date(2026, 10, 15)) == "2026Q4"
    assert ir.fiscal_period_from_text(text) == "2026Q3"
    catalog = ir.load_catalog("TSLA")
    parsed = ir.parse_company_document(text, catalog, **_meta(source_kind="deck", fiscal_period="2026Q4"))
    rows = parsed["rows"]
    assert _value(rows, "total_deliveries", "2026Q3") == 486_532
    assert _value(rows, "energy_storage_deployed", "2026Q3") == pytest.approx(13.7)
    assert not any(row["fiscal_period"] == "2026Q4" for row in rows)


def test_update_deck_period_and_flowing_text_rows():
    text = _real("tsla_q2_2026_deck.htm")
    assert ir.classify_release(ir.title_from_html(text), text) == "deck"
    assert ir.fiscal_period_for_end(date(2026, 7, 22)) == "2026Q3"
    assert ir.fiscal_period_from_text(text) == "2026Q2"
    catalog = ir.load_catalog("TSLA")
    parsed = ir.parse_company_document(
        text,
        catalog,
        **_meta(source_kind="press_release", fiscal_period="2026Q3", published_date="2026-07-22"),
    )
    rows = parsed["rows"]
    assert _value(rows, "revenue_gaap", "2026Q2") == pytest.approx(28_236_000_000)
    assert _value(rows, "total_deliveries", "2026Q2") == 480_126
    assert _value(rows, "fsd_subscriptions", "2026Q2") == pytest.approx(1_480_000)
    assert _value(rows, "free_cash_flow", "2026Q2") == pytest.approx(-1_092_000_000)
    assert _value(rows, "energy_storage_deployed", "2026Q2") == pytest.approx(13.5)


def test_flowing_text_keeps_lower_bounds_nulls_and_footnotes():
    catalog = ir.load_catalog("TSLA")
    text = """
    ($ in millions)
    Q2-2025 Q3-2025 Q4-2025 Q1-2026 Q2-2026 YoY
    California Model 3/Y >550,000 >550,000 >550,000 >550,000 >550,000 —
    Active FSD Subscriptions(2) (mil) 0.95 1.04 1.10 1.28 1.48 56%
    Robotaxi fleet — — — — — —
    """
    parsed = ir.parse_company_document(text, catalog, **_meta(source_kind="deck", fiscal_period="2026Q2"))
    capacity = next(row for row in parsed["proposals"] if "california" in row["metric_id"])
    assert capacity["lower_bound"] is True
    fsd = next(
        row for row in parsed["rows"] if row["metric_id"] == "fsd_subscriptions" and row["fiscal_period"] == "2026Q2"
    )
    assert fsd["value"] == pytest.approx(1_480_000)
    assert not any(row["metric_id"] == "robotaxi_fleet" and row["value"] == 0 for row in parsed["rows"])


def test_spacex_release_parses_segment_rows_without_html_tables():
    text = _real("spcx_q2_2026_release.htm")
    assert ir.fiscal_period_from_text(text) == "2026Q2"
    catalog = ir.load_catalog("SPCX")
    parsed = ir.parse_company_document(
        text,
        catalog,
        **_meta(ticker="SPCX", source_kind="press_release", fiscal_period="2026Q4"),
    )
    rows = parsed["rows"]
    assert _value(rows, "revenue_space", "2026Q2") == pytest.approx(962_000_000)
    assert _value(rows, "revenue_connectivity", "2026Q2") == pytest.approx(4_291_000_000)
    assert _value(rows, "revenue_ai", "2026Q2") == pytest.approx(2_561_000_000)
    assert _value(rows, "total_launches", "2026Q2") == 38
    assert _value(rows, "mass_to_orbit", "2026Q2") == 485
    assert _value(rows, "starlink_subscribers", "2026Q2") == pytest.approx(12_000_000)
    assert _value(rows, "starlink_arpu", "2026Q2") == 66
    assert _value(rows, "ai_nameplate_compute", "2026Q2") == pytest.approx(1.4)
    assert _value(rows, "segment_adj_ebitda_connectivity", "2026Q2") == pytest.approx(2_597_000_000)
    assert _value(rows, "adjusted_ebitda", "2026Q2") == pytest.approx(3_538_000_000)
    assert _value(rows, "capex_total", "2026Q2") == pytest.approx(18_369_000_000)


def test_nvidia_exhibits_use_fiscal_q2_fy27_not_the_august_filing_date():
    commentary = _real("nvda_q2_fy27_cfo_commentary.htm")
    release = _real("nvda_q2_fy27_press_release.htm")
    assert ir.classify_release("Document", commentary) == "press_release"
    assert ir.fiscal_end_month_from_submissions({"fiscalYearEnd": "0131"}) == 1
    assert ir.fiscal_period_for_end(date(2026, 8, 26)) == "2026Q3"
    assert ir.fiscal_period_for_end(date(2026, 7, 26), 1) == "2027Q2"
    assert ir.fiscal_period_from_text(commentary, 1) == "2027Q2"
    assert ir.fiscal_period_from_text(release, 1) == "2027Q2"
    catalog = ir.load_catalog("NVDA")
    parsed = ir.parse_company_document(
        commentary,
        catalog,
        **_meta(ticker="NVDA", fiscal_end_month=1, fiscal_period="2026Q3"),
    )
    rows = parsed["rows"]
    assert _value(rows, "revenue_gaap", "2027Q2") == pytest.approx(96_221_000_000)
    assert _value(rows, "revenue_hyperscale", "2027Q2") == pytest.approx(48_710_000_000)
    assert _value(rows, "revenue_acie", "2027Q2") == pytest.approx(40_313_000_000)
    assert _value(rows, "revenue_edge_computing", "2027Q2") == pytest.approx(7_198_000_000)
    assert _value(rows, "revenue_compute_networking", "2027Q2") == pytest.approx(88_299_000_000)
    assert _value(rows, "revenue_graphics", "2027Q2") == pytest.approx(7_922_000_000)
    assert _value(rows, "revenue_data_center", "2027Q2") == pytest.approx(89_023_000_000)
    assert _value(rows, "gross_margin_gaap", "2027Q2") == pytest.approx(0.75)


def test_scale_flags_do_not_leak_across_tickers():
    tagged = {
        "ticker": "TSLA",
        "metric_id": "capex",
        "fiscal_period": "2026Q1",
        "status": "scale_error",
        "reason": "scale_error",
    }
    untagged = {
        "metric_id": "capex",
        "fiscal_period": "2026Q1",
        "status": "scale_error",
        "reason": "scale_error",
    }
    flags = [tagged, untagged]
    run = ir.run_record(status="ok", sources={"tickers": 2}, mismatches=flags)
    tesla = ir.build_serving(
        "TSLA",
        ir.load_catalog("TSLA"),
        [],
        run=run,
        generated_at="2026-10-10T00:00:00Z",
        mismatches=flags,
    )
    spacex = ir.build_serving(
        "SPCX",
        ir.load_catalog("SPCX"),
        [],
        run=run,
        generated_at="2026-10-10T00:00:00Z",
        mismatches=flags,
    )
    assert tesla["mismatches"] == [tagged]
    assert spacex["mismatches"] == []


def test_generic_catalog_is_proposed_and_company_files_stay_specific():
    generic = ir.load_catalog("RIVN")
    ids = {item["metric_id"] for item in generic["metrics"]}
    expected = {
        "revenue_gaap",
        "gross_profit",
        "operating_cash_flow",
        "capex",
        "share_repurchases",
        "dividends_paid",
    }
    assert expected <= ids
    assert all(item["status"] == "proposed" and item["approved_by"] is None for item in generic["metrics"])
    assert ir.validate_catalog(generic) == []
    for ticker, specific in (
        ("SPCX", {"revenue_space", "starlink_subscribers", "ai_nameplate_compute", "total_launches"}),
        ("NVDA", {"revenue_hyperscale", "revenue_acie", "revenue_edge_computing", "revenue_graphics"}),
    ):
        loaded = ir.load_catalog(ticker)
        loaded_ids = {item["metric_id"] for item in loaded["metrics"]}
        assert specific <= loaded_ids
        assert "revenue_gaap" in loaded_ids
        assert "share_repurchases" in loaded_ids
        on_disk = json.loads((Path(ir.__file__).resolve().parent / "catalog" / f"{ticker}.json").read_text())
        disk_ids = {item["metric_id"] for item in on_disk["metrics"]}
        assert "revenue_gaap" not in disk_ids
        assert specific <= disk_ids
        assert all(item["status"] == "proposed" and item["approved_by"] is None for item in on_disk["metrics"])


def _cash_fact(start, end, value, form="10-Q", fp=None, fy=None):
    fact = {"form": form, "start": start, "end": end, "val": value, "filed": end}
    if fp:
        fact["fp"] = fp
    if fy:
        fact["fy"] = fy
    return fact


def test_ytd_cash_flow_becomes_single_quarters():
    facts = [
        _cash_fact("2025-01-01", "2025-03-31", 100),
        _cash_fact("2025-01-01", "2025-06-30", 250),
        _cash_fact("2025-01-01", "2025-09-30", 400),
        _cash_fact("2025-01-01", "2025-12-31", 500, form="10-K"),
    ]
    series = ir.discrete_cashflow_quarters(facts)
    assert series == {"2025Q1": 100, "2025Q2": 150, "2025Q3": 150, "2025Q4": 100}
    discrete = ir.discrete_cashflow_quarters(
        [
            _cash_fact("2025-01-01", "2025-03-31", 100),
            _cash_fact("2025-04-01", "2025-06-30", 80),
            _cash_fact("2025-01-01", "2025-06-30", 999),
        ]
    )
    assert discrete["2025Q2"] == 80
    companyfacts = {
        "facts": {
            "us-gaap": {
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            _cash_fact("2025-01-01", "2025-03-31", 10),
                            _cash_fact("2025-01-01", "2025-06-30", 40),
                            _cash_fact("2025-01-01", "2025-09-30", 70),
                            _cash_fact("2025-01-01", "2025-12-31", 100, form="10-K"),
                        ]
                    }
                }
            }
        }
    }
    rows, _flags = ir.companyfacts_observations(
        companyfacts,
        ir.load_catalog("TSLA"),
        ticker="TSLA",
        source_url="https://data.sec.gov/x",
        source_doc_hash="c" * 64,
        extracted_at=EXTRACTED,
    )
    capex = {row["fiscal_period"]: row["value"] for row in rows if row["metric_id"] == "capex"}
    assert capex["2025Q1"] == 10
    assert capex["2025Q2"] == 30
    assert capex["2025Q3"] == 30
    assert capex["2025Q4"] == 30


def test_xbrl_period_uses_fiscal_year_and_tag_gaps():
    nvidia_quarter = ir.parse_xbrl_quarters(
        [
            _cash_fact("2026-04-27", "2026-07-26", 96, fp="Q2", fy=2027),
        ],
        fiscal_end_month=1,
    )
    assert nvidia_quarter == {"2027Q2": 96}
    assert ir._fiscal_period(date(2026, 7, 26), 12) == "2026Q3"

    def observe(gaap, ticker):
        rows, _flags = ir.companyfacts_observations(
            {"facts": {"us-gaap": gaap}},
            ir.load_catalog(ticker),
            ticker=ticker,
            source_url="https://data.sec.gov/x",
            source_doc_hash="d" * 64,
            extracted_at=EXTRACTED,
        )
        return rows

    def usd(facts):
        return {"units": {"USD": facts}}

    qs_rows = observe({"OperatingIncomeLoss": usd([_cash_fact("2026-04-01", "2026-06-30", -20)])}, "QS")
    assert qs_rows and all(row["metric_id"] != "revenue_gaap" for row in qs_rows)
    goog = observe(
        {
            "Revenues": usd([_cash_fact("2026-04-01", "2026-06-30", 100)]),
            "CostOfRevenue": usd([_cash_fact("2026-04-01", "2026-06-30", 40)]),
        },
        "GOOG",
    )
    assert _value(goog, "gross_profit", "2026Q2") == 60
    assert _value(goog, "revenue_gaap", "2026Q2") == 100
    meta = observe(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": usd([_cash_fact("2026-04-01", "2026-06-30", 80)]),
            "CostOfRevenue": usd([_cash_fact("2026-04-01", "2026-06-30", 15)]),
        },
        "META",
    )
    assert _value(meta, "gross_profit", "2026Q2") == 65
    amzn = observe(
        {
            "GrossProfit": usd([_cash_fact("2009-04-01", "2009-06-30", 5)]),
            "RevenueFromContractWithCustomerExcludingAssessedTax": usd([_cash_fact("2026-04-01", "2026-06-30", 200)]),
            "CostOfRevenue": usd([_cash_fact("2026-04-01", "2026-06-30", 110)]),
        },
        "AMZN",
    )
    assert _value(amzn, "gross_profit", "2026Q2") == 90
    assert not any(row["metric_id"] == "gross_profit" and row["fiscal_period"].startswith("2009") for row in amzn)
    assert all(row["metric_id"] != "research_and_development" for row in amzn)


def _tagged_fact(start, end, value, accn, filed, form="10-Q", fp=None, fy=None):
    fact = _cash_fact(start, end, value, form=form, fp=fp, fy=fy)
    fact["accn"] = accn
    fact["filed"] = filed
    return fact


def _xbrl_rows(gaap, ticker, fiscal_end_month=None):
    kwargs = {
        "ticker": ticker,
        "source_url": "https://data.sec.gov/x",
        "source_doc_hash": "e" * 64,
        "extracted_at": EXTRACTED,
    }
    if fiscal_end_month is not None:
        kwargs["fiscal_end_month"] = fiscal_end_month
    rows, _flags = ir.companyfacts_observations({"facts": {"us-gaap": gaap}}, ir.load_catalog(ticker), **kwargs)
    return rows


def test_tesla_deck_keeps_balance_sheet_rows_and_positive_capex():
    assert ir._parse_cell("(1)") == (None, False)
    assert ir._parse_cell("(29)") == (-29.0, False)
    assert ir.quarter_token("4Q-2022") == "2022Q4"
    assert ir.quarter_token("2026Q2|2026-06-30") == "2026Q2"
    parsed = ir.parse_company_document(
        _real("tsla_q2_2026_deck.htm"),
        ir.load_catalog("TSLA"),
        **_meta(source_kind="deck", fiscal_period="2026Q2", published_date="2026-07-22"),
    )
    winners, _superseded = ir.dedupe_observations(parsed["rows"])
    assert _value(winners, "capex", "2026Q2") == pytest.approx(5_789_000_000)
    assert _value(winners, "inventory", "2026Q2") == pytest.approx(13_752_000_000)
    assert _value(winners, "deferred_revenue", "2026Q2") == pytest.approx(3_427_000_000)
    assert _value(winners, "days_sales_outstanding", "2026Q2") == 13
    assert _value(winners, "days_payable_outstanding", "2026Q2") == 58
    assert _value(winners, "free_cash_flow", "2026Q2") == pytest.approx(-1_092_000_000)


def test_release_headings_pick_the_earliest_period_and_stay_unclassified():
    update = "Q4 and FY 2025 Update\nLater mention of Q1 2026."
    assert ir.classify_release("Document", update) == "deck"
    assert ir.fiscal_period_from_text(update) == "2025Q4"
    assert ir.fiscal_period_from_text("Q1 2026 results ahead of the Q4 and FY 2025 Update") == "2026Q1"
    assert ir.fiscal_period_from_text("Results for the second quarter of fiscal year 2027") == "2027Q2"
    assert ir.fiscal_period_from_text("second quarter FY2027") == "2027Q2"
    assert ir.fiscal_period_from_text("Apple reports fiscal 2026 fourth quarter results") == "2026Q4"
    assert ir.classify_release("Document", "The company entered into a lease.") == "other"


def test_week_year_end_in_the_first_week_belongs_to_the_prior_month():
    assert ir.fiscal_period_for_end(date(2027, 1, 2), 12) == "2026Q4"
    assert ir.fiscal_period_for_end(date(2027, 1, 8), 12) == "2027Q1"


def test_ytd_difference_stays_inside_one_filing_and_follows_a_year_end_change():
    first = [
        _tagged_fact("2025-01-01", "2025-03-31", 100, "filing-a", "2025-04-23"),
        _tagged_fact("2025-01-01", "2025-06-30", 250, "filing-a", "2025-07-23"),
        _tagged_fact("2025-01-01", "2025-06-30", 300, "filing-b", "2025-10-23"),
    ]
    assert ir.discrete_cashflow_quarters(first)["2025Q2"] == 150
    restated = [
        *first[:2],
        _tagged_fact("2025-01-01", "2025-03-31", 120, "filing-b", "2025-10-23"),
        _tagged_fact("2025-01-01", "2025-06-30", 300, "filing-b", "2025-10-23"),
    ]
    series = ir.discrete_cashflow_quarters(restated)
    assert series["2025Q1"] == 120
    assert series["2025Q2"] == 180
    # A December year, filed while the catalog month is January, still uses the filing's own year end.
    december_year = [
        _tagged_fact("2024-01-01", "2024-03-31", 10, "fy2024", "2025-02-20"),
        _tagged_fact("2024-01-01", "2024-06-30", 30, "fy2024", "2025-02-20"),
        _tagged_fact("2024-01-01", "2024-12-31", 60, "fy2024", "2025-02-20", form="10-K", fp="FY", fy=2024),
    ]
    shifted = ir.discrete_cashflow_quarters(december_year, fiscal_end_month=1)
    assert shifted["2024Q1"] == 10
    assert shifted["2024Q2"] == 20
    assert "2025Q1" not in shifted
    assert "2025Q2" not in shifted


def test_comparatives_and_restatements_use_the_fact_end_not_the_filing_label():
    facts = [
        _tagged_fact("2026-04-01", "2026-06-30", 130, "q2-2026", "2026-07-23", fp="Q2", fy=2026),
        _tagged_fact("2025-04-01", "2025-06-30", 100, "q2-2026", "2026-07-23", fp="Q2", fy=2026),
        _tagged_fact("2026-04-01", "2026-06-30", 128, "10k-2026", "2027-02-02", form="10-K", fp="FY", fy=2026),
        _tagged_fact("2026-01-01", "2026-12-31", 500, "10k-2026", "2027-02-02", form="10-K", fp="FY", fy=2026),
    ]
    rows = _xbrl_rows({"Revenues": {"units": {"USD": facts}}}, "TSLA")
    assert _value(rows, "revenue_gaap", "2026Q2") == 128
    assert _value(rows, "revenue_gaap", "2025Q2") == 100
    current = next(row for row in rows if row["metric_id"] == "revenue_gaap" and row["fiscal_period"] == "2026Q2")
    assert current["period_end"] == date(2026, 6, 30)


def test_period_end_comes_from_the_fact_the_release_or_the_fiscal_calendar():
    nvidia = _tagged_fact("2026-04-27", "2026-07-26", 96_221_000_000, "nvda-q2", "2026-08-26", fp="Q2", fy=2027)
    rows = _xbrl_rows({"Revenues": {"units": {"USD": [nvidia]}}}, "NVDA")
    quarter = next(row for row in rows if row["metric_id"] == "revenue_gaap" and row["fiscal_period"] == "2027Q2")
    assert quarter["period_end"] == date(2026, 7, 26)
    assert ir.period_end_for("2027Q2", 1) == date(2026, 7, 31)
    assert ir.period_end_for("2026Q2") == date(2026, 6, 30)
    release = _real("nvda_q2_fy27_press_release.htm")
    assert ir.period_ends_from_text(release, 1)["2027Q2"] == date(2026, 7, 26)
    synthetic = """
    NVIDIA Announces Financial Results for Second Quarter Fiscal 2027
    Revenue for the quarter ended July 26, 2026.
    <table>
    <tr><td>($ in millions)</td><td>Q2 FY26</td><td>Q2 FY27</td></tr>
    <tr><td>Revenue</td><td>30,040</td><td>96,221</td></tr>
    </table>
    """
    parsed = ir.parse_company_document(
        synthetic,
        ir.load_catalog("NVDA"),
        **_meta(ticker="NVDA", fiscal_end_month=1, fiscal_period="2026Q3"),
    )
    assert _value(parsed["rows"], "revenue_gaap", "2027Q2") == pytest.approx(96_221_000_000)
    ended = next(
        row for row in parsed["rows"] if row["metric_id"] == "revenue_gaap" and row["fiscal_period"] == "2027Q2"
    )
    assert ended["period_end"] == date(2026, 7, 26)


def test_capex_is_stored_as_positive_spend_for_the_deck_and_xbrl():
    deck = """
    ($ in millions) | Q1-2026 | Q2-2026
    Capital expenditures | (1,000) | (5,789)
    """
    parsed = ir.parse_company_document(
        deck,
        ir.load_catalog("TSLA"),
        **_meta(source_kind="deck", fiscal_period="2026Q2"),
    )
    facts = [
        _cash_fact("2026-01-01", "2026-03-31", 1_000_000_000),
        _cash_fact("2026-01-01", "2026-06-30", 6_789_000_000),
    ]
    xbrl = _xbrl_rows(
        {"PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": facts}}},
        "TSLA",
    )
    deck_capex = _value(parsed["rows"], "capex", "2026Q2")
    xbrl_capex = _value(xbrl, "capex", "2026Q2")
    assert deck_capex == pytest.approx(5_789_000_000)
    assert xbrl_capex == pytest.approx(5_789_000_000)
    assert ir.reconcile_xbrl(deck_capex, xbrl_capex)["status"] == "match"
    assert ir.reconcile_rows(parsed["rows"], ir.xbrl_value_map(xbrl)) == []
    negative = _xbrl_rows(
        {
            "PaymentsToAcquirePropertyPlantAndEquipment": {
                "units": {"USD": [_cash_fact("2026-04-01", "2026-06-30", -5_789_000_000)]}
            }
        },
        "TSLA",
    )
    assert _value(negative, "capex", "2026Q2") == pytest.approx(5_789_000_000)


def test_collect_stops_on_the_time_budget_without_fetching_submission_text(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "investor-dashboard (contact: test@example.com)")
    calls = []

    def fetch(url, headers):
        calls.append(url)
        return _Response(200, "{}")

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
        budget_s=0,
    )
    assert result["status"] == "partial"
    assert any(reason.get("reason") == "time_budget" for reason in result["reasons"])
    assert calls == []
    assert not any(str(reason.get("url") or "").endswith(".txt") for reason in result["reasons"])
