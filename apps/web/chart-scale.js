// One x-scale for the price chart and every under-chart lane.
// Plot margins live here only; drawers must call plotX instead of copying them.

export const CHART_PLOT = Object.freeze({
  width: 940,
  left: 70,
  right: 890,
});

export function plotX(index, count, plot = CHART_PLOT) {
  const steps = Math.max(1, count - 1);
  const clamped = !count || count <= 1 ? 0 : Math.min(Math.max(Number(index) || 0, 0), count - 1);
  return plot.left + (clamped / steps) * (plot.right - plot.left);
}

export function indexAtPlotX(x, count, plot = CHART_PLOT) {
  if (!count || count <= 1) return 0;
  const span = plot.right - plot.left;
  const ratio = (Number(x) - plot.left) / span;
  return Math.min(count - 1, Math.max(0, Math.round(ratio * (count - 1))));
}

export function plotEdgePixels(elementWidth, plot = CHART_PLOT) {
  const width = Number(elementWidth) || 0;
  return {
    start: (plot.left / plot.width) * width,
    end: (plot.right / plot.width) * width,
  };
}

export function barStamp(bar) {
  return String(bar?.ts || bar?.date || "");
}

function quarterOf(bar) {
  const stamp = barStamp(bar);
  const date = stamp.slice(0, 10);
  const year = date.slice(0, 4);
  const month = Number(date.slice(5, 7));
  if (!/^\d{4}$/.test(year) || month < 1 || month > 12) return null;
  const quarter = Math.floor((month - 1) / 3) + 1;
  return { year, quarter, key: `${year}Q${quarter}` };
}

export function formatAxisTick(bar) {
  const quarter = quarterOf(bar);
  return quarter ? `Q${quarter.quarter}` : "";
}

function niceStep(span, count) {
  const rough = Math.abs(span) / Math.max(1, count - 1);
  if (!Number.isFinite(rough) || rough === 0) return 1;
  const power = 10 ** Math.floor(Math.log10(rough));
  const error = rough / power;
  const nice = error >= 7.5 ? 10 : error >= 3.5 ? 5 : error >= 1.5 ? 2 : 1;
  return nice * power;
}

export function niceAmountTicks(min, max, count = 4) {
  let lo = Number(min);
  let hi = Number(max);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [];
  if (lo === hi) {
    const pad = Math.abs(lo) || 1;
    lo -= pad;
    hi += pad;
  }
  if (lo > hi) [lo, hi] = [hi, lo];
  const step = niceStep(hi - lo, count);
  const start = Math.ceil((lo - step * 1e-9) / step) * step;
  const ticks = [];
  for (let value = start; value <= hi + step * 1e-6; value += step) {
    const rounded = Number(value.toPrecision(12));
    if (rounded >= lo - step * 0.05 && rounded <= hi + step * 0.05) ticks.push(rounded);
  }
  return ticks.length >= 2 ? ticks : [lo, hi];
}

export function formatAmount(value, unit = "") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const abs = Math.abs(number);
  if (unit === "%" || unit === "percent") {
    return `${number.toFixed(abs >= 10 ? 0 : 1)}%`;
  }
  if (unit === "ratio") {
    const percent = number * 100;
    const digits = Math.abs(percent) >= 10 && Math.abs(percent - Math.round(percent)) < 0.05 ? 0 : 1;
    return `${percent.toFixed(digits)}%`;
  }
  if (unit === "USD" || unit === "$" || unit === "currency") {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      notation: abs >= 1000 ? "compact" : "standard",
      maximumFractionDigits: abs >= 1000 ? 1 : abs >= 100 ? 0 : 2,
    }).format(number);
  }
  const body = new Intl.NumberFormat("en-US", {
    notation: abs >= 1000 ? "compact" : "standard",
    maximumFractionDigits: abs >= 100 ? 0 : abs >= 10 ? 1 : 2,
  }).format(number);
  return unit ? `${body} ${unit}` : body;
}

export function fiscalAxis(periods, { compact = false } = {}) {
  const parsed = (periods || []).map((period) => {
    const match = /^(\d{4})Q([1-4])$/.exec(String(period || ""));
    return match ? { year: match[1], quarter: Number(match[2]), label: `Q${match[2]}` } : null;
  });
  const step = compact || parsed.length > 12 ? 2 : 1;
  const ticks = parsed.map((item, index) => ({
    year: item?.year || "",
    quarter: item?.quarter || 0,
    label: item?.label || "",
    show: Boolean(item) && (index % step === 0 || index === parsed.length - 1),
  }));
  const years = [];
  ticks.forEach((tick, index) => {
    if (!tick.year) return;
    const last = years.at(-1);
    if (!last || last.year !== tick.year) years.push({ year: tick.year, start: index, end: index });
    else last.end = index;
  });
  return { ticks, years };
}

export function chartTicks(bars, periodId, plot = CHART_PLOT, { compact = false } = {}) {
  if (!Array.isArray(bars) || !bars.length) return [];
  const groups = [];
  bars.forEach((bar, index) => {
    const quarter = quarterOf(bar);
    if (!quarter) return;
    const last = groups.at(-1);
    if (!last || last.key !== quarter.key) {
      groups.push({ ...quarter, start: index, end: index });
    } else last.end = index;
  });
  if (!groups.length) return [];
  const step = compact ? 2 : 1;
  const shown = groups.filter((group, index) => index % step === 0 || index === groups.length - 1);
  return shown.map((group) => {
    const full = groups.find((item) => item.key === group.key);
    const index = Math.round((full.start + full.end) / 2);
    const previous = groups[groups.indexOf(full) - 1];
    return {
      index,
      x: plotX(index, bars.length, plot),
      label: `Q${group.quarter}`,
      year: group.year,
      anchor: "middle",
      boundary: Boolean(previous && previous.year !== group.year),
      boundaryX: plotX(full.start, bars.length, plot),
      spanStart: plotX(full.start, bars.length, plot),
      spanEnd: plotX(full.end, bars.length, plot),
    };
  });
}

export function chartYearLabels(ticks) {
  const groups = [];
  (ticks || []).forEach((tick) => {
    if (!tick?.year) return;
    const last = groups.at(-1);
    if (!last || last.year !== tick.year) groups.push({ year: tick.year, ticks: [tick] });
    else last.ticks.push(tick);
  });
  return groups.map((group) => {
    const start = Math.min(...group.ticks.map((tick) => tick.spanStart ?? tick.x));
    const end = Math.max(...group.ticks.map((tick) => tick.spanEnd ?? tick.x));
    return { year: group.year, x: (start + end) / 2, start, end };
  });
}
