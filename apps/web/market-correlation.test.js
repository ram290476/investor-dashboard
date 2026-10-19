import assert from "node:assert/strict";
import test from "node:test";

import {
  BETA_MIN_PAIRS,
  BETA_WINDOW,
  CORRELATION_DRIVERS,
  CORRELATION_SHIFT,
  correlationFlag,
  correlationPanelModel,
  deltaText,
  formatLink,
  marketComparisonModel,
  marketFooter,
  nowText,
  pairedLink,
  signedPercent,
  signedPoints,
} from "./market-correlation.js";

function walk(start, returns) {
  const prices = [start];
  returns.forEach((value) => prices.push(prices.at(-1) * (1 + value)));
  return prices;
}

function dated(values, start = "2026-01-05") {
  const rows = [];
  const cursor = new Date(`${start}T00:00:00Z`);
  values.forEach((value) => {
    while (cursor.getUTCDay() === 0 || cursor.getUTCDay() === 6) cursor.setUTCDate(cursor.getUTCDate() + 1);
    rows.push({ date: cursor.toISOString().slice(0, 10), close: value, adj_close: value });
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  });
  return rows;
}

test("beta regresses the stock on the index and ignores unpaired dates", () => {
  const moves = Array.from({ length: 32 }, (_, index) => [0.01, -0.02, 0.015, 0.005][index % 4]);
  const index = dated(walk(100, moves));
  const stock = dated(walk(200, moves.map((value) => value * 1.5)));
  const link = pairedLink(stock, index);
  assert.equal(link.n, 32);
  assert.ok(Math.abs(link.rho - 1) < 1e-9);
  assert.ok(Math.abs(link.beta - 1.5) < 1e-9);
  assert.equal(pairedLink(stock.slice(0, 10), index.slice(0, 10)).rho, null);
  assert.equal(pairedLink(dated(Array(30).fill(50)), index.slice(0, 30)).rho, null);
  // Dropping the index's first session removes one return date. The rest stay paired.
  const gapped = pairedLink(stock, index.slice(1));
  assert.equal(gapped.n, 31);
  assert.ok(Math.abs(gapped.beta - 1.5) < 1e-9);
  assert.equal(BETA_WINDOW, 90);
  assert.equal(BETA_MIN_PAIRS, 20);
});

test("market comparison follows selected indexes and the chart period", () => {
  const indexMoves = Array.from({ length: 40 }, (_, index) => [0.01, -0.008, 0.006, -0.002][index % 4]);
  const stock = dated(walk(100, indexMoves.map((value) => value + 0.004)));
  const spy = dated(walk(100, indexMoves));
  const model = marketComparisonModel({
    ticker: "TSLA",
    periodId: "1M",
    overlayIds: ["SPY", "MA20", "SPY"],
    stockHistory: stock,
    indexHistories: { SPY: spy },
    labels: { SPY: "S&P 500 (SPY)" },
  });
  assert.equal(model.period, "1M");
  assert.equal(model.rows.length, 1);
  assert.equal(model.rows[0].label, "S&P 500 (SPY)");
  assert.ok(model.rows[0].relative > 0);
  assert.equal(model.headline, `TSLA ${signedPercent(model.stockReturn)} over 1M · ahead of every index shown`);
  assert.match(signedPoints(model.rows[0].relative), /^\+/);
  assert.match(formatLink(model.rows[0].rho), /^-?\d+\.\d{2}$/);
  const behind = marketComparisonModel({
    ticker: "TSLA",
    periodId: "1M",
    overlayIds: ["QQQ"],
    stockHistory: spy,
    indexHistories: { QQQ: stock },
    labels: { QQQ: "Nasdaq-100 (QQQ)" },
  });
  assert.match(behind.headline, /behind every index shown$/);
  const empty = marketComparisonModel({
    ticker: "TSLA", periodId: "1M", overlayIds: ["MA20"], stockHistory: stock,
  });
  assert.equal(empty.summary, "No index on the chart");
  assert.equal(empty.rows.length, 0);
});

test("server market_comparison wins over a recomputed return", () => {
  const stock = dated(walk(100, Array(30).fill(0.01)));
  const model = marketComparisonModel({
    ticker: "TSLA",
    periodId: "1D",
    overlayIds: ["DIA"],
    stockHistory: stock,
    indexHistories: { DIA: stock },
    server: {
      returns: { "1D": 0.5 },
      indexes: { DIA: { returns: { "1D": 0.25 }, rho_90d: 0.4, beta_90d: 1.2, n: 90 } },
    },
  });
  assert.equal(model.stockReturn, 0.5);
  assert.equal(model.rows[0].ret, 0.25);
  assert.equal(model.rows[0].rho, 0.4);
  assert.equal(model.rows[0].beta, 1.2);
  assert.equal(signedPercent(0), "+0.0%");
  assert.equal(signedPoints(-1.25), "−1.3 pp");
  assert.match(marketFooter([{ n: 40, rho: 0.2 }]), /fewer than 90/);
});

test("correlation flags use the 30-day link against the 90-day link", () => {
  assert.equal(CORRELATION_SHIFT, 0.08);
  assert.equal(correlationFlag(0.2, -0.1).flag, "sign-flip");
  assert.equal(correlationFlag(-0.51, -0.42).flag, "strengthening");
  assert.equal(correlationFlag(-0.3, -0.42).flag, "weakening");
  assert.equal(correlationFlag(0.1, 0.12).flag, "stable");
  assert.equal(correlationFlag(null, 0.2), null);
  assert.equal(correlationFlag(0, 0.09).flag, "sign-flip");
  assert.equal(nowText(-0.51), "-0.51");
  assert.equal(nowText(0.15), "+0.15");
  assert.equal(deltaText(-0.09), "▼ -0.09");
  assert.equal(deltaText(0.08), "▲ +0.08");
  const history = Array.from({ length: 10 }, (_, index) => ({ date: `2026-01-${index + 1}`, corr_30d: -0.2 + index * 0.01, corr_90d: -0.1 }));
  const model = correlationPanelModel("TSLA", [
    { series_id: "DGS10", corr_30d: -0.51, corr_90d: -0.42 },
    { series_id: "VIXCLS", corr_30d: 0.2, corr_90d: -0.1 },
  ], { DGS10: history, VIXCLS: history });
  assert.deepEqual(model.rows.map((row) => row.id), CORRELATION_DRIVERS.map((driver) => driver.id));
  assert.equal(model.rows[0].link.flag, "strengthening");
  assert.equal(model.rows[0].spark.d.includes("M"), true);
  assert.equal(model.rows[3].link.flag, "sign-flip");
  assert.equal(model.rows[1].link, null);
  assert.equal(model.strengthening, 1);
  assert.equal(model.flips, 1);
  assert.equal(model.summary, "TSLA · 1 strengthening · 1 sign flip");
  assert.match(model.footer, /6-month rolling 30-day/);
  assert.match(model.footer, /orange = against/);
});
