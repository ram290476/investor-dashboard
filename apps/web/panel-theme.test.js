import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { THEMES, themeProperties } from "./theme.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

function slice(start, end) {
  const from = app.indexOf(start);
  const to = app.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `${start} .. ${end}`);
  return app.slice(from, to);
}

test("the chart panel no longer renders an Open company page link", () => {
  const price = slice("function renderPricePanel", "function renderContracts");
  assert.doesNotMatch(price, /company-page-link|chart-entry|Open \$\{session\.selected\} page/);
  assert.doesNotMatch(app, /company-page-link|chart-entry/);
  assert.doesNotMatch(styles, /\.company-page-link|\.chart-entry/);
});

test("the ticker bar keeps its research arrow and does not gain a company-page arrow", () => {
  const watch = slice("function renderWatchlist", "function periodDirection");
  assert.match(watch, /ticker-research/);
  assert.match(watch, /navigateToResearch/);
  assert.doesNotMatch(watch, /ticker-stock-link|Open \$\{ticker\} page/);
});

test("government contracts use catalyst rows and month-day dates", () => {
  const contracts = slice("function renderContracts", "function renderContractRow");
  const row = slice("function renderContractRow", "function renderCatalystRow");
  assert.match(contracts, /catalyst-recent-list/);
  assert.match(contracts, /renderContractRow/);
  assert.match(contracts, /monthDay\(contracts\.as_of\)/);
  assert.match(contracts, /monthDay\(contracts\.observed_at\)/);
  assert.doesNotMatch(contracts, /news-list|news-item|news-title|formatTime\(contracts/);
  assert.match(row, /catalyst-recent-row record-row/);
  assert.match(row, /catalyst-dot/);
  assert.match(row, /monthDay\(row\.date\)/);
  assert.match(row, /monthDay\(row\.observed_at\)/);
  assert.match(row, /collected \$\{collected\}/);
  assert.match(row, /date\.dateTime = String\(row\.date \|\| ""\)\.slice\(0, 10\)/);
  assert.doesNotMatch(row, /news-meta|textContent[^;]*slice\(0,\s*10\)/);
});

test("about this data uses the compact themed button and the theme face", () => {
  const about = slice("function renderAboutData", "let marketTimer");
  assert.match(about, /overlay-chip panel-action/);
  assert.match(about, /View collection schedules and source health/);
  assert.doesNotMatch(about, /button-link/);
  assert.match(styles, /\.panel\[data-drawer="about-data"\][\s\S]*font-family:\s*"IBM Plex Sans"/);
  assert.match(styles, /\.panel\[data-drawer="about-data"\] \.overlay-chip/);
});

test("company page panels use panel chrome, month-day dates, and direction marks", () => {
  const page = slice("function renderStockPage", "function renderResearchPage");
  const card = slice("function renderMetricCard", "function renderStockPage");
  const mark = slice("function renderDirectionMark", "function renderMetricCard");
  assert.match(page, /panel stock-section/);
  assert.match(page, /monthDay\(session\.dashboard\?\.generated_at\)/);
  assert.match(page, /stockFreshnessText\([\s\S]*displayZone\(\)/);
  assert.match(page, /overlay-chip stock-tab/);
  assert.match(card, /renderDirectionMark\("QoQ"/);
  assert.match(card, /renderDirectionMark\("YoY"/);
  assert.match(mark, /Uptrend/);
  assert.match(mark, /Downtrend/);
  assert.match(mark, /catalyst-dot/);
  assert.match(mark, /var\(--green\)/);
  assert.match(mark, /var\(--red\)/);
  assert.match(styles, /\.stock-section\s*\{[^}]*font-family:\s*"IBM Plex Sans"/);
  assert.match(styles, /\.direction-mark-up \.direction-word\s*\{[^}]*var\(--green\)/);
  assert.match(styles, /\.direction-mark-down \.direction-word\s*\{[^}]*var\(--red\)/);
  assert.match(styles, /\.stock-page-tabs \.stock-tab\s*\{/);
  assert.doesNotMatch(styles, /\.direction-mark[^{]*\{[^}]*#[0-9a-fA-F]{3,8}/);
  assert.doesNotMatch(styles, /\.record-meta[^{]*\{[^}]*#[0-9a-fA-F]{3,8}/);
  assert.doesNotMatch(styles, /\.stock-section\s*\{[^}]*#[0-9a-fA-F]{3,8}/);
});

test("filings, news, and the dashboard company panel stay the reference", () => {
  const filings = slice("function renderFilings", "function renderCompanyPanel");
  const news = slice("function renderNewsHeadlineGroups", "function filingsSummary");
  const company = slice("function renderCompanyPanel", "function renderAboutData");
  assert.match(filings, /renderCatalystSection/);
  assert.doesNotMatch(filings, /renderContractRow|renderDirectionMark|record-row/);
  assert.match(news, /variant:\s*"news"/);
  assert.doesNotMatch(news, /renderContractRow|renderDirectionMark|record-row/);
  assert.match(company, /button-link/);
  assert.match(company, /signal-note/);
  assert.doesNotMatch(company, /catalyst-recent-row|renderDirectionMark|monthDay\(/);
});

test("themed panel colors stay on theme tokens for all six themes", () => {
  for (const theme of THEMES) {
    const properties = themeProperties(theme.id, "green-red");
    assert.ok(properties["--text"].startsWith("#"), theme.id);
    assert.ok(properties["--green"].startsWith("#"), theme.id);
    assert.ok(properties["--red"].startsWith("#"), theme.id);
    assert.ok(properties["--surface"].startsWith("#"), theme.id);
  }
});
