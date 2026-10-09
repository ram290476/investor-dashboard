import assert from "node:assert/strict";
import test from "node:test";

import {
  chartSettingsFor,
  isMarketOverlay,
  overlayDefinition,
  overlayGroups,
  OVERLAYS,
  fundamentalSummary,
  valuesForOverlay,
} from "./chart-overlays.js";

test("chart controls default to volume and pressure and retain valid per-ticker choices", () => {
  assert.deepEqual(chartSettingsFor({}, "TSLA"), { overlays: [], lanes: ["VOL", "PRESS"] });
  assert.deepEqual(
    chartSettingsFor({ chart_settings: { TSLA: { overlays: ["MA20", "invalid"], lanes: ["OPT", "bad"] } } }, "TSLA"),
    { overlays: ["MA20"], lanes: ["OPT"] },
  );
  assert.equal(isMarketOverlay("SPY"), true);
  assert.equal(overlayDefinition("MA20").window, 20);
});

test("overlay groups only expose series present for the selected ticker", () => {
  const groups = overlayGroups(
    { ticker: "TSLA" },
    { macro_series: { DGS10: [{ date: "2026-10-01", value: 4.2 }] }, fundamentals: [{ series_id: "deliveries", value: 10 }] },
    { tickers: { SPY: { price_history: [{ date: "2026-10-01", close: 500 }] }, TSLA: { price_history: [] } } },
  );
  assert.deepEqual(groups.map((group) => group.id), ["Market", "Rates", "Fundamentals"]);
  assert.deepEqual(groups.find((group) => group.id === "Rates").overlays.map((overlay) => overlay.id), ["DGS10"]);
});

test("fundamentals always expose seven metrics, marking unavailable ones explicitly", () => {
  const group = overlayGroups({ ticker: "SPCX" }, null, {}).find(group => group.id === "Fundamentals");
  assert.equal(group.label, "Fundamentals");
  assert.equal(group.overlays.length, 7);
  assert.ok(group.overlays.every(overlay => !overlay.available));
  assert.ok(group.overlays.some(overlay => overlay.id === "FUNDAMENTAL:shares_outstanding"));
  assert.ok(group.overlays.some(overlay => overlay.id === "FUNDAMENTAL:public_float_usd"));
  assert.ok(OVERLAYS.filter(overlay => overlay.kind === "market").every(overlay => overlay.short === overlay.id));
});

test("macro and quarterly series align to daily chart bars without inventing observations", () => {
  const bars = ["2026-10-01", "2026-10-02", "2026-10-05"].map((date) => ({ date, close: 100 }));
  const chartData = {
    macro_series: { DGS10: [{ date: "2026-10-01", value: 4.1 }, { date: "2026-10-05", value: 4.3 }] },
    fundamentals: [{ date: "2026-09-30", series_id: "deliveries", value: 500000 }],
  };
  const context = { bars, tickerData: {}, chartData, dashboard: {} };
  assert.deepEqual(valuesForOverlay("DGS10", context), [4.1, 4.1, 4.3]);
  assert.deepEqual(valuesForOverlay("FUNDAMENTAL:deliveries", context), [500000, 500000, 500000]);
});

test("moving averages use full price history before aligning to a short selected window", () => {
  const history = Array.from({ length: 22 }, (_, index) => ({
    date: new Date(Date.UTC(2026, 8, index + 1)).toISOString().slice(0, 10),
    close: index + 1,
    adj_close: index + 1,
  }));
  const bars = history.slice(-2);
  assert.deepEqual(
    valuesForOverlay("MA20", { bars, tickerData: { price_history: history }, chartData: {}, dashboard: {} }),
    [11.5, 12.5],
  );
});

test("ETF overlays match timestamps for intraday bars", () => {
  const bars = [
    { ts: "2026-10-06T14:00:00Z" },
    { ts: "2026-10-06T15:00:00Z" },
    { ts: "2026-10-06T16:00:00Z" },
  ];
  const dashboard = { tickers: { SPY: { intraday: { bars: [{ ts: "2026-10-06T14:00:00Z", close: 500 }, { ts: "2026-10-06T15:30:00Z", close: 501 }] } } } };
  assert.deepEqual(valuesForOverlay("SPY", { bars, tickerData: {}, chartData: {}, dashboard }), [500, 500, 501]);
});

test("fundamental summaries are unit-aware, quarter-labelled and publication-date safe", () => {
  const chart = { fundamentals: [
    { series_id: "revenue_gaap", date: "2026-01-01", value: 27000000000, unit: "USD", fiscal_quarter: "2025Q4" },
    { series_id: "gross_margin_gaap", date: "2026-01-01", value: 0.205, unit: "ratio", fiscal_quarter: "2025Q4" },
    { series_id: "shares_outstanding", date: "2026-01-01", value: 1234567, unit: "shares", fiscal_quarter: "2025Q4" },
    { series_id: "revenue_gaap", date: "2026-05-01", value: 999, unit: "USD", fiscal_quarter: "2026Q1" },
  ] };
  assert.equal(fundamentalSummary("FUNDAMENTAL:revenue_gaap", chart, "2026-02-01"), "$27.0B · 2025Q4");
  assert.equal(fundamentalSummary("FUNDAMENTAL:gross_margin_gaap", chart, "2026-02-01"), "20.5% · 2025Q4");
  assert.equal(fundamentalSummary("FUNDAMENTAL:shares_outstanding", chart, "2026-02-01"), "1,234,567 · 2025Q4");
  assert.equal(fundamentalSummary("FUNDAMENTAL:revenue_gaap", chart, "2025-12-31"), "not available");
});