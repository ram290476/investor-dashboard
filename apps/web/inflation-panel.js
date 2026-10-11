// Inflation surprise columns. Missing prints stay empty; a YoY change is not a nowcast.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function finite(value) {
  if (typeof value === "boolean" || value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function monthLabel(period, fallback) {
  const match = /^(\d{4})-(\d{2})$/.exec(String(period || ""));
  if (!match) return fallback || "";
  return MONTHS[Number(match[2]) - 1] || fallback || "";
}

export function inflationSurpriseView(releases, dashboardState = "ready") {
  const block = releases?.inflation;
  const prints = Array.isArray(block?.prints) ? block.prints : [];
  if (!prints.length && dashboardState === "loading") {
    return { kind: "loading", message: "Loading CPI prints…", columns: [] };
  }
  if (!prints.length && dashboardState === "error") {
    return { kind: "error", message: "CPI prints could not be loaded. Refresh data to retry.", columns: [] };
  }
  if (!prints.length) {
    return {
      kind: "unavailable",
      message: "CPI YoY prints are not in this snapshot yet.",
      columns: [],
    };
  }
  const levels = prints.flatMap((row) => {
    const yoy = finite(row.yoy);
    return yoy == null ? [] : [yoy];
  });
  const lo = levels.length ? Math.min(...levels) : 0;
  const hi = levels.length ? Math.max(...levels) : 1;
  const span = hi === lo ? 1 : hi - lo;
  const heightOf = (value) => `${Math.round(18 + ((value - lo) / span) * 72)}%`;
  const columns = prints.slice(-6).map((row) => {
    const yoy = finite(row.yoy);
    const surprise = finite(row.surprise_pp);
    const pending = yoy == null;
    const surpriseText = pending || surprise == null
      ? "—"
      : `${surprise > 0 ? "hot +" : surprise < 0 ? "cool " : ""}${Math.round(surprise * 100)} bp`;
    return {
      month: row.month || monthLabel(row.period),
      valueText: pending ? "pending" : `${yoy.toFixed(1)}%`,
      height: pending ? "0%" : heightOf(yoy),
      pending,
      surpriseText,
      surpriseClass: surprise == null || Math.round(surprise * 100) === 0 ? "bp-flat" : surprise > 0 ? "bp-up" : "bp-down",
    };
  });
  const hot = prints.slice(-5).filter((row) => {
    const surprise = finite(row.surprise_pp);
    return surprise != null && surprise > 0;
  }).length;
  const headline = block.headline || "unavailable";
  const core = block.core || "unavailable";
  return {
    kind: "ready",
    state: `Core ${core} · headline ${headline}`,
    streak: `${hot} of last 5 YoY prints rose`,
    columns,
    nowcast: "Cleveland Fed nowcast is not in this snapshot.",
  };
}
