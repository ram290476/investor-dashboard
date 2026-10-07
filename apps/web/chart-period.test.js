import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_PERIOD,
  normalizePeriod,
  periodQuote,
  resolveChartPeriod,
  sessionReturn,
  validBars,
} from "./chart-period.js";

function bars(count, startPrice = 100, startDate = "2020-01-02") {
  const rows = [];
  const cursor = new Date(`${startDate}T00:00:00Z`);
  for (let index = 0; index < count; index += 1) {
    const date = cursor.toISOString().slice(0, 10);
    rows.push({ date, adj_close: startPrice + index, close: startPrice + index, close_raw: startPrice + index + 5 });
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return rows;
}

function weekdayBars(startDate, endDate, startPrice = 20) {
  const rows = [];
  const cursor = new Date(`${startDate}T00:00:00Z`);
  const end = new Date(`${endDate}T00:00:00Z`);
  let price = startPrice;
  while (cursor <= end) {
    const day = cursor.getUTCDay();
    if (day !== 0 && day !== 6) {
      rows.push({
        date: cursor.toISOString().slice(0, 10),
        adj_close: price,
        close: price,
      });
      price += 0.25;
    }
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return rows;
}

test("1D and 1M keep the existing session offsets", () => {
  const history = bars(40, 100);
  assert.equal(sessionReturn(history, 1), 139 / 138 - 1);
  assert.equal(sessionReturn(history, 21), 139 / 118 - 1);
  const oneDay = periodQuote(history, "1D");
  const oneMonth = periodQuote(history, "1M");
  assert.equal(oneDay.window.length, 2);
  assert.equal(oneMonth.window.length, 22);
  assert.equal(oneDay.returnValue, sessionReturn(history, 1));
  assert.equal(oneMonth.returnValue, sessionReturn(history, 21));
});

test("each chip return is the chart window's first close to its last close", () => {
  const history = bars(400, 50);
  for (const id of ["1D", "1W", "1M", "3M", "1Y"]) {
    const quote = periodQuote(history, id);
    assert.equal(quote.available, true);
    const first = quote.window[0].adj_close;
    const last = quote.window.at(-1).adj_close;
    assert.equal(quote.returnValue, last / first - 1);
    for (const row of quote.window) assert.ok(history.includes(row));
  }
  assert.equal(periodQuote(history, "3Y").available, false);
  assert.equal(periodQuote(history, "5Y").available, false);
});

test("null closes are skipped and never treated as prices", () => {
  const history = [
    { date: "2026-10-01", adj_close: null, close: 10, close_raw: 10 },
    { date: "2026-10-02", adj_close: 20, close: 20, close_raw: 40 },
    { date: "2026-10-03", adj_close: null, close: null },
    { date: "2026-10-06", adj_close: 22, close: 18, close_raw: 90 },
  ];
  const quote = periodQuote(history, "1D");
  assert.equal(quote.available, true);
  assert.equal(quote.returnValue, 22 / 20 - 1);
  assert.deepEqual(quote.window.map((row) => row.date), ["2026-10-02", "2026-10-06"]);
  assert.equal(periodQuote(history, "1W").available, false);
  assert.equal(periodQuote(history, "1W").returnValue, null);
  assert.deepEqual(periodQuote(history, "1W").window, []);
  assert.deepEqual(
    validBars(history).map((row) => row.date),
    ["2026-10-01", "2026-10-02", "2026-10-06"],
  );
});

test("legacy rows without adj_close fall back to close", () => {
  const history = [
    { date: "2026-10-01", close: 10 },
    { date: "2026-10-02", close: 11 },
  ];
  assert.ok(Math.abs(periodQuote(history, "1D").returnValue - 0.1) < 1e-12);
});

test("a zero anchor does not produce an infinite return", () => {
  const history = [
    { date: "2026-10-01", adj_close: 0, close: 0 },
    { date: "2026-10-02", adj_close: 5, close: 5 },
  ];
  assert.equal(periodQuote(history, "1D").returnValue, null);
  assert.equal(periodQuote(history, "1D").window.length, 2);
});

test("short listings disable long chips and fall back toward 1M", () => {
  const spcx = weekdayBars("2026-06-12", "2026-10-02");
  assert.ok(spcx.length > 22);
  assert.ok(spcx.length < 253);
  assert.equal(spcx[0].date, "2026-06-12");
  for (const id of ["1D", "1W", "1M"]) assert.equal(periodQuote(spcx, id).available, true, id);
  assert.equal(periodQuote(spcx, "3M").available, spcx.length > 63);
  for (const id of ["1Y", "3Y", "5Y"]) {
    const quote = periodQuote(spcx, id);
    assert.equal(quote.available, false, id);
    assert.equal(quote.returnValue, null);
    assert.equal(quote.historyStarts, "2026-06-12");
  }
  assert.equal(resolveChartPeriod("5Y", spcx), "1M");
  assert.equal(resolveChartPeriod("1D", spcx), "1D");
});

test("history shorter than 1M falls back to the longest chip that fits", () => {
  const history = bars(10);
  assert.equal(resolveChartPeriod("5Y", history), "1W");
  assert.equal(resolveChartPeriod("1W", history), "1W");
  assert.equal(periodQuote(history, "1M").available, false);
});

test("unknown and missing periods resolve to 1M when that window exists", () => {
  const history = bars(80);
  assert.equal(normalizePeriod("YTD"), DEFAULT_PERIOD);
  assert.equal(normalizePeriod("1W"), "1W");
  assert.equal(resolveChartPeriod(undefined, history), "1M");
  assert.equal(resolveChartPeriod("5D", history), "1M");
  assert.equal(resolveChartPeriod("3M", history), "3M");
});

test("empty history leaves every chip without a window", () => {
  assert.equal(periodQuote(null, "1M").available, false);
  assert.equal(periodQuote([], "1D").returnValue, null);
  assert.equal(resolveChartPeriod("3Y", []), "1M");
  assert.equal(periodQuote(bars(1), "1D").available, false);
});

test("5Y uses the stored backfill and does not extend it", () => {
  const capped = bars(1260, 50, "2021-10-07");
  const quote = periodQuote(capped, "5Y");
  assert.equal(quote.available, true);
  assert.equal(quote.window.length, 1260);
  assert.equal(quote.window[0], capped[0]);
  assert.equal(quote.window.at(-1), capped.at(-1));
  assert.equal(quote.returnValue, capped.at(-1).adj_close / capped[0].adj_close - 1);

  const completed = datedSpan("2021-10-07", "2026-10-06", 1240);
  assert.ok(completed.length < 1260);
  assert.ok(completed.length >= 1000);
  const fiveYear = periodQuote(completed, "5Y");
  assert.equal(fiveYear.available, true);
  assert.equal(fiveYear.window.length, 1240);
  assert.equal(fiveYear.window[0], completed[0]);
  assert.equal(fiveYear.window.at(-1), completed.at(-1));
  assert.equal(fiveYear.window[0].date, "2021-10-07");
  assert.equal(fiveYear.window.at(-1).date, "2026-10-06");
  assert.equal(fiveYear.returnValue, completed.at(-1).adj_close / completed[0].adj_close - 1);
});

function datedSpan(startDate, endDate, count, startPrice = 80) {
  const start = Date.parse(`${startDate}T00:00:00Z`);
  const end = Date.parse(`${endDate}T00:00:00Z`);
  const rows = [];
  for (let index = 0; index < count; index += 1) {
    const at = new Date(start + ((end - start) * index) / (count - 1));
    rows.push({
      date: at.toISOString().slice(0, 10),
      adj_close: startPrice + index,
      close: startPrice + index,
    });
  }
  return rows;
}
