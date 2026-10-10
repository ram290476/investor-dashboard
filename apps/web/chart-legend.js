import { isNumericValue } from "./chart-period.js";

// One dash definition for the plot and the legend. Averages share the price
// axis and stay solid. Every other overlay is dashed, including market series
// and indicators drawn on their own scale.
export function seriesLineStyle(kind) {
  if (kind === "price") return { dash: "none", width: 2.5 };
  if (kind === "average") return { dash: "none", width: 1.5 };
  return { dash: "5 4", width: 1.5 };
}

function money(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
}

function lastNumeric(values) {
  for (let index = (values || []).length - 1; index >= 0; index -= 1) {
    if (isNumericValue(values[index])) return { index, value: Number(values[index]) };
  }
  return null;
}

function finite(values) {
  return (values || []).filter(isNumericValue).map(Number);
}

function rhoText(value) {
  if (!isNumericValue(value)) return "";
  const number = Number(value);
  return `ρ ${number > 0 ? "+" : ""}${number.toFixed(2)}`;
}

function rangeText(values) {
  const numbers = finite(values);
  if (!numbers.length) return "";
  const min = Math.min(...numbers);
  const max = Math.max(...numbers);
  const shown = (value) => value.toFixed(2);
  return min === max ? shown(min) : `${shown(min)}–${shown(max)}`;
}

function averageText(definition, values, prices, formatMoney) {
  const last = lastNumeric(values);
  if (!last) return `${definition.label} average · not available`;
  const price = isNumericValue(prices?.[last.index]) ? Number(prices[last.index]) : lastNumeric(prices)?.value;
  let relation = "";
  if (isNumericValue(price) && last.value !== 0) {
    const distance = Number(price) / last.value - 1;
    relation = distance === 0
      ? " · price unchanged"
      : ` · price ${distance > 0 ? "above" : "below"} by ${Math.abs(distance * 100).toFixed(1)}%`;
  }
  return `${definition.label} average ${formatMoney(last.value)}${relation}`;
}

function marketText(definition, values) {
  const first = (values || []).find(isNumericValue);
  const last = lastNumeric(values);
  if (!last || !isNumericValue(first) || Number(first) === 0) return `${definition.label} · not available`;
  const change = (last.value / Number(first) - 1) * 100;
  return `${definition.label} · ${change > 0 ? "+" : ""}${change.toFixed(1)}% from period start`;
}

function macroText(definition, values, correlation) {
  const range = rangeText(values);
  if (!range) return `${definition.label} · not available`;
  const rho = rhoText(correlation);
  const scale = definition.inverse ? "inverted, own scale" : "own scale";
  return `${definition.label} · ${rho ? `${rho} · ` : ""}${range} (${scale})`;
}

function sentimentText(definition, values, observedOn) {
  const last = lastNumeric(values);
  if (!last) return `${definition.label} · not available`;
  const when = observedOn ? ` · observed ${observedOn}` : "";
  return `${definition.label} · ${last.value.toFixed(2)}${when} · own scale`;
}

export function overlayCorrelation(id, tickerData, chartData) {
  const rows = tickerData?.trend?.rows;
  const row = Array.isArray(rows) ? rows.find((item) => item.series_id === id) : null;
  if (isNumericValue(row?.corr_90d)) return Number(row.corr_90d);
  const history = chartData?.correlation_history?.[id];
  if (!Array.isArray(history)) return null;
  for (let index = history.length - 1; index >= 0; index -= 1) {
    if (isNumericValue(history[index]?.corr_90d)) return Number(history[index].corr_90d);
  }
  return null;
}

function itemText(overlay, prices, formatMoney) {
  const { definition, values } = overlay;
  if (definition.kind === "average") return averageText(definition, values, prices, formatMoney);
  if (definition.kind === "market") return marketText(definition, values);
  if (definition.kind === "macro") return macroText(definition, values, overlay.correlation);
  if (definition.kind === "fundamental") return `${definition.label} · ${overlay.summary || "not available"}`;
  if (definition.kind === "sentiment") return sentimentText(definition, values, overlay.observedOn);
  const last = lastNumeric(values);
  return last ? `${definition.label} · ${last.value.toFixed(2)}` : `${definition.label} · not available`;
}

export function legendItems({ ticker, prices, adjustedMin, adjustedMax, overlays }, formatters = {}) {
  const formatMoney = formatters.money || money;
  const price = seriesLineStyle("price");
  const items = [{
    id: "price",
    kind: "price",
    text: `${ticker} price`,
    title: isNumericValue(adjustedMin) && isNumericValue(adjustedMax)
      ? `Adjusted ${formatMoney(adjustedMin)} – ${formatMoney(adjustedMax)}`
      : "",
    dash: price.dash,
    width: price.width,
  }];
  for (const overlay of overlays || []) {
    const definition = overlay?.definition;
    if (!definition) continue;
    const style = seriesLineStyle(definition.kind);
    items.push({
      id: definition.id,
      kind: definition.kind,
      text: itemText(overlay, prices, formatMoney),
      title: "",
      dash: style.dash,
      width: style.width,
    });
  }
  return items;
}
