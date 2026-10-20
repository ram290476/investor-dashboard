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

test("company page panels use panel chrome and direction marks", () => {
  const page = slice("function renderStockPage", "function renderResearchPage");
  const card = slice("function renderMetricCard", "function renderStockPage");
  const mark = slice("function renderDirectionMark", "function renderMetricCard");
  assert.match(page, /panel stock-section/);
  assert.match(page, /companyName\(session\.selected, payload, tickerData\)/);
  assert.doesNotMatch(page, /monthDay\(session\.dashboard\?\.generated_at\)/);
  assert.doesNotMatch(page, /stockFreshnessText\(/);
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

test("chart cards omit review badges and the review toggle sits above Operating KPIs", () => {
  const card = slice("function renderMetricCard", "async function submitMetricReview");
  assert.doesNotMatch(card, /renderReviewBadge/);
  assert.doesNotMatch(card, /Pending review/);
  assert.doesNotMatch(card, /stock-metric-pending/);
  assert.doesNotMatch(card, /metric-status/);
  const groups = slice("function appendMetricGroups", "function renderStockPage");
  assert.doesNotMatch(groups, /Pending review/);
  assert.doesNotMatch(groups, /renderReviewBadge/);
  const page = slice("function renderStockPage", "function renderResearchPage");
  const heading = page.slice(0, page.indexOf("const page = node"));
  assert.doesNotMatch(heading, /renderMetricReview/);
  assert.match(page, /id === "kpis"\) block\.append\(renderMetricReview\(\)\)/);
  const review = styles.slice(styles.indexOf(".metric-review {"), styles.indexOf(".metric-review-option {"));
  assert.match(review, /margin-bottom:\s*var\(--space-3\)/);
  assert.doesNotMatch(review, /margin(?:-top|-bottom)?:\s*-/);
  assert.doesNotMatch(review, /position:\s*absolute/);
  assert.doesNotMatch(styles, /\.metric-review-option\s*\{[^}]*position:\s*absolute/);
  assert.match(styles, /\.metric-review-option\[aria-pressed="true"\]\s*\{[^}]*background:\s*var\(--selected\)/);
  const toggle = slice("function readStoredMetricReview", "function renderCompanyPanel");
  assert.match(toggle, /setAttribute\("aria-pressed", String\(mode === id\)\)/);
  assert.match(toggle, /storeMetricReview\(id\)/);
  assert.match(toggle, /localPrefs\.getItem\("investor-dashboard\.metric-review"\)/);
  assert.match(toggle, /localPrefs\.setItem\("investor-dashboard\.metric-review"/);
  const price = slice("function renderPricePanel", "function renderContracts");
  assert.match(price, /mergeCompanyFundamentals\(/);
  const appended = page.slice(page.indexOf("page.append("));
  const metricsAt = appended.indexOf("renderCompanyMetrics(charts, pageState)");
  const kpisAt = appended.indexOf('section("kpis", "Operating KPIs", charts.operating');
  const notesAt = appended.indexOf("renderQuarterlyNotes(charts.guidance)");
  assert.match(page, /stockPanels\(payload, chartData, \{ \.\.\.periodOptions, review \}\)/);
  assert.doesNotMatch(page, /review: "all"/);
  assert.match(page, /charts\.fundamentals/);
  assert.match(page, /charts\.guidance/);
  assert.ok(kpisAt > 0 && metricsAt > kpisAt && notesAt > metricsAt);
  assert.match(app, /No quarterly notes yet/);
  assert.doesNotMatch(page, /callsBlock/);
  const phone = styles.slice(styles.indexOf(".stock-page [data-stock-panel]"), styles.indexOf("/* Data refresh"));
  assert.match(phone, /\.stock-page\[data-stock-tab="admin"\] \.company-metrics\s*\{[^}]*display:\s*grid/);
  assert.doesNotMatch(phone, /\.stock-page\[data-stock-tab="overview"\] \.company-metrics/);
  assert.match(phone, /\.stock-page\[data-stock-tab="trends"\] \[data-stock-panel="trends"\][\s\S]*?display:\s*block/);
  assert.doesNotMatch(phone, /data-stock-panel="calls"/);
  const metrics = slice("function renderCompanyMetrics", "function appendMetricGroups");
  assert.match(metrics, /Pending review/);
  assert.match(metrics, /renderReviewBadge\(\)/);
  assert.match(metrics, /"Approve"/);
  assert.match(metrics, /"Undo"/);
  assert.match(metrics, /canApproveMetrics/);
  assert.match(metrics, /metric-review-toast/);
  assert.match(metrics, /role", "alert"/);
  assert.match(metrics, /dataset\.metricReviewError/);
  assert.match(metrics, /stock-section company-metrics/);
  const submit = slice("async function submitMetricReview", "function renderCompanyMetrics");
  assert.match(submit, /method:\s*"PUT"|apiPut\(/);
  assert.match(submit, /stock\/\$\{encodeURIComponent\(session\.selected\)\}\/metrics\/\$\{encodeURIComponent\(metricId\)\}/);
  assert.match(submit, /Authorization: `Bearer \$\{session\.accessToken\}`|apiPut\(/);
  assert.match(submit, /session\.stockPage\[session\.selected\] = saved/);
  assert.match(submit, /session\.metricReviewError = error\.message/);
  assert.doesNotMatch(submit, /if \(session\.metricSave \|\| !canApproveMetrics/);
  assert.match(styles, /\.metric-review-toast\s*\{[^}]*color:\s*var\(--error\)/);
  const dashboard = slice("function renderDashboard()", "async function refreshData");
  assert.doesNotMatch(dashboard, /renderCompanyPanel/);
  const research = slice("function renderResearchPage", "function renderNotFoundPage");
  assert.match(research, /renderCompanyPanel/);
  const tabs = slice("const TAB_DETAIL_IDS", "function panelLabel");
  assert.doesNotMatch(tabs, /"company"/);
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
