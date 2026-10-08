import { isNumericValue } from "./chart-period.js";

export const DRIVER_LABELS = {
  DGS2: "2Y yield", DGS10: "10Y yield", DGS30: "30Y yield", T10Y2Y: "10Y-2Y curve",
  DFII10: "10Y real yield", T10YIE: "10Y breakeven", DFEDTARU: "Fed target (upper)",
  EFFR: "Effective fed funds", SOFR: "SOFR", VIXCLS: "VIX", DTWEXBGS: "Broad dollar",
  DCOILWTICO: "WTI crude", USEPUINDXD: "Policy uncertainty",
  CPI_YOY: "CPI YoY", CORE_CPI_YOY: "Core CPI YoY", PCE_YOY: "PCE YoY", CORE_PCE_YOY: "Core PCE YoY",
};
export const RELEASE_WINDOWS = {
  week_before: "Week before (T-5..T-1)",
  days_before: "Days before (T-2..T-1)",
  release_day: "Release day (T0)",
};

export function number(value) {
  return ["number", "string"].includes(typeof value) && isNumericValue(value) ? Number(value) : null;
}

export function driverLabel(id) {
  return DRIVER_LABELS[id] || (id.startsWith("ETF:") ? `${id.slice(4)} ETF` : id);
}

export function driverRows(tickerData) {
  return (tickerData?.trend?.rows || [])
    .filter(row => row.series_id && !row.series_id.startsWith("PX:"))
    .slice().sort((a, b) => {
      const ae = number(a.effect);
      const be = number(b.effect);
      if (ae == null && be != null) return 1;
      if (be == null && ae != null) return -1;
      return Math.abs(be ?? 0) - Math.abs(ae ?? 0) || a.series_id.localeCompare(b.series_id);
    });
}

export function signed(value, digits = 2) {
  const numeric = number(value);
  return numeric == null ? "--" : `${numeric > 0 ? "+" : ""}${numeric.toFixed(digits)}`;
}

export function driverValue(row) {
  const value = number(row.value);
  return value == null ? "--" : `${value.toFixed(2)}${row.unit === "%" ? "%" : row.unit === "USD" ? " USD" : ""}`;
}

export function driverChange(row) {
  const value = number(row.change_1m_display);
  return value == null ? "--" : `${signed(value, row.change_unit === "bp" ? 0 : 2)}${row.change_unit || ""}`;
}

export function driverTrend(row) {
  const state = { up: "Uptrend", down: "Downtrend", flat: "Range" }[row.trend_state];
  return state ? `${state}${row.days_in_state == null ? "" : ` - ${row.days_in_state}d`}` : "Trend warming up";
}

export function pressureSummary(tickerData) {
  const rows = driverRows(tickerData);
  const pressure = rows.map(row => number(row.net_pressure)).find(value => value != null) ?? null;
  const linked = rows.filter(row => number(row.effect) != null).length;
  return {
    value: pressure, linked, total: rows.length,
    label: pressure == null ? "Unavailable" : pressure > 0 ? "Tailwind" : pressure < 0 ? "Headwind" : "Neutral",
    width: pressure == null ? 0 : Math.min(1, Math.abs(pressure)) * 50,
  };
}

export function correlationDrift(row, history) {
  const finite = (history || []).filter(point => number(point.corr_90d) != null);
  const now = number(row.corr_90d);
  const previous = finite.length >= 22 ? number(finite[finite.length - 22].corr_90d) : null;
  if (now == null || previous == null) return null;
  return { delta: now - previous, signFlip: now * previous < 0, stronger: Math.abs(now) > Math.abs(previous) };
}
