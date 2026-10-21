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

test("every page renders the one shared chart panel (#203)", () => {
  assert.equal((app.match(/renderPricePanel\(/g) || []).length, 2, "defined once, called only from renderChartPanel");
  const entry = slice("function renderChartPanel", "function renderPricePanel");
  assert.match(entry, /chartPanelOptions\(page, ticker\)/);
  assert.match(entry, /renderPricePanel\(/);
  const dashboardAt = app.indexOf('renderChartPanel("dashboard", session.selected)');
  const research = slice("function renderResearchPage", "async function selectChartPeriod");
  assert.ok(dashboardAt > 0);
  assert.match(research, /renderChartPanel\("research", session\.selected\)/);
  const stock = slice("function renderStockPage", "function renderResearchPage");
  assert.match(stock, /renderChartPanel\("company", session\.selected, \{ stockPanel \}\)/);
  assert.doesNotMatch(stock, /renderChartPanel\("company", session\.selected\)/);
  assert.doesNotMatch(stock, /classList\.add\(/);
  assert.doesNotMatch(styles, /price-panel-compact/);
  assert.match(stock, /renderTechnicalSummary\(session\.selected, history\)/);
  assert.doesNotMatch(stock, /page\.append\(\s*chart,/);
});

test("Overview keeps the full chart and Fundamentals and KPIs return after the quote", () => {
  const panel = slice("function renderPricePanel", "function renderContracts");
  assert.match(panel, /renderPeriodChips\(/);
  assert.match(panel, /renderTrend\(/);
  assert.match(panel, /options\.companyLink && stockHash/);
  assert.match(panel, /options\.stockPanel === "fundamentals" \|\| options\.stockPanel === "kpis"/);
  assert.match(panel, /if \(quoteOnly\) return panel;/);
  const chart = panel.slice(panel.indexOf("if (quoteOnly) return panel;"));
  for (const piece of [/renderOverlayControls\(/, /renderChartLanes\(/, /renderLaneControls\(/,
    /catalyst-filters/, /attachSharedCrosshair\(/, /chart-legend/, /drawChart\(/]) {
    assert.match(chart, piece);
  }
  assert.doesNotMatch(panel.slice(0, panel.indexOf("if (quoteOnly) return panel;")), /drawChart\(|catalyst-filters/);
  assert.doesNotMatch(panel, /session\.route\.page/);
  const stock = slice("function renderStockPage", "function renderResearchPage");
  assert.doesNotMatch(stock, /page\.append\(\s*chart,/);
  assert.match(stock, /renderTechnicalSummary\(session\.selected, history\)/);
});
