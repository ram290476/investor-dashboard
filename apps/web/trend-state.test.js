import assert from "node:assert/strict";
import test from "node:test";

import {
  resolveTrend,
  servingPriceTrend,
  trendLabel,
  trendRelation,
  trendSentence,
  trendState,
  trendTitle,
} from "./trend-state.js";

function historyFromCloses(closes, start = "2026-01-02") {
  const cursor = new Date(`${start}T00:00:00Z`);
  return closes.map((close) => {
    const date = cursor.toISOString().slice(0, 10);
    cursor.setUTCDate(cursor.getUTCDate() + 1);
    return { date, adj_close: close, close, close_raw: close + 3 };
  });
}

function average(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

test("uptrend when close is above a rising 20-day average", () => {
  const closes = Array.from({ length: 80 }, (_, index) => 100 + index);
  const trend = trendState(historyFromCloses(closes));
  const ma20 = average(closes.slice(-20));
  const ma50 = average(closes.slice(-50));
  assert.ok(closes.at(-1) > ma20 && ma20 > ma50);
  assert.equal(trend.state, "uptrend");
  assert.equal(trend.ma20, ma20);
  assert.equal(trend.ma50, ma50);
  assert.equal(trend.vsMa20, closes.at(-1) / ma20 - 1);
  assert.equal(trend.days, 31);
  assert.equal(trend.fromStart, true);
  assert.equal(trend.since, historyFromCloses(closes)[49].date);
  assert.equal(trendLabel(trend), "Uptrend · ≥31d");
  assert.match(trendRelation(trend), /above its 20-day and 50-day/);
});

test("downtrend is the reverse of uptrend", () => {
  const closes = Array.from({ length: 80 }, (_, index) => 200 - index);
  const trend = trendState(historyFromCloses(closes));
  const ma20 = average(closes.slice(-20));
  const ma50 = average(closes.slice(-50));
  assert.ok(closes.at(-1) < ma20 && ma20 < ma50);
  assert.equal(trend.state, "downtrend");
  assert.equal(trend.fromStart, true);
  assert.equal(trend.days, 31);
  assert.equal(trendLabel(trend), "Downtrend · ≥31d");
});

test("a flat series is range, including ties", () => {
  const trend = trendState(historyFromCloses(Array(60).fill(100)));
  assert.equal(trend.state, "range");
  assert.equal(trend.vsMa20, 0);
  assert.equal(trend.fromStart, true);
  assert.equal(trend.days, 11);
  assert.equal(trendLabel(trend), "Range · ≥11d");
});

test("range when price is above the 20-day average but that average is below the 50-day", () => {
  const closes = [...Array(30).fill(100), ...Array(19).fill(70), 80];
  const trend = trendState(historyFromCloses(closes));
  assert.equal(closes.length, 50);
  assert.ok(trend.close > trend.ma20 && trend.ma20 < trend.ma50);
  assert.equal(trend.state, "range");
  assert.equal(trend.days, 1);
  assert.equal(trend.fromStart, true);
  assert.equal(trendRelation(trend), "price above 20D, but 20D below 50D");
});

test("range when price is below the 20-day average but that average is above the 50-day", () => {
  const closes = [...Array(30).fill(50), ...Array(19).fill(90), 80];
  const trend = trendState(historyFromCloses(closes));
  assert.ok(trend.close < trend.ma20 && trend.ma20 > trend.ma50);
  assert.equal(trend.state, "range");
  assert.equal(trendRelation(trend), "price below 20D, but 20D above 50D");
});

test("day count stops when the state changes and does not reach the start of history", () => {
  const closes = [...Array(50).fill(100), 130, 140, 150, 160];
  const history = historyFromCloses(closes, "2026-08-01");
  const trend = trendState(history);
  assert.equal(trend.state, "uptrend");
  assert.equal(trend.days, 4);
  assert.equal(trend.fromStart, false);
  assert.equal(trend.since, history[50].date);
  assert.equal(trendLabel(trend), "Uptrend · 4d");
});

test("fewer than 50 closes has no trend state, and fewer than 20 omits the 20-day average", () => {
  const short = trendState(historyFromCloses(Array.from({ length: 34 }, (_, index) => 20 + index), "2026-06-12"));
  assert.equal(short.state, null);
  assert.equal(short.days, null);
  assert.equal(short.ma50, null);
  assert.equal(short.sessionsAvailable, 34);
  assert.equal(short.historyStarts, "2026-06-12");
  assert.ok(short.ma20 > 0);
  assert.equal(short.vsMa20, short.close / short.ma20 - 1);
  assert.equal(trendLabel(short), "Not enough history");

  const tiny = trendState(historyFromCloses(Array.from({ length: 15 }, (_, index) => index + 1)));
  assert.equal(tiny.state, null);
  assert.equal(tiny.ma20, null);
  assert.equal(tiny.vsMa20, null);
  assert.equal(tiny.sessionsAvailable, 15);
});

test("null closes are skipped and close_raw is not an input", () => {
  const rising = Array.from({ length: 60 }, (_, index) => 100 + index);
  const history = historyFromCloses(rising).flatMap((row) => [
    row,
    { date: row.date, adj_close: null, close: null, close_raw: 999 },
  ]);
  history[0] = { ...history[0], adj_close: null, close: rising[0], close_raw: 1 };
  const trend = trendState(history);
  assert.equal(trend.sessionsAvailable, 60);
  assert.equal(trend.close, rising.at(-1));
  assert.equal(trend.state, "uptrend");
  assert.ok(trend.ma20 < 200);
});

test("a zero 20-day average does not invent an infinite percent", () => {
  const closes = [...Array(40).fill(0), ...Array(10).fill(0)];
  const trend = trendState(historyFromCloses(closes));
  assert.equal(trend.ma20, 0);
  assert.equal(trend.vsMa20, null);
});

test("macro driver trend rows are ignored and a PX row is preferred", () => {
  const history = historyFromCloses(Array(60).fill(100));
  const tickerData = {
    trend: {
      rows: [
        { series_id: "DGS10", trend_state: "up", days_in_state: 12 },
        { series_id: "PX:TSLA", trend_state: "down", days_in_state: 9, since: "2026-09-20" },
      ],
    },
  };
  assert.equal(servingPriceTrend(tickerData, "TSLA").state, "downtrend");
  assert.equal(servingPriceTrend({ trend: { rows: tickerData.trend.rows.slice(0, 1) } }, "TSLA"), null);
  const resolved = resolveTrend(history, servingPriceTrend(tickerData, "TSLA"));
  assert.equal(resolved.source, "serving");
  assert.equal(resolved.state, "downtrend");
  assert.equal(resolved.days, 9);
  assert.equal(resolved.since, "2026-09-20");
  assert.equal(resolved.fromStart, false);
  assert.equal(trendLabel(resolved), "Downtrend · 9d");
  assert.equal(resolved.ma20, 100);
});

test("a run that reaches the first 50-day session says at least, and names that session", () => {
  const history = historyFromCloses(Array.from({ length: 80 }, (_, index) => 100 + index));
  const trend = trendState(history);
  assert.equal(trend.fromStart, true);
  assert.equal(trend.since, history[49].date);
  const title = trendTitle(trend);
  const sentence = trendSentence(trend);
  for (const copy of [title, sentence]) {
    assert.match(copy, /at least 31 sessions/);
    assert.match(copy, new RegExp(trend.since));
    assert.match(copy, /Earlier sessions have no 50-day average/);
    assert.doesNotMatch(copy, /start of history/);
  }
});

test("a missing 50-day average is not described as above or below the 20-day", () => {
  const history = historyFromCloses(Array.from({ length: 34 }, (_, index) => 20 + index * 0.1));
  const serving = { state: "downtrend", days: 9, since: "2026-09-20", fromStart: false };
  const trend = resolveTrend(history, serving);
  assert.equal(trend.ma50, null);
  assert.equal(trend.state, "downtrend");
  const title = trendTitle(trend);
  assert.match(title, /50-day avg unavailable/);
  assert.doesNotMatch(title, /20D above 50D|20D below 50D|20D in line with 50D/);
  assert.equal(trendRelation(trend), null);
});

test("not enough history still speaks the 20-day comparison when that average exists", () => {
  const partial = trendState(historyFromCloses(Array.from({ length: 34 }, (_, index) => 20 + index), "2026-06-12"));
  const sentence = trendSentence(partial);
  assert.match(sentence, /Not enough history/);
  assert.match(sentence, /34 available/);
  assert.match(sentence, /history starts 2026-06-12/);
  assert.match(sentence, /versus the 20-day average/);
  assert.match(sentence, /\+[\d.]+%/);

  const tiny = trendState(historyFromCloses(Array.from({ length: 15 }, (_, index) => index + 1)));
  const shortSentence = trendSentence(tiny);
  assert.match(shortSentence, /15 available/);
  assert.doesNotMatch(shortSentence, /20-day average/);
});

test("a partial serving override does not borrow the computed run", () => {
  const history = historyFromCloses(Array(60).fill(100));
  const computed = trendState(history);
  assert.equal(computed.state, "range");
  assert.equal(computed.days, 11);

  const stateOnly = servingPriceTrend(
    { trend: { rows: [{ series_id: "PX:TSLA", trend_state: "down" }] } },
    "TSLA",
  );
  const daysOnly = servingPriceTrend(
    { trend: { rows: [{ series_id: "PX:TSLA", trend_state: "down", days_in_state: 9 }] } },
    "TSLA",
  );
  for (const serving of [stateOnly, daysOnly]) {
    const resolved = resolveTrend(history, serving);
    assert.equal(resolved.source, "price_history");
    assert.equal(resolved.state, "range");
    assert.equal(resolved.days, computed.days);
    assert.equal(resolved.since, computed.since);
    assert.equal(trendLabel(resolved), "Range · ≥11d");
  }
});

function pillFor(history, tickerData, ticker) {
  return resolveTrend(history, servingPriceTrend(tickerData, ticker));
}

test("switching tickers does not keep the first ticker's trend", () => {
  const tslaHistory = historyFromCloses([...Array(50).fill(100), 130, 140, 150, 160], "2026-08-01");
  const spcxHistory = historyFromCloses(Array.from({ length: 80 }, (_, index) => 300 - index), "2026-06-12");
  const sharedDrivers = [
    { series_id: "DGS10", ticker: "TSLA", trend_state: "up", days_in_state: 40, since: "2026-08-01" },
  ];
  // The same price_trend object copied onto every ticker, the way one shared serving value shows up.
  const sharedPriceTrend = { ticker: "TSLA", trend_state: "up", days_in_state: 4, since: "2026-10-02", vs_ma20: 0.03 };
  const tslaData = {
    price_trend: sharedPriceTrend,
    trend: {
      rows: [
        ...sharedDrivers,
        { series_id: "PX:TSLA", ticker: "TSLA", trend_state: "up", days_in_state: 4, since: "2026-10-02", vs_ma20: 0.029 },
      ],
    },
  };
  const spcxData = {
    price_trend: sharedPriceTrend,
    trend: {
      rows: [
        { ...sharedDrivers[0], ticker: "SPCX" },
        { series_id: "PX:TSLA", ticker: "TSLA", trend_state: "up", days_in_state: 4, since: "2026-10-02", vs_ma20: 0.029 },
      ],
    },
  };

  const tsla = pillFor(tslaHistory, tslaData, "TSLA");
  const spcx = pillFor(spcxHistory, spcxData, "SPCX");

  assert.equal(trendLabel(tsla), "Uptrend · 4d");
  assert.equal(tsla.since, "2026-10-02");
  assert.equal(tsla.days, 4);
  assert.equal(tsla.vsMa20, 0.029);
  assert.equal(trendLabel(spcx), "Downtrend · ≥31d");
  assert.equal(spcx.state, "downtrend");
  assert.equal(spcx.fromStart, true);
  assert.notEqual(spcx.since, tsla.since);
  assert.notEqual(spcx.days, tsla.days);
  assert.ok(spcx.vsMa20 < 0);
  assert.notEqual(trendLabel(tsla), trendLabel(spcx));
  assert.notEqual(spcx.vsMa20, tsla.vsMa20);
});

test("a copied price history does not keep another ticker's distance from the 20-day average", () => {
  const sharedHistory = historyFromCloses(Array.from({ length: 80 }, (_, index) => 100 + index));
  const spcxData = {
    price_trend: { ticker: "TSLA", trend_state: "up", days_in_state: 31, since: "2026-02-20", vs_ma20: 0.05 },
    trend: {
      rows: [
        { series_id: "DGS10", trend_state: "up", days_in_state: 40, since: "2026-08-01", vs_ma20: 0.05 },
        {
          series_id: "PX:SPCX",
          ticker: "SPCX",
          trend_state: "down",
          days_in_state: 9,
          since: "2026-09-20",
          vs_ma20: -0.081,
          close: 90,
          ma20: 98,
          ma50: 110,
        },
      ],
    },
  };
  const spcx = pillFor(sharedHistory, spcxData, "SPCX");
  assert.equal(spcx.source, "serving");
  assert.equal(trendLabel(spcx), "Downtrend · 9d");
  assert.equal(spcx.since, "2026-09-20");
  assert.equal(spcx.vsMa20, -0.081);
  assert.equal(spcx.close, 90);
  assert.equal(spcx.ma20, 98);
  assert.notEqual(trendLabel(spcx), trendLabel(pillFor(sharedHistory, { trend: { rows: [] } }, "TSLA")));
});
