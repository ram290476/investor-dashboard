// Market comparison and correlation drift, matching the design artboard.
// Returns for the chart period, and ρ/β, prefer the chart file's market_comparison
// block. Older chart files fall back to the same formulas on adjusted closes.
// Correlation flags compare the latest 30-day link with the 90-day link.
// GDELT tone and tariff-news intensity are in the design sample only; they are
// not live drivers, so they are not invented here.

import { MARKET_OVERLAY_IDS } from "./chart-overlays.js";
import { chartValue, isNumericValue, periodQuote, resolveChartPeriod, validBars } from "./chart-period.js";

export const BETA_WINDOW = 90;
export const BETA_MIN_PAIRS = 20;
export const CORRELATION_WINDOW = 126;
export const CORRELATION_SHIFT = 0.08;

export const CORRELATION_DRIVERS = Object.freeze([
  Object.freeze({ id: "DGS10", label: "10Y yield" }),
  Object.freeze({ id: "DGS2", label: "2Y yield" }),
  Object.freeze({ id: "DFII10", label: "10Y real yield" }),
  Object.freeze({ id: "VIXCLS", label: "VIX" }),
  Object.freeze({ id: "DTWEXBGS", label: "Dollar (broad)" }),
  Object.freeze({ id: "DCOILWTICO", label: "WTI crude" }),
  Object.freeze({ id: "USEPUINDXD", label: "Policy uncertainty" }),
]);

const FLAG_LABELS = {
  "sign-flip": "Sign flip",
  strengthening: "Strengthening",
  weakening: "Weakening",
  stable: "Stable",
};

function finite(value) {
  return ["number", "string"].includes(typeof value) && isNumericValue(value) ? Number(value) : null;
}

export function signedPercent(value, digits = 1) {
  const number = finite(value);
  if (number == null) return "—";
  const body = Math.abs(number * 100).toFixed(digits);
  return number < 0 ? `-${body}%` : `+${body}%`;
}

export function signedPoints(points, digits = 1) {
  const number = finite(points);
  if (number == null) return "—";
  const body = `${Math.abs(number).toFixed(digits)} pp`;
  return number < 0 ? `−${body}` : `+${body}`;
}

export function formatLink(value) {
  const number = finite(value);
  if (number == null) return "—";
  return number.toFixed(2);
}

export function nowText(value) {
  const number = finite(value);
  if (number == null) return "—";
  const body = Math.abs(number).toFixed(2);
  return number < 0 ? `-${body}` : `+${body}`;
}

export function deltaText(value) {
  const number = finite(value);
  if (number == null) return "—";
  return number >= 0 ? `▲ +${number.toFixed(2)}` : `▼ ${number.toFixed(2)}`;
}

export function returnTone(value) {
  const number = finite(value);
  if (number == null) return "";
  return number >= 0 ? "positive" : "negative";
}

function dailyReturns(history) {
  const bars = validBars(history);
  const out = [];
  for (let i = 1; i < bars.length; i += 1) {
    const previous = chartValue(bars[i - 1]);
    const current = chartValue(bars[i]);
    if (previous == null || current == null || previous === 0) continue;
    out.push({ date: String(bars[i].date || "").slice(0, 10), ret: current / previous - 1 });
  }
  return out;
}

// β is the regression of the stock's daily return on the index. Dates are paired,
// then the newest BETA_WINDOW pairs are kept. Fewer than BETA_MIN_PAIRS stays null.
export function pairedLink(stockHistory, indexHistory, window = BETA_WINDOW, minPairs = BETA_MIN_PAIRS) {
  const stock = dailyReturns(stockHistory);
  const index = new Map(dailyReturns(indexHistory).map((row) => [row.date, row.ret]));
  const paired = [];
  for (let i = stock.length - 1; i >= 0 && paired.length < window; i -= 1) {
    const indexReturn = index.get(stock[i].date);
    if (indexReturn == null) continue;
    paired.push([indexReturn, stock[i].ret]);
  }
  paired.reverse();
  const n = paired.length;
  if (n < minPairs) return { rho: null, beta: null, n };
  const meanX = paired.reduce((sum, row) => sum + row[0], 0) / n;
  const meanY = paired.reduce((sum, row) => sum + row[1], 0) / n;
  let cov = 0;
  let varX = 0;
  let varY = 0;
  paired.forEach(([x, y]) => {
    cov += (x - meanX) * (y - meanY);
    varX += (x - meanX) ** 2;
    varY += (y - meanY) ** 2;
  });
  if (varX <= 1e-18 || varY <= 1e-18) return { rho: null, beta: null, n };
  return { rho: cov / Math.sqrt(varX * varY), beta: cov / varX, n };
}

function serverValue(block, period) {
  if (!block || typeof block !== "object" || !Object.prototype.hasOwnProperty.call(block, period)) return undefined;
  return finite(block[period]);
}

export function marketHeadline(ticker, period, stockReturn, rows) {
  if (stockReturn == null) return `Return unavailable for ${period}`;
  const comparable = rows.filter((row) => row.relative != null);
  const every = comparable.length === rows.length && rows.length > 0;
  let stance = "";
  if (every && comparable.every((row) => row.relative >= 0)) stance = " · ahead of every index shown";
  else if (every && comparable.every((row) => row.relative < 0)) stance = " · behind every index shown";
  else if (comparable.length) stance = " · mixed vs indexes";
  return `${ticker} ${signedPercent(stockReturn)} over ${period}${stance}`;
}

export function marketFooter(rows) {
  const short = (rows || []).some((row) => row.n != null && row.n < BETA_WINDOW && row.rho != null);
  const fewer = short ? " Some links use fewer than 90 sessions." : "";
  return `ρ and β use the last 90 paired daily returns.${fewer} Index data: free ETF proxies (SPY, DIA, QQQ, IWM, XLY, ITA, SMH).`;
}

export function marketComparisonModel({
  ticker,
  periodId,
  overlayIds,
  stockHistory,
  indexHistories = {},
  server = null,
  labels = {},
  linkLoading = false,
}) {
  const period = resolveChartPeriod(periodId, stockHistory);
  const stockFromServer = serverValue(server?.returns, period);
  const stockReturn = stockFromServer === undefined ? periodQuote(stockHistory, period).returnValue : stockFromServer;
  const ids = [];
  for (const id of overlayIds || []) {
    if (!MARKET_OVERLAY_IDS.includes(id) || id === ticker || ids.includes(id)) continue;
    ids.push(id);
  }
  const rows = ids.map((id) => {
    const block = server?.indexes?.[id];
    const hasBlock = block && typeof block === "object";
    const fromServer = serverValue(block?.returns, period);
    const ret = fromServer === undefined ? periodQuote(indexHistories[id] || [], period).returnValue : fromServer;
    let rho = null;
    let beta = null;
    let n = null;
    let linkState = "unavailable";
    if (hasBlock) {
      rho = finite(block.rho_90d);
      beta = finite(block.beta_90d);
      n = finite(block.n);
      linkState = rho == null ? "unavailable" : "ready";
    } else if (linkLoading) {
      linkState = "loading";
    } else {
      const link = pairedLink(stockHistory, indexHistories[id] || []);
      rho = link.rho;
      beta = link.beta;
      n = link.n;
      linkState = rho == null ? "unavailable" : "ready";
    }
    return {
      id,
      label: labels[id] || id,
      ret,
      relative: stockReturn == null || ret == null ? null : (stockReturn - ret) * 100,
      rho,
      beta,
      n,
      linkState,
    };
  });
  const headline = rows.length ? marketHeadline(ticker, period, stockReturn, rows) : "No index on the chart";
  return { period, stockReturn, rows, headline, summary: headline, footer: marketFooter(rows) };
}

// Design rule: a sign change wins; otherwise a shift of at least 0.08 is
// strengthening when the 30-day link is larger in magnitude, else weakening.
export function correlationFlag(corr30, corr90) {
  const current = finite(corr30);
  const baseline = finite(corr90);
  if (current == null || baseline == null) return null;
  const delta = current - baseline;
  const signFlip = (current > 0) !== (baseline > 0);
  let flag = "stable";
  if (signFlip) flag = "sign-flip";
  else if (Math.abs(delta) >= CORRELATION_SHIFT && Math.abs(current) > Math.abs(baseline)) flag = "strengthening";
  else if (Math.abs(delta) >= CORRELATION_SHIFT) flag = "weakening";
  return {
    delta,
    flag,
    label: FLAG_LABELS[flag],
    signFlip,
    stronger: flag === "strengthening",
    withStock: current >= 0,
  };
}

function latestFinite(history, key) {
  const points = Array.isArray(history) ? history : [];
  for (let i = points.length - 1; i >= 0; i -= 1) {
    const value = finite(points[i]?.[key]);
    if (value != null) return value;
  }
  return null;
}

export function correlationSpark(values) {
  const nums = (values || []).map(finite);
  if (nums.filter((value) => value != null).length < 2) return null;
  const height = 28;
  const width = 100;
  const yAt = (value) => {
    const clamped = Math.max(-1, Math.min(1, value));
    return (2 + (1 - (clamped + 1) / 2) * (height - 4)).toFixed(2);
  };
  let pen = false;
  let path = "";
  nums.forEach((value, index) => {
    if (value == null) {
      pen = false;
      return;
    }
    const x = (nums.length === 1 ? 0 : (index / (nums.length - 1)) * width).toFixed(2);
    path += `${pen ? "L" : "M"}${x} ${yAt(value)} `;
    pen = true;
  });
  return { d: path.trim(), zero: yAt(0), width, height };
}

export function correlationFooter(ticker) {
  return `6-month rolling 30-day ρ with ${ticker} daily returns. Blue = moves with the stock, orange = against. Flags fire when the 30D link shifts ≥ 0.08 from 90D or changes sign.`;
}

export function correlationPanelModel(ticker, trendRows, history) {
  const byId = new Map((trendRows || []).filter((row) => row?.series_id).map((row) => [row.series_id, row]));
  const rows = CORRELATION_DRIVERS.map((driver) => {
    const row = byId.get(driver.id);
    const series = Array.isArray(history?.[driver.id]) ? history[driver.id].slice(-CORRELATION_WINDOW) : [];
    const corr30 = finite(row?.corr_30d) ?? latestFinite(series, "corr_30d");
    const corr90 = finite(row?.corr_90d) ?? latestFinite(series, "corr_90d");
    const link = correlationFlag(corr30, corr90);
    return {
      id: driver.id,
      label: driver.label,
      corr30,
      corr90,
      link,
      spark: correlationSpark(series.map((point) => point?.corr_30d)),
    };
  });
  const known = rows.filter((row) => row.link);
  const strengthening = known.filter((row) => row.link.flag === "strengthening").length;
  const flips = known.filter((row) => row.link.flag === "sign-flip").length;
  const summary = known.length
    ? `${ticker} · ${strengthening} strengthening · ${flips} sign flip${flips === 1 ? "" : "s"}`
    : "Unavailable";
  return { rows, known: known.length, strengthening, flips, summary, footer: correlationFooter(ticker) };
}
