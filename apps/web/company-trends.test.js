import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { companyBookmarks, TREND_SECTIONS } from "./company-sections.js";
import { redirectStockHash } from "./routes.js";
import { canApproveMetrics } from "./settings-model.js";
import { nextStockTab, quarterlyNoteEntries, stockTabs, visibleStockTab } from "./stock-page.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

const deepDive = { deep_dive: { sections: [{ id: "scoreboard", title: "Scoreboard" }] } };

function accessToken(claims) {
  const payload = Buffer.from(JSON.stringify(claims)).toString("base64url");
  return `e.${payload}.s`;
}

test("company tabs are Overview, Analysis, Research, Fundamentals, and KPIs", () => {
  assert.deepEqual(stockTabs(null).map((tab) => tab.id), ["summary", "analysis", "fundamentals", "kpis"]);
  assert.deepEqual(stockTabs(deepDive).map((tab) => tab.label), ["Overview", "Analysis", "Research", "Fundamentals", "KPIs"]);
  assert.equal(stockTabs(deepDive).find((tab) => tab.id === "summary").label, "Overview");
  assert.equal(stockTabs(deepDive).find((tab) => tab.id === "analysis").label, "Analysis");
  assert.equal(visibleStockTab("summary", stockTabs(deepDive), "analysis"), "summary");
  assert.equal(visibleStockTab("trends", stockTabs(deepDive), "analysis"), "summary");
  assert.equal(visibleStockTab("analysis", stockTabs(deepDive), "summary"), "analysis");
  assert.equal(visibleStockTab("overview", stockTabs(deepDive), "summary"), "analysis");
  assert.equal(visibleStockTab("technical", stockTabs(deepDive), "summary"), "analysis");
  assert.equal(visibleStockTab("deep-dive", stockTabs(deepDive), "summary"), "research");
  assert.equal(stockTabs(deepDive).some((tab) => tab.label === "Deep dive"), false);
  assert.equal(stockTabs(deepDive).some((tab) => tab.label === "Technical"), false);
  assert.equal(stockTabs({ deep_dive: { sections: [] } }).some((tab) => tab.id === "research"), false);
  assert.equal(stockTabs(null).some((tab) => tab.id === "calls"), false);
  assert.equal(stockTabs(deepDive).some((tab) => tab.id === "admin"), false);
  const tabs = stockTabs(deepDive);
  assert.equal(nextStockTab("summary", "ArrowRight", tabs), "analysis");
  assert.equal(nextStockTab("trends", "ArrowRight", tabs), "analysis");
  assert.equal(nextStockTab("analysis", "ArrowRight", tabs), "research");
  assert.equal(nextStockTab("overview", "ArrowRight", tabs), "research");
  assert.equal(nextStockTab("technical", "ArrowRight", tabs), "research");
  assert.equal(nextStockTab("deep-dive", "ArrowRight", tabs), "fundamentals");
  assert.equal(nextStockTab("fundamentals", "ArrowLeft", tabs), "research");
  assert.equal(nextStockTab("kpis", "ArrowRight", tabs), "summary");
  assert.equal(nextStockTab("kpis", "Home", tabs), "summary");
  assert.equal(nextStockTab("analysis", "End", tabs), "kpis");
  assert.equal(nextStockTab("summary", "ArrowRight", stockTabs(null)), "analysis");
});

test("Admin is last, only for a signed-in admin, and a non-admin hash cannot open it", () => {
  const demo = "ram.vellamsetti@gmail.com";
  const demoToken = accessToken({ "cognito:groups": ["admin"], email: demo, username: demo });
  assert.equal(canApproveMetrics(demoToken, demo), false);
  const hidden = stockTabs(deepDive, { admin: canApproveMetrics(demoToken, demo) });
  assert.equal(hidden.some((tab) => tab.id === "admin"), false);
  assert.equal(visibleStockTab("admin", hidden, "summary"), "analysis");
  assert.equal(visibleStockTab("admin", hidden, "kpis"), "analysis");
  assert.equal(visibleStockTab("admin", stockTabs(null), "analysis"), "analysis");

  const owner = "owner@example.com";
  const ownerToken = accessToken({ "cognito:groups": ["admin"], email: owner, username: owner });
  assert.equal(canApproveMetrics(ownerToken, owner), true);
  const tabs = stockTabs(deepDive, { admin: canApproveMetrics(ownerToken, owner) });
  assert.deepEqual(tabs.map((tab) => tab.id), ["summary", "analysis", "research", "fundamentals", "kpis", "admin"]);
  assert.equal(tabs.at(-1).label, "Admin");
  assert.equal(nextStockTab("kpis", "ArrowRight", tabs), "admin");
  assert.equal(nextStockTab("admin", "ArrowRight", tabs), "summary");
  assert.equal(nextStockTab("admin", "ArrowLeft", tabs), "kpis");
  assert.equal(nextStockTab("analysis", "End", tabs), "admin");
  assert.equal(nextStockTab("admin", "Home", tabs), "summary");
  assert.equal(visibleStockTab("admin", tabs, "analysis"), "admin");
  assert.equal(visibleStockTab("", tabs, "admin"), "analysis");
  assert.equal(stockTabs({ deep_dive: { sections: [] } }, { admin: true }).some((tab) => tab.id === "research"), false);
  assert.equal(visibleStockTab("deep-dive", tabs, "overview"), "research");
  assert.equal(visibleStockTab("research", tabs, "overview"), "research");

  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  assert.match(page, /stockTabs\(payload, \{ admin: metricAdmin \}\)/);
  assert.match(page, /const metricAdmin = canApproveMetrics\(session\.accessToken, session\.email\)/);
  assert.match(page, /visibleStockTab\(hashTab, pageTabs, session\.stockTab\)/);
  assert.match(page, /hashTab === "admin" && !metricAdmin/);
  assert.match(page, /stockTabHash\(session\.selected, ANALYSIS_TAB_ID\)/);
  assert.match(page, /window\.history\.replaceState\(window\.history\.state/);
  assert.doesNotMatch(page, /Back to dashboard/);
  assert.doesNotMatch(page, /stock-dashboard-link/);
  assert.match(page, /pageTabs\.forEach/);
  assert.match(page, /nextStockTab\(item\.id, event\.key, pageTabs\)/);
  assert.match(page, /tab\.tabIndex = item\.id === session\.stockTab \? 0 : -1/);
  assert.match(app, /if \(id === "admin" && !canApproveMetrics\(session\.accessToken, session\.email\)\) id = ANALYSIS_TAB_ID/);
  assert.match(app, /if \(tab === "admin" && !canApproveMetrics\(session\.accessToken, session\.email\)\) tab = ANALYSIS_TAB_ID/);
});

test("quarterly notes keep the latest quarter first and stay empty without guidance", () => {
  assert.deepEqual(quarterlyNoteEntries([]), []);
  assert.deepEqual(quarterlyNoteEntries(null), []);
  const notes = quarterlyNoteEntries([
    {
      id: "older",
      latestPeriod: "2025Q4",
      periodEnd: "2025-12-31",
      text: "Prior outlook.",
      sourceUrl: "https://example.com/older",
      sourceTitle: "Older letter",
    },
    {
      id: "latest",
      latestPeriod: "2026Q2",
      periodEnd: "2026-06-30",
      text: "Raised the outlook.",
      sourceUrl: "https://example.com/latest",
      sourceTitle: "Shareholder letter",
      pending: true,
      latest: "Raised",
      name: "Outlook",
    },
  ]);
  assert.deepEqual(notes.map((note) => note.quarter), ["Q2 2026", "Q4 2025"]);
  assert.equal(notes[0].date, "2026-06-30");
  assert.deepEqual(notes[0].points, ["Raised the outlook.", "Outlook: Raised"]);
  assert.equal(notes[0].sourceUrl, "https://example.com/latest");
  assert.equal(notes[0].pending, true);
  const guidance = notes.map((note) => ({ latestPeriod: note.quarter === "Q2 2026" ? "2026Q2" : "2025Q4", text: note.points[0], sourceUrl: note.sourceUrl }));
  const tile = companyBookmarks("overview", { guidance });
  assert.deepEqual(companyBookmarks("technical", { guidance }).map((item) => item.id), tile.map((item) => item.id));
  assert.deepEqual(companyBookmarks("analysis", { guidance }).map((item) => item.id), tile.map((item) => item.id));
  const notesTile = tile.find((item) => item.id === "quarterly-notes");
  assert.equal(notesTile.title, "Quarterly notes");
  assert.equal(notesTile.headline, undefined);
});

test("Overview tiles follow the sections that are actually on the page", () => {
  const present = ["price", "news", "filings"];
  const tiles = companyBookmarks("summary", { present, history: [{ close: 4 }, { close: 5 }] });
  assert.deepEqual(companyBookmarks("trends", { present }).map((item) => item.id), present);
  assert.deepEqual(tiles.map((item) => item.id), present);
  assert.equal(tiles[0].anchorId, "trends-panel-price");
  assert.equal(tiles[0].title, "Price");
  assert.equal(tiles[0].headline, undefined);
  assert.equal(TREND_SECTIONS.some((item) => item.id === "technical"), false);
  assert.equal(companyBookmarks("trends", { present: ["price", "technical"] }).some((item) => item.id === "technical"), false);
  assert.equal(companyBookmarks("trends", {}).length, TREND_SECTIONS.length);
});

test("old main-page hashes and Calls land on Overview or quarterly notes", () => {
  assert.equal(redirectStockHash("#chart", "spy"), "#stock/SPY/summary/price");
  assert.equal(redirectStockHash("#calendar", "qqq"), "#stock/QQQ/summary/catalyst-calendar");
  assert.equal(redirectStockHash("#stock/dia/calls/notes"), "#stock/DIA/analysis/quarterly-notes");
});

test("the company page renders former main-page panels on Overview and keeps the tab on ticker switch", () => {
  const trends = app.slice(app.indexOf("function renderTrendsLayout"), app.indexOf("function renderQuarterlyNotes"));
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
  assert.match(page, /renderQuarterlyNotes\(charts\.guidance\)/);
  assert.match(app.slice(app.indexOf("function renderQuarterlyNotes"), app.indexOf("function appendStatusAlerts")), /No quarterly notes yet/);
  assert.match(app, /navigateToStock\(ticker, \{ keepTab: true \}\)/);
  const navigate = app.slice(app.indexOf("function navigateToStock"), app.indexOf("function navigateToDashboard"));
  assert.match(navigate, /keepTab/);
  assert.match(navigate, /session\.companyChart = null/);
  assert.match(app, /stockTabHash\(ticker, OVERVIEW_TAB_ID\)/);
  assert.doesNotMatch(page, /callsBlock|data-stock-panel = "calls"/);
});
