import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { companyBookmarks, TREND_SECTIONS } from "./company-sections.js";
import { redirectStockHash } from "./routes.js";
import { canApproveMetrics } from "./settings-model.js";
import { nextStockTab, stockTabs, visibleStockTab } from "./stock-page.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

const deepDive = { deep_dive: { sections: [{ id: "scoreboard", title: "Scoreboard" }] } };

function accessToken(claims) {
  const payload = Buffer.from(JSON.stringify(claims)).toString("base64url");
  return `e.${payload}.s`;
}

test("company tabs are Trends, Technical, Deep dive, Fundamentals, and KPIs", () => {
  assert.deepEqual(stockTabs(null).map((tab) => tab.id), ["trends", "overview", "fundamentals", "kpis"]);
  assert.deepEqual(stockTabs(deepDive).map((tab) => tab.label), ["Trends", "Technical", "Deep dive", "Fundamentals", "KPIs"]);
  assert.equal(stockTabs(deepDive).some((tab) => tab.label === "Overview"), false);
  assert.equal(stockTabs({ deep_dive: { sections: [] } }).some((tab) => tab.id === "deep-dive"), false);
  assert.equal(stockTabs(null).some((tab) => tab.id === "calls"), false);
  assert.equal(stockTabs(deepDive).some((tab) => tab.id === "admin"), false);
  const tabs = stockTabs(deepDive);
  assert.equal(nextStockTab("trends", "ArrowRight", tabs), "overview");
  assert.equal(nextStockTab("overview", "ArrowRight", tabs), "deep-dive");
  assert.equal(nextStockTab("deep-dive", "ArrowRight", tabs), "fundamentals");
  assert.equal(nextStockTab("fundamentals", "ArrowLeft", tabs), "deep-dive");
  assert.equal(nextStockTab("kpis", "ArrowRight", tabs), "trends");
  assert.equal(nextStockTab("kpis", "Home", tabs), "trends");
  assert.equal(nextStockTab("overview", "End", tabs), "kpis");
  assert.equal(nextStockTab("trends", "ArrowRight", stockTabs(null)), "overview");
});

test("Admin is last, only for a signed-in admin, and a non-admin hash cannot open it", () => {
  const demo = "ram.vellamsetti@gmail.com";
  const demoToken = accessToken({ "cognito:groups": ["admin"], email: demo, username: demo });
  assert.equal(canApproveMetrics(demoToken, demo), false);
  const hidden = stockTabs(deepDive, { admin: canApproveMetrics(demoToken, demo) });
  assert.equal(hidden.some((tab) => tab.id === "admin"), false);
  assert.equal(visibleStockTab("admin", hidden, "trends"), "overview");
  assert.equal(visibleStockTab("admin", hidden, "kpis"), "overview");
  assert.equal(visibleStockTab("admin", stockTabs(null), "overview"), "overview");

  const owner = "owner@example.com";
  const ownerToken = accessToken({ "cognito:groups": ["admin"], email: owner, username: owner });
  assert.equal(canApproveMetrics(ownerToken, owner), true);
  const tabs = stockTabs(deepDive, { admin: canApproveMetrics(ownerToken, owner) });
  assert.deepEqual(tabs.map((tab) => tab.id), ["trends", "overview", "deep-dive", "fundamentals", "kpis", "admin"]);
  assert.equal(tabs.at(-1).label, "Admin");
  assert.equal(nextStockTab("kpis", "ArrowRight", tabs), "admin");
  assert.equal(nextStockTab("admin", "ArrowRight", tabs), "trends");
  assert.equal(nextStockTab("admin", "ArrowLeft", tabs), "kpis");
  assert.equal(nextStockTab("overview", "End", tabs), "admin");
  assert.equal(nextStockTab("admin", "Home", tabs), "trends");
  assert.equal(visibleStockTab("admin", tabs, "overview"), "admin");
  assert.equal(visibleStockTab("", tabs, "admin"), "overview");
  assert.equal(stockTabs({ deep_dive: { sections: [] } }, { admin: true }).some((tab) => tab.id === "deep-dive"), false);

  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  assert.match(page, /stockTabs\(payload, \{ admin: metricAdmin \}\)/);
  assert.match(page, /const metricAdmin = canApproveMetrics\(session\.accessToken, session\.email\)/);
  assert.match(page, /visibleStockTab\(hashTab, pageTabs, session\.stockTab\)/);
  assert.match(page, /hashTab === "admin" && !metricAdmin/);
  assert.match(page, /stockTabHash\(session\.selected, "overview"\)/);
  assert.match(page, /window\.history\.replaceState\(window\.history\.state/);
  assert.doesNotMatch(page, /Back to dashboard/);
  assert.doesNotMatch(page, /stock-dashboard-link/);
  assert.match(page, /pageTabs\.forEach/);
  assert.match(page, /nextStockTab\(item\.id, event\.key, pageTabs\)/);
  assert.match(page, /tab\.tabIndex = item\.id === session\.stockTab \? 0 : -1/);
  assert.match(app, /if \(id === "admin" && !canApproveMetrics\(session\.accessToken, session\.email\)\) id = "overview"/);
  assert.match(app, /if \(tab === "admin" && !canApproveMetrics\(session\.accessToken, session\.email\)\) tab = "overview"/);
});

test("overview bookmarks are the Technical Summary only", () => {
  const tile = companyBookmarks("overview", {
    guidance: [{ latestPeriod: "2026Q2", text: "Raised the outlook.", sourceUrl: "https://example.com/latest" }],
  });
  assert.deepEqual(tile.map((item) => item.id), ["technical"]);
  assert.equal(tile.some((item) => item.id === "quarterly-notes"), false);
});

test("Trends tiles follow the sections that are actually on the page", () => {
  const present = ["price", "news", "filings"];
  const tiles = companyBookmarks("trends", { present, history: [{ close: 4 }, { close: 5 }] });
  assert.deepEqual(tiles.map((item) => item.id), present);
  assert.equal(tiles[0].anchorId, "trends-panel-price");
  assert.equal(tiles[0].title, "Price");
  assert.equal(tiles[0].headline, undefined);
  assert.equal(TREND_SECTIONS.some((item) => item.id === "technical"), false);
  assert.equal(companyBookmarks("trends", { present: ["price", "technical"] }).some((item) => item.id === "technical"), false);
  assert.equal(companyBookmarks("trends", {}).length, TREND_SECTIONS.length);
});

test("old main-page hashes and Calls land on Trends or Overview", () => {
  assert.equal(redirectStockHash("#chart", "spy"), "#stock/SPY/trends/price");
  assert.equal(redirectStockHash("#calendar", "qqq"), "#stock/QQQ/trends/catalyst-calendar");
  assert.equal(redirectStockHash("#stock/dia/calls/notes"), "#stock/DIA");
  assert.equal(redirectStockHash("#stock/DIA/overview/quarterly-notes"), "#stock/DIA");
});

test("the company page renders former main-page panels on Trends and keeps the tab on ticker switch", () => {
  const trends = app.slice(app.indexOf("function renderTrendsLayout"), app.indexOf("function appendStatusAlerts"));
  for (const piece of [
    'renderChartPanel("dashboard", session.selected)',
    "renderMacroPanels(tickerData, chartData, chartDataState)",
    "renderContracts(tickerData)",
    "renderNews(tickerData)",
    "renderFilings(tickerData)",
    "renderDrivers(tickerData)",
    "renderAboutData()",
  ]) {
    assert.match(trends, new RegExp(piece.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.doesNotMatch(trends, /renderTechnicalSummary\(/);
  assert.match(trends, /trends-layout dashboard-grid/);
  assert.match(trends, /dataset\.mobileView = "all"/);
  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  assert.match(page, /renderTrendsLayout\(tickerData, chartData, chartDataState, payload\)/);
  assert.match(app, /renderTrendDisclaimer\(payload, document\)/);
  assert.match(page, /page\.prepend\(trends\)/);
  assert.doesNotMatch(app, /renderQuarterlyNotes|quarterly-notes|No quarterly notes yet|Quarterly notes/);
  assert.match(app, /navigateToStock\(ticker, \{ keepTab: true \}\)/);
  const navigate = app.slice(app.indexOf("function navigateToStock"), app.indexOf("function navigateToDashboard"));
  assert.match(navigate, /keepTab/);
  assert.match(navigate, /session\.companyChart = null/);
  assert.match(app, /stockTabHash\(ticker, "trends"\)/);
  assert.doesNotMatch(page, /callsBlock|data-stock-panel = "calls"/);
});
