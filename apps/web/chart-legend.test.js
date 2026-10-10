import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { legendItems, overlayCorrelation, seriesLineStyle } from "./chart-legend.js";

const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

const average = { id: "MA50", label: "50-day", kind: "average" };
const market = { id: "SPY", label: "S&P 500 (SPY)", kind: "market" };
const macro = { id: "DGS10", label: "10Y Treasury", kind: "macro", inverse: true };
const plainMacro = { id: "DCOILWTICO", label: "WTI crude", kind: "macro" };
const fundamental = { id: "FUNDAMENTAL:revenue_gaap", label: "Revenue", kind: "fundamental" };
const sentiment = { id: "NEWS:SENTIMENT", label: "7-day news sentiment", kind: "sentiment" };

test("legend items cover the price line and each overlay kind, including a missing value", () => {
  const items = legendItems({
    ticker: "TSLA",
    prices: [100, 110, 120],
    adjustedMin: 100,
    adjustedMax: 120,
    overlays: [
      { definition: average, values: [null, null, 100] },
      { definition: { ...average, id: "MA20", label: "20-day" }, values: [null, null, 150] },
      { definition: { ...average, id: "MA10", label: "10-day" }, values: [120, 120, 120] },
      { definition: { ...average, id: "MA200", label: "200-day" }, values: [null, null, null] },
      { definition: { ...average, id: "MA100", label: "100-day" }, values: [0, 0, 0] },
      { definition: market, values: [100, null, 110] },
      { definition: { ...market, id: "QQQ", label: "Nasdaq-100 (QQQ)" }, values: [0, 10] },
      { definition: macro, values: [4.18, null, 4.34], correlation: -0.42 },
      { definition: plainMacro, values: [70, 72.5], correlation: null },
      { definition: { ...macro, id: "VIXCLS", label: "VIX" }, values: [null, null] },
      { definition: fundamental, values: [10], summary: "$1.2B · Q3 2026" },
      { definition: { ...fundamental, id: "FUNDAMENTAL:deliveries", label: "Deliveries" }, values: [], summary: "" },
      { definition: sentiment, values: [0.2, -0.2], observedOn: "2026-10-06" },
    ],
  });
  const byId = Object.fromEntries(items.map((item) => [item.id, item]));
  assert.equal(byId.price.text, "TSLA price");
  assert.equal(byId.price.dash, "none");
  assert.equal(byId.price.width, 2.5);
  assert.equal(byId.price.title, "Adjusted $100.00 – $120.00");
  assert.equal(byId.MA50.text, "50-day average $100.00 · price above by 20.0%");
  assert.equal(byId.MA50.dash, "none");
  assert.equal(byId.MA20.text, "20-day average $150.00 · price below by 20.0%");
  assert.equal(byId.MA10.text, "10-day average $120.00 · price unchanged");
  assert.equal(byId.MA200.text, "200-day average · not available");
  assert.equal(byId.MA100.text, "100-day average $0.00");
  assert.equal(byId.SPY.text, "S&P 500 (SPY) · +10.0% from period start");
  assert.equal(byId.SPY.dash, "5 4");
  assert.equal(byId.QQQ.text, "Nasdaq-100 (QQQ) · not available");
  assert.equal(byId.DGS10.text, "10Y Treasury · ρ -0.42 · 4.18–4.34 (inverted, own scale)");
  assert.equal(byId.DGS10.dash, "5 4");
  assert.equal(byId.DGS10.width, 1.5);
  assert.equal(byId.DCOILWTICO.text, "WTI crude · 70.00–72.50 (own scale)");
  assert.equal(byId.VIXCLS.text, "VIX · not available");
  assert.equal(byId["FUNDAMENTAL:revenue_gaap"].text, "Revenue · $1.2B · Q3 2026");
  assert.equal(byId["FUNDAMENTAL:revenue_gaap"].dash, "5 4");
  assert.equal(byId["FUNDAMENTAL:deliveries"].text, "Deliveries · not available");
  assert.equal(byId["NEWS:SENTIMENT"].text, "7-day news sentiment · -0.20 · observed 2026-10-06 · own scale");
  assert.equal(byId["NEWS:SENTIMENT"].dash, "5 4");
});

test("90-day correlation prefers the trend row and otherwise uses the latest history point", () => {
  const tickerData = { trend: { rows: [{ series_id: "DGS10", corr_90d: -0.42 }] } };
  const chartData = { correlation_history: { DGS10: [{ corr_90d: -0.11 }], VIXCLS: [{ corr_90d: null }, { corr_90d: 0.2 }] } };
  assert.equal(overlayCorrelation("DGS10", tickerData, chartData), -0.42);
  assert.equal(overlayCorrelation("VIXCLS", { trend: { rows: [] } }, chartData), 0.2);
  assert.equal(overlayCorrelation("SOFR", tickerData, chartData), null);
  assert.equal(seriesLineStyle("price").dash, seriesLineStyle("average").dash);
  assert.notEqual(seriesLineStyle("market").dash, seriesLineStyle("average").dash);
  assert.equal(seriesLineStyle("macro").dash, seriesLineStyle("fundamental").dash);
  assert.equal(seriesLineStyle("sentiment").dash, "5 4");
});

test("the legend keys and the plot share one dash helper, and the legend CSS uses theme tokens", () => {
  const block = styles.slice(styles.indexOf("/* chart legend keys (#93) */"), styles.indexOf("/* end chart legend keys (#93) */"));
  assert.match(block, /\.legend-line/);
  assert.match(block, /overflow-wrap: anywhere/);
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}/);
  assert.match(app, /seriesLineStyle\(definition\.kind\)/);
  assert.match(app, /seriesLineStyle\("price"\)/);
  assert.match(app, /legendItems\(/);
  assert.match(app, /aria-hidden", "true"/);
  assert.doesNotMatch(app.slice(app.indexOf("const legend = node"), app.indexOf("panel.append(legend)")), /overlay-swatch/);
});
