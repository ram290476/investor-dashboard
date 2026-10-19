"""Deep-dive catalog, NHTSA aggregation, manual gating, and the forecast placeholder."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

import company_ir as ir
import deep_dive as dd
import deep_dive_sources as src

SGO_HEADER = (
    "Report ID,Report Version,Reporting Entity,Report Month,Report Year,Crash With,"
    "SV Contact Area - Rear,SV Contact Area - Left,SV Contact Area - Front,SV Pre-Crash Movement\n"
)


def _csv(*rows: str) -> str:
    return SGO_HEADER + "\n".join(rows) + "\n"


def test_packaged_catalog_deep_dive_is_valid_and_metrics_stay_proposed():
    catalog = ir.load_catalog("TSLA")
    assert ir.validate_catalog(catalog) == []
    assert catalog["deep_dive"]["sections"]
    assert all(item["status"] == "proposed" for item in catalog["metrics"])
    assert all(item.get("approved_by") in (None, "") for item in catalog["metrics"])
    nvda = ir.load_catalog("NVDA")
    assert "deep_dive" not in nvda
    assert dd.assemble_deep_dive(nvda) is None
    source = Path(dd.__file__).read_text()
    assert "TSLA" not in source
    assert '== "TSLA"' not in source
    assert "SPCX" not in source
    assert "SpaceX" not in source


def test_spcx_deep_dive_is_catalog_data_and_stays_proposed():
    catalog = ir.load_catalog("SPCX")
    assert ir.validate_catalog(catalog) == []
    assert all(item["status"] == "proposed" for item in catalog["metrics"])
    notes = " ".join(catalog["deep_dive"]["name_notes"])
    assert "Terraform" in notes and "unconfirmed" in notes
    assert "Starmind" in notes and "Starshield" in notes
    document = dd.assemble_deep_dive(catalog, [], generated_at="2026-10-19T00:00:00Z")
    assert document["model_status"] == "placeholder"
    assert document["forecast"]["model_id"] == "seasonal_naive_v0"
    assert document["forecast"]["consensus"] is None
    ids = [section["id"] for section in document["sections"]]
    for required in (
        "scoreboard", "forecast", "launches", "projected-launches", "terraform",
        "factories", "starship", "starlink", "starmind", "ai-deals", "revenue",
    ):
        assert required in ids
    forecast = document["forecast"]["segments"]
    assert [segment["id"] for segment in forecast] == [
        "launch", "starlink", "starmind", "starship", "ai", "total",
    ]
    total = forecast[-1]
    assert all(point.get("band_low") is None for metric in total["metrics"] for point in metric["forecast"])


def test_optional_section_headline_passes_through():
    catalog = ir.load_catalog("TSLA")
    headlines = {section["id"]: section.get("headline") for section in catalog["deep_dive"]["sections"]}
    assert headlines["robotaxi"] == "robotaxi_fleet"
    assert headlines["forecast"] == "revenue_gaap"
    assert headlines["scoreboard"] == "total_deliveries"
    document = dd.assemble_deep_dive(catalog, [])
    assert document["sections"][0]["headline"] == "total_deliveries"
    spcx = ir.load_catalog("SPCX")
    spcx_headlines = {section["id"]: section.get("headline") for section in spcx["deep_dive"]["sections"]}
    assert spcx_headlines["launches"] == "total_launches"
    assert spcx_headlines["starlink"] == "starlink_subscribers"
    errors = dd.validate_deep_dive({
        "sections": [{
            "id": "a",
            "title": "A",
            "headline": "Not A Metric",
            "panels": [{"id": "p", "kind": "table", "title": "P"}],
        }],
    })
    assert any("headline" in error for error in errors)
    assert dd.validate_deep_dive({
        "sections": [{
            "id": "a",
            "title": "A",
            "panels": [{"id": "p", "kind": "table", "title": "P"}],
        }],
    }) == []


def test_sgo_parser_dedupes_versions_and_ignores_other_entities():
    text = _csv(
        "1,1,Example Motors,8,2026,Passenger Car,Y,,,,Stopped",
        "1,3,Example Motors,8,2026,Pedestrian,,,,,",
        "2,1,Other Automaker,8,2026,Passenger Car,Y,,,,Stopped",
        "3,1,Example Motors,9,2026,Pole / Tree,,,,,",
    )
    parsed = dd.parse_sgo_csv(text, reporting_entity="Example", mode="ads")
    assert parsed["matched"] == 2
    assert parsed["counts"] == {"2026-08": 1, "2026-09": 1}
    assert parsed["by_kind"]["2026-08"] == {"vulnerable_road_user": 1}
    assert parsed["by_kind"]["2026-09"] == {"fixed_object": 1}
    assert "VIN" not in json.dumps(parsed)


def test_crash_kinds_use_structured_fields_only():
    assert dd.classify_crash({"Crash With": "Bicycle"}) == "vulnerable_road_user"
    assert dd.classify_crash({"Crash With": "Passenger Car", "SV Contact Area - Rear": "Y"}) == "rear_contact"
    assert dd.classify_crash({"Crash With": "Passenger Car", "SV Contact Area - Left": "Y"}) == "side_contact"
    assert dd.classify_crash({"SV Pre-Crash Movement": "Turning left"}) == "intersection"
    assert dd.classify_crash({"Crash With": "Other, see Narrative", "Narrative": "rear ended"}) == "other"


def test_manual_entries_cannot_be_approved_and_need_a_source():
    approved = dd.normalize_manual_entry({
        "metric_id": "robotaxi_fleet",
        "period": "2026Q2",
        "value": 10,
        "confidence": "low",
        "source_url": "https://www.sec.gov/Archives/edgar/data/example",
        "status": "approved",
        "approved_by": "someone",
        "dimensions": {"place": "Austin"},
    })
    assert approved["approval_state"] == "proposed"
    assert approved["approved"] is False
    assert dd.normalize_manual_entry({
        "metric_id": "robotaxi_fleet",
        "period": "2026Q2",
        "value": 10,
        "source_url": "http://insecure.example/note",
    }) is None
    series = dd.series_from_manual([approved])
    assert series[0]["source_kind"] == "manual"
    assert series[0]["points"][0]["dimensions"]["place"] == "Austin"


def test_forecast_is_a_labeled_placeholder_and_does_not_invent_bands():
    catalog = {
        "ticker": "EX",
        "metrics": [{
            "metric_id": "revenue_gaap",
            "display_name": "Revenue",
            "unit": "USD",
            "status": "proposed",
            "disclosure_status": "numeric",
        }],
        "deep_dive": {
            "sections": [{"id": "forecast", "title": "Forecast", "panels": [
                {"id": "forecast", "kind": "forecast-band", "title": "Forecast"},
            ]}],
            "forecast": {"segments": [
                {"id": "total", "label": "Total", "revenue_metric": "revenue_gaap", "eps_metric": "eps_diluted"},
            ]},
        },
    }
    rows = [
        {"metric_id": "revenue_gaap", "fiscal_period": "2025Q3", "value": 20},
        {"metric_id": "revenue_gaap", "fiscal_period": "2026Q2", "value": 28},
    ]
    document = dd.assemble_deep_dive(catalog, rows, generated_at="2026-10-19T00:00:00Z")
    forecast = document["forecast"]
    assert forecast["model_status"] == "placeholder"
    assert "not a real forecast model" in forecast["disclaimer"]
    assert forecast["consensus"] is None
    revenue = forecast["segments"][0]["metrics"][0]
    assert revenue["actuals"][-1]["approval_state"] == "proposed"
    by_period = {point["period"]: point for point in revenue["forecast"]}
    assert by_period["2026Q3"]["value"] == 20
    assert by_period["2026Q3"]["baseline_period"] == "2025Q3"
    assert by_period["2026Q3"]["band_low"] is None
    assert by_period["2026Q3"]["confidence"] == "placeholder"
    assert by_period["2027Q1"]["reported"] is False
    earnings = forecast["segments"][0]["metrics"][1]
    assert earnings["empty_label"] == "Not yet disclosed"
    assert document["sections"][0]["panels"][0]["forecast"]["model_id"] == "seasonal_naive_v0"


def test_empty_panels_say_not_disclosed_and_do_not_zero_fill():
    catalog = ir.load_catalog("TSLA")
    document = dd.assemble_deep_dive(catalog, [], generated_at="2026-10-19T00:00:00Z")
    scoreboard = document["sections"][0]["panels"][0]
    fleet = next(tile for tile in scoreboard["kpis"] if tile["metric_id"] == "robotaxi_fleet")
    assert fleet["value"] is None
    assert fleet["empty_label"] == "Not yet disclosed"
    assert fleet["sparkline"] == []
    nhtsa = next(tile for tile in scoreboard["kpis"] if tile["metric_id"] == "nhtsa_sgo_ads")
    assert nhtsa["empty_label"] == "Not yet collected"


def test_collect_writes_proposed_counts_and_serve_publishes_them():
    text = _csv(
        "9,1,Example Motors,7,2026,Passenger Car,Y,,,,Stopped",
        "9,2,Example Motors,7,2026,Passenger Car,,,Y,,Stopped",
    )
    catalog = {
        "ticker": "EX",
        "metrics": [],
        "deep_dive": {
            "sections": [{"id": "reports", "title": "Reports", "panels": [
                {"id": "nhtsa", "kind": "trend-bars", "title": "Reports", "modes": [
                    {"id": "ads", "label": "ADS", "metric_id": "sgo_ads"},
                ]},
            ]}],
            "sources": [{
                "id": "nhtsa_sgo",
                "reporting_entity": "Example",
                "outputs": {"ads": "sgo_ads"},
                "series_breaks": [{"period": "2025-06", "note": "Thresholds changed."}],
            }],
        },
    }
    store = {}

    def fetch(url: str) -> str:
        assert url.startswith("https://static.nhtsa.gov/")
        return text if url.endswith("ADS.csv") else ""

    result = dd.run_collect(
        {},
        fetch=fetch,
        read_json=lambda key: store.get(key),
        write_json=lambda key, obj: store.__setitem__(key, obj),
        tickers=["EX"],
        load_catalog=lambda ticker: catalog if ticker == "EX" else {},
        now="2026-10-19T12:00:00Z",
    )
    assert result["tickers"] == ["EX"]
    assert result["run_status"] == "ok"
    saved = store[dd.observations_key("EX")]
    series = saved["series"][0]
    assert series["approval_state"] == "proposed"
    assert series["approved"] is False
    assert series["points"] == [{"period": "2026-07", "value": 1, "reported": True}]
    assert series["breakdowns"][0]["kind"] == "front_contact"
    assert saved["forecast_model"]["model_status"] == "placeholder"
    mode = dd.assemble_deep_dive(catalog, [], saved)["sections"][0]["panels"][0]["modes"][0]
    assert mode["series"][0]["points"][0]["value"] == 1
    assert mode["series"][0]["approved"] is False


def test_serve_attaches_the_catalog_deep_dive_and_leaves_other_tickers_alone():
    store = {}

    def read_json(key):
        return store.get(key)

    def write_json(key, obj):
        store[key] = obj

    served = ir.run_serve(
        {"tickers": ["TSLA", "NVDA"], "run_status": "ok"},
        read_json=read_json,
        write_json=write_json,
        now="2026-10-19T12:00:00Z",
    )
    assert served["tickers"] == ["TSLA", "NVDA"]
    tesla = store[ir.serving_stock_key("TSLA")]
    nvidia = store[ir.serving_stock_key("NVDA")]
    assert tesla["deep_dive"]["title"] == "Deep dive"
    assert tesla["deep_dive"]["model_status"] == "placeholder"
    assert "deep_dive" not in nvidia
    public = ir.public_stock_document(tesla, "TSLA")
    assert public["deep_dive"]["sections"]


def test_company_metric_keeps_its_review_state_inside_the_deep_dive():
    catalog = ir.load_catalog("TSLA")
    rows = [{
        "metric_id": "total_deliveries",
        "fiscal_period": "2026Q3",
        "value": 486532,
        "unit": "vehicles",
        "source_url": "https://www.sec.gov/Archives/edgar/data/fixture",
        "confidence": 0.95,
    }]
    reviewed = ir.set_catalog_status(catalog, "total_deliveries", "approved", "ram", "2026-10-09T00:00:00Z")
    document = dd.assemble_deep_dive(reviewed, rows)
    tile = next(
        item for item in document["sections"][0]["panels"][0]["kpis"] if item["metric_id"] == "total_deliveries"
    )
    assert tile["value"] == 486532
    assert tile["approved"] is True
    assert tile["approval_state"] == "approved"
    pending = dd.assemble_deep_dive(catalog, rows)
    pending_tile = next(
        item for item in pending["sections"][0]["panels"][0]["kpis"] if item["metric_id"] == "total_deliveries"
    )
    assert pending_tile["approved"] is False
    assert pending_tile["approval_state"] == "proposed"


def test_api_passes_the_deep_dive_block_through():
    import prefs_api

    published = prefs_api._public_stock({
        "metrics": [],
        "deep_dive": {"title": "Deep dive", "sections": [{"id": "scoreboard", "panels": []}]},
    }, "EX")
    assert published["deep_dive"]["title"] == "Deep dive"
    assert "deep_dive" not in prefs_api._public_stock({"metrics": []}, "EX")


def test_collect_with_no_deep_dive_catalog_does_not_write():
    result = dd.run_collect(
        {},
        fetch=lambda url: "unused",
        read_json=lambda key: None,
        write_json=lambda key, obj: (_ for _ in ()).throw(AssertionError(key)),
        tickers=["NVDA"],
        load_catalog=lambda ticker: {"ticker": ticker, "metrics": []},
        now="2026-10-19T12:00:00Z",
    )
    assert result["tickers"] == []


def _launch(net, name, vehicle, mission, orbit, outcome, *, reused=None, flight=None):
    rocket = {"configuration": {"full_name": vehicle}}
    if reused is not None:
        rocket["launcher_stage"] = [{"reused": reused, "launcher_flight_number": flight}]
    return {
        "name": name,
        "net": net,
        "url": f"https://ll.thespacedevs.com/2.3.0/launches/{name.split()[0].lower()}/",
        "rocket": rocket,
        "mission": {"name": mission, "orbit": {"name": orbit}, "agencies": [{"name": "Customer"}]},
        "status": {"name": outcome},
    }


def _launch_source():
    return {
        "id": "launch_library",
        "lsp_name": "Example Launch",
        "base_url": "https://ll.thespacedevs.com/2.3.0",
        "page_limit": 2,
        "max_pages": 2,
        "schedule_limit": 5,
        "total_metric": "total_launches",
        "projected_metric": "launch_count_projected",
        "schedule_metric": "launches_schedule",
        "vehicles": [
            {"id": "heavy", "metric_id": "launch_heavy", "terms": ["heavy"]},
            {"id": "ship", "metric_id": "launch_ship", "flights_metric": "ship_flights", "terms": ["ship"]},
            {"id": "nine", "metric_id": "launch_nine", "terms": ["nine"]},
        ],
    }


def _previous_launches():
    return [
        _launch(
            "2026-01-15T00:00:00Z", "Heavy One", "Example Heavy", "One", "LEO", "Launch Successful",
            reused=True, flight=4,
        ),
        _launch("2026-02-15T00:00:00Z", "Ship Two", "Example Ship", "Two", "LEO", "Launch Successful"),
        _launch(
            "2026-04-02T00:00:00Z", "Nine Three", "Example Nine", "Three", "SSO", "Launch Successful",
            reused=False, flight=1,
        ),
    ]


def test_launch_library_pages_are_keyless_and_stay_proposed():
    source = _launch_source()
    url = src.launch_page_url(source, "previous", 0)
    query = url.split("?", 1)[1]
    assert "mode=normal" in query
    assert "lsp__name=Example+Launch" in query
    assert "api_key" not in url and "token=" not in url and "&key=" not in query
    previous = _previous_launches()
    upcoming = [
        _launch("2026-11-01T00:00:00Z", "Ship Next", "Example Ship", "Next", "LEO", "Go for Launch"),
    ]
    pages = {
        src.launch_page_url(source, "previous", 0): {"results": previous[:2]},
        src.launch_page_url(source, "previous", 2): {"results": previous[2:]},
        src.launch_page_url(source, "upcoming", 0): {"results": upcoming},
    }
    fetched = []

    def fetch(page_url: str) -> str:
        fetched.append(page_url)
        return json.dumps(pages[page_url])

    series, status = src.collect_launches(source, fetch)
    assert status["status"] == "ok"
    assert all("api_key" not in page and "mode=normal" in page for page in fetched)
    by_id = {item["metric_id"]: item for item in series}
    assert by_id["launch_heavy"]["approved"] is False
    assert by_id["launch_heavy"]["points"] == [{"period": "2026Q1", "value": 1, "reported": True}]
    assert by_id["ship_flights"]["points"] == [{"period": "2026Q1", "value": 1, "reported": True}]
    assert by_id["launch_nine"]["points"] == [{"period": "2026Q2", "value": 1, "reported": True}]
    assert by_id["total_launches"]["points"] == [
        {"period": "2026Q1", "value": 2, "reported": True},
        {"period": "2026Q2", "value": 1, "reported": True},
    ]
    assert by_id["launch_count_projected"]["points"] == [{"period": "2026Q4", "value": 1, "reported": True}]
    assert "forecast model" in by_id["launch_count_projected"]["notes"][0]
    schedule = by_id["launches_schedule"]["points"]
    boosters = {point["dimensions"]["mission"]: point["dimensions"]["booster"] for point in schedule}
    assert boosters["One — Customer"] == "Reused (flight 4)"
    assert boosters["Two — Customer"] == ""
    assert boosters["Three — Customer"] == "New"


def test_a_failed_launch_page_keeps_the_rows_already_read():
    source = _launch_source()
    first = src.launch_page_url(source, "previous", 0)

    def fetch(page_url: str) -> str:
        if page_url == first:
            return json.dumps({"results": _previous_launches()[:2]})
        raise httpx.ConnectError("down")

    series, status = src.collect_launches(source, fetch)
    assert status["status"] == "partial"
    by_id = {item["metric_id"]: item for item in series}
    assert by_id["total_launches"]["points"][0]["value"] == 2
    assert "launch_count_projected" not in by_id


def test_constellation_count_merges_quarters_and_replaces_the_current_one():
    source = {
        "id": "celestrak",
        "url": "https://celestrak.org/NORAD/elements/gp.php?GROUP=example&FORMAT=json",
        "metric_id": "constellation",
        "unit": "satellites",
    }
    previous = {
        "series": [{
            "metric_id": "constellation",
            "source_kind": "celestrak",
            "points": [
                {"period": "2026Q2", "value": 2, "reported": True},
                {"period": "2026Q4", "value": 9, "reported": True},
            ],
        }],
    }
    payload = [{"OBJECT_NAME": "A"}, {"OBJECT_NAME": "B"}, {"OBJECT_NAME": "C"}]
    series, status = src.collect_constellation(
        source, lambda url: json.dumps(payload), "2026-10-19T00:00:00Z", previous,
    )
    assert status["status"] == "ok"
    assert status["rows"] == 3
    assert series[0]["approved"] is False
    assert series[0]["confidence"] == "high"
    assert series[0]["points"] == [
        {"period": "2026Q2", "value": 2, "reported": True},
        {"period": "2026Q4", "value": 3, "reported": True},
    ]
    assert src.parse_constellation_count({"satellites": payload}) == 3


def _estimate_rules():
    return [
        {"metric_id": "orders", "unit": "vehicles", "match": ["hauler"], "quantity": ["order"], "aggregate": "sum"},
        {"metric_id": "fleet", "unit": "vehicles", "match": ["cab"], "quantity": ["fleet"], "aggregate": "max"},
        {
            "metric_id": "storage",
            "unit": "gwh",
            "match": ["pack"],
            "quantity": ["deployed"],
            "units": ["gwh", "mwh"],
        },
        {
            "metric_id": "by_country",
            "kind": "place",
            "unit": "gwh",
            "match": ["deployed"],
            "quantity": ["deployed"],
            "units": ["gwh", "mwh"],
        },
        {
            "metric_id": "miles",
            "kind": "milestone",
            "milestones": [{"id": "pilot-line", "terms": ["pilot line"]}],
        },
        {
            "metric_id": "sites",
            "kind": "site",
            "milestones": [{"id": "construction", "label": "Construction", "terms": ["construction"]}],
        },
        {
            "metric_id": "deals",
            "kind": "deal",
            "match": ["intelligence"],
            "require": ["partnership"],
            "segments": [{"id": "compute", "metric_id": "deals_compute", "terms": ["compute"]}],
        },
    ]


def _article(url, title, published, **extra):
    return {"url": url, "title": title, "published_at": published, "tickers": ["EX"], **extra}


def test_news_estimates_stay_pending_and_do_not_replace_a_reported_figure():
    assert src.contains_term("semiconductor order", "semi", boundary=True) is False
    assert src.contains_term("an ai partnership", "ai", boundary=True) is True
    assert src.contains_term("xai satellite", "ai", boundary=True) is False
    articles = [
        _article(
            "https://news.example/order",
            "Hauler order of 120 vehicles in 2024, not 15%",
            "2026-08-02T00:00:00Z",
            approved=True,
        ),
        _article("http://news.example/insecure", "Hauler order of 999 vehicles", "2026-08-02T00:00:00Z"),
        _article("https://news.example/order", "Hauler order of 7 vehicles", "2026-08-02T00:00:00Z"),
        _article("https://news.example/fleet-a", "Cab fleet of 30 vehicles", "2026-08-03T00:00:00Z"),
        _article("https://news.example/fleet-b", "Cab fleet of 40 vehicles", "2026-08-04T00:00:00Z"),
        _article("https://news.example/pack", "Pack deployed 2.5 GWh", "2026-05-01T00:00:00Z"),
        _article("https://news.example/place", "Storage deployed 500 MWh in Australia", "2026-05-02T00:00:00Z"),
        _article("https://news.example/mile", "Pilot line started", "2026-09-01T00:00:00Z"),
        _article("https://news.example/site", "Construction of the Boca Plant is underway", "2026-06-01T00:00:00Z"),
        _article(
            "https://news.example/deal",
            "Intelligence partnership with Example Labs for compute worth $2 billion",
            "2026-07-01T00:00:00Z",
        ),
        _article("https://news.example/xai", "An xai note without a partnership", "2026-07-02T00:00:00Z"),
    ]
    found = {item["metric_id"]: item for item in src.estimates_from_articles(articles, _estimate_rules())}
    orders = found["orders"]
    assert orders["approved"] is False
    assert orders["approval_state"] == "proposed"
    assert orders["estimate"] is True
    assert orders["confidence"] == "low"
    assert orders["points"][0]["value"] == 120
    assert orders["points"][0]["source_url"] == "https://news.example/order"
    assert found["fleet"]["points"][0]["value"] == 40
    assert found["fleet"]["confidence"] in {"low", "medium"}
    assert found["storage"]["points"][0]["value"] == 2.5
    assert found["storage"]["confidence"] == "medium"
    assert found["storage"]["confidence"] != "high"
    place = found["by_country"]["points"][0]
    assert place["value"] == 0.5
    assert place["dimensions"]["country"] == "Australia"
    assert found["miles"]["points"][0]["dimensions"]["milestone_id"] == "pilot-line"
    assert found["miles"]["approved"] is False
    assert found["sites"]["points"][0]["dimensions"]["site"] == "Boca"
    deal = found["deals"]["points"][0]["dimensions"]
    assert deal["partner"] == "Example Labs"
    assert deal["size"] == "$2 billion"
    assert found["deals_compute"]["points"][0]["value"] == 1
    assert all(item["approved"] is False and item["confidence"] != "high" for item in found.values())

    catalog = {
        "ticker": "EX",
        "metrics": [{
            "metric_id": "orders",
            "display_name": "Orders",
            "unit": "vehicles",
            "status": "approved",
            "disclosure_status": "numeric",
        }],
        "deep_dive": {"sections": [{"id": "score", "title": "Score", "panels": [
            {
                "id": "tiles",
                "kind": "scoreboard",
                "title": "Score",
                "kpis": [{"metric_id": "orders", "label": "Orders"}],
            },
            {"id": "chart", "kind": "trend-bars", "title": "Chart", "series": [
                {"metric_id": "orders", "label": "Orders", "role": "bar"},
            ]},
        ]}]},
    }
    rows = [{"metric_id": "orders", "fiscal_period": "2026Q3", "value": 5, "unit": "vehicles"}]
    document = dd.assemble_deep_dive(catalog, rows, {"series": [orders]})
    tile = document["sections"][0]["panels"][0]["kpis"][0]
    assert tile["value"] == 5
    assert tile["approved"] is True
    assert tile["estimate_value"] == 120
    assert tile.get("estimate_only") is not True
    chart = document["sections"][0]["panels"][1]["series"]
    assert chart[0]["role"] == "bar"
    assert chart[0]["approved"] is True
    assert chart[1]["role"] == "line-dashed"
    assert chart[1]["estimate"] is True
    assert chart[1]["approved"] is False
    assert chart[1]["label"] == "Orders estimate"


def test_energy_topics_tag_only_the_catalog_ticker():
    block = {"sources": [
        {
            "id": "news_topics",
            "metric_id": "energy_news",
            "window_days": 90,
            "topics": [{"id": "pack", "label": "Pack", "terms": ["megapack"]}],
        },
        {"id": "news_estimates", "rules": _estimate_rules()[:1]},
    ]}
    rows = [
        _article(
            "https://news.example/pack-ex", "Megapack installed", "2026-09-01T00:00:00Z",
            sentiment_score=0.5, topics=["launch"],
        ),
        {
            "url": "https://news.example/other",
            "title": "Megapack elsewhere",
            "published_at": "2026-09-01T00:00:00Z",
            "tickers": ["OTHER"],
            "topics": [],
        },
    ]
    catalogs = {"EX": {"deep_dive": block}, "OTHER": {"metrics": []}}
    tagged = src.tag_catalog_topics(rows, src.topic_rules_for(catalogs))
    assert tagged[0]["topics"] == ["launch", "energy:pack"]
    assert tagged[1]["topics"] == []
    rollup = src.build_news_rollup([tagged[0]], block, "2026-10-19T00:00:00Z")
    point = rollup["topics"][0]["points"][0]
    assert point["value"] == 1
    assert point["dimensions"]["topic"] == "Pack"
    assert point["dimensions"]["sentiment"] == "0.50"
    assert rollup["topics"][0]["estimate"] is False
    assert rollup["estimates"] == []
    written = {}
    saved = dd.write_news_rollups(
        ["EX", "OTHER"],
        tagged,
        lambda ticker: catalogs.get(ticker, {}),
        lambda key, obj: written.__setitem__(key, obj),
        "2026-10-19T00:00:00Z",
    )
    assert saved == ["EX"]
    assert set(written) == {"curated/news_topics/ticker=EX/topics.json"}
    fresh = dd.prepare_news_articles(rows, ["EX"], lambda ticker: catalogs.get(ticker, {}))
    assert "energy:pack" in fresh[0]["topics"]


def test_a_headline_locks_once_and_compares_after_the_actual():
    catalog = {
        "ticker": "EX",
        "metrics": [{"metric_id": "revenue", "display_name": "Revenue", "unit": "USD", "status": "proposed"}],
        "deep_dive": {
            "sections": [{"id": "forecast", "title": "Forecast", "panels": [
                {"id": "forecast", "kind": "forecast-band", "title": "Forecast"},
            ]}],
            "forecast": {"segments": [{"id": "total", "label": "Total", "revenue_metric": "revenue"}]},
        },
    }
    early = [
        {"metric_id": "revenue", "fiscal_period": "2025Q3", "value": 20},
        {"metric_id": "revenue", "fiscal_period": "2026Q2", "value": 28},
    ]
    first = dd.assemble_deep_dive(
        catalog, early, generated_at="2026-08-01T00:00:00Z", projections=[], lock_projections=True,
    )
    stored = first["forecast"]["stored_headlines"]
    assert stored == [{
        "segment_id": "total",
        "segment_label": "Total",
        "field": "revenue",
        "metric_id": "revenue",
        "period": "2026Q3",
        "value": 20,
        "unit": "USD",
        "stored_at": "2026-08-01T00:00:00Z",
        "model_id": "seasonal_naive_v0",
        "model_status": "placeholder",
        "approval_state": "proposed",
    }]
    assert first["forecast"]["prior_projection"] == []
    assert "stored when deep-dive collect runs" in first["forecast"]["prior_projection_note"]
    changed = dd.placeholder_forecast(catalog["deep_dive"]["forecast"], {
        "revenue": {"unit": "USD", "points": [
            {"period": "2025Q3", "value": 99, "reported": True},
            {"period": "2026Q2", "value": 28, "reported": True},
        ]},
    })
    kept = dd.lock_headlines(changed, stored, "2026-09-01T00:00:00Z")
    assert kept[0]["value"] == 20
    later_rows = [*early, {"metric_id": "revenue", "fiscal_period": "2026Q3", "value": 25}]
    second = dd.assemble_deep_dive(
        catalog, later_rows, generated_at="2026-11-01T00:00:00Z", projections=stored, lock_projections=True,
    )
    compared = second["forecast"]["prior_projection"]
    match = next(row for row in compared if row["period"] == "2026Q3")
    assert match["projected"] == 20
    assert match["actual"] == 25
    assert match["delta"] == 5
    assert any(row["period"] == "2026Q3" and row["value"] == 20 for row in second["forecast"]["stored_headlines"])


def _free_catalog():
    return {
        "ticker": "EX",
        "metrics": [{"metric_id": "revenue", "display_name": "Revenue", "unit": "USD", "status": "proposed"}],
        "deep_dive": {
            "sections": [{"id": "launches", "title": "Launches", "panels": [
                {"id": "counts", "kind": "trend-bars", "title": "Counts", "series": [
                    {"metric_id": "total_launches", "label": "Launches", "role": "bar"},
                ]},
            ]}],
            "forecast": {"segments": [{"id": "total", "label": "Total", "revenue_metric": "revenue"}]},
            "sources": [{**_launch_source(), "page_limit": 100, "max_pages": 1}, {
                "id": "celestrak",
                "url": "https://celestrak.org/example.json",
                "metric_id": "constellation",
                "unit": "satellites",
            }],
        },
    }


def test_collect_uses_free_sources_only_when_the_catalog_declares_them():
    store = {
        "curated/company_metrics/ticker=EX/observations.json": {"rows": [
            {"metric_id": "revenue", "fiscal_period": "2025Q3", "value": 20},
            {"metric_id": "revenue", "fiscal_period": "2026Q2", "value": 28},
        ]},
    }
    fetched = []

    def fetch(url: str) -> str:
        fetched.append(url)
        if "launches/upcoming" in url:
            raise httpx.ConnectError("down")
        if "launches/previous" in url and "offset=0" in url:
            return json.dumps({"results": _previous_launches()})
        if "launches/previous" in url:
            return json.dumps({"results": []})
        if "celestrak.org" in url:
            return json.dumps([{"OBJECT_NAME": "A"}, {"OBJECT_NAME": "B"}])
        raise AssertionError(url)

    result = dd.run_collect(
        {},
        fetch=fetch,
        read_json=lambda key: store.get(key),
        write_json=lambda key, obj: store.__setitem__(key, obj),
        tickers=["EX"],
        load_catalog=lambda ticker: _free_catalog() if ticker == "EX" else {},
        now="2026-10-19T12:00:00Z",
    )
    assert result["run_status"] == "ok"
    launch_status = next(item for item in result["sources"] if item["id"] == "launch_library")
    assert launch_status["status"] == "partial"
    assert all("nhtsa" not in url and "api_key" not in url for url in fetched)
    saved = store[dd.observations_key("EX")]
    kinds = {item["source_kind"] for item in saved["series"]}
    assert "launch_library" in kinds
    assert "celestrak" in kinds
    assert saved["stored_headlines"][0]["period"] == "2026Q3"
    assert saved["stored_headlines"][0]["value"] == 20
    quiet = dd.run_collect(
        {},
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        read_json=lambda key: None,
        write_json=lambda key, obj: store.__setitem__(key, obj),
        tickers=["EX"],
        load_catalog=lambda ticker: {
            "ticker": "EX",
            "metrics": [],
            "deep_dive": {"sections": [{"id": "only", "title": "Only", "panels": [
                {"id": "only", "kind": "table", "title": "Only"},
            ]}], "sources": [{"id": "news_estimates", "rules": []}]},
        },
        now="2026-10-19T12:00:00Z",
    )
    assert quiet["run_status"] == "ok"


def test_packaged_free_sources_validate_and_name_no_ticker_in_code():
    for ticker, source_id in (("TSLA", "news_topics"), ("SPCX", "launch_library")):
        catalog = ir.load_catalog(ticker)
        assert ir.validate_catalog(catalog) == []
        ids = [item["id"] for item in catalog["deep_dive"]["sources"]]
        assert source_id in ids
        assert "news_estimates" in ids
    notes = json.dumps(ir.load_catalog("TSLA")["deep_dive"]) + json.dumps(ir.load_catalog("SPCX")["deep_dive"])
    assert "launch-library key" not in notes
    assert "does not yet tag" not in notes
    for module in (dd, src):
        text = Path(module.__file__).read_text()
        assert "TSLA" not in text
        assert "SPCX" not in text
        assert "SpaceX" not in text
        assert "Tesla" not in text
    root = Path(__file__).resolve().parents[3]
    jobs = (root / "infra/terraform/variables.tf").read_text()
    news = jobs.split("news-sentiment = {", 1)[1].split("hourly-prices = {", 1)[0]
    assert '"curated/news_topics/"' in news.split("write_prefixes", 1)[1].split("api_keys", 1)[0]
    assert '"curated/"' not in news
    collect = jobs.split("deep-dive-collect = {", 1)[1].split("short-interest = {", 1)[0]
    reads = collect.split("read_prefixes", 1)[1].split("write_prefixes", 1)[0]
    writes = collect.split("write_prefixes", 1)[1].split("api_keys", 1)[0]
    for prefix in ("manual/deep_dive/", "curated/deep_dive/", "curated/company_metrics/", "curated/news_topics/"):
        assert f'"{prefix}"' in reads
    assert writes.strip().startswith("= [\"curated/deep_dive/\"]")
    assert "api_keys       = []" in collect
    serve = jobs.split("q2-company-ir-serve = {", 1)[1].split("deep-dive-collect = {", 1)[0]
    assert '"curated/news_topics/"' in serve.split("read_prefixes", 1)[1].split("write_prefixes", 1)[0]
    assert 'write_prefixes = ["serving/stock/"]' in serve
    role = (root / "infra/terraform/modules/jobs/main.tf").read_text()
    assert "permissions_boundary = var.permissions_boundary_arn" in role
    assert 'actions   = ["s3:GetObject"]' in role
    assert 'actions   = ["s3:PutObject"]' in role
    assert "vpc_config" not in role
    from http_client import ALLOWED_HOSTS

    assert "ll.thespacedevs.com" in ALLOWED_HOSTS
    assert "celestrak.org" in ALLOWED_HOSTS
