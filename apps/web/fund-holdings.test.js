import assert from "node:assert/strict";
import test from "node:test";
import {
  CONFLICT_NOTES, DAILY_UNAVAILABLE_NOTE, EMPTY_COPY, classificationNote, TOP_N, UNNAMED, asOfLabel, defaultUiState, factsLine, fallbackNote, formatDate,
  formatPct, formatUsd, holdingName, isFund, nextSort, panelState, toggleLabel, visibleRows,
} from "./fund-holdings.js";
import { fundRetryDelayMs as _fundRetryDelayMs } from "./fund-holdings.js";

const holdings = Array.from({ length: 14 }, (_, i) => ({
  ticker: i === 3 ? null : `T${i}`, name: i === 5 ? "N/A" : `Holding ${i}`, title: i === 5 ? "SpaceX SPV" : null,
  cusip: i === 3 ? "000000001" : `C${i}`, value_usd: (14 - i) * 1e6, pct_nav: (14 - i) / 2,
}));
const doc = {
  ticker: "ARKK", status: "ok", holdings_source: "sec_nport", holdings_as_of: "2026-07-31", period_end: "2026-07-31",
  filed_at: "2026-09-22", net_assets_usd: 5.56e9, net_assets_source: "sec_nport", holdings,
  profile: { source: "yahoo", expense_ratio: 0.0075, category: "Mid-Cap Growth", family: "ARK ETF Trust" },
};

test("only fund tickers get the panel", () => {
  assert.equal(isFund({ instrument_kind: "fund" }), true);
  assert.equal(isFund({}), false);
  assert.equal(isFund(null), false);
});

test("the write-once is_fund stamp wins over instrument_kind; conflicts get a note", () => {
  assert.equal(isFund({ is_fund: true, instrument_kind: "operating" }), true);
  assert.equal(isFund({ is_fund: false, instrument_kind: "fund" }), false);
  assert.equal(isFund({ instrument_kind: "fund" }), true);
  assert.equal(classificationNote({ is_fund: true }), null);
  assert.equal(classificationNote({ fund_classification_conflict: "is_fund_stamped_true_but_now_operating" }),
    CONFLICT_NOTES.is_fund_stamped_true_but_now_operating);
  assert.match(classificationNote({ fund_classification_conflict: "something_new" }), /under review/);
});

test("N/A names fall back to issuer, then title, then Unnamed holding", () => {
  assert.equal(holdingName({ name: "N/A", issuer: "Space Exploration Technologies" }), "Space Exploration Technologies");
  assert.equal(holdingName({ name: " n/a ", title: "SpaceX SPV" }), "SpaceX SPV");
  assert.equal(holdingName({ name: "", title: "N/A" }), UNNAMED);
  assert.equal(holdingName({ name: "TESLA INC" }), "TESLA INC");
});

test("as-of label names the source", () => {
  assert.equal(asOfLabel(doc), "As of Jul 31, 2026 · SEC N-PORT, filed Sep 22, 2026");
  assert.equal(asOfLabel({ holdings_source: "yahoo_top_holdings", holdings_as_of: "2026-10-13" }),
    "Top holdings via Yahoo Finance · Oct 13, 2026");
  assert.equal(asOfLabel({ holdings_source: null }), "");
});

test("fallback note says daily holdings are unavailable when showing the SEC filing", () => {
  assert.equal(fallbackNote(doc), DAILY_UNAVAILABLE_NOTE);
  assert.equal(fallbackNote({ holdings_source: "yahoo_top_holdings", holdings_note: "SEC not yet" }), "SEC not yet");
  assert.equal(fallbackNote({ holdings_source: "ark_daily" }), "");
});

test("facts line", () => {
  assert.deepEqual(factsLine(doc), ["Net assets $5.6B (SEC N-PORT)", "14 holdings", "Expense ratio 0.75%",
    "Mid-Cap Growth", "ARK ETF Trust"]);
});

test("formatting", () => {
  assert.equal(formatUsd(64745993.4), "$64.7M");
  assert.equal(formatUsd(null), "—");
  assert.equal(formatPct(1.164), "1.16%");
  assert.equal(formatDate("2026-09-22"), "Sep 22, 2026");
});

test("states: loading, empty, error, ready", () => {
  assert.equal(panelState(null, "loading"), "loading");
  assert.equal(panelState(null, "missing"), "empty");
  assert.equal(panelState({ holdings: [] }, "ready"), "empty");
  assert.equal(panelState(null, "error"), "error");
  assert.equal(panelState(doc, "ready"), "ready");
  assert.match(EMPTY_COPY, /about 60 days/);
});

test("collapsed shows the top 10 by % NAV; expanded shows all", () => {
  const ui = defaultUiState();
  const top = visibleRows(holdings, ui);
  assert.equal(top.rows.length, TOP_N);
  assert.equal(top.rows[0].ticker, "T0");
  assert.equal(top.rows[5].displayName, "SpaceX SPV");
  assert.equal(toggleLabel(ui, top.total), "Show all 14 holdings");
  const all = visibleRows(holdings, { ...ui, expanded: true });
  assert.equal(all.rows.length, 14);
  assert.equal(toggleLabel({ expanded: true }, 14), "Show top 10");
});

test("collapsed top 10 ignores the sort; expanded honours it", () => {
  const sorted = { ...defaultUiState(), sort: "name", direction: "asc" };
  assert.equal(visibleRows(holdings, sorted).rows[0].ticker, "T0");
  const expanded = nextSort(defaultUiState(), "name");
  assert.equal(expanded.expanded, true);
  assert.equal(visibleRows(holdings, expanded).rows[0].displayName, "Holding 0");
  assert.equal(nextSort(expanded, "name").direction, "desc");
});

test("search covers all holdings, suspends the collapse and matches CUSIP", () => {
  const view = visibleRows(holdings, { ...defaultUiState(), query: "holding 1" });
  assert.equal(view.filtered, true);
  assert.deepEqual(view.rows.map((r) => r.ticker), ["T1", "T10", "T11", "T12", "T13"]);
  assert.equal(visibleRows(holdings, { ...defaultUiState(), query: "000000001" }).rows[0].cusip, "000000001");
});

test("watchlist holdings are starred and can be filtered", () => {
  const view = visibleRows(holdings, { ...defaultUiState(), onlyWatchlist: true }, ["t2", "T7"]);
  assert.deepEqual(view.rows.map((r) => r.ticker), ["T2", "T7"]);
  assert.ok(view.rows.every((r) => r.watched));
});

test("the company page shows the panel only for funds and fetches GET /fund/<T> lazily", async () => {
  const { readFile } = await import("node:fs/promises");
  const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
  const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");
  assert.match(app, /import \{ classificationNote, fundRetryDelayMs, isFund, renderFundHoldingsPanel \} from "\.\/fund-holdings\.js"/);
  // #227: an "error" load waits out its backoff unless forced (Refresh data), so re-renders cannot loop.
  const loader = app.slice(app.indexOf("async function loadFundHoldings"), app.indexOf("function renderFundPanel"));
  assert.match(loader, /!force && session\.fundState\[ticker\] === "error" && retry && Date\.now\(\) < retry\.notBefore\) return;/);
  assert.match(loader, /notBefore: Date\.now\(\) \+ fundRetryDelayMs\(attempts\)/);
  assert.match(app, /apiGet\(`fund\/\$\{encodeURIComponent\(ticker\)\}`\)/);
  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  const gate = page.indexOf("const fund = fundTicker");
  assert.ok(gate > 0 && page.indexOf("loadFundHoldings(session.selected)") > gate);
  assert.match(page, /const fundTicker = isFund\(tickerData\)/);
  assert.match(page, /stockTabs\(payload, \{ admin: metricAdmin, fund: fundTicker \}\)/);
  assert.doesNotMatch(page, /ARKK|ARKF|ARKVX|AGIQ|XOVR|SPCX/);
  // Holdings and the summary stay in the page, and the Fund tab is the only place they show.
  const fundBranch = page.slice(gate, page.indexOf("page.append(", gate));
  assert.match(fundBranch, /page\.dataset\.instrumentKind = "fund"/);
  assert.match(fundBranch, /renderFundPanel\(session\.selected, classificationNote\(tickerData\)\)/);
  assert.match(fundBranch, /renderFundSummary\(session\.selected\)/);
  assert.doesNotMatch(fundBranch, /page\.dataset\.stockTab = "overview"/);
  assert.doesNotMatch(fundBranch, /return;/);
  const appended = page.slice(page.indexOf("page.append(", gate));
  assert.match(appended, /renderTechnicalSummary\(session\.selected, history\),\s+\.\.\.fundExtras/);
  assert.match(appended, /section\("kpis"/);
  const holdings = await readFile(new URL("./fund-holdings.js", import.meta.url), "utf8");
  assert.match(holdings, /dataset\.stockPanel = FUND_TAB_ID/);
  assert.doesNotMatch(holdings, /dataset\.stockPanel = ANALYSIS_TAB_ID/);
  const summary = await readFile(new URL("./fund-summary.js", import.meta.url), "utf8");
  assert.match(summary, /dataset\.stockPanel = FUND_TAB_ID/);
  assert.doesNotMatch(summary, /ANALYSIS_TAB_ID/);
  const phone = css.slice(css.indexOf(".stock-page [data-stock-panel]"), css.indexOf("/* Data refresh"));
  assert.match(phone, /\.stock-page\[data-stock-tab="fund"\] \[data-stock-panel="fund"\]/);
  assert.match(phone, /@media \(max-width: 640px\)[\s\S]*\.stock-page\[data-stock-tab="fund"\] \[data-stock-panel="fund"\]/);
  const panelCss = css.slice(css.indexOf("Fund holdings panel (issue 223)"));
  assert.doesNotMatch(panelCss, /#[0-9a-fA-F]{3,8}\b/);
  assert.match(panelCss, /@media \(max-width: 640px\)/);
});

test("fundRetryDelayMs backs off exponentially and caps at 5 minutes (#227)", () => {
  assert.equal(_fundRetryDelayMs(1), 5000);
  assert.equal(_fundRetryDelayMs(2), 10000);
  assert.equal(_fundRetryDelayMs(3), 20000);
  assert.equal(_fundRetryDelayMs(20), 300000);
  assert.equal(_fundRetryDelayMs(0), 5000);
});
