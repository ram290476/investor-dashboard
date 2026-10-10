import assert from "node:assert/strict";
import test from "node:test";

import {
  chartSettingsFor,
  isMarketOverlay,
  mergeCompanyFundamentals,
  overlayDefinition,
  overlayGroups,
  OVERLAYS,
  fundamentalSummary,
  fundamentalObservation,
  addOverlay,
  availableLanes,
  CHART_LANES,
  laneValues,
  pressureFillPath,
  visibleLanes,
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
    { ticker: "TSLA", price_history: [{ date: "2026-10-01", close: 100 }] },
    { macro_series: { DGS10: [{ date: "2026-10-01", value: 4.2 }] }, fundamentals: [{ date: "2026-10-01", series_id: "deliveries", value: 10 }] },
    { tickers: { SPY: { price_history: [{ date: "2026-10-01", close: 500 }] }, TSLA: { price_history: [] } } },
  );
  assert.deepEqual(groups.map((group) => group.id), ["Market", "Rates", "Fundamentals"]);
  assert.deepEqual(groups.find((group) => group.id === "Rates").overlays.map((overlay) => overlay.id), ["DGS10"]);
});

test("sentiment overlays hide without data and use only the selected ticker's history", () => {
  const group = overlayGroups({ ticker: "AAPL" }, null, {}).find(group => group.id === "Sentiment");
  assert.equal(group, undefined);
  const values = valuesForOverlay("NEWS:SENTIMENT", {
    bars: [{ date: "2026-10-01" }, { date: "2026-10-02" }],
    tickerData: { news: { sentiment_history: [{ date: "2026-10-01", value: -0.2 }] } },
  });
  assert.deepEqual(values, [-0.2, null]);
});

test("proposed company series join Fundamentals and stay labeled pending review", () => {
  assert.equal(OVERLAYS.filter((overlay) => overlay.kind === "fundamental").length, 7);
  const merged = mergeCompanyFundamentals(
    { fundamentals: [{ series_id: "revenue_gaap", date: "2026-01-01", value: 1, unit: "USD" }] },
    [{
      metric_id: "capex",
      display_name: "Capital expenditures",
      unit: "USD",
      approval_state: "proposed",
      series: [
        { fiscal_period: "2025Q4", value: 2000, reported: true, period_end: "2025-12-31" },
        { fiscal_period: "2026Q1", value: 2493, reported: true, period_end: "2026-03-31" },
      ],
    }],
  );
  assert.equal(merged.fundamentals.some((row) => row.series_id === "revenue_gaap"), true);
  assert.equal(merged.fundamentals.filter((row) => row.series_id === "capex").every((row) => row.approval_state === "proposed"), true);
  const bars = [{ date: "2026-04-01", close: 100 }];
  const groups = overlayGroups({ ticker: "TSLA", price_history: bars }, merged, {});
  const capex = groups.find((group) => group.id === "Fundamentals").overlays.find((overlay) => overlay.id === "FUNDAMENTAL:capex");
  assert.match(capex.label, /Pending review/);
  assert.equal(overlayDefinition("FUNDAMENTAL:capex").unit, "USD");
  assert.deepEqual(
    chartSettingsFor({ chart_settings: { TSLA: { overlays: ["FUNDAMENTAL:capex", "nope"], lanes: ["VOL"] } } }, "TSLA").overlays,
    ["FUNDAMENTAL:capex"],
  );
});

test("empty fundamentals groups are hidden while all seven definitions remain supported", () => {
  const group = overlayGroups({ ticker: "SPCX" }, null, {}).find(group => group.id === "Fundamentals");
  assert.equal(group, undefined);
  assert.equal(OVERLAYS.filter(overlay => overlay.kind === "fundamental").length, 7);
  assert.ok(OVERLAYS.filter(overlay => overlay.kind === "market").every(overlay => overlay.short === overlay.id));
});

test("availability uses finite aligned values in the selected period, including prior MA and publication history", () => {
  const history = Array.from({ length: 210 }, (_, i) => ({
    date: new Date(Date.UTC(2026, 0, i + 1)).toISOString().slice(0, 10), close: i + 100,
  }));
  const bars = history.slice(-5);
  const chart = { macro_series: {
    DGS10: [{ date: "2026-01-01", value: 4 }],
    DGS2: [{ date: "2027-01-01", value: 3 }],
    SOFR: [{ date: "2026-01-01", value: NaN }],
  }, fundamentals: [
    { date: "2026-01-01", series_id: "revenue_gaap", value: 0 },
    { date: "2027-01-01", series_id: "deliveries", value: 1 },
  ] };
  const ticker = { price_history: history, news: { sentiment_history: [{ date: bars[0].date, value: 0 }] } };
  const dashboard = { tickers: { SPY: { price_history: [{ date: "2025-01-01", close: 5 }] } } };
  const ids = overlayGroups(ticker, chart, dashboard, bars).flatMap(group => group.overlays).map(item => item.id);
  assert.ok(ids.includes("MA200") && ids.includes("DGS10") && ids.includes("FUNDAMENTAL:revenue_gaap") && ids.includes("NEWS:SENTIMENT"));
  assert.ok(!ids.includes("SPY") && !ids.includes("DGS2") && !ids.includes("SOFR") && !ids.includes("FUNDAMENTAL:deliveries"));
  assert.deepEqual(overlayGroups(ticker, chart, dashboard, []), []);
  assert.equal(overlayGroups(ticker, chart, dashboard, history.slice(0, 5)).some(group => group.id === "Moving averages"), false);
});

test("lanes keep their order and each chart shares one color token", () => {
  assert.deepEqual(CHART_LANES.map((lane) => lane.id), ["VOL", "SI", "PRESS", "OPT"]);
  assert.deepEqual(CHART_LANES.map((lane) => lane.colorVar), ["--lane-volume", "--lane-si", "--lane-press", "--lane-opt"]);
  assert.deepEqual(CHART_LANES.map((lane) => lane.glyph), ["bars", "step", "pressure", "dashed"]);
});

test("macro pressure fill splits at zero and at gaps", () => {
  const yAt = (value) => 10 - value;
  const positive = pressureFillPath([{ x: 0, value: 1 }, { x: 10, value: 1 }], 1, yAt);
  assert.equal(positive, "M0.00 9.00L10.00 9.00L10.00 10.00L0.00 10.00Z");
  assert.equal(pressureFillPath([{ x: 0, value: 1 }, { x: 10, value: 1 }], -1, yAt), "");
  const up = pressureFillPath([{ x: 0, value: 1 }, { x: 10, value: -1 }], 1, yAt);
  const down = pressureFillPath([{ x: 0, value: 1 }, { x: 10, value: -1 }], -1, yAt);
  assert.match(up, /^M0\.00 9\.00L5\.00 10\.00/);
  assert.match(down, /M5\.00 10\.00L10\.00 11\.00/);
  const gapped = pressureFillPath([{ x: 0, value: 1 }, null, { x: 8, value: 1 }], 1, yAt);
  assert.equal(gapped, "");
  const rejoin = pressureFillPath([{ x: 0, value: 1 }, { x: 4, value: 0 }, { x: 8, value: 2 }], 1, yAt);
  assert.equal(rejoin.match(/Z/g).length, 2);
});

test("lane availability and fixed order use aligned finite data without rewriting the selection", () => {
  const bars = [{ date: "2026-10-01", volume: 0 }, { date: "2026-10-02", volume: null }];
  const chart = {
    short_interest: [{ date: "2026-09-01", shares_short: 100 }, { date: "2027-01-01", short_pct_denominator: 2 }],
    macro_pressure: [{ date: "2026-10-01", value: 0 }],
    options: [{ date: "2026-10-01", put_call_volume_ratio: 1 }],
  };
  const selected = ["OPT", "PRESS", "SI", "VOL"];
  assert.deepEqual(visibleLanes(selected, bars, chart).map(lane => lane.id), ["VOL", "SI", "PRESS", "OPT"]);
  assert.deepEqual(selected, ["OPT", "PRESS", "SI", "VOL"]);
  assert.deepEqual(laneValues("SI", bars, chart), [100, 100]);
  assert.deepEqual(availableLanes(bars, { options: [{ date: "2026-10-01", iv30: 0.5 }] }).map(lane => lane.id), ["VOL"]);
  assert.deepEqual(availableLanes([{ date: "2026-01-01", volume: NaN }], chart), []);
  assert.deepEqual(visibleLanes([], bars, chart), []);
});

test("adding overlays evicts hidden selections first and keeps the five-overlay limit", () => {
  const selected = ["SPY", "DGS10", "MA20", "QQQ", "MA50"];
  assert.deepEqual(addOverlay(selected, "MA10", ["SPY", "MA20", "QQQ", "MA50", "MA10"]), ["SPY", "MA20", "QQQ", "MA50", "MA10"]);
  assert.deepEqual(addOverlay(selected, "MA10", selected), ["DGS10", "MA20", "QQQ", "MA50", "MA10"]);
  assert.deepEqual(selected, ["SPY", "DGS10", "MA20", "QQQ", "MA50"]);
});

test("market availability aligns intraday timestamps and excludes future-only observations", () => {
  const bars = [{ ts: "2026-10-01T14:00:00Z", close: 100 }, { ts: "2026-10-01T15:00:00Z", close: 102 }];
  const dashboard = { tickers: {
    SPY: { intraday: { bars: [{ ts: "2026-10-01T14:30:00Z", close: 0 }] } },
    QQQ: { intraday: { bars: [{ ts: "2026-10-01T16:00:00Z", close: 100 }] } },
    DIA: { intraday: { bars: [{ ts: "2026-10-01T14:00:00Z", close: Infinity }] } },
  } };
  assert.deepEqual(overlayGroups({}, null, dashboard, bars)[0].overlays.map(overlay => overlay.id), ["SPY"]);
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
    {
      series_id: "revenue_gaap",
      date: "2026-01-01",
      value: 27000000000,
      unit: "USD",
      fiscal_quarter: "2025Q4",
      source_id: "DS-11",
    },
    { series_id: "gross_margin_gaap", date: "2026-01-01", value: 0.205, unit: "ratio", fiscal_quarter: "2025Q4" },
    { series_id: "shares_outstanding", date: "2026-01-01", value: 1234567, unit: "shares", fiscal_quarter: "2025Q4" },
    { series_id: "revenue_gaap", date: "2026-05-01", value: 999, unit: "USD", fiscal_quarter: "2026Q1", source_id: "DS-11" },
  ] };
  assert.equal(fundamentalSummary("FUNDAMENTAL:revenue_gaap", chart, "2026-02-01"), "$27.0B · 2025Q4");
  assert.equal(fundamentalSummary("FUNDAMENTAL:gross_margin_gaap", chart, "2026-02-01"), "20.5% · 2025Q4");
  assert.equal(fundamentalSummary("FUNDAMENTAL:shares_outstanding", chart, "2026-02-01"), "1,234,567 · 2025Q4");
  assert.equal(fundamentalSummary("FUNDAMENTAL:revenue_gaap", chart, "2025-12-31"), "not available");
  assert.equal(fundamentalObservation("FUNDAMENTAL:revenue_gaap", chart, "2026-02-01").source_id, "DS-11");
  assert.equal(fundamentalObservation("FUNDAMENTAL:revenue_gaap", chart, "2025-12-31"), undefined);
});

test("sentiment overlays leave unobserved dates empty instead of carrying stale scores", () => {
  const bars = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-20"]
    .map(date => ({ date }));
  const tickerData = { news: { sentiment_history: [
    { date: "2026-10-01", value: 0.2 },
    { date: "2026-10-06", value: 0.4 },
  ] } };
  assert.deepEqual(valuesForOverlay("NEWS:SENTIMENT", { bars, tickerData }), [0.2, null, null, 0.4, null]);
});