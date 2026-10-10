import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CHART_PLOT,
  chartTicks,
  formatAxisTick,
  indexAtPlotX,
  plotEdgePixels,
  plotX,
} from "./chart-scale.js";

function days(start, count) {
  const bars = [];
  const cursor = new Date(`${start}T00:00:00Z`);
  while (bars.length < count) {
    const dow = cursor.getUTCDay();
    if (dow !== 0 && dow !== 6) bars.push({ date: cursor.toISOString().slice(0, 10) });
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return bars;
}

test("price and lane charts share one plot definition", () => {
  assert.equal(plotX(0, 21), CHART_PLOT.left);
  assert.equal(plotX(20, 21), CHART_PLOT.right);
  assert.equal(plotX(0, 1259), plotX(0, 8));
  assert.equal(plotX(7, 8), CHART_PLOT.right);
  const edges = plotEdgePixels(CHART_PLOT.width);
  assert.deepEqual(edges, { start: CHART_PLOT.left, end: CHART_PLOT.right });
  const wide = plotEdgePixels(1440);
  assert.ok(Math.abs((wide.end - wide.start) / 1440 - (CHART_PLOT.right - CHART_PLOT.left) / CHART_PLOT.width) < 1e-12);
});

test("every bar index round-trips through the shared x-scale", () => {
  for (const count of [2, 8, 21, 63, 252, 1259]) {
    for (let index = 0; index < count; index += Math.max(1, Math.floor(count / 40))) {
      assert.equal(indexAtPlotX(plotX(index, count), count), index);
    }
    assert.equal(indexAtPlotX(plotX(count - 1, count), count), count - 1);
  }
});

test("a later series stays on the shared scale instead of stretching across the plot", () => {
  const count = 252;
  const firstOptions = 124;
  assert.equal(plotX(0, count), CHART_PLOT.left);
  assert.ok(plotX(firstOptions, count) > CHART_PLOT.left + (CHART_PLOT.right - CHART_PLOT.left) * 0.4);
  assert.ok(plotX(firstOptions, count) < CHART_PLOT.right);
});

test("ticks use hours, days or weeks, months, and years for each period", () => {
  const intraday = [9, 10, 11, 12, 13, 14, 15, 16].map((hour) => ({
    ts: `2026-10-07T${String(hour).padStart(2, "0")}:30:00Z`,
  }));
  const hours = chartTicks(intraday, "1D");
  assert.ok(hours.length >= 4 && hours.length <= 8);
  assert.ok(hours.every((tick) => /^\d{2}:\d{2}$/.test(tick.label)));
  assert.equal(hours[0].label, "09:30");
  assert.equal(hours.at(-1).x, plotX(hours.at(-1).index, intraday.length));

  const week = chartTicks(days("2026-10-05", 5), "1W");
  assert.deepEqual(week.map((tick) => tick.label), ["Oct 5", "Oct 6", "Oct 7", "Oct 8", "Oct 9"]);

  const month = chartTicks(days("2026-09-08", 21), "1M");
  const quarter = chartTicks(days("2026-07-10", 63), "3M");
  for (const ticks of [month, quarter]) {
    assert.ok(ticks.length >= 3 && ticks.length <= 6);
    assert.ok(ticks.every((tick) => /^[A-Z][a-z]{2} \d{1,2}$/.test(tick.label)));
  }

  const year = chartTicks(days("2025-10-08", 252), "1Y");
  const ytd = chartTicks(days("2026-01-02", 200), "YTD");
  for (const ticks of [year, ytd]) {
    assert.ok(ticks.length >= 3 && ticks.length <= 6);
    assert.ok(ticks.every((tick) => /^[A-Z][a-z]{2}( \d{2})?$/.test(tick.label)));
  }
  assert.equal(formatAxisTick({ date: "2026-03-02" }, "1Y"), "Mar");

  const fiveYear = chartTicks(days("2021-10-01", 1259), "5Y");
  const threeYear = chartTicks(days("2023-10-02", 756), "3Y");
  for (const ticks of [fiveYear, threeYear]) {
    assert.ok(ticks.length >= 3 && ticks.length <= 6);
    assert.ok(ticks.every((tick) => /^\d{4}$/.test(tick.label)));
    assert.deepEqual(ticks.map((tick) => tick.label), [...new Set(ticks.map((tick) => tick.label))]);
  }
});

test("tick positions are the same x as the price chart bar they label", () => {
  const bars = days("2024-01-02", 756);
  for (const periodId of ["1M", "3M", "YTD", "1Y", "3Y", "5Y"]) {
    const window = periodId === "1M" ? bars.slice(-21) : periodId === "3M" ? bars.slice(-63) : bars;
    for (const tick of chartTicks(window, periodId)) {
      assert.equal(tick.x, plotX(tick.index, window.length));
      assert.ok(tick.x >= CHART_PLOT.left && tick.x <= CHART_PLOT.right);
    }
  }
  assert.deepEqual(chartTicks([], "1M"), []);
});

test("chart drawers do not copy the plot margins", () => {
  const app = readFileSync(new URL("./app.js", import.meta.url), "utf8");
  const scale = readFileSync(new URL("./chart-scale.js", import.meta.url), "utf8");
  assert.match(app, /from "\.\/chart-scale\.js"/);
  assert.match(app, /plotX\(/);
  assert.match(app, /chartTicks\(/);
  assert.equal(app.includes("const left = 70"), false);
  assert.equal(app.includes("const right = 890"), false);
  assert.match(scale, /left:\s*70/);
  assert.match(scale, /right:\s*890/);
  assert.match(app, /preserveAspectRatio", "none"/);
});
