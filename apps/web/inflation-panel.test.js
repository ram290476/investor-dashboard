import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { inflationSurpriseView } from "./inflation-panel.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const panel = await readFile(new URL("./inflation-panel.js", import.meta.url), "utf8");

function print(period, yoy, surprise) {
  return { period, yoy, surprise_pp: surprise };
}

test("missing CPI prints stay unavailable and do not invent a nowcast", () => {
  const empty = inflationSurpriseView({ inflation: { prints: [] } }, "ready");
  assert.equal(empty.kind, "unavailable");
  assert.match(empty.message, /not in this snapshot/);
  assert.equal(empty.columns.length, 0);
  assert.equal(inflationSurpriseView(null, "loading").kind, "loading");
  assert.match(inflationSurpriseView(null, "error").message, /could not be loaded/);
});

test("six columns use the previous YoY change and a relative bar", () => {
  const view = inflationSurpriseView({
    inflation: {
      headline: "range-bound",
      core: "easing",
      prints: [
        print("2026-03", 3.0, 0.1),
        print("2026-04", 2.9, -0.1),
        print("2026-05", 3.1, 0.2),
        print("2026-06", 2.8, -0.3),
        print("2026-07", 2.9, 0.1),
        print("2026-08", 2.9, 0),
        print("2026-09", null, null),
      ],
    },
  }, "ready");
  assert.equal(view.kind, "ready");
  assert.equal(view.state, "Core easing · headline range-bound");
  assert.equal(view.streak, "2 of last 5 YoY prints rose");
  assert.match(view.nowcast, /Cleveland Fed nowcast is not in this snapshot/);
  assert.deepEqual(view.columns.map((column) => column.month), ["Apr", "May", "Jun", "Jul", "Aug", "Sep"]);
  assert.equal(view.columns[0].valueText, "2.9%");
  assert.equal(view.columns[0].surpriseText, "cool -10 bp");
  assert.equal(view.columns[0].surpriseClass, "bp-down");
  assert.equal(view.columns[1].surpriseText, "hot +20 bp");
  assert.equal(view.columns[1].surpriseClass, "bp-up");
  assert.equal(view.columns[4].surpriseText, "0 bp");
  assert.equal(view.columns[5].valueText, "pending");
  assert.equal(view.columns[5].surpriseText, "—");
  assert.equal(view.columns[5].pending, true);
  const may = Number.parseFloat(view.columns[1].height);
  const jun = Number.parseFloat(view.columns[2].height);
  assert.ok(may > jun);
  assert.ok(may <= 100);
});

test("the inflation drawer is the surprise chart and the rates drawer drops the sparkline rows", () => {
  assert.match(app, /title: "Inflation surprise"/);
  assert.doesNotMatch(app, /Inflation & release links/);
  assert.match(app, /"cpi-prints"/);
  assert.match(panel, /Cleveland Fed nowcast is not in this snapshot/);
  assert.match(app, /curve-fill/);
  assert.match(app, /ratesSpark\(/);
  assert.doesNotMatch(app, /Loading rate history/);
  const ratesStart = app.indexOf('id: "rates"');
  const inflationStart = app.indexOf('id: "inflation"');
  assert.ok(ratesStart > 0 && inflationStart > ratesStart);
  const ratesBody = app.slice(ratesStart, inflationStart);
  assert.doesNotMatch(ratesBody, /rate-entry/);
  assert.doesNotMatch(ratesBody, /release-window/);
});
