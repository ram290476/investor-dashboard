import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CHART_PLOT,
  chartTicks,
  chartYearLabels,
  fiscalAxis,
  formatAmount,
  formatAxisTick,
  indexAtPlotX,
  niceAmountTicks,
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

test("time axes use one quarter label and a separate year line", () => {
  const intraday = [9, 10, 11, 12, 13, 14, 15, 16].map((hour) => ({
    ts: `2026-10-07T${String(hour).padStart(2, "0")}:30:00Z`,
  }));
  const hours = chartTicks(intraday, "1D");
  assert.deepEqual(hours.map((tick) => tick.label), ["Q4"]);
  assert.deepEqual(chartYearLabels(hours).map((band) => band.year), ["2026"]);
  assert.equal(hours[0].x, plotX(hours[0].index, intraday.length));
  assert.equal(formatAxisTick({ date: "2026-03-02" }), "Q1");
  assert.doesNotMatch(formatAxisTick({ date: "2025-05-02" }), /2025|Q2 '/);

  const year = chartTicks(days("2025-01-02", 252), "1Y");
  assert.deepEqual(year.map((tick) => tick.label), ["Q1", "Q2", "Q3", "Q4"]);
  assert.ok(year.every((tick) => tick.year === "2025"));
  assert.equal(chartYearLabels(year).length, 1);
  assert.ok(chartYearLabels(year)[0].x > year[0].x && chartYearLabels(year)[0].x < year.at(-1).x);

  const span = chartTicks(days("2024-10-01", 400), "3Y");
  assert.ok(span.filter((tick) => tick.year === "2025").length === 4);
  assert.ok(span.some((tick) => tick.boundary && tick.year === "2025"));
  const years = chartYearLabels(span);
  assert.deepEqual(years.map((band) => band.year), ["2024", "2025", "2026"]);
  assert.ok(years.every((band) => !band.year.includes("Q")));

  const phone = chartTicks(days("2024-10-01", 400), "3Y", CHART_PLOT, { compact: true });
  assert.ok(phone.length < span.length);
  assert.ok(phone.every((tick) => /^Q[1-4]$/.test(tick.label)));
  assert.ok(chartYearLabels(phone).length >= 2);

  const fiscal = fiscalAxis(["2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1"], { compact: false });
  assert.deepEqual(fiscal.ticks.map((tick) => tick.label), ["Q1", "Q2", "Q3", "Q4", "Q1"]);
  assert.deepEqual(fiscal.years.map((band) => band.year), ["2025", "2026"]);
  const thin = fiscalAxis(["2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1"], { compact: true });
  assert.equal(thin.ticks.filter((tick) => tick.show).length < thin.ticks.length, true);
  assert.equal(thin.years.length, 2);
});

test("amount ticks are rounded and keep their unit", () => {
  const prices = niceAmountTicks(412.2, 488.8, 4);
  assert.ok(prices.length >= 2 && prices.length <= 6);
  assert.ok(prices.every((tick) => tick >= 400 && tick <= 500));
  assert.equal(formatAmount(250, "USD"), "$250");
  assert.match(formatAmount(1.2e9, "USD"), /\$1\.2B/);
  assert.equal(formatAmount(45, "%"), "45%");
  assert.equal(formatAmount(12.5, "GWh"), "12.5 GWh");
  assert.equal(formatAmount(0.125, "ratio"), "12.5%");
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
