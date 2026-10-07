// 20/50-day trend state from daily closes already in price_history.
// Uptrend: close > 20-day average and that average > the 50-day average.
// Downtrend: the reverse. Anything else is Range.
// The rule is fixed to daily closes. It does not follow the selected chart period.
// trend_metrics publishes one price row per equity (series_id PX:<ticker>).
// That row can override the label. Macro driver rows use a different rule and are ignored.
// A price trend is used only when it belongs to the ticker being shown.

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

function ownedByTicker(source, ticker) {
  const owner = String(source?.ticker || "").toUpperCase();
  return !owner || owner === ticker;
}

function finiteOrNull(value) {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

// A published PX:<ticker> row wins. price_trend is used only when it names this
// ticker. Another symbol's price trend, and every macro driver row, is ignored.
export function servingPriceTrend(tickerData, ticker) {
  if (!tickerData || !ticker) return null;
  const symbol = String(ticker).toUpperCase();
  const rows = Array.isArray(tickerData.trend?.rows) ? tickerData.trend.rows : [];
  const row = rows.find((item) => {
    const id = String(item?.series_id || "");
    const matches = id === `PX:${symbol}` || id === `PRICE:${symbol}`;
    return matches && ownedByTicker(item, symbol);
  });
  const priceTrend = tickerData.price_trend;
  const priceTrendOk = priceTrend && String(priceTrend.ticker || "").toUpperCase() === symbol;
  const source = row || (priceTrendOk ? priceTrend : null);
  if (!source) return null;
  const state = SERVING_STATES[String(source.trend_state || source.state || "").toLowerCase()];
  if (!state) return null;
  const days = Number(source.days_in_state ?? source.days);
  return {
    state,
    days: Number.isFinite(days) && days > 0 ? days : null,
    since: source.since || source.trend_since || null,
    fromStart: Boolean(source.from_start || source.fromStart),
    vsMa20: finiteOrNull(source.vs_ma20 ?? source.vsMa20),
    close: finiteOrNull(source.close),
    ma20: finiteOrNull(source.ma20),
    ma50: finiteOrNull(source.ma50),
  };
}

function fallbackPrice(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return `$${Number(value).toFixed(2)}`;
}

function fallbackPercent(value, digits = 1) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const number = Number(value) * 100;
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}%`;
}

// A serving row replaces the computed run only when it brings its own state,
// day count, and start date. A state alone must not reuse another run's days.
export function resolveTrend(history, serving) {
  const computed = trendState(history);
  if (!serving?.state || serving.days == null || !serving.since) return computed;
  const resolved = {
    ...computed,
    state: serving.state,
    days: serving.days,
    since: serving.since,
    fromStart: Boolean(serving.fromStart),
    source: "serving",
  };
  // The selected ticker's published price trend carries its own distance from the
  // 20-day average. A copied price history must not keep another symbol's percent.
  for (const key of ["vsMa20", "close", "ma20", "ma50"]) {
    if (serving[key] != null && Number.isFinite(serving[key])) resolved[key] = serving[key];
  }
  return resolved;
}

export function trendLabel(trend) {
  if (!trend?.state || trend.days == null) return "Not enough history";
  const name = trend.state === "uptrend" ? "Uptrend" : trend.state === "downtrend" ? "Downtrend" : "Range";
  return `${name} · ${trend.fromStart ? "≥" : ""}${trend.days}d`;
}

export function averageNote(trend) {
  if (trend?.ma20 == null || trend?.ma50 == null) return null;
  if (trend.ma20 > trend.ma50) return "20D above 50D";
  if (trend.ma20 < trend.ma50) return "20D below 50D";
  return "20D in line with 50D";
}

function sessionPhrase(days) {
  return `${days} session${days === 1 ? "" : "s"}`;
}

export function trendTitle(trend, formatPrice = fallbackPrice, formatPercent = fallbackPercent) {
  if (!trend?.state || trend.days == null) {
    const start = trend?.historyStarts ? `, history starts ${trend.historyStarts}` : "";
    const lines = [`Not enough history · trend needs ${trend?.needed} daily closes (have ${trend?.sessionsAvailable}${start})`];
    if (trend?.ma20 != null) {
      const vs = trend.vsMa20 == null ? "" : `   (${formatPercent(trend.vsMa20, 1)} vs 20D)`;
      lines.push(`20-day avg ${formatPrice(trend.ma20)}${vs}`);
    }
    return lines.join("\n");
  }
  const name = trend.state === "uptrend" ? "Uptrend" : trend.state === "downtrend" ? "Downtrend" : "Range";
  const sessions = sessionPhrase(trend.days);
  const held = trend.fromStart ? `at least ${sessions}` : sessions;
  const since = `since ${trend.since}`;
  const unknown = trend.fromStart ? ". Earlier sessions have no 50-day average" : "";
  const relation = trendRelation(trend);
  const lines = [
    trend.state === "range" && relation
      ? `Range for ${held} (${since})${unknown}: ${relation}`
      : `${name} for ${held} (${since})${unknown}`,
  ];
  const vs = trend.vsMa20 == null ? "" : `   (${formatPercent(trend.vsMa20, 1)} vs 20D)`;
  const note = averageNote(trend);
  lines.push(`Price      ${formatPrice(trend.close)}${vs}`);
  lines.push(trend.ma20 == null ? "20-day avg unavailable" : `20-day avg ${formatPrice(trend.ma20)}`);
  lines.push(trend.ma50 == null ? "50-day avg unavailable" : `50-day avg ${formatPrice(trend.ma50)}${note ? `   (${note})` : ""}`);
  lines.push(`Daily closes, as of ${trend.asOf || "the latest close"}`);
  return lines.join("\n");
}

export function trendSentence(trend, formatPercent = fallbackPercent) {
  if (!trend?.state || trend.days == null) {
    const start = trend?.historyStarts ? `, history starts ${trend.historyStarts}` : "";
    const vs = trend?.vsMa20 == null ? "" : `, ${formatPercent(trend.vsMa20, 1)} versus the 20-day average`;
    return `Not enough history. Trend needs ${trend?.needed} daily closes, ${trend?.sessionsAvailable} available${start}${vs}.`;
  }
  const name = trend.state === "uptrend" ? "Uptrend" : trend.state === "downtrend" ? "Downtrend" : "Range";
  const sessions = sessionPhrase(trend.days);
  const relation = trendRelation(trend);
  const parts = [];
  if (relation) parts.push(relation);
  if (trend.vsMa20 != null) parts.push(`${formatPercent(trend.vsMa20, 1)} versus the 20-day average`);
  const detail = parts.length ? `: ${parts.join(", ")}` : "";
  if (trend.fromStart) {
    const lead = `Daily closes. ${name} for at least ${sessions} since ${trend.since}. Earlier sessions have no 50-day average`;
    return parts.length ? `${lead}: ${parts.join(", ")}.` : `${lead}.`;
  }
  return `Daily closes. ${name} for ${sessions}${detail}.`;
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
