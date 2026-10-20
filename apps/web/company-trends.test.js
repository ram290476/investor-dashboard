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

test("company tabs are Overview, Trends, Deep dive, Fundamentals, and KPIs", () => {
  assert.deepEqual(stockTabs(null).map((tab) => tab.id), ["overview", "trends", "fundamentals", "kpis"]);
  assert.deepEqual(stockTabs(deepDive).map((tab) => tab.label), ["Overview", "Trends", "Deep dive", "Fundamentals", "KPIs"]);
  assert.equal(stockTabs({ deep_dive: { sections: [] } }).some((tab) => tab.id === "deep-dive"), false);
  assert.equal(stockTabs(null).some((tab) => tab.id === "calls"), false);
  assert.equal(stockTabs(deepDive).some((tab) => tab.id === "admin"), false);
  const tabs = stockTabs(deepDive);
  assert.equal(nextStockTab("overview", "ArrowRight", tabs), "trends");
  assert.equal(nextStockTab("trends", "ArrowRight", tabs), "deep-dive");
  assert.equal(nextStockTab("deep-dive", "ArrowRight", tabs), "fundamentals");
  assert.equal(nextStockTab("fundamentals", "ArrowLeft", tabs), "deep-dive");
  assert.equal(nextStockTab("kpis", "ArrowRight", tabs), "overview");
  assert.equal(nextStockTab("kpis", "Home", tabs), "overview");
  assert.equal(nextStockTab("overview", "End", tabs), "kpis");
  assert.equal(nextStockTab("trends", "ArrowRight", stockTabs(null)), "fundamentals");
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
  assert.deepEqual(tabs.map((tab) => tab.id), ["overview", "trends", "deep-dive", "fundamentals", "kpis", "admin"]);
  assert.equal(tabs.at(-1).label, "Admin");
  assert.equal(nextStockTab("kpis", "ArrowRight", tabs), "admin");
  assert.equal(nextStockTab("admin", "ArrowRight", tabs), "overview");
  assert.equal(nextStockTab("admin", "ArrowLeft", tabs), "kpis");
  assert.equal(nextStockTab("overview", "End", tabs), "admin");
  assert.equal(nextStockTab("admin", "Home", tabs), "overview");
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
  const tile = companyBookmarks("overview", { guidance: notes.map((note) => ({ latestPeriod: note.quarter === "Q2 2026" ? "2026Q2" : "2025Q4", text: note.points[0], sourceUrl: note.sourceUrl })) });
  const notesTile = tile.find((item) => item.id === "quarterly-notes");
  assert.equal(notesTile.title, "Quarterly notes");
  assert.equal(notesTile.headline, undefined);
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

test("old main-page hashes and Calls land on Trends or quarterly notes", () => {
  assert.equal(redirectStockHash("#chart", "spy"), "#stock/SPY/trends/price");
  assert.equal(redirectStockHash("#calendar", "qqq"), "#stock/QQQ/trends/catalyst-calendar");
  assert.equal(redirectStockHash("#stock/dia/calls/notes"), "#stock/DIA/overview/quarterly-notes");
});

test("the company page renders former main-page panels on Trends and keeps the tab on ticker switch", () => {
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
  assert.match(page, /renderTrendsLayout\(tickerData, chartData, chartDataState\)/);
  assert.match(page, /page\.prepend\(trends\)/);
  assert.match(page, /renderQuarterlyNotes\(charts\.guidance\)/);
  assert.match(app.slice(app.indexOf("function renderQuarterlyNotes"), app.indexOf("function appendStatusAlerts")), /No quarterly notes yet/);
  assert.match(app, /navigateToStock\(ticker, \{ keepTab: true \}\)/);
  const navigate = app.slice(app.indexOf("function navigateToStock"), app.indexOf("function navigateToDashboard"));
  assert.match(navigate, /keepTab/);
  assert.match(navigate, /session\.companyChart = null/);
  assert.match(app, /stockTabHash\(ticker, "trends"\)/);
  assert.doesNotMatch(page, /callsBlock|data-stock-panel = "calls"/);
});
