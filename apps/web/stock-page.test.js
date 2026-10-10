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
  nextStockTab,
  stockFreshnessText,
  safeHttpUrl,
  stockPanels,
  STOCK_TABS,
} from "./stock-page.js";

const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

test("proposed metrics are omitted and missing quarters say not reported", () => {
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
  const panels = stockPanels(payload, { fundamentals: [] });
  const card = panels.operating[0];
  assert.equal(card.name, "Tesla Semi");
  assert.equal(card.series.find((point) => point.fiscal_period === "2025Q2").reported, false);
  assert.equal(formatMetricValue(null, "vehicles"), "not reported");
  assert.equal(card.qoq, "+100.0%");
  assert.equal(card.yoy, null);
  assert.equal(card.sourceUrl, "https://ir.example/update");
  assert.equal(safeHttpUrl("javascript:alert(1)"), "");
  assert.equal(panels.fundamentalsSource, "none");
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
  assert.match(stockFreshnessText({
    freshness_label: "IR data approved",
    run_status: "ok",
    generated_at: "2026-10-08T12:00:00Z",
    metrics: [{ metric_id: "tesla_semi", approved: true, approval_state: "approved" }],
  }, "ready", "TSLA"), /IR data approved · ok · 2026-10-08/);
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
