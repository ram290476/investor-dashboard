import { chartValue, validBars } from "./chart-period.js";

// Last 22 stored closes match the 1M chip (21 sessions back). Ram has not
// decided whether the strip should follow the selected chart period instead.
export const SPARK_WINDOW = 22;

const cache = new Map();

export function sparklineModel(history, cacheKey = "") {
  if (cacheKey && cache.has(cacheKey)) return cache.get(cacheKey);
  const values = validBars(history).slice(-SPARK_WINDOW).map(chartValue);
  const model = values.length < 2
    ? { points: "", direction: "", change: null, sessions: values.length }
    : lineModel(values);
  if (cacheKey) {
    cache.set(cacheKey, model);
    if (cache.size > 80) cache.delete(cache.keys().next().value);
  }
  return model;
}

function lineModel(values) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * 64;
    const y = 18 - ((value - min) / span) * 16;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const first = values[0];
  const last = values.at(-1);
  const direction = last > first ? "positive" : last < first ? "negative" : "flat";
  const change = first === 0 ? null : last / first - 1;
  return { points, direction, change, sessions: values.length };
}

export function sparklineSummary(model) {
  if (!model?.points || model.change == null) return "";
  const window = model.sessions >= SPARK_WINDOW ? "1M" : `${model.sessions} sessions`;
  if (model.change === 0) return `unchanged over ${window}`;
  const percent = Math.abs(model.change) * 100;
  const text = percent >= 10 ? percent.toFixed(0) : percent >= 1 ? percent.toFixed(1) : percent.toFixed(2);
  return `${model.change > 0 ? "up" : "down"} ${text}% over ${window}`;
}

export function sparkline(history, options = {}) {
  const model = sparklineModel(history, options.cacheKey || "");
  const doc = options.document || document;
  if (!model.points) {
    const empty = doc.createElement("span");
    empty.className = "spark-empty";
    empty.textContent = "—";
    return empty;
  }
  const svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 64 20");
  svg.setAttribute("class", `spark ${model.direction}`);
  svg.setAttribute("aria-hidden", "true");
  const line = doc.createElementNS("http://www.w3.org/2000/svg", "polyline");
  line.setAttribute("points", model.points);
  svg.append(line);
  return svg;
}
