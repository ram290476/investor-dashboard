import assert from "node:assert/strict";
import test from "node:test";
import {
  NOT_STAMPED, TYPE_LOADING, adminFundFlagRow, flagRows, paintFundFlagOutcome, renderFundFlagAdmin,
  resetConfirmText, resetOutcome, resetStatus, typeLabel,
} from "./fund-flag-admin.js";
import { canApproveMetrics } from "./settings-model.js";

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.attrs = {};
    this.textContent = "";
    this.listeners = {};
    this.className = "";
    this.scrollTop = 0;
    this.parentElement = null;
    this.ownerDocument = null;
  }
  append(...kids) {
    for (const kid of kids) {
      if (kid) kid.parentElement = this;
      this.children.push(kid);
    }
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  remove() {
    const parent = this.parentElement;
    if (!parent) return;
    parent.children = parent.children.filter((child) => child !== this);
    this.parentElement = null;
  }
  get text() { return [this.textContent, ...this.children.map((c) => c.text ?? "")].join(" "); }
  find(pred) { return pred(this) ? [this] : this.children.flatMap((c) => c.find?.(pred) || []); }
  byClass(cls) { return this.find((n) => (n.className || "").split(" ").includes(cls)); }
  matches(selector) {
    if (selector.startsWith(".")) return (this.className || "").split(/\s+/).includes(selector.slice(1));
    const attr = /^(\w+)(?:\[(\w+)="([^"]+)"\])?$/.exec(selector);
    if (!attr || this.tagName !== attr[1]) return false;
    if (!attr[2]) return true;
    return String(this[attr[2]] ?? this.attrs[attr[2]] ?? "") === attr[3];
  }
  querySelectorAll(selector) {
    return this.children.flatMap((child) => child.find((node) => node.matches(selector)));
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  get classList() {
    const node = this;
    const parts = () => (node.className || "").split(/\s+/).filter(Boolean);
    return {
      contains: (name) => parts().includes(name),
      toggle(name, force) {
        const has = parts().includes(name);
        const on = force === undefined ? !has : Boolean(force);
        const next = parts().filter((item) => item !== name);
        if (on) next.push(name);
        node.className = next.join(" ");
        return on;
      },
    };
  }
}
const document = {
  createElement(tag) {
    const node = new Node(tag);
    node.ownerDocument = this;
    return node;
  },
};
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

test("missing is_fund falls back to instrument_kind fund, and a dashboard that has not loaded stays loading", () => {
  assert.equal(adminFundFlagRow("ONE", undefined, "loading").loading, true);
  assert.equal(flagRows([adminFundFlagRow("ONE", { instrument_kind: "fund" }, "loading")])[0].type, TYPE_LOADING);
  assert.equal(flagRows([adminFundFlagRow("ONE", null, "error")])[0].type, NOT_STAMPED);
  const knownFund = adminFundFlagRow("ONE", { instrument_kind: "fund" }, "ready");
  assert.equal(knownFund.is_fund, true);
  assert.equal(flagRows([knownFund])[0].type, "Fund");
  assert.equal(flagRows([adminFundFlagRow("TWO", { is_fund: false, instrument_kind: "fund" }, "ready")])[0].type, "Stock");
  assert.equal(flagRows([adminFundFlagRow("THREE", { is_fund: true, instrument_kind: "operating" }, "ready")])[0].type, "Fund");
  assert.equal(flagRows([adminFundFlagRow("FOUR", { instrument_kind: "operating" }, "ready")])[0].type, NOT_STAMPED);
  assert.equal(flagRows([adminFundFlagRow("FIVE", {}, "ready")])[0].type, NOT_STAMPED);
});

test("selecting a row keeps the same table node and its scrollTop", () => {
  const panel = new Node("div");
  panel.className = "settings-panel";
  panel.scrollTop = 90;
  const reset = [];
  const section = renderFundFlagAdmin({ rows, selected: "TSLA", outcomes: {} }, document, {
    onSelect: () => {},
    onReset: (ticker) => reset.push(ticker),
  });
  panel.append(section);
  const wrap = section.byClass("fund-flag-table-wrap")[0];
  const table = section.byClass("fund-flag-table")[0];
  wrap.scrollTop = 40;
  table.scrollTop = 15;
  const radios = section.find((n) => n.type === "radio");
  radios[0].listeners.change();
  assert.equal(section.byClass("fund-flag-table")[0], table);
  assert.equal(table.scrollTop, 15);
  assert.equal(wrap.scrollTop, 40);
  assert.equal(panel.scrollTop, 90);
  const selectedRows = section.byClass("is-selected");
  assert.equal(selectedRows.length, 1);
  assert.equal(selectedRows[0].dataset.ticker, "ARKK");
  assert.equal(selectedRows[0].attrs["aria-selected"], "true");
  assert.equal(section.byClass("ffa-row").filter((row) => row.attrs["aria-selected"]).length, 1);
  assert.equal(radios[0].checked, true);
  assert.equal(radios[1].checked, false);
  const [button] = section.byClass("ffa-reset");
  assert.equal(button.textContent, "Reset fund flag for ARKK");
  assert.equal(button.dataset.ticker, "ARKK");
  button.listeners.click();
  assert.deepEqual(reset, ["ARKK"]);
});

test("reset updates the status and type cell in place and keeps the table scroll", () => {
  const section = renderFundFlagAdmin({ rows, selected: "ARKK", outcomes: {} }, document);
  const table = section.byClass("fund-flag-table")[0];
  const wrap = section.byClass("fund-flag-table-wrap")[0];
  table.scrollTop = 22;
  wrap.scrollTop = 33;
  paintFundFlagOutcome(section, "ARKK", { state: "saving" });
  assert.match(section.text, /Resetting ARKK/);
  assert.equal(section.byClass("ffa-reset")[0].disabled, true);
  assert.equal(section.byClass("fund-flag-table")[0], table);
  const arkk = section.byClass("ffa-row").find((row) => row.dataset.ticker === "ARKK");
  const type = arkk.querySelector(".ffa-col-type");
  assert.equal(type.textContent, "Fund");
  paintFundFlagOutcome(section, "ARKK", { state: "done" });
  assert.equal(type.textContent, NOT_STAMPED);
  assert.match(section.text, /cleared for ARKK/);
  assert.equal(section.byClass("ffa-reset")[0].disabled, false);
  assert.equal(section.byClass("fund-flag-table")[0], table);
  assert.equal(table.scrollTop, 22);
  assert.equal(wrap.scrollTop, 33);
  assert.equal(section.byClass("is-selected")[0], arkk);
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
  assert.match(section.text, /No flag stored for TSLA/);
  radios[0].listeners.change();
  assert.deepEqual(picked, ["ARKK"]);
  const [button] = section.byClass("ffa-reset");
  assert.equal(button.textContent, "Reset fund flag for ARKK");
  button.listeners.click();
  assert.deepEqual(reset, ["ARKK"]);
  assert.doesNotMatch(section.text, /No flag stored for TSLA/);
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
  const adminTab = settings.slice(settings.indexOf("function renderAdminTab"), settings.indexOf("async function resetFundFlag"));
  assert.doesNotMatch(adminTab, /renderPanel\(/);
  const resetFn = settings.slice(settings.indexOf("async function resetFundFlag"), settings.indexOf("function renderTabError"));
  assert.doesNotMatch(resetFn, /renderPanel\(/);
  assert.match(resetFn, /paintFundFlagOutcome\(/);
  assert.match(app, /adminFundFlagRow\(ticker, session\.dashboard\?\.tickers\?\.\[ticker\], session\.dashboardState\)/);
  assert.doesNotMatch(summary, /reset/i);
  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  assert.doesNotMatch(page, /fund-flag|resetFundFlag/);
  const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");
  assert.doesNotMatch(css.slice(css.indexOf("Admin tab: fund flag reset")), /#[0-9a-fA-F]{3,8}\b/);
  for (const rule of [css.match(/\.ffa-row\.is-selected td \{[^}]*\}/)[0], css.match(/\.ffa-row\[aria-selected="true"\] td \{[^}]*\}/)[0]]) {
    assert.match(rule, /color-mix\(in srgb, var\(--blue\) 14%, transparent\)/);
    assert.match(rule, /box-shadow:\s*inset 3px 0 0 var\(--blue\)/);
    assert.match(rule, /font-weight:\s*600/);
  }
});
