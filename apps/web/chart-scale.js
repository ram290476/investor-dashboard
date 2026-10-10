// One x-scale for the price chart and every under-chart lane.
// Plot margins live here only; drawers must call plotX instead of copying them.

export const CHART_PLOT = Object.freeze({
  width: 940,
  left: 70,
  right: 890,
});

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

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

function dateParts(bar) {
  const stamp = barStamp(bar);
  const date = stamp.slice(0, 10);
  const month = Number(date.slice(5, 7));
  return {
    stamp,
    date,
    year: date.slice(0, 4),
    month,
    day: Number(date.slice(8, 10)),
    monthName: MONTHS[month - 1] || "",
  };
}

export function formatAxisTick(bar, periodId, { withYear = false } = {}) {
  const { stamp, year, monthName, day } = dateParts(bar);
  if (periodId === "1D") return stamp.includes("T") ? stamp.slice(11, 16) : stamp.slice(0, 10);
  if (periodId === "3Y" || periodId === "5Y") return year;
  if (periodId === "YTD" || periodId === "1Y") return withYear && year ? `${monthName} ${year.slice(2)}` : monthName;
  return `${monthName} ${day}`.trim();
}

function weekKey(date) {
  const parsed = Date.parse(`${date}T00:00:00Z`);
  if (!Number.isFinite(parsed)) return date;
  const value = new Date(parsed);
  value.setUTCDate(value.getUTCDate() - ((value.getUTCDay() + 6) % 7));
  return value.toISOString().slice(0, 10);
}

function bucketKey(bar, periodId) {
  const { stamp, date, year } = dateParts(bar);
  if (periodId === "1D") return stamp.includes("T") ? stamp.slice(0, 13) : date;
  if (periodId === "1W") return date;
  if (periodId === "1M" || periodId === "3M") return weekKey(date);
  if (periodId === "YTD" || periodId === "1Y") return date.slice(0, 7);
  return year;
}

export function chartTicks(bars, periodId, plot = CHART_PLOT) {
  if (!Array.isArray(bars) || !bars.length) return [];
  const span = plot.right - plot.left;
  const budget = periodId === "1W" ? bars.length : 5;
  const minGap = bars.length <= budget ? 0 : span / budget;
  const candidates = [];
  let previousKey = null;
  bars.forEach((bar, index) => {
    const key = bucketKey(bar, periodId);
    if (key === previousKey) return;
    previousKey = key;
    candidates.push({ index, key, x: plotX(index, bars.length, plot), bar });
  });
  const chosen = [];
  candidates.forEach((tick) => {
    const previous = chosen.at(-1);
    if (previous && tick.x - previous.x < minGap - 0.5) return;
    chosen.push(tick);
  });
  const last = candidates.at(-1);
  if (last && !chosen.some((tick) => tick.index === last.index)) {
    const previous = chosen.at(-1);
    if (!previous || last.key !== previous.key && last.x - previous.x >= minGap * 0.55) chosen.push(last);
  }
  const monthCounts = new Map();
  if (periodId === "YTD" || periodId === "1Y") {
    chosen.forEach((tick) => {
      const name = dateParts(tick.bar).monthName;
      monthCounts.set(name, (monthCounts.get(name) || 0) + 1);
    });
  }
  return chosen.map((tick) => ({
    index: tick.index,
    x: tick.x,
    label: formatAxisTick(tick.bar, periodId, {
      withYear: (monthCounts.get(dateParts(tick.bar).monthName) || 0) > 1,
    }),
    anchor: tick.index === 0 ? "start" : tick.index === bars.length - 1 ? "end" : "middle",
  }));
}
