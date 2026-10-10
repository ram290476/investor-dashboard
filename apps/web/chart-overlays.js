import { chartValue, isNumericValue, validBars } from "./chart-period.js";

export const CHART_LANES = Object.freeze([
  { id: "VOL", label: "Volume" },
  { id: "SI", label: "Short interest" },
  { id: "PRESS", label: "Macro pressure" },
  { id: "OPT", label: "Options" },
]);

export const DEFAULT_CHART_SETTINGS = Object.freeze({ overlays: [], lanes: ["VOL", "PRESS"] });

export const OVERLAYS = [
  { id: "SPY", label: "S&P 500 (SPY)", group: "Market", kind: "market" },
  { id: "DIA", label: "Dow Jones (DIA)", group: "Market", kind: "market" },
  { id: "QQQ", label: "Nasdaq-100 (QQQ)", group: "Market", kind: "market" },
  { id: "IWM", label: "Russell 2000 (IWM)", group: "Market", kind: "market" },
  { id: "XLY", label: "Consumer discretionary", group: "Market", kind: "market" },
  { id: "ITA", label: "Aerospace & defense", group: "Market", kind: "market" },
  { id: "SMH", label: "Semiconductors", group: "Market", kind: "market" },
  { id: "MA10", label: "10-day", group: "Moving averages", kind: "average", window: 10 },
  { id: "MA20", label: "20-day", group: "Moving averages", kind: "average", window: 20 },
  { id: "MA50", label: "50-day", group: "Moving averages", kind: "average", window: 50 },
  { id: "MA100", label: "100-day", group: "Moving averages", kind: "average", window: 100 },
  { id: "MA200", label: "200-day", group: "Moving averages", kind: "average", window: 200 },
  { id: "DGS10", label: "10Y Treasury", group: "Rates", kind: "macro", inverse: true },
  { id: "DGS2", label: "2Y Treasury", group: "Rates", kind: "macro", inverse: true },
  { id: "T10Y2Y", label: "10Y–2Y curve", group: "Rates", kind: "macro" },
  { id: "DFII10", label: "10Y real yield", group: "Rates", kind: "macro", inverse: true },
  { id: "SOFR", label: "SOFR", group: "Rates", kind: "macro", inverse: true },
  { id: "CPI_YOY", label: "CPI YoY", group: "Inflation", kind: "macro", inverse: true },
  { id: "CORE_CPI_YOY", label: "Core CPI YoY", group: "Inflation", kind: "macro", inverse: true },
  { id: "PCE_YOY", label: "PCE YoY", group: "Inflation", kind: "macro", inverse: true },
  { id: "T10YIE", label: "10Y breakeven", group: "Inflation", kind: "macro" },
  { id: "VIXCLS", label: "VIX", group: "Risk", kind: "macro", inverse: true },
  { id: "DTWEXBGS", label: "Broad dollar index", group: "Risk", kind: "macro", inverse: true },
  { id: "DCOILWTICO", label: "WTI crude", group: "Risk", kind: "macro" },
  { id: "USEPUINDXD", label: "Policy uncertainty", group: "Policy & geo", kind: "macro", inverse: true },
  { id: "FUNDAMENTAL:revenue_gaap", label: "Revenue", group: "Fundamentals", kind: "fundamental" },
  { id: "FUNDAMENTAL:gross_profit_gaap", label: "Gross profit", group: "Fundamentals", kind: "fundamental" },
  { id: "FUNDAMENTAL:gross_margin_gaap", label: "Gross margin", group: "Fundamentals", kind: "fundamental" },
  { id: "FUNDAMENTAL:shares_outstanding", label: "Shares outstanding", group: "Fundamentals", kind: "fundamental" },
  { id: "FUNDAMENTAL:public_float_usd", label: "Public float", group: "Fundamentals", kind: "fundamental" },
  { id: "FUNDAMENTAL:deliveries", label: "Deliveries", group: "Fundamentals", kind: "fundamental" },
  { id: "FUNDAMENTAL:fsd_subscribers", label: "FSD subscribers", group: "Fundamentals", kind: "fundamental" },
  { id: "NEWS:SENTIMENT", label: "7-day news sentiment", group: "Sentiment", kind: "sentiment" },
].map((overlay, index) => ({ ...overlay, short: overlay.kind === "market" ? overlay.id : undefined, color: index }));

const GROUP_ORDER = ["Market", "Moving averages", "Rates", "Inflation", "Risk", "Policy & geo", "Fundamentals", "Sentiment"];
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

export function overlayGroups(tickerData, chartData, dashboard, bars = validBars(tickerData?.price_history)) {
  return GROUP_ORDER.map((label) => ({
    id: label,
    label,
    overlays: OVERLAYS.filter(overlay => overlay.group === label
      && valuesForOverlay(overlay.id, { bars, tickerData, chartData, dashboard }).some(isNumericValue))
      .map(overlay => ({ ...overlay, available: true })),
  })).filter((group) => group.overlays.length);
}

export function addOverlay(selected, id, visibleIds) {
  const next = [...selected.filter(value => value !== id), id];
  while (next.length > 5) {
    const hidden = next.findIndex(value => value !== id && !visibleIds.includes(value));
    next.splice(hidden < 0 ? 0 : hidden, 1);
  }
  return next;
}

export function alignedLaneValues(points, bars, key) {
  return alignedValues(points, bars, key);
}

export function laneValues(id, bars, chartData) {
  if (id === "VOL") return bars.map(bar => isNumericValue(bar.volume) ? Number(bar.volume) : null);
  if (id === "SI") {
    const points = chartData?.short_interest || [];
    const percent = alignedLaneValues(points, bars, "short_pct_denominator");
    return percent.some(isNumericValue) ? percent : alignedLaneValues(points, bars, "shares_short");
  }
  if (id === "OPT") return alignedLaneValues(chartData?.options, bars, "put_call_volume_ratio");
  return alignedLaneValues(chartData?.macro_pressure, bars, "value");
}

export function availableLanes(bars, chartData) {
  return CHART_LANES.filter(lane => laneValues(lane.id, bars, chartData).some(isNumericValue));
}

export function visibleLanes(selected, bars, chartData) {
  return availableLanes(bars, chartData).filter(lane => selected.includes(lane.id));
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
  if (overlay.kind === "sentiment") {
    const observed = new Map((tickerData?.news?.sentiment_history || []).map(point => [dateKey(point), pointValue(point)]));
    return bars.map(bar => observed.get(dateKey(bar)) ?? null);
  }
  const metric = overlay.id.slice("FUNDAMENTAL:".length);
  return alignedValues((chartData?.fundamentals || []).filter((row) => row.series_id === metric), bars);
}

export function overlayDefinition(id) {
  return overlayById.get(id) || null;
}

export function fundamentalObservation(id, chartData, through) {
  return (chartData?.fundamentals || [])
    .filter(row => row.series_id === id.slice("FUNDAMENTAL:".length)
      && isNumericValue(row.value) && String(row.date) <= String(through).slice(0, 10))
    .sort((a, b) => String(a.date).localeCompare(String(b.date))).at(-1);
}

export function fundamentalSummary(id, chartData, through) {
  const row = fundamentalObservation(id, chartData, through);
  if (!row) return "not available";
  const value = row.unit === "ratio" ? `${(Number(row.value) * 100).toFixed(1)}%`
    : row.unit === "USD" ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(row.value)
    : Number(row.value).toLocaleString("en-US");
  return `${value}${row.fiscal_quarter ? ` · ${row.fiscal_quarter}` : ""}`;
}

export function isMarketOverlay(id) {
  return overlayById.get(id)?.kind === "market";
}

// Detail cards that follow overlay groups (#94). Unmapped drawers stay up until dismissed.
export const DETAIL_PANELS = Object.freeze([
  { id: "market-comparison", groups: Object.freeze(["Market"]) },
  { id: "moving-averages", groups: Object.freeze(["Moving averages"]) },
  { id: "rates", groups: Object.freeze(["Rates"]) },
  { id: "inflation", groups: Object.freeze(["Inflation"]) },
  { id: "volatility", groups: Object.freeze(["Risk"]) },
  { id: "dollar-oil", groups: Object.freeze(["Risk"]) },
  { id: "tariffs", groups: Object.freeze(["Policy & geo"]) },
  { id: "company", groups: Object.freeze(["Fundamentals"]) },
  { id: "news", groups: Object.freeze(["Sentiment"]) },
]);

const detailById = new Map(DETAIL_PANELS.map((panel) => [panel.id, panel]));

export function activeOverlayGroups(overlayIds) {
  const groups = [];
  for (const id of overlayIds || []) {
    const group = overlayById.get(id)?.group;
    if (group && !groups.includes(group)) groups.push(group);
  }
  return groups;
}

export function normalizePanelMode(value) {
  return value === "all" ? "all" : "follow";
}

export function detailPanelGroups(id) {
  return detailById.get(id)?.groups || null;
}

export function panelsRevealedByOverlay(overlayId) {
  const group = overlayById.get(overlayId)?.group;
  if (!group) return [];
  return DETAIL_PANELS.filter((panel) => panel.groups.includes(group)).map((panel) => panel.id);
}

/** Follow mode shows a mapped card only when one of its groups is active. Dismissed cards stay hidden. */
export function panelVisible(id, { mode = "follow", state = "", groups = [] } = {}) {
  if (state === "dismissed") return false;
  const spec = detailById.get(id);
  if (!spec || normalizePanelMode(mode) === "all") return true;
  return spec.groups.some((group) => groups.includes(group));
}