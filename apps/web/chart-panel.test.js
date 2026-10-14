import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { CHART_PANEL_PAGES, chartPanelOptions } from "./chart-panel.js";

const app = readFileSync(new URL("./app.js", import.meta.url), "utf8");
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

function slice(start, end) {
  return app.slice(app.indexOf(start), app.indexOf(end));
}

test("page options differ only in links and placement, never in chart features", () => {
  assert.deepEqual(CHART_PANEL_PAGES, ["dashboard", "research", "company"]);
  const dashboard = chartPanelOptions("dashboard", "abc");
  assert.equal(dashboard.ticker, "ABC");
  assert.equal(dashboard.companyLink, true);
  assert.equal(dashboard.mobilePanel, "chart");
  assert.equal(dashboard.id, "mobile-panel-chart");
  const company = chartPanelOptions("company", "XYZ");
  assert.equal(company.companyLink, false);
  assert.equal(company.stockPanel, "overview");
  assert.equal(company.mobilePanel, "");
  assert.equal(chartPanelOptions("research", "XYZ").companyLink, false);
  assert.equal(chartPanelOptions("unknown", "XYZ").page, "dashboard");
  assert.equal(chartPanelOptions("company", "not a ticker!").ticker, "");
  const keys = Object.keys(company).sort();
  assert.deepEqual(keys, ["companyLink", "id", "mobilePanel", "page", "stockPanel", "ticker"]);
});

test("the dashboard and research pages render the one shared chart panel (#203, #207)", () => {
  assert.equal((app.match(/renderPricePanel\(/g) || []).length, 2, "defined once, called only from renderChartPanel");
  const entry = slice("function renderChartPanel", "function renderPricePanel");
  assert.match(entry, /chartPanelOptions\(page, ticker\)/);
  assert.match(entry, /renderPricePanel\(/);
  const dashboardAt = app.indexOf('renderChartPanel("dashboard", session.selected)');
  const research = slice("function renderResearchPage", "async function selectChartPeriod");
  assert.ok(dashboardAt > 0);
  assert.match(research, /renderChartPanel\("research", session\.selected\)/);
  // The company page has no chart panel (#207).
  const stock = slice("function renderStockPage", "function renderResearchPage");
  assert.doesNotMatch(stock, /renderChartPanel\(|renderPricePanel\(/);
  assert.doesNotMatch(styles, /price-panel-compact/);
});

test("the shared panel keeps every control and gates only the company link", () => {
  const panel = slice("function renderPricePanel", "function renderContracts");
  for (const piece of [/renderPeriodChips\(/, /renderTrend\(/, /renderOverlayControls\(/, /renderChartLanes\(/,
    /renderLaneControls\(/, /catalyst-filters/, /attachSharedCrosshair\(/, /chart-legend/]) {
    assert.match(panel, piece);
  }
  assert.match(panel, /options\.companyLink && stockHash/);
  assert.doesNotMatch(panel, /session\.route\.page/);
});
