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

test("the chart panel opens the company page from the right of the live price", () => {
  const price = slice("function renderPricePanel", "function renderContracts");
  assert.match(price, /node\("a", "company-page-link", "Open company page"\)/);
  assert.match(price, /navigateToStock\(session\.selected\)/);
  assert.match(price, /overview-side/);
  assert.equal(price.match(/company-page-link/g).length, 1);
  assert.doesNotMatch(price, /chart-entry|Open \$\{session\.selected\} page/);
  const watch = slice("function renderWatchlist", "function periodDirection");
  assert.doesNotMatch(watch, /company-page-link/);
  assert.match(styles, /\.overview-side\s*\{[^}]*align-items:\s*flex-end/);
  assert.match(styles, /\.overview-side\s*\{[^}]*margin-left:\s*auto/);
  assert.match(styles, /\.company-page-link\s*\{[^}]*var\(--blue\)/);
  assert.doesNotMatch(styles, /\.company-page-link[^{]*\{[^}]*#[0-9a-fA-F]{3,8}/);
});

test("the ticker bar has no research arrow on desktop or mobile", () => {
  const watch = slice("function renderWatchlist", "function periodDirection");
  assert.doesNotMatch(watch, /ticker-research/);
  assert.doesNotMatch(watch, /navigateToResearch/);
  assert.doesNotMatch(watch, /Open \$\{ticker\} research/);
  assert.match(watch, /event\.key === "Enter"/);
  assert.match(watch, /LONG_PRESS_MS/);
  assert.match(watch, /navigateToStock\(ticker\)/);
  assert.doesNotMatch(watch, /ticker-stock-link/);
  assert.doesNotMatch(watch, /Open \$\{ticker\} page/);
  assert.doesNotMatch(styles, /\.ticker-stock-link/);
  assert.doesNotMatch(styles, /\.ticker-research/);
  const phone = styles.slice(styles.indexOf("@media (max-width: 640px)"), styles.indexOf(".stock-page-tabs {"));
  assert.doesNotMatch(phone, /ticker-research/);
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

test("filings, news, and the company themes panel stay the reference", () => {
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
