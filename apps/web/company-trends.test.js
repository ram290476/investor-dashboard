import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { companyBookmarks, TREND_SECTIONS } from "./company-sections.js";
import { redirectStockHash } from "./routes.js";
import { nextStockTab, quarterlyNoteEntries, stockTabs } from "./stock-page.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

const deepDive = { deep_dive: { sections: [{ id: "scoreboard", title: "Scoreboard" }] } };

test("company tabs are Trends, Overview, Fundamentals, Deep dive, and KPIs", () => {
  assert.deepEqual(stockTabs(null).map((tab) => tab.id), ["trends", "overview", "fundamentals", "kpis"]);
  assert.deepEqual(stockTabs(deepDive).map((tab) => tab.label), ["Trends", "Overview", "Fundamentals", "Deep dive", "KPIs"]);
  assert.equal(stockTabs({ deep_dive: { sections: [] } }).some((tab) => tab.id === "deep-dive"), false);
  assert.equal(stockTabs(null).some((tab) => tab.id === "calls"), false);
  const tabs = stockTabs(deepDive);
  assert.equal(nextStockTab("trends", "ArrowRight", tabs), "overview");
  assert.equal(nextStockTab("fundamentals", "ArrowRight", tabs), "deep-dive");
  assert.equal(nextStockTab("deep-dive", "ArrowRight", tabs), "kpis");
  assert.equal(nextStockTab("kpis", "ArrowLeft", tabs), "deep-dive");
  assert.equal(nextStockTab("kpis", "Home", tabs), "trends");
  assert.equal(nextStockTab("trends", "End", tabs), "kpis");
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
  assert.equal(tile.find((item) => item.id === "quarterly-notes").headline.display, "Q2 2026");
  assert.equal(tile.find((item) => item.id === "quarterly-notes").headline.label, "Latest quarter");
});

test("Trends tiles follow the sections that are actually on the page", () => {
  const present = ["price", "technical", "news", "filings"];
  const tiles = companyBookmarks("trends", { present, history: [{ close: 4 }, { close: 5 }] });
  assert.deepEqual(tiles.map((item) => item.id), present);
  assert.equal(tiles[0].anchorId, "trends-panel-price");
  assert.equal(tiles[0].headline.value, 5);
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
    "renderTechnicalSummary(session.selected",
    "renderMacroPanels(tickerData, chartData, chartDataState)",
    "renderContracts(tickerData)",
    "renderNews(tickerData)",
    "renderFilings(tickerData)",
    "renderDrivers(tickerData)",
    "renderAboutData()",
  ]) {
    assert.match(trends, new RegExp(piece.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(trends, /trends-layout dashboard-grid/);
  assert.match(trends, /dataset\.mobileView = "all"/);
  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  assert.match(page, /renderTrendsLayout\(tickerData, chartData, chartDataState\)/);
  assert.match(page, /page\.prepend\(trends\)/);
  assert.match(page, /renderQuarterlyNotes\(charts\.guidance\)/);
  assert.match(app.slice(app.indexOf("function renderQuarterlyNotes"), app.indexOf("function appendStatusAlerts")), /No quarterly notes yet/);
  assert.match(app, /navigateToStock\(ticker, \{ keepTab: true \}\)/);
  const navigate = app.slice(app.indexOf("function navigateToStock"), app.indexOf("function navigateToResearch"));
  assert.match(navigate, /keepTab/);
  assert.match(navigate, /session\.companyChart = null/);
  assert.match(app, /stockTabHash\(ticker, "trends"\)/);
  assert.doesNotMatch(page, /callsBlock|data-stock-panel = "calls"/);
});
