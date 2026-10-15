import assert from "node:assert/strict";
import test from "node:test";
import {
  NOT_STAMPED, flagRows, renderFundFlagAdmin, resetConfirmText, resetOutcome, resetStatus, typeLabel,
} from "./fund-flag-admin.js";
import { canApproveMetrics } from "./settings-model.js";

class Node {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attrs = {}; this.textContent = ""; this.listeners = {};
  }
  append(...kids) { this.children.push(...kids); }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  get text() { return [this.textContent, ...this.children.map((c) => c.text ?? "")].join(" "); }
  find(pred) { return pred(this) ? [this] : this.children.flatMap((c) => c.find?.(pred) || []); }
  byClass(cls) { return this.find((n) => n.className?.split(" ").includes(cls)); }
}
const document = { createElement: (tag) => new Node(tag) };
const rows = [{ ticker: "ARKK", is_fund: true }, { ticker: "TSLA", is_fund: false }, { ticker: "NEW" }];

test("Type column: Fund, Stock or Not stamped", () => {
  assert.equal(typeLabel(true), "Fund");
  assert.equal(typeLabel(false), "Stock");
  assert.equal(typeLabel(undefined), NOT_STAMPED);
  assert.deepEqual(flagRows(rows).map((r) => r.type), ["Fund", "Stock", "Not stamped"]);
  // After a reset (or a 404 'no flag stored') the row shows Not stamped until the next collection.
  assert.equal(flagRows(rows, { ARKK: { state: "done" } })[0].type, NOT_STAMPED);
  assert.equal(flagRows(rows, { TSLA: { state: "forbidden" } })[1].type, "Stock");
});

test("confirmation text and API outcomes map to success, 404 and 403 states", () => {
  assert.equal(resetConfirmText("ARKK"), "Clear the stored fund flag for ARKK? The next collection will re-stamp it.");
  assert.deepEqual(resetOutcome(null), { state: "done" });
  assert.deepEqual(resetOutcome({ status: 404 }), { state: "missing" });
  assert.deepEqual(resetOutcome({ status: 403 }), { state: "forbidden" });
  assert.equal(resetOutcome({ status: 502, message: "boom" }).state, "error");
  assert.match(resetStatus("ARKK", { state: "done" }).text, /cleared for ARKK/);
  assert.equal(resetStatus("ARKK", { state: "missing" }).text, "No flag stored for ARKK.");
  assert.match(resetStatus("ARKK", { state: "forbidden" }).text, /Only an admin/);
  assert.equal(resetStatus("ARKK", null), null);
});

test("renderer: a table with Ticker and Type, a picker and the Reset button for the selection", () => {
  const picked = [];
  const reset = [];
  const section = renderFundFlagAdmin({ rows, selected: "TSLA", outcomes: { TSLA: { state: "missing" } } }, document, {
    onSelect: (t) => picked.push(t), onReset: (t) => reset.push(t),
  });
  const headers = section.find((n) => n.tagName === "th").map((n) => n.textContent);
  assert.deepEqual(headers, ["", "Ticker", "Type"]);
  const types = section.byClass("ffa-col-type").filter((n) => n.tagName === "td").map((n) => n.textContent);
  assert.deepEqual(types, ["Fund", "Not stamped", "Not stamped"]);
  const radios = section.find((n) => n.type === "radio");
  assert.deepEqual(radios.map((r) => r.checked), [false, true, false]);
  radios[0].listeners.change();
  assert.deepEqual(picked, ["ARKK"]);
  const [button] = section.byClass("ffa-reset");
  assert.equal(button.textContent, "Reset fund flag for TSLA");
  button.listeners.click();
  assert.deepEqual(reset, ["TSLA"]);
  assert.match(section.text, /No flag stored for TSLA/);
  const saving = renderFundFlagAdmin({ rows, selected: "ARKK", outcomes: { ARKK: { state: "saving" } } }, document);
  assert.equal(saving.byClass("ffa-reset")[0].disabled, true);
  assert.match(renderFundFlagAdmin({ rows: [], outcomes: {} }, document).text, /Add tickers/);
});

test("admin detection is the metric-approval check, and the demo account is never an admin", () => {
  const token = (claims) => `h.${Buffer.from(JSON.stringify(claims)).toString("base64url")}.s`;
  assert.equal(canApproveMetrics(token({ "cognito:groups": ["admin"], username: "owner@example.com" }), "owner@example.com"), true);
  assert.equal(canApproveMetrics(token({ "cognito:groups": ["admin"] }), "ram.vellamsetti@gmail.com"), false);
  assert.equal(canApproveMetrics(token({ "cognito:groups": [] }), "owner@example.com"), false);
});

test("wiring: the reset lives in the Account settings Admin tab, never on the company page", async () => {
  const { readFile } = await import("node:fs/promises");
  const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
  const settings = await readFile(new URL("./account-settings.js", import.meta.url), "utf8");
  const summary = await readFile(new URL("./fund-summary.js", import.meta.url), "utf8");
  assert.match(app, /isAdmin: \(\) => canApproveMetrics\(session\.accessToken, session\.email\)/);
  assert.match(app, /apiPost\(`admin\/fund-flag\/\$\{encodeURIComponent\(ticker\)\}\/reset`/);
  assert.match(settings, /settingsTabs\(isAdmin\(\)\)/);
  assert.match(settings, /if \(!ask\(resetConfirmText\(ticker\)\)\) return;/);
  assert.ok(settings.indexOf("if (!ask(") < settings.indexOf("await ctx.resetFundFlag(ticker)"));
  assert.doesNotMatch(summary, /reset/i);
  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  assert.doesNotMatch(page, /fund-flag|resetFundFlag/);
  const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");
  assert.doesNotMatch(css.slice(css.indexOf("Admin tab: fund flag reset")), /#[0-9a-fA-F]{3,8}\b/);
});
