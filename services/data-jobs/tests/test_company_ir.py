"""Fixture tests for company IR collection, extraction, catalog gating and serving JSON."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

import company_ir as ir

EXTRACTED = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
QUARTERS = ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]
REQUIRED_WITH_HISTORY = (
    "fsd_subscriptions",
    "tesla_semi",
    "robotaxi_fleet",
    "supercharger_stations",
    "supercharger_connectors",
    "energy_storage_deployed",
    "optimus",
    "production_model_3y",
    "deliveries_model_3y",
    "inventory_days_supply",
    "operating_cash_flow",
    "free_cash_flow",
    "capex",
)


def _deck(names):
    header = "Metric | " + " | ".join(QUARTERS)
    lines = [header]
    for index, name in enumerate(names, start=1):
        values = " | ".join(str(index * 10 + quarter) for quarter in range(1, 5))
        lines.append(f"{name} | {values}")
    return "\n".join(lines)


def _meta(ticker="TSLA"):
    return {
        "ticker": ticker,
        "source_url": "https://ir.tesla.com/fixture-update",
        "source_doc_hash": "a" * 64,
        "extracted_at": EXTRACTED,
        "published_date": "2026-01-29",
    }


def test_tesla_catalog_lists_required_metrics_as_proposed():
    catalog = ir.load_catalog("TSLA")
    ids = {item["metric_id"] for item in catalog["metrics"]}
    assert set(ir.REQUIRED_TESLA_METRICS) <= ids
    assert all(item["status"] == "proposed" for item in catalog["metrics"])
    assert all(item["approved_by"] is None for item in catalog["metrics"])


def test_fixture_deck_keeps_four_quarters_and_does_not_serve_them_until_approved():
    catalog = ir.load_catalog("TSLA")
    names = [ir.catalog_entry(catalog, metric_id)["aliases"][0] for metric_id in REQUIRED_WITH_HISTORY]
    rows = ir.parse_deck_text(_deck(names), catalog, **_meta())
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["metric_id"]] = counts.get(row["metric_id"], 0) + 1
        assert row["approved"] is False
        assert row["value"] != 0
    assert counts == {metric_id: 4 for metric_id in REQUIRED_WITH_HISTORY}
    payload = ir.build_serving(
        "TSLA",
        catalog,
        rows,
        run=ir.run_record(status="ok", sources={"fetched": 1}),
        generated_at="2026-10-08T12:00:00Z",
    )
    assert payload["metrics"] == []
    assert payload["discovered"] is False
    blob = ir.visible_html(payload)
    assert "<script" not in blob.lower()
    assert "10" not in json.dumps(payload["metrics"])


def test_not_reported_cells_are_omitted_rather_than_zero():
    catalog = ir.load_catalog("TSLA")
    text = "Metric | 2025Q1 | 2025Q2 | 2025Q3 | 2025Q4\nTesla Semi | 1 | not reported | — | 4\n"
    rows = ir.parse_deck_text(text, catalog, **_meta())
    assert [row["fiscal_period"] for row in rows] == ["2025Q1", "2025Q4"]
    assert all(row["value"] != 0 for row in rows)


def test_ex991_html_parser_ignores_script_and_strips_handlers():
    catalog = ir.load_catalog("TSLA")
    html = """
    <html><script>Supercharger stations | 9 | 9 | 9 | 9</script>
    <table>
      <tr><th>Metric</th><th>2025Q1</th><th>2025Q2</th><th>2025Q3</th><th>2025Q4</th></tr>
      <tr><td onclick="alert(1)">Supercharger stations</td><td>10</td><td>11</td><td>12</td><td>13</td></tr>
    </table></html>
    """
    cleaned = ir.sanitize_html(html)
    assert "<script" not in cleaned.lower()
    assert "onclick" not in cleaned.lower()
    rows = ir.parse_ex991_html(html, catalog, **_meta())
    assert len(rows) == 4
    assert rows[0]["value"] == 10
    assert all(row["confidence"] == ir.CONFIDENCE["table"] for row in rows)


def test_xbrl_parser_and_mismatch_are_flagged_not_overwritten():
    series = ir.parse_xbrl_quarters([
        {"form": "10-Q", "start": "2025-01-01", "end": "2025-03-31", "filed": "2025-04-20", "val": 100},
        {"form": "10-Q", "start": "2025-04-01", "end": "2025-06-30", "filed": "2025-07-20", "val": 120},
        {"form": "8-K", "start": "2025-01-01", "end": "2025-03-31", "filed": "2025-04-01", "val": 1},
    ])
    assert series["2025Q1"] == 100
    assert "2025Q2" in series
    match = ir.reconcile_xbrl(100, 100)
    mismatch = ir.reconcile_xbrl(180, 100)
    assert match["status"] == "match"
    assert mismatch["status"] == "mismatch"
    assert mismatch["ir_value"] == 180 and mismatch["xbrl_value"] == 100
    flagged = ir.reconcile_rows(
        [{"metric_id": "revenue_gaap", "fiscal_period": "2025Q1", "value": 180}],
        {("revenue_gaap", "2025Q1"): 100},
    )
    assert flagged[0]["status"] == "mismatch"


def test_catalog_approval_gates_serving_and_llm_values_stay_unapproved():
    catalog = ir.set_catalog_status(
        ir.load_catalog("TSLA"), "supercharger_stations", "approved", "ram", "2026-10-09T00:00:00Z",
    )
    text = "Metric | 2025Q1 | 2025Q2 | 2025Q3 | 2025Q4\nSupercharger stations | 100 | 110 | 120 | 140\n"
    table_rows = ir.parse_deck_text(text, catalog, **_meta())
    assert all(row["approved"] is True for row in table_rows)
    llm_rows = ir.observations_from_tables(
        ir.text_tables(text), catalog, method="llm", **_meta(),
    )
    assert llm_rows[0]["confidence"] == ir.CONFIDENCE["llm"]
    assert all(row["approved"] is False for row in llm_rows)
    payload = ir.build_serving(
        "TSLA", catalog, table_rows + llm_rows,
        run=ir.run_record(status="ok", sources={"fetched": 1}),
        generated_at="2026-10-08T12:00:00Z",
    )
    assert [item["metric_id"] for item in payload["metrics"]] == ["supercharger_stations"]
    metric = payload["metrics"][0]
    assert metric["latest"]["value"] == 140
    assert metric["qoq"] == pytest.approx((140 - 120) / 120)
    assert metric["yoy"] is None
    assert metric["provenance"]["source_url"].startswith("https://")
    assert metric["provenance"]["published_date"] == "2026-01-29"
    assert metric["provenance"]["confidence"] == ir.CONFIDENCE["table"]
    assert metric["series"][-1]["reported"] is True


def test_unreported_gap_is_labeled_inside_the_series():
    catalog = ir.set_catalog_status(ir.load_catalog("TSLA"), "tesla_semi", "approved", "ram", "2026-10-09T00:00:00Z")
    text = "Metric | 2025Q1 | 2025Q2 | 2025Q3 | 2025Q4\nTesla Semi | 2 | not reported | 3 | 4\n"
    rows = ir.parse_deck_text(text, catalog, **_meta())
    payload = ir.build_serving(
        "TSLA",
        catalog,
        rows,
        run=ir.run_record(status="ok", sources={"fetched": 1}),
        generated_at="2026-10-08T12:00:00Z",
    )
    series = payload["metrics"][0]["series"]
    gap = next(point for point in series if point["fiscal_period"] == "2025Q2")
    assert gap["reported"] is False and gap["value"] is None


def test_parquet_schema_is_zstd_and_carries_provenance_columns(tmp_path: Path):
    catalog = ir.load_catalog("TSLA")
    rows = ir.parse_deck_text(
        "Metric | 2025Q1 | 2025Q2 | 2025Q3 | 2025Q4\nOptimus | 1 | 2 | 3 | 4\n",
        catalog,
        **_meta(),
    )
    frame = ir.metrics_frame(rows)
    assert frame.columns == ir.PARQUET_COLUMNS
    path = tmp_path / "company_metrics.parquet"
    ir.write_metrics_parquet(frame, path)
    parquet = pq.ParquetFile(path)
    assert parquet.schema_arrow.names == ir.PARQUET_COLUMNS
    assert parquet.metadata.row_group(0).column(0).compression == "ZSTD"
    reloaded = frame.to_dicts()
    assert reloaded[0]["source_url"].startswith("https://")
    assert reloaded[0]["source_doc_hash"] == "a" * 64
    assert reloaded[0]["confidence"] == ir.CONFIDENCE["table"]
    assert reloaded[0]["approved"] is False


def test_raw_manifest_records_url_time_and_hash():
    body = b"<html>quarterly update</html>"
    stored = ir.store_raw_document(
        ticker="tsla",
        period="2025Q4",
        url="https://ir.tesla.com/update",
        body=body,
        fetched_at="2026-01-29T15:00:00Z",
        status_code=200,
        content_type="text/html",
        etag="abc",
        robots_decision="allow",
        extension="html",
    )
    assert stored["key"] == f"raw/company_ir/TSLA/2025Q4/{ir.sha256_hex(body)}.html"
    manifest = stored["manifest"]
    assert manifest["source_url"] == "https://ir.tesla.com/update"
    assert manifest["fetched_at"] == "2026-01-29T15:00:00Z"
    assert manifest["sha256"] == ir.sha256_hex(body)


def test_robots_fixture_and_policy_stops_are_honored():
    robots = """
    User-agent: *
    Disallow: /private

    User-agent: InvestorDashboardIR
    Disallow: /webcast
    Allow: /press
    """
    agent = ir.crawler_user_agent("investor-dashboard (contact: test@example.com)")
    assert agent.startswith("InvestorDashboardIR/1.0")
    assert ir.robots_allows(robots, agent, "/webcast") is False
    assert ir.robots_allows(robots, agent, "/press/q3") is True
    assert ir.robots_allows(robots, agent, "/quarterly") is True
    assert ir.robots_allows(robots, "OtherBot/1.0", "/private/file") is False
    calls = []

    def fetch(status):
        calls.append(status)
        decision = ir.fetch_decision(status)
        return decision

    assert fetch(403)["retry"] is False
    assert fetch(429)["retry"] is False
    assert calls == [403, 429]


def test_hash_cache_skips_a_second_llm_call():
    cache = ir.ExtractionCache()
    calls = {"n": 0}

    def deterministic():
        return None

    def llm():
        calls["n"] += 1
        return [{"metric_id": "optimus", "value": 1}]

    monkey_secret = pytest.MonkeyPatch()
    monkey_secret.setenv("COMPANY_IR_LLM_SECRET_ID", "company-ir-llm")
    try:
        first = cache.extract("doc-1", deterministic, llm)
        second = cache.extract("doc-1", deterministic, llm)
    finally:
        monkey_secret.undo()
    assert first == second
    assert calls["n"] == 1
    assert cache.llm_calls == 1


def test_llm_refuses_to_run_without_a_secret_id():
    cache = ir.ExtractionCache()
    monkey_secret = pytest.MonkeyPatch()
    monkey_secret.delenv("COMPANY_IR_LLM_SECRET_ID", raising=False)
    try:
        with pytest.raises(RuntimeError, match="COMPANY_IR_LLM_SECRET_ID"):
            cache.extract("doc-2", lambda: None, lambda: [])
    finally:
        monkey_secret.undo()


def test_partial_run_keeps_previous_values_and_asks_for_an_alert():
    catalog = ir.set_catalog_status(ir.load_catalog("TSLA"), "optimus", "approved", "ram", "2026-10-09T00:00:00Z")
    rows = ir.parse_deck_text(
        "Metric | 2025Q1 | 2025Q2 | 2025Q3 | 2025Q4\nOptimus | 1 | 2 | 3 | 4\n",
        catalog,
        **_meta(),
    )
    previous = ir.build_serving(
        "TSLA",
        catalog,
        rows,
        run=ir.run_record(status="ok", sources={"fetched": 1}),
        generated_at="2026-10-01T00:00:00Z",
    )
    partial = ir.run_record(status="partial", sources={"fetched": 0, "failed": 1}, errors=["timeout"])
    payload = ir.build_serving(
        "TSLA", catalog, [], run=partial, generated_at="2026-10-08T12:00:00Z", previous=previous,
    )
    assert payload["stale"] is True
    assert payload["run_status"] == "partial"
    assert payload["metrics"][0]["latest"]["value"] == 4
    assert "previous approved values kept" in payload["freshness_label"]
    assert ir.run_needs_alert(partial) is True
    assert ir.run_needs_alert(ir.run_record(status="ok", sources={})) is False


def test_collection_window_and_edgar_exhibit_trigger():
    earnings = date(2026, 10, 22)
    assert ir.earnings_window(earnings)[0] == date(2026, 10, 19)
    assert ir.earnings_window(earnings)[-1] == date(2026, 10, 27)
    assert ir.should_collect(date(2026, 10, 19), [earnings], "schedule") is True
    assert ir.should_collect(date(2026, 10, 1), [earnings], "schedule") is False
    assert ir.should_collect(date(2026, 10, 1), [earnings], "weekly") is True
    watch = {"TSLA", "SPCX"}
    assert ir.filing_triggers_extraction("8-K", ["EX-99.1"], "tsla", watch) is True
    assert ir.filing_triggers_extraction("8-K", ["EX-99.2"], "TSLA", watch) is False
    assert ir.filing_triggers_extraction("10-Q", ["EX-99.1"], "TSLA", watch) is False
    assert ir.filing_triggers_extraction("8-K", ["EX-99.1"], "NVDA", watch) is False
    monday = ir.plan_collection({"source": "schedule"}, date(2026, 10, 5))
    assert monday["collect"] is True and monday["reason"] == "weekly"
    quiet = ir.plan_collection({"source": "schedule", "earnings_dates": ["2026-10-22"]}, date(2026, 10, 7))
    assert quiet["collect"] is False
    window = ir.plan_collection({"source": "schedule", "earnings_dates": ["2026-10-22"]}, date(2026, 10, 21))
    assert window["reason"] == "earnings-window"
    edgar = ir.plan_collection(
        {
            "source": "schedule",
            "watchlist": ["TSLA"],
            "filing": {"form": "8-K", "exhibits": ["EX-99.1"], "ticker": "TSLA"},
        },
        date(2026, 10, 7),
    )
    assert edgar["extract"] is True and edgar["reason"] == "edgar-8k"


def test_transcripts_stay_closed_for_paywalled_sources():
    assert ir.transcripts_permitted(None) is False
    assert ir.transcripts_permitted({"paywalled": True, "company_hosted": True}) is False
    assert ir.transcripts_permitted({"company_hosted": True}) is True
    assert ir.transcripts_permitted({"explicitly_permitted": True}) is True


def test_public_document_drops_proposed_metrics_and_source_has_no_secret_literal():
    leaked = {
        "ticker": "TSLA",
        "metrics": [
            {"metric_id": "optimus", "approved": False, "approval_state": "proposed", "latest": {"value": 99}},
            {"metric_id": "tesla_semi", "approved": True, "approval_state": "approved", "latest": {"value": 1}},
        ],
    }
    public = ir.public_stock_document(leaked, "tsla")
    assert [item["metric_id"] for item in public["metrics"]] == ["tesla_semi"]
    empty = ir.public_stock_document(None, "NVDA")
    assert empty["metrics"] == [] and empty["discovered"] is False
    source = Path(ir.__file__).read_text(encoding="utf-8")
    assert "sk-" not in source
    assert "BEGIN PRIVATE" not in source
    assert "api_key = \"" not in source
