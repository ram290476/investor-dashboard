import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { contrastRatio, THEMES, themeProperties } from "./theme.js";
import {
  approvedMetrics,
  callsCopy,
  emptyKpiCopy,
  fillQuarterGaps,
  formatMetricValue,
  freshnessBadge,
  monthDayLabel,
  nextStockTab,
  panelType,
  pendingMetrics,
  stockFreshnessText,
  safeHttpUrl,
  stockPanels,
  STOCK_TABS,
} from "./stock-page.js";

const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

test("proposed metrics stay labeled pending and missing quarters say not reported", () => {
  const payload = {
    metrics: [
      { metric_id: "optimus", approved: false, approval_state: "proposed", display_name: "Optimus", latest: { value: 9 } },
      {
        metric_id: "tesla_semi",
        approved: true,
        approval_state: "approved",
        display_name: "Tesla Semi",
        unit: "vehicles",
        category: "operating",
        latest: { fiscal_period: "2025Q4", value: 4, reported: true },
        qoq: 1,
        yoy: null,
        series: [
          { fiscal_period: "2025Q1", value: 2, reported: true },
          { fiscal_period: "2025Q4", value: 4, reported: true },
        ],
        provenance: {
          source_url: "https://ir.example/update",
          source_title: "Fixture deck",
          published_date: "2026-01-29",
          confidence: 0.95,
          approval_state: "approved",
        },
      },
    ],
  };
  assert.deepEqual(approvedMetrics(payload).map((metric) => metric.metric_id), ["tesla_semi"]);
  assert.deepEqual(pendingMetrics(payload).map((metric) => metric.metric_id), ["optimus"]);
  const panels = stockPanels(payload, { fundamentals: [] });
  const card = panels.operating[0];
  assert.equal(card.name, "Tesla Semi");
  assert.equal(card.pending, false);
  assert.equal(panels.operating[1].id, "optimus");
  assert.equal(panels.operating[1].pending, true);
  assert.equal(panels.operating[1].approval, "proposed");
  const approvedOnly = stockPanels(payload, { fundamentals: [] }, { review: "approved" });
  assert.equal(approvedOnly.operating.some((item) => item.id === "optimus"), false);
  assert.equal(card.series.find((point) => point.fiscal_period === "2025Q2").reported, false);
  assert.equal(formatMetricValue(null, "vehicles"), "not reported");
  assert.equal(card.qoq, "+100.0%");
  assert.equal(card.yoy, null);
  assert.equal(card.sourceUrl, "https://ir.example/update");
  assert.equal(safeHttpUrl("javascript:alert(1)"), "");
  assert.equal(panels.fundamentalsSource, "none");
});

test("panel type follows the registry and the shape of collected values", () => {
  assert.equal(panelType({ panel_type: "kpi", series: [{ value: 1, reported: true }, { value: 2, reported: true }] }), "kpi");
  assert.equal(panelType({ rows: [{ segment: "Auto", value: 1 }] }), "table");
  assert.equal(panelType({ text: "Factory note" }), "text");
  assert.equal(panelType({ narrative: "Starlink note", series: [{ value: 1, reported: true }] }), "text");
  assert.equal(panelType({ latest: { value: 4, reported: true }, series: [] }), "kpi");
  assert.equal(panelType({
    series: [
      { fiscal_period: "2025Q1", value: 1, reported: true },
      { fiscal_period: "2025Q2", value: 2, reported: true },
    ],
  }), "chart");
  const table = stockPanels({
    metrics: [{
      metric_id: "segments",
      approved: false,
      approval_state: "proposed",
      display_name: "Segments",
      category: "operating",
      panel_type: "table",
      rows: [{ name: "Automotive", value: 1 }],
    }],
  }, null);
  assert.equal(table.operating[0].panelType, "table");
  assert.deepEqual(table.operating[0].rows, [{ name: "Automotive", value: 1 }]);
  assert.equal(table.operating[0].pending, true);
  const text = stockPanels({
    metrics: [{
      metric_id: "factories",
      approved: true,
      approval_state: "approved",
      display_name: "Factories",
      category: "operating",
      panel_type: "text",
      text: "Gigafactory note",
    }],
  }, null);
  assert.equal(text.operating[0].panelType, "text");
  assert.equal(text.operating[0].text, "Gigafactory note");
  assert.equal(text.operating[0].pending, false);
});

test("collected XBRL metrics and CapEx render as pending charts with revision context", () => {
  const ids = [
    "revenue_gaap",
    "net_income_gaap",
    "operating_income",
    "research_and_development",
    "sga",
    "eps_diluted",
    "deferred_revenue",
    "capex",
    "operating_cash_flow",
    "cash_and_investments",
  ];
  const names = {
    capex: "Capital expenditures",
    operating_cash_flow: "Operating cash flow",
    free_cash_flow: "Free cash flow",
  };
  const series = [
    { fiscal_period: "2024Q4", value: 8, reported: true },
    { fiscal_period: "2025Q1", value: 9, reported: true },
    { fiscal_period: "2025Q2", value: 10, reported: true },
  ];
  const metrics = [...ids, "free_cash_flow"].map((id) => ({
    metric_id: id,
    approved: false,
    approval_state: "proposed",
    display_name: names[id] || id,
    unit: "USD",
    category: "fundamentals",
    latest: { fiscal_period: "2025Q2", value: 10, reported: true },
    series,
    revised_from: id === "capex" ? "2025Q1" : "",
    revision_note: id === "capex" ? "Capex uses a revised definition from 2025Q1." : "",
    provenance: { source_kind: "xbrl", source_url: "https://www.sec.gov/example", confidence: 0.95, approval_state: "proposed" },
  }));
  const payload = { metrics, freshness_label: "Pending review", run_status: "ok" };
  const panels = stockPanels(payload, { fundamentals: [] });
  assert.equal(panels.fundamentalsSource, "ir");
  assert.equal(panels.fundamentals[0].id, "capex");
  for (const id of ids) {
    const card = panels.fundamentals.find((item) => item.id === id);
    assert.equal(card.panelType, "chart", id);
    assert.equal(card.pending, true, id);
  }
  const capex = panels.fundamentals[0];
  assert.equal(capex.name, "CapEx");
  assert.equal(capex.detailName, "Capital expenditures");
  assert.equal(capex.series.find((point) => point.fiscal_period === "2024Q4").revised, false);
  assert.equal(capex.series.find((point) => point.fiscal_period === "2025Q1").revised, true);
  assert.match(capex.revisionNote, /2025Q1/);
  assert.deepEqual(capex.context.map((item) => item.name), ["Operating cash flow", "Free cash flow"]);
  assert.equal(capex.context.every((item) => item.pending), true);
  const badge = freshnessBadge(payload, "ready");
  assert.equal(badge.tone, "pending");
  assert.equal(badge.text, "Pending review");
  assert.equal(freshnessBadge(payload, "ready", "TSLA", "approved").text, "No approved company metrics");
  const hidden = stockPanels(payload, { fundamentals: [] }, { review: "approved" });
  assert.equal(hidden.pendingCount, 0);
  assert.equal(hidden.fundamentals.length, 0);
  assert.match(app, /review-badge/);
  assert.match(app, /stock-metric-capex/);
  assert.match(app, /Pending review/);
});

test("company metric errors stay distinct from an empty catalog", () => {
  assert.equal(freshnessBadge(null, "forbidden", "TSLA").text, "Add TSLA to your watchlist to see company metrics");
  assert.equal(freshnessBadge(null, "missing").text, "No company-specific metrics discovered");
  assert.equal(freshnessBadge(null, "error").text, "Company metrics could not be loaded. Retry");
  const ready = freshnessBadge({
    freshness_label: "IR data approved",
    run_status: "ok",
    generated_at: "2026-10-08T12:00:00Z",
    metrics: [{ metric_id: "tesla_semi", approved: true, approval_state: "approved" }],
  }, "ready");
  assert.equal(ready.tone, "ok");
  const now = new Date("2026-10-10T15:00:00Z");
  assert.equal(stockFreshnessText({
    freshness_label: "IR data approved",
    run_status: "ok",
    generated_at: "2026-10-08T12:00:00Z",
    metrics: [{ metric_id: "tesla_semi", approved: true, approval_state: "approved" }],
  }, "ready", "TSLA", now, "America/New_York"), "IR data approved · ok · Oct 8");
  assert.equal(monthDayLabel("2026-10-02", now, "America/New_York"), "Oct 2");
  assert.equal(monthDayLabel("2025-10-02", now, "UTC"), "Oct 2, 2025");
});

test("a partial payload keeps a stale badge and does not invent a zero", () => {
  const badge = freshnessBadge({
    stale: true,
    run_status: "partial",
    freshness_label: "Partial run · previous approved values kept",
    metrics: [{ metric_id: "optimus", approved: true, approval_state: "approved", latest: { value: 4, reported: true }, series: [] }],
  }, "ready");
  assert.equal(badge.tone, "partial");
  assert.match(badge.text, /previous approved values kept/);
  assert.equal(formatMetricValue(0, "vehicles") === "not reported", false);
  assert.doesNotMatch(emptyKpiCopy(), /0/);
});

test("edgar fundamentals fill the page only when no approved IR fundamentals exist", () => {
  const chart = {
    fundamentals: [
      { series_id: "revenue_gaap", fiscal_quarter: "2025Q3", value: 25e9, unit: "USD", date: "2025-10-20", source_id: "DS-11" },
      { series_id: "revenue_gaap", fiscal_quarter: "2025Q4", value: 28e9, unit: "USD", date: "2026-01-29", source_id: "DS-11" },
    ],
  };
  const panels = stockPanels({ metrics: [] }, chart);
  assert.equal(panels.fundamentalsSource, "edgar");
  assert.equal(panels.fundamentals[0].name, "Revenue");
  assert.equal(panels.fundamentals[0].sourceTitle, "DS-11");
  assert.match(panels.fundamentals[0].latest, /\$/);
  const withIr = stockPanels({
    metrics: [{
      metric_id: "revenue_gaap",
      approved: true,
      approval_state: "approved",
      display_name: "Revenue",
      unit: "USD",
      category: "fundamentals",
      latest: { fiscal_period: "2025Q4", value: 28e9, reported: true },
      series: [{ fiscal_period: "2025Q4", value: 28e9, reported: true }],
      provenance: { source_url: "https://ir.example/letter", source_title: "Update", published_date: "2026-01-29", confidence: 0.95, approval_state: "approved" },
      xbrl: { status: "mismatch" },
    }],
  }, chart);
  assert.equal(withIr.fundamentalsSource, "ir");
  assert.equal(withIr.fundamentals.length, 1);
  assert.equal(withIr.fundamentals[0].xbrl, "XBRL mismatch");
});

test("stock tabs move by keyboard and calls stay unlicensed", () => {
  assert.equal(nextStockTab("overview", "ArrowRight"), "kpis");
  assert.equal(nextStockTab("overview", "End"), "calls");
  assert.equal(nextStockTab("calls", "ArrowRight"), "overview");
  assert.equal(STOCK_TABS.length, 4);
  assert.match(callsCopy().transcripts, /not collected/);
  const gap = fillQuarterGaps([
    { fiscal_period: "2025Q1", value: 1, reported: true },
    { fiscal_period: "2025Q2", value: null, reported: false },
  ]);
  assert.equal(gap[1].reported, false);
  assert.equal(gap[1].value, null);
});

test("stock page panels are two-up on desktop and one-up below 1024px", () => {
  assert.match(styles, /\.stock-metric-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(styles, /@media \(max-width: 1023px\)[\s\S]*?\.stock-metric-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(styles, /\.stock-chart\s*\{[^}]*grid-template-columns:\s*auto minmax\(0,\s*1fr\)/);
  assert.match(styles, /\.stock-chart\s*\{[^}]*width:\s*100%/);
  assert.match(styles, /\.stock-bars\s*\{[^}]*grid-column:\s*2/);
  assert.match(styles, /\.stock-quarters\s*\{[^}]*grid-column:\s*2/);
  assert.match(styles, /\.stock-bar\s*\{[^}]*min-width:\s*0/);
  assert.match(styles, /\.stock-metric[\s\S]*var\(--/);
  assert.doesNotMatch(styles, /\.stock-metric[^{]*\{[^}]*#[0-9a-fA-F]{3,8}/);
  assert.match(app, /class="stock-chart"|node\("div", "stock-chart"\)/);
  assert.match(app, /node\("div", "stock-axis stock-quarters"\)/);
  const card = app.slice(app.indexOf("function renderMetricCard"), app.indexOf("function renderStockPage"));
  assert.doesNotMatch(card, /stock-provenance|stock-xbrl|DS-11/);
});

test("company header pins Back to dashboard at the top-right", () => {
  const header = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  const copyAt = header.indexOf('node("div", "stock-heading-copy")');
  const linkAt = header.indexOf('node("a", "stock-dashboard-link", "Back to dashboard")');
  assert.ok(copyAt > 0 && linkAt > copyAt);
  assert.match(header, /back\.href = "\/"/);
  assert.match(header, /heading\.append\(copy, back\)/);
  assert.match(header, /navigateToDashboard\(\)/);
  assert.match(styles, /\.stock-heading\s*\{[^}]*display:\s*grid/);
  assert.match(styles, /\.stock-heading\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) auto/);
  const linkCss = styles.slice(styles.indexOf(".stock-dashboard-link {"), styles.indexOf(".stock-price {"));
  assert.match(linkCss, /justify-self:\s*end/);
  assert.match(linkCss, /align-self:\s*start/);
  assert.match(linkCss, /margin-left:\s*auto/);
  assert.match(linkCss, /min-height:\s*44px/);
  assert.match(linkCss, /var\(--/);
  assert.doesNotMatch(linkCss, /#[0-9a-fA-F]{3,8}/);
  assert.doesNotMatch(styles, /\.stock-dashboard-link\s*\{[^}]*align-self:\s*flex-end/);
  assert.doesNotMatch(styles, /\.stock-heading-copy\s*\{[^}]*flex-basis:\s*100%/);
});

test("stock page text and up/down colors stay AA on all six themes", () => {
  for (const theme of THEMES) {
    const properties = themeProperties(theme.id, "green-red");
    assert.ok(contrastRatio(properties["--text"], properties["--surface"]) >= 4.5, theme.id);
    assert.ok(contrastRatio(properties["--green"], properties["--surface"]) >= 4.5, `${theme.id} up`);
    assert.ok(contrastRatio(properties["--red"], properties["--surface"]) >= 4.5, `${theme.id} down`);
  }
});
