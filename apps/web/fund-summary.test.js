import assert from "node:assert/strict";
import test from "node:test";
import {
  CHANGE_ROWS, EMPTY_SUMMARY_COPY, NO_PREVIOUS_COPY, changeGroups, factsLine, managerSummaries, quarterLabel,
  renderFundSummaryPanels, sectorRows, topNames,
} from "./fund-summary.js";

class Node {
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.attrs = {}; this.textContent = ""; }
  append(...kids) { for (const k of kids) this.children.push(typeof k === "string" ? { text: k } : k); }
  setAttribute(k, v) { this.attrs[k] = v; }
  get text() { return [this.textContent, ...this.children.map((c) => c.text ?? "")].join(" "); }
  all(cls) { return [...(this.className?.split(" ").includes(cls) ? [this] : []), ...this.children.flatMap((c) => c.all?.(cls) || [])]; }
}
const document = { createElement: (tag) => new Node(tag) };

const change = (ticker, extra) => ({ ticker, name: `${ticker} Inc`, cusip: `${ticker}000`, ...extra });
const summary = {
  period_end: "2026-06-30", previous_period_end: "2026-03-31", positions: 190, value_total_usd: 15.4e9,
  top: Array.from({ length: 12 }, (_, i) => ({ ticker: i === 2 ? null : `T${i}`, name: `Name ${i}`, pct_portfolio: 12 - i, value_usd: 1e8 })),
  sectors: [{ sector: "Technology", pct_portfolio: 31.3, positions: 58 }, { sector: "Other", pct_portfolio: 1.1, positions: 18 }],
  sector_source: "SEC",
  changes: {
    new_buys: [change("SPCX", { value_usd: 765e6 })], full_sells: [change("HON", { value_prev_usd: 8.7e6 })],
    increases: Array.from({ length: 8 }, (_, i) => change(`U${i}`, { trade_value_usd: 1e8 - i, shares_change_pct: 50 })),
    decreases: [change("AMD", { trade_value_usd: -753e6, shares_change_pct: -40 })],
    counts: { new_buys: 16, full_sells: 7, increased: 79, decreased: 94, unchanged: 1 },
  },
};
const fundDoc = { managers: [{ cik: "1697748", name: "ARK Investment Management LLC", filed_at: "2026-08-14", summary_13f: summary }] };

test("labels and facts", () => {
  assert.equal(quarterLabel(summary, fundDoc.managers[0]), "Quarter ended Jun 30, 2026 · filed Aug 14, 2026 · SEC 13F");
  assert.deepEqual(factsLine(summary), ["$15.4B reported value", "190 positions"]);
});

test("top names are capped at 10 and fall back to the issuer name without a ticker", () => {
  const top = topNames(summary);
  assert.equal(top.length, 10);
  assert.equal(top[2].label, "Name 2");
});

test("sectors keep the backend order, Other included", () => {
  assert.deepEqual(sectorRows(summary).map((s) => s.sector), ["Technology", "Other"]);
});

test("QoQ groups carry the full counts and the top rows", () => {
  const groups = changeGroups(summary);
  assert.deepEqual(groups.map((g) => [g.key, g.count]), [["new_buys", 16], ["full_sells", 7], ["increases", 79], ["decreases", 94]]);
  assert.equal(groups[2].rows.length, CHANGE_ROWS);
  assert.equal(groups[3].rows[0].detail, "−$753M");
  assert.equal(groups[3].rows[0].sub, "-40% shares");
  assert.equal(groups[1].rows[0].detail, "was $8.7M");
  assert.equal(changeGroups({ ...summary, changes: null }), null);
});

test("renderer: one panel per manager, previous quarter missing, and empty states", () => {
  const [panel] = renderFundSummaryPanels(fundDoc, "ready", document);
  assert.match(panel.text, /Fund summary · ARK Investment Management LLC/);
  assert.match(panel.text, /Changes since Mar 31, 2026/);
  assert.equal(panel.all("fsum-change").length, 4);
  const [noPrev] = renderFundSummaryPanels({ managers: [{ ...fundDoc.managers[0], summary_13f: { ...summary, changes: null, previous_period_end: null } }] }, "ready", document);
  assert.match(noPrev.text, new RegExp(NO_PREVIOUS_COPY.slice(0, 30)));
  const [empty] = renderFundSummaryPanels({ managers: [{ cik: "1", name: "X" }] }, "ready", document);
  assert.match(empty.text, new RegExp(EMPTY_SUMMARY_COPY.slice(0, 20)));
  assert.deepEqual(renderFundSummaryPanels({ managers: [] }, "ready", document), []);
  assert.deepEqual(renderFundSummaryPanels(fundDoc, "loading", document), []);
  assert.equal(managerSummaries(null).length, 0);
});

test("styles use theme tokens only and have a phone layout; CI syntax-checks the new files", async () => {
  const { readFile } = await import("node:fs/promises");
  const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");
  const block = css.slice(css.indexOf("Fund summary panel (issue 223)"));
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}\b/);
  assert.match(block, /@media \(max-width: 640px\)/);
  const ci = await readFile(new URL("../../.github/workflows/ci.yml", import.meta.url), "utf8");
  assert.match(ci, /node --check apps\/web\/fund-holdings\.js/);
  assert.match(ci, /node --check apps\/web\/fund-summary\.js/);
});
