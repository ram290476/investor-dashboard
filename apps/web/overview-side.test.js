import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  OVERVIEW_SIDE_KEY,
  applyOverviewSideColumn,
  readOverviewSideCollapsed,
  resizeOverviewCharts,
  writeOverviewSideCollapsed,
} from "./overview-side.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const source = await readFile(new URL("./overview-side.js", import.meta.url), "utf8");

function memoryStorage(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => { map.set(key, String(value)); },
  };
}

function button() {
  const attrs = {};
  return {
    attrs,
    type: "",
    textContent: "",
    setAttribute(key, value) { attrs[key] = String(value); },
    getAttribute(key) { return attrs[key]; },
  };
}

test("the macro column choice is stored and restored", () => {
  const storage = memoryStorage();
  assert.equal(readOverviewSideCollapsed(storage), false);
  assert.equal(readOverviewSideCollapsed(null), false);
  writeOverviewSideCollapsed(storage, true);
  assert.equal(storage.getItem(OVERVIEW_SIDE_KEY), "collapsed");
  assert.equal(readOverviewSideCollapsed(storage), true);
  writeOverviewSideCollapsed(storage, false);
  assert.equal(storage.getItem(OVERVIEW_SIDE_KEY), "expanded");
  assert.equal(readOverviewSideCollapsed(storage), false);
  assert.equal(readOverviewSideCollapsed({ getItem() { throw new Error("blocked"); } }), false);
  assert.doesNotThrow(() => writeOverviewSideCollapsed({ setItem() { throw new Error("blocked"); } }, true));
  assert.doesNotMatch(source, /TSLA|NVDA|SPCX|AAPL/);
});

test("the toggle is a button with aria-expanded and hides the column", () => {
  const layout = { dataset: {} };
  const side = { id: "", hidden: false };
  const control = button();
  applyOverviewSideColumn(layout, side, control, false);
  assert.equal(side.id, "overview-side-column");
  assert.equal(side.hidden, false);
  assert.equal(layout.dataset.sideCollapsed, "false");
  assert.equal(control.type, "button");
  assert.equal(control.getAttribute("aria-expanded"), "true");
  assert.equal(control.getAttribute("aria-controls"), "overview-side-column");
  assert.equal(control.textContent, "Hide macro signals");
  applyOverviewSideColumn(layout, side, control, true);
  assert.equal(side.hidden, true);
  assert.equal(layout.dataset.sideCollapsed, "true");
  assert.equal(control.getAttribute("aria-expanded"), "false");
  assert.equal(control.textContent, "Show macro signals");
  assert.match(control.getAttribute("aria-label"), /Show macro signals column/);
});

test("toggling the column resizes the price chart", () => {
  const seen = [];
  const chart = {
    style: {},
    getBoundingClientRect() { seen.push("measure"); return { width: 900 }; },
  };
  const lane = { style: {}, getBoundingClientRect() { seen.push("lane"); return { width: 900 }; } };
  const root = {
    querySelectorAll(selector) {
      assert.equal(selector, ".price-chart, .lane-chart");
      return [chart, lane];
    },
  };
  let resized = 0;
  resizeOverviewCharts(root, {
    requestAnimationFrame(fn) { fn(); },
    dispatchEvent(event) {
      assert.equal(event.type, "resize");
      resized += 1;
    },
    Event: class Event { constructor(type) { this.type = type; } },
  });
  assert.equal(chart.style.width, "100%");
  assert.equal(lane.style.width, "100%");
  assert.deepEqual(seen, ["measure", "lane"]);
  assert.equal(resized, 1);
});

test("Overview wires the toggle for every ticker and drops the scoreboard label", () => {
  const trends = app.slice(app.indexOf("function renderTrendsLayout"), app.indexOf("function appendStatusAlerts"));
  assert.match(trends, /readOverviewSideCollapsed\(localPrefs\)/);
  assert.match(trends, /writeOverviewSideCollapsed\(localPrefs/);
  assert.match(trends, /applyOverviewSideColumn\(/);
  assert.match(trends, /resizeOverviewCharts\(layout, window\)/);
  assert.match(trends, /side-column-toggle/);
  assert.match(trends, /dataset\.sideToggle = "macro"/);
  assert.doesNotMatch(trends, /renderTrendDisclaimer/);
  assert.doesNotMatch(trends, /trend-scoreboard-notice/);
  assert.doesNotMatch(app, /renderTrendDisclaimer/);
  assert.doesNotMatch(source, /localStorage/);
  const block = styles.slice(styles.indexOf(".overview-side-toggle-row"));
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}/);
  assert.match(block, /\.trends-layout\.dashboard-grid\[data-side-collapsed="true"\]\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  assert.match(block, /\.trends-layout\.dashboard-grid\[data-side-collapsed="true"\] > \.side-column\s*\{[^}]*display:\s*none/);
  assert.match(styles, /\.trends-layout \.price-chart,\s*\n\.trends-layout \.lane-chart\s*\{[^}]*width:\s*100%/);
});
