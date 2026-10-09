import { chartValue, isNumericValue, validBars } from "./chart-period.js";
import { driverRows, number } from "./signals.js";

export const CATALYST_CATEGORIES = [
  { id: "rates", label: "Rates", slot: 0 },
  { id: "inflation", label: "Inflation", slot: 1 },
  { id: "policy", label: "Policy & geopolitics", slot: 2 },
  { id: "robotaxi", label: "Robotaxi", slot: 3 },
  { id: "filings", label: "Filings", slot: 4 },
  { id: "space", label: "Space operations", slot: 5 },
  { id: "other", label: "Other events", slot: 6 },
];

export function catalystCategory(type) {
  const key = String(type || "").toLowerCase();
  if (/cpi|pce|inflation/.test(key)) return "inflation";
  if (/fomc|(?:^|[\s_-])rates?(?:$|[\s_-])|treasury/.test(key)) return "rates";
  if (/robotaxi|autonomous|av_permit|fsd/.test(key)) return "robotaxi";
  if (/filing|earnings|^form_/.test(key)) return "filings";
  if (/launch|space|starlink|starship/.test(key)) return "space";
  if (/tariff|sanction|policy|geopolitic|trade/.test(key)) return "policy";
  return "other";
}

export function catalystRows(dashboard, ticker) {
  const data = dashboard?.tickers?.[ticker];
  const records = [
    ...(dashboard?.events || []).filter(row => !row.tickers?.length || row.tickers.includes(ticker))
      .map(row => ({ date: row.event_ts, type: row.type, title: row.title, url: row.source_url, source: "Curated event" })),
    ...(data?.filings || []).map(row => ({
      date: row.filed_at, type: "filing", title: row.title || row.form, url: row.url, source: "SEC filing",
    })),
    ...(dashboard?.releases?.next || []).map(row => ({
      date: row.release_ts, type: row.series, title: `${row.series} release`, source: "Published release calendar",
    })),
    ...(data?.release_links?.latest || []).map(row => ({
      date: row.release_date, type: row.series_id, title: `${row.series_id} release`, source: "Stored macro release",
    })),
  ];
  const unique = new Map();
  records.forEach(row => {
    if (!row.date || !Number.isFinite(Date.parse(row.date))) return;
    const key = `${row.type}|${row.date}|${row.title}|${row.url || ""}`;
    if (!unique.has(key)) unique.set(key, { ...row, id: encodeURIComponent(key), category: catalystCategory(row.type) });
  });
  return [...unique.values()].sort((a, b) => Date.parse(a.date) - Date.parse(b.date) || a.id.localeCompare(b.id));
}

export function markerIndex(event, bars) {
  const day = String(event.date).slice(0, 10);
  const days = bars.map(bar => String(bar.ts || bar.date || "").slice(0, 10));
  if (!days.length || day < days[0] || day > days.at(-1)) return -1;
  return days.findIndex(date => date >= day);
}

export function sortedDrivers(tickerData, sort = "effect") {
  const rows = driverRows(tickerData);
  if (sort === "name") return rows.sort((a, b) => a.series_id.localeCompare(b.series_id));
  const key = sort === "correlation" ? "corr_90d" : sort === "change" ? "change_1m_display" : "effect";
  return rows.sort((a, b) => {
    const left = number(a[key]), right = number(b[key]);
    if (left == null && right != null) return 1;
    if (right == null && left != null) return -1;
    return Math.abs(right ?? 0) - Math.abs(left ?? 0) || a.series_id.localeCompare(b.series_id);
  });
}

export function movingAverageRows(history) {
  const bars = validBars(history);
  const latest = bars.length ? chartValue(bars.at(-1)) : null;
  return [10, 20, 50, 100, 200].map(window => {
    const value = bars.length < window ? null : bars.slice(-window).reduce((sum, bar) => sum + chartValue(bar), 0) / window;
    return { window, value, distance: value && isNumericValue(latest) ? latest / value - 1 : null };
  });
}

export function sensitivityRows(tickerData) {
  return (tickerData?.release_links?.summaries || [])
    .filter(row => number(row.correlation_surprise) != null && number(row.n_releases) >= 12)
    .slice().sort((a, b) => Math.abs(Number(b.correlation_surprise)) - Math.abs(Number(a.correlation_surprise)));
}
