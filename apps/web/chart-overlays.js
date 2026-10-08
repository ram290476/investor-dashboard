import { chartValue, isNumericValue, validBars } from "./chart-period.js";

export const CHART_LANES = Object.freeze([
  { id: "VOL", label: "Volume" },
  { id: "SI", label: "Short interest" },
  { id: "OPT", label: "Options" },
  { id: "PRESS", label: "Macro pressure" },
]);

export const DEFAULT_CHART_SETTINGS = Object.freeze({ overlays: [], lanes: ["VOL", "PRESS"] });

const OVERLAYS = [
  { id: "SPY", label: "S&P 500 (SPY)", group: "Market", kind: "market", color: "#e6eaf0" },
  { id: "DIA", label: "Dow Jones (DIA)", group: "Market", kind: "market", color: "#f2a33a" },
  { id: "QQQ", label: "Nasdaq-100 (QQQ)", group: "Market", kind: "market", color: "#39c5b0" },
  { id: "IWM", label: "Russell 2000 (IWM)", group: "Market", kind: "market", color: "#ec7ba6" },
  { id: "XLY", label: "Consumer discretionary", group: "Market", kind: "market", color: "#b392f0" },
  { id: "ITA", label: "Aerospace & defense", group: "Market", kind: "market", color: "#60a5fa" },
  { id: "SMH", label: "Semiconductors", group: "Market", kind: "market", color: "#fb7185" },
  { id: "MA10", label: "10-day", group: "Moving averages", kind: "average", window: 10, color: "#7dd3fc" },
  { id: "MA20", label: "20-day", group: "Moving averages", kind: "average", window: 20, color: "#fde68a" },
  { id: "MA50", label: "50-day", group: "Moving averages", kind: "average", window: 50, color: "#fb923c" },
  { id: "MA100", label: "100-day", group: "Moving averages", kind: "average", window: 100, color: "#f9a8d4" },
  { id: "MA200", label: "200-day", group: "Moving averages", kind: "average", window: 200, color: "#a5b4fc" },
  { id: "DGS10", label: "10Y Treasury", group: "Rates", kind: "macro", inverse: true, color: "#e3b341" },
  { id: "DGS2", label: "2Y Treasury", group: "Rates", kind: "macro", inverse: true, color: "#fdbA74" },
  { id: "T10Y2Y", label: "10Y–2Y curve", group: "Rates", kind: "macro", color: "#8ebbff" },
  { id: "DFII10", label: "10Y real yield", group: "Rates", kind: "macro", inverse: true, color: "#5eead4" },
  { id: "SOFR", label: "SOFR", group: "Rates", kind: "macro", inverse: true, color: "#c084fc" },
  { id: "CPI_YOY", label: "CPI YoY", group: "Inflation", kind: "macro", inverse: true, color: "#ff8fa3" },
  { id: "CORE_CPI_YOY", label: "Core CPI YoY", group: "Inflation", kind: "macro", inverse: true, color: "#f472b6" },
  { id: "PCE_YOY", label: "PCE YoY", group: "Inflation", kind: "macro", inverse: true, color: "#e879f9" },
  { id: "T10YIE", label: "10Y breakeven", group: "Inflation", kind: "macro", color: "#a78bfa" },
  { id: "VIXCLS", label: "VIX", group: "Risk", kind: "macro", inverse: true, color: "#c084fc" },
  { id: "DTWEXBGS", label: "Broad dollar index", group: "Risk", kind: "macro", inverse: true, color: "#5eead4" },
  { id: "DCOILWTICO", label: "WTI crude", group: "Risk", kind: "macro", color: "#a3e635" },
  { id: "USEPUINDXD", label: "Policy uncertainty", group: "Policy & geo", kind: "macro", inverse: true, color: "#fca5a5" },
  { id: "FUNDAMENTAL:revenue_gaap", label: "Revenue", group: "Company", kind: "fundamental", color: "#60a5fa" },
  { id: "FUNDAMENTAL:gross_profit_gaap", label: "Gross profit", group: "Company", kind: "fundamental", color: "#facc15" },
  { id: "FUNDAMENTAL:gross_margin_gaap", label: "Gross margin", group: "Company", kind: "fundamental", color: "#fb7185" },
  { id: "FUNDAMENTAL:deliveries", label: "Deliveries", group: "Company", kind: "fundamental", color: "#39c5b0" },
  { id: "FUNDAMENTAL:fsd_subscribers", label: "FSD subscribers", group: "Company", kind: "fundamental", color: "#b392f0" },
];

const GROUP_ORDER = ["Market", "Moving averages", "Rates", "Inflation", "Risk", "Policy & geo", "Company"];
const overlayById = new Map(OVERLAYS.map((overlay) => [overlay.id, overlay]));

function dateKey(bar) {
  return String(bar?.date || bar?.ts || "").slice(0, 10);
}

function pointValue(point, key = "value") {
  return isNumericValue(point?.[key]) ? Number(point[key]) : null;
}

export function chartSettingsFor(prefs, ticker) {
  const saved = prefs?.chart_settings?.[ticker];
  if (!saved) return { overlays: [...DEFAULT_CHART_SETTINGS.overlays], lanes: [...DEFAULT_CHART_SETTINGS.lanes] };
  return {
    overlays: Array.isArray(saved.overlays) ? saved.overlays.filter((id) => overlayById.has(id)).slice(0, 5) : [],
    lanes: Array.isArray(saved.lanes) ? saved.lanes.filter((id) => CHART_LANES.some((lane) => lane.id === id)) : [],
  };
}

export function overlayGroups(tickerData, chartData, dashboard) {
  const availableFundamentals = new Set((chartData?.fundamentals || []).map((row) => row.series_id));
  return GROUP_ORDER.map((label) => ({
    id: label,
    label: label === "Company" ? (tickerData?.ticker || "Company") : label,
    overlays: OVERLAYS.filter((overlay) => {
      if (overlay.group !== label) return false;
      if (overlay.kind === "market") return Boolean(dashboard?.tickers?.[overlay.id]?.price_history?.length);
      if (overlay.kind === "average") return validBars(tickerData?.price_history).length >= overlay.window;
      if (overlay.kind === "macro") return Boolean(chartData?.macro_series?.[overlay.id]?.length);
      if (overlay.kind === "fundamental") return availableFundamentals.has(overlay.id.slice("FUNDAMENTAL:".length));
      return true;
    }),
  })).filter((group) => group.overlays.length);
}

function alignedValues(points, bars, valueKey = "value") {
  const ordered = [...(points || [])]
    .filter((point) => dateKey(point) && pointValue(point, valueKey) !== null)
    .sort((left, right) => dateKey(left).localeCompare(dateKey(right)));
  const valuesByDate = new Map(ordered.map((point) => [dateKey(point), pointValue(point, valueKey)]));
  let previous = null;
  let pointIndex = 0;
  return bars.map((bar) => {
    const day = dateKey(bar);
    while (pointIndex < ordered.length && dateKey(ordered[pointIndex]) <= day) {
      previous = pointValue(ordered[pointIndex], valueKey);
      pointIndex += 1;
    }
    return valuesByDate.get(day) ?? previous;
  });
}

function alignedIntradayValues(points, bars) {
  const ordered = [...points]
    .filter((point) => point.ts && pointValue(point, "close") !== null)
    .sort((left, right) => left.ts.localeCompare(right.ts));
  let previous = null;
  let pointIndex = 0;
  return bars.map((bar) => {
    while (pointIndex < ordered.length && ordered[pointIndex].ts <= bar.ts) {
      previous = pointValue(ordered[pointIndex], "close");
      pointIndex += 1;
    }
    return previous;
  });
}

function movingAverage(priceHistory, window, bars) {
  const prices = validBars(priceHistory);
  const averagePoints = [];
  for (let index = window - 1; index < prices.length; index += 1) {
    const values = prices.slice(index - window + 1, index + 1).map(chartValue);
    averagePoints.push({ date: prices[index].date, value: values.reduce((sum, value) => sum + value, 0) / window });
  }
  return alignedValues(averagePoints, bars);
}

function marketValues(overlay, dashboard, bars) {
  const record = dashboard?.tickers?.[overlay.id];
  if (!record) return bars.map(() => null);
  if (bars.some((bar) => bar.ts) && record.intraday?.bars?.length) {
    return alignedIntradayValues(record.intraday.bars, bars);
  }
  const daily = (record.price_history || []).map((bar) => ({ date: bar.date, value: chartValue(bar) }));
  const valuesByDate = new Map(daily.map((point) => [point.date, point.value]));
  return bars.map((bar) => valuesByDate.get(dateKey(bar)) ?? null);
}

export function valuesForOverlay(id, { bars, tickerData, chartData, dashboard }) {
  const overlay = overlayById.get(id);
  if (!overlay) return bars.map(() => null);
  if (overlay.kind === "market") return marketValues(overlay, dashboard, bars);
  if (overlay.kind === "average") return movingAverage(tickerData?.price_history || [], overlay.window, bars);
  if (overlay.kind === "macro") return alignedValues(chartData?.macro_series?.[overlay.id], bars);
  const metric = overlay.id.slice("FUNDAMENTAL:".length);
  return alignedValues((chartData?.fundamentals || []).filter((row) => row.series_id === metric), bars);
}

export function overlayDefinition(id) {
  return overlayById.get(id) || null;
}

export function isMarketOverlay(id) {
  return overlayById.get(id)?.kind === "market";
}