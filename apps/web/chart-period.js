// Period chips re-window the price chart using daily closes already in price_history.
// Session counts match the live 1D (1 session) and 1M (21 sessions) definitions.
// 5Y is the serving backfill cap (dashboard_build keeps 1,260 rows). A finished
// five-year backfill can land a few sessions short of that cap, so it also unlocks
// when the dated span is at least 4.75 years and 1,000 closes are present.
// Hourly bars are not in the lake; every chip uses these daily closes. Missing
// sessions stay missing — nothing here invents a price.

export const PERIODS = Object.freeze([
  Object.freeze({ id: "1D", sessions: 1 }),
  Object.freeze({ id: "1W", sessions: 5 }),
  Object.freeze({ id: "1M", sessions: 21 }),
  Object.freeze({ id: "3M", sessions: 63 }),
  Object.freeze({ id: "YTD", sessions: null }),
  Object.freeze({ id: "1Y", sessions: 252 }),
  Object.freeze({ id: "3Y", sessions: 756 }),
  Object.freeze({ id: "5Y", sessions: 1259 }),
]);

export const DEFAULT_PERIOD = "1M";
export const MAX_PRICE_ROWS = 1260;
export const FIVE_YEAR_MIN_SPAN_YEARS = 4.75;
export const FIVE_YEAR_MIN_BARS = 1000;

const PERIOD_IDS = new Set(PERIODS.map((period) => period.id));

export function isNumericValue(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

// Charts and returns use adj_close (split- and dividend-adjusted). They fall back to
// close, which older rows carry alone. close_raw is the traded print and is not a return input.
export function chartValue(row) {
  if (!row) return null;
  return isNumericValue(row.adj_close) ? Number(row.adj_close) : isNumericValue(row.close) ? Number(row.close) : null;
}

export function validBars(history) {
  return (Array.isArray(history) ? history : []).filter((row) => chartValue(row) !== null);
}

export function sessionReturn(bars, offset) {
  if (!bars || bars.length <= offset || offset < 0) return null;
  const before = chartValue(bars[bars.length - 1 - offset]);
  const latest = chartValue(bars.at(-1));
  if (before === null || latest === null || before === 0) return null;
  return latest / before - 1;
}

export function periodById(id) {
  return PERIODS.find((period) => period.id === id) || null;
}

export function normalizePeriod(id) {
  return PERIOD_IDS.has(id) ? id : DEFAULT_PERIOD;
}

function spanYears(bars) {
  const first = bars[0]?.date;
  const last = bars.at(-1)?.date;
  if (typeof first !== "string" || typeof last !== "string") return null;
  const start = Date.parse(`${first}T00:00:00Z`);
  const end = Date.parse(`${last}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  return (end - start) / (365.25 * 24 * 60 * 60 * 1000);
}

export function periodAvailable(bars, period) {
  if (!bars || bars.length < 2) return false;
  if (period.id === "YTD") {
    const year = String(bars.at(-1)?.date || "").slice(0, 4);
    return bars.filter((bar) => String(bar.date || "").startsWith(year)).length > 1;
  }
  if (period.id === "5Y") {
    if (bars.length >= MAX_PRICE_ROWS) return true;
    const span = spanYears(bars);
    return span !== null && span >= FIVE_YEAR_MIN_SPAN_YEARS && bars.length >= FIVE_YEAR_MIN_BARS;
  }
  return bars.length > period.sessions;
}

function offsetFor(bars, period) {
  if (period.id === "YTD") {
    const year = String(bars.at(-1)?.date || "").slice(0, 4);
    const first = bars.findIndex((bar) => String(bar.date || "").startsWith(year));
    return bars.length - first - 1;
  }
  if (period.id === "5Y") return Math.min(period.sessions, bars.length - 1);
  return period.sessions;
}

// Quote for one chip. window is a suffix of the valid bars already stored — never a filled series.
// Overlays and under-chart lanes should draw quote.window so they share this axis.
export function periodQuote(history, periodId) {
  const period = periodById(periodId) || periodById(DEFAULT_PERIOD);
  const bars = validBars(history);
  const historyStarts = bars[0]?.date || null;
  const needed = period.id === "5Y" ? MAX_PRICE_ROWS : period.id === "YTD" ? 2 : period.sessions + 1;
  if (!periodAvailable(bars, period)) {
    return {
      id: period.id,
      available: false,
      returnValue: null,
      window: [],
      sessionsAvailable: bars.length,
      historyStarts,
      needed,
    };
  }
  const offset = offsetFor(bars, period);
  const window = bars.slice(bars.length - 1 - offset);
  return {
    id: period.id,
    available: true,
    returnValue: sessionReturn(bars, offset),
    window,
    sessionsAvailable: bars.length,
    historyStarts,
    needed,
  };
}

// Keep the saved chip when that window exists. Otherwise prefer 1M, then the longest chip that fits.
export function resolveChartPeriod(requested, history) {
  const id = normalizePeriod(requested);
  if (periodQuote(history, id).available) return id;
  if (id !== DEFAULT_PERIOD && periodQuote(history, DEFAULT_PERIOD).available) return DEFAULT_PERIOD;
  const available = PERIODS.filter(
    (period) => period.id !== "YTD" && periodQuote(history, period.id).available,
  );
  return available.length ? available[available.length - 1].id : DEFAULT_PERIOD;
}
