// 20/50-day trend state from daily closes already in price_history.
// Uptrend: close > 20-day average and that average > the 50-day average.
// Downtrend: the reverse. Anything else is Range.
// The rule is fixed to daily closes. It does not follow the selected chart period.
// No prices are filled in. A future per-ticker serving row (series_id PX:<ticker>)
// can override the label; nothing in the lake provides that row today.

import { chartValue, validBars } from "./chart-period.js";

export const TREND_SHORT = 20;
export const TREND_LONG = 50;

const SERVING_STATES = {
  up: "uptrend",
  uptrend: "uptrend",
  down: "downtrend",
  downtrend: "downtrend",
  flat: "range",
  range: "range",
};

function average(values) {
  let sum = 0;
  for (const value of values) sum += value;
  return sum / values.length;
}

function stateAt(closes, index) {
  if (index < TREND_LONG - 1) return null;
  const ma20 = average(closes.slice(index - (TREND_SHORT - 1), index + 1));
  const ma50 = average(closes.slice(index - (TREND_LONG - 1), index + 1));
  const close = closes[index];
  if (close > ma20 && ma20 > ma50) return "uptrend";
  if (close < ma20 && ma20 < ma50) return "downtrend";
  return "range";
}

export function trendState(history) {
  const bars = validBars(history);
  const closes = bars.map((row) => chartValue(row));
  const sessionsAvailable = bars.length;
  const historyStarts = bars[0]?.date || null;
  const asOf = bars.at(-1)?.date || null;
  const close = sessionsAvailable ? closes.at(-1) : null;
  const base = {
    state: null,
    days: null,
    since: null,
    fromStart: false,
    close,
    ma20: null,
    ma50: null,
    vsMa20: null,
    sessionsAvailable,
    needed: TREND_LONG,
    historyStarts,
    asOf,
    source: "price_history",
  };
  if (sessionsAvailable >= TREND_SHORT) {
    const ma20 = average(closes.slice(-TREND_SHORT));
    base.ma20 = ma20;
    base.vsMa20 = ma20 ? close / ma20 - 1 : null;
  }
  if (sessionsAvailable < TREND_LONG) return base;

  const latest = sessionsAvailable - 1;
  const state = stateAt(closes, latest);
  base.state = state;
  base.ma50 = average(closes.slice(-TREND_LONG));
  let days = 0;
  let sinceIndex = latest;
  for (let index = latest; index >= TREND_LONG - 1; index -= 1) {
    if (stateAt(closes, index) !== state) break;
    days += 1;
    sinceIndex = index;
  }
  base.days = days;
  base.since = bars[sinceIndex]?.date || null;
  base.fromStart = sinceIndex === TREND_LONG - 1;
  return base;
}

// Prefer a per-ticker price trend if serving ever publishes one.
// Macro driver rows (DGS10, ETF:SPY, …) use a different rule and are ignored.
export function servingPriceTrend(tickerData, ticker) {
  if (!tickerData || !ticker) return null;
  const rows = tickerData.trend?.rows || [];
  const row = rows.find((item) => {
    const id = String(item?.series_id || "");
    return id === `PX:${ticker}` || id === `PRICE:${ticker}`;
  });
  const source = tickerData.price_trend || row;
  if (!source) return null;
  const state = SERVING_STATES[String(source.trend_state || source.state || "").toLowerCase()];
  if (!state) return null;
  const days = Number(source.days_in_state ?? source.days);
  return {
    state,
    days: Number.isFinite(days) && days > 0 ? days : null,
    since: source.since || source.trend_since || null,
    fromStart: Boolean(source.from_start || source.fromStart),
  };
}

export function resolveTrend(history, serving) {
  const computed = trendState(history);
  if (!serving?.state) return computed;
  return {
    ...computed,
    state: serving.state,
    days: serving.days ?? computed.days,
    since: serving.since || computed.since,
    fromStart: serving.days != null ? serving.fromStart : computed.fromStart,
    source: "serving",
  };
}

export function trendLabel(trend) {
  if (!trend?.state || trend.days == null) return "Not enough history";
  const name = trend.state === "uptrend" ? "Uptrend" : trend.state === "downtrend" ? "Downtrend" : "Range";
  return `${name} · ${trend.fromStart ? "≥" : ""}${trend.days}d`;
}

export function trendRelation(trend) {
  if (trend?.close == null || trend.ma20 == null || trend.ma50 == null) return null;
  const above20 = trend.close > trend.ma20;
  const below20 = trend.close < trend.ma20;
  const maAbove = trend.ma20 > trend.ma50;
  const maBelow = trend.ma20 < trend.ma50;
  if (above20 && maAbove) return "price above its 20-day and 50-day averages";
  if (below20 && maBelow) return "price below its 20-day and 50-day averages";
  if (above20 && maBelow) return "price above 20D, but 20D below 50D";
  if (below20 && maAbove) return "price below 20D, but 20D above 50D";
  if (!above20 && !below20) return "price at its 20-day average";
  return "20-day average in line with the 50-day";
}
