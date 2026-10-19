"""Deep-dive catalog, NHTSA aggregation, manual gating, and the forecast placeholder."""

from __future__ import annotations

import json
from pathlib import Path

import company_ir as ir
import deep_dive as dd

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
