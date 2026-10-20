// View model for the per-stock page. Approved metrics stay primary.
// Proposed metrics stay in the data so the company metrics list can label them
// Pending review. Chart cards do not repeat that status. Text is plain.
import { periodQuote } from "./chart-period.js";
import { catalystDateLabel } from "./roadmap.js";

export const LONG_PRESS_MS = 550;

export const STOCK_TABS = [
  { id: "overview", label: "Overview" },
  { id: "trends", label: "Trends" },
  { id: "fundamentals", label: "Fundamentals" },
  { id: "kpis", label: "KPIs" },
];

// Last tab, and only for a signed-in admin. The demo account is never an admin.
export const ADMIN_STOCK_TAB = { id: "admin", label: "Admin" };

const STOCK_TAB_ORDER = ["overview", "trends", "deep-dive", "fundamentals", "kpis"];

// Last resort only: companyName() uses the API company_name / SEC entity name first.
// SPCX has no SEC entity name, so it keeps a display name here.
export const COMPANY_NAMES = {
  TSLA: "Tesla, Inc.",
  SPCX: "SpaceX",
};

const EDGAR_METRICS = [
  { id: "revenue_gaap", name: "Revenue", unit: "USD", source: "SEC XBRL" },
  { id: "gross_profit_gaap", name: "Gross profit", unit: "USD", source: "SEC XBRL" },
  { id: "gross_margin_gaap", name: "Gross margin", unit: "ratio", source: "SEC XBRL" },
  { id: "shares_outstanding", name: "Shares outstanding", unit: "shares", source: "SEC DEI" },
  { id: "public_float_usd", name: "Public float", unit: "USD", source: "SEC DEI" },
  { id: "deliveries", name: "Deliveries", unit: "vehicles", source: "Reviewed manual" },
  { id: "fsd_subscribers", name: "FSD subscribers", unit: "subscribers", source: "Reviewed manual" },
];

const QUARTER = /^(\d{4})Q([1-4])$/;

// Periods shorter than one quarter. 3M is the first chip that can hold a quarter end.
const SHORT_PERIODS = new Set(["1D", "1W", "5D", "1M"]);
const PERIOD_DAYS = { "3M": 92, "1Y": 365, "3Y": 365 * 3, "5Y": 365 * 5 };

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function addDays(iso, days) {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return isoDate(date);
}

function lastWeekday(year, month, weekday) {
  const last = new Date(Date.UTC(year, month, 0));
  last.setUTCDate(last.getUTCDate() - ((last.getUTCDay() - weekday + 7) % 7));
  return isoDate(last);
}

function saturdayNearest(year, month, day) {
  const target = new Date(Date.UTC(year, month - 1, day));
  const dow = target.getUTCDay();
  const forward = (6 - dow + 7) % 7;
  const back = (dow - 6 + 7) % 7;
  target.setUTCDate(target.getUTCDate() + (forward <= back ? forward : -back));
  return isoDate(target);
}

// Nvidia's fiscal year ends on the last Sunday in January. Quarter ends are the
// last Sunday of April, July, October, and January — not the calendar quarter.
function nvidiaQuarterEnd(year, quarter) {
  const month = { 1: 4, 2: 7, 3: 10, 4: 1 }[quarter];
  return lastWeekday(quarter === 4 ? year : year - 1, month, 0);
}

// Marvell's fiscal year ends on the Saturday closest to January 31. Q1–Q3 are
// 13 weeks; a 53-week year keeps the extra week in Q4.
function marvellQuarterEnd(year, quarter) {
  if (quarter === 4) return saturdayNearest(year, 1, 31);
  return addDays(saturdayNearest(year - 1, 1, 31), 13 * quarter * 7);
}

function calendarQuarterEnd(year, quarter) {
  return isoDate(new Date(Date.UTC(year, quarter * 3, 0)));
}

/** Period-end date for a fiscal label. NVDA and MRVL use their fiscal calendars. */
export function fiscalQuarterEnd(fiscalPeriod, ticker = "") {
  const match = QUARTER.exec(String(fiscalPeriod || ""));
  if (!match) return "";
  const year = Number(match[1]);
  const quarter = Number(match[2]);
  const symbol = String(ticker || "").toUpperCase();
  if (symbol === "NVDA") return nvidiaQuarterEnd(year, quarter);
  if (symbol === "MRVL") return marvellQuarterEnd(year, quarter);
  return calendarQuarterEnd(year, quarter);
}

/**
 * Date window for a chart chip. A short chip asks for the latest quarter only.
 * When the price window exists, its first and last session dates are the bounds.
 * Otherwise the window is a calendar span ending on asOf.
 */
export function metricPeriodRange(periodId, history, asOf = "") {
  const id = String(periodId || "");
  if (!id) return null;
  if (SHORT_PERIODS.has(id)) return { latestOnly: true };
  let start = "";
  let end = String(asOf || "").slice(0, 10);
  if (Array.isArray(history) && history.length) {
    const quote = periodQuote(history, id);
    if (quote.available && quote.window.length) {
      const first = quote.window[0];
      const last = quote.window.at(-1);
      start = String(first.date || first.ts || "").slice(0, 10);
      end = String(last.date || last.ts || "").slice(0, 10);
    }
  }
  if (!end) return null;
  if (!start) {
    if (id === "YTD") start = `${end.slice(0, 4)}-01-01`;
    else if (PERIOD_DAYS[id]) start = addDays(end, -PERIOD_DAYS[id]);
  }
  if (!start) return null;
  return { latestOnly: false, start, end };
}

function withPeriodEnd(point, ticker) {
  const periodEnd = String(point?.period_end || fiscalQuarterEnd(point?.fiscal_period, ticker) || "").slice(0, 10);
  return periodEnd ? { ...point, period_end: periodEnd } : null;
}

/** Quarters whose period-end falls in the chip. Short chips keep the latest quarter only. */
export function quartersInPeriod(series, periodId, options = {}) {
  const ticker = options.ticker || "";
  const points = (series || []).map((point) => withPeriodEnd(point, ticker)).filter(Boolean);
  const range = metricPeriodRange(periodId, options.history, options.asOf || "");
  if (!range) return points.sort((a, b) => a.period_end.localeCompare(b.period_end));
  if (range.latestOnly) {
    const reported = points.filter((point) => point.reported !== false && point.value != null && point.value !== "");
    const pool = reported.length ? reported : points;
    const latestEnd = pool.reduce((max, point) => (point.period_end > max ? point.period_end : max), "");
    return points.filter((point) => point.period_end === latestEnd);
  }
  return points
    .filter((point) => point.period_end >= range.start && point.period_end <= range.end)
    .sort((a, b) => a.period_end.localeCompare(b.period_end));
}

const COMPANY_NAME_FIELDS = ["company_name", "sec_company_name", "entity_name", "entityName", "name"];

function recordCompanyName(record, ticker) {
  if (!record || typeof record !== "object") return "";
  const symbol = String(ticker || "").trim().toUpperCase();
  for (const field of COMPANY_NAME_FIELDS) {
    const value = String(record[field] ?? "").replace(/\s+/g, " ").trim();
    if (value && value.toUpperCase() !== symbol) return value;
  }
  return "";
}

/** Full company name. Stock payload, then dashboard or SEC metadata, then the ticker. */
export function companyName(ticker, payload, tickerData) {
  return recordCompanyName(payload, ticker)
    || recordCompanyName(tickerData, ticker)
    || COMPANY_NAMES[ticker]
    || ticker;
}

const COMPANY_DESCRIPTION_FIELDS = ["company_description", "description"];

/** Short business description for the header. Stock payload, then dashboard data; "" when absent. */
export function companyDescription(payload, tickerData) {
  for (const record of [payload, tickerData]) {
    if (!record || typeof record !== "object") continue;
    for (const field of COMPANY_DESCRIPTION_FIELDS) {
      const value = String(record[field] ?? "").replace(/\s+/g, " ").trim();
      if (value) return value;
    }
  }
  return "";
}

/**
 * Company tabs, in order. Deep dive sits between Trends and Fundamentals, and only when sections exist.
 * Admin is last, and only when options.admin is true (a signed-in admin, never the demo account).
 */
export function stockTabs(payload, options = {}) {
  const sections = payload?.deep_dive?.sections;
  const deep = Array.isArray(sections) && sections.length
    ? { id: "deep-dive", label: String(payload.deep_dive.title || "Deep dive").trim() || "Deep dive" }
    : null;
  const byId = new Map(STOCK_TABS.map((tab) => [tab.id, tab]));
  const tabs = STOCK_TAB_ORDER
    .map((id) => (id === "deep-dive" ? deep : byId.get(id)))
    .filter(Boolean);
  if (options.admin === true) tabs.push({ ...ADMIN_STOCK_TAB });
  return tabs;
}

/**
 * Tab a company hash may open.
 * Admin is refused unless that tab is in the visible list, so a non-admin URL cannot open it.
 * A bare company hash is Overview. An unknown tab keeps the current one when it is still visible.
 */
export function visibleStockTab(hashTab, tabs, current = "overview") {
  const ids = (Array.isArray(tabs) ? tabs : [])
    .map((tab) => (typeof tab === "string" ? tab : tab?.id))
    .filter(Boolean);
  const requested = String(hashTab || "");
  if (requested === "admin" && !ids.includes("admin")) return "overview";
  if (requested && ids.includes(requested)) return requested;
  if (!requested) return "overview";
  if (current && ids.includes(current)) return current;
  return "overview";
}

export function nextStockTab(current, key, tabs = STOCK_TABS) {
  const ids = (Array.isArray(tabs) ? tabs : STOCK_TABS).map((tab) => tab.id);
  const index = Math.max(0, ids.indexOf(current));
  const next = key === "ArrowRight" ? (index + 1) % ids.length
    : key === "ArrowLeft" ? (index + ids.length - 1) % ids.length
    : key === "Home" ? 0
    : key === "End" ? ids.length - 1
    : null;
  return next == null ? null : ids[next];
}

export function safeHttpUrl(url) {
  return /^https?:\/\//i.test(String(url || "")) ? String(url) : "";
}

export function isRejectedMetric(metric) {
  const state = metric?.approval_state || metric?.status;
  return state === "rejected";
}

export function isPendingMetric(metric) {
  if (!metric || isRejectedMetric(metric)) return false;
  const state = metric.approval_state || metric.status;
  return state === "proposed";
}

export function approvedMetrics(payload) {
  return (payload?.metrics || []).filter((metric) => (
    metric?.approved === true
    && metric.approval_state !== "proposed"
    && metric.approval_state !== "rejected"
    && metric.status !== "proposed"
    && metric.status !== "rejected"
  ));
}

export function pendingMetrics(payload) {
  return (payload?.metrics || []).filter(isPendingMetric);
}

/** Approved values first. "approved" hides proposed rows instead of mixing them in. */
export function metricsForReview(payload, review = "all") {
  const approved = approvedMetrics(payload);
  if (review === "approved") return approved;
  return [...approved, ...pendingMetrics(payload)];
}

const PANEL_TYPES = new Set(["chart", "kpi", "table", "text"]);

/** Panel kind comes from the metric registry and the shape of the collected values. */
export function panelType(metric) {
  const explicit = metric?.panel_type || metric?.panel;
  if (PANEL_TYPES.has(explicit)) return explicit;
  if (Array.isArray(metric?.rows) && metric.rows.length) return "table";
  if (Array.isArray(metric?.table) && metric.table.length) return "table";
  const reported = (metric?.series || []).filter((point) => (
    point && point.reported !== false && point.value != null && point.value !== ""
  ));
  const narrative = metric?.disclosure_status === "narrative"
    || metric?.unit === "statement"
    || metric?.text
    || metric?.narrative;
  if (narrative && reported.length < 2) return "text";
  if (reported.length >= 2) return "chart";
  if (reported.length === 1 || (metric?.latest && metric.latest.reported !== false && metric.latest.value != null)) {
    return "kpi";
  }
  return "text";
}

function quarterIndex(fiscalPeriod) {
  const match = QUARTER.exec(String(fiscalPeriod || ""));
  if (!match) return null;
  return [Number(match[1]), Number(match[2])];
}

function shiftQuarter(fiscalPeriod, quarters) {
  const index = quarterIndex(fiscalPeriod);
  if (!index) return "";
  const absolute = index[0] * 4 + (index[1] - 1) - quarters;
  return `${Math.floor(absolute / 4)}Q${absolute % 4 + 1}`;
}

export function fillQuarterGaps(series, ticker = "") {
  const points = (series || []).filter((point) => quarterIndex(point.fiscal_period));
  if (!points.length) return [];
  const ordered = [...points].sort((a, b) => {
    const left = quarterIndex(a.fiscal_period);
    const right = quarterIndex(b.fiscal_period);
    return left[0] - right[0] || left[1] - right[1];
  });
  const byPeriod = new Map(ordered.map((point) => [point.fiscal_period, point]));
  const start = quarterIndex(ordered[0].fiscal_period);
  const end = quarterIndex(ordered.at(-1).fiscal_period);
  const filled = [];
  let year = start[0];
  let quarter = start[1];
  while (year < end[0] || (year === end[0] && quarter <= end[1])) {
    const fiscalPeriod = `${year}Q${quarter}`;
    const current = byPeriod.get(fiscalPeriod);
    const reported = Boolean(current && current.reported !== false && current.value != null && current.value !== "");
    filled.push({
      fiscal_period: fiscalPeriod,
      period_end: (current && current.period_end) || fiscalQuarterEnd(fiscalPeriod, ticker),
      value: reported ? Number(current.value) : null,
      reported,
      revised: Boolean(current?.revised),
    });
    quarter += 1;
    if (quarter === 5) {
      year += 1;
      quarter = 1;
    }
  }
  return filled;
}

export function formatMetricValue(value, unit) {
  if (value == null || Number.isNaN(Number(value))) return "not reported";
  const number = Number(value);
  if (unit === "USD") {
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1,
    }).format(number);
  }
  if (unit === "ratio") return `${(number * 100).toFixed(1)}%`;
  if (unit === "percent") return `${number.toFixed(1)}%`;
  return number.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

export function formatChange(value) {
  if (value == null || Number.isNaN(Number(value))) return null;
  const number = Number(value);
  const sign = number > 0 ? "+" : "";
  return `${sign}${(number * 100).toFixed(1)}%`;
}

export function freshnessBadge(payload, state, ticker = "", review = "all") {
  if (state === "loading") return { tone: "unavailable", text: "Loading company metrics" };
  if (state === "forbidden") {
    const symbol = ticker || "this ticker";
    return { tone: "unavailable", text: `Add ${symbol} to your watchlist to see company metrics` };
  }
  if (state === "missing") return { tone: "unavailable", text: "No company-specific metrics discovered" };
  if (state === "error") return { tone: "unavailable", text: "Company metrics could not be loaded. Retry" };
  if (!payload) return { tone: "unavailable", text: "No company-specific metrics discovered" };
  if (payload.stale || payload.run_status === "partial" || payload.run_status === "failed") {
    return { tone: "partial", text: payload.freshness_label || "Partial run · previous approved values kept" };
  }
  const approved = approvedMetrics(payload);
  const pending = pendingMetrics(payload);
  if (!approved.length && pending.length && review !== "approved") {
    return { tone: "pending", text: payload.freshness_label || "Pending review" };
  }
  if (!approved.length) {
    return { tone: "unavailable", text: "No approved company metrics" };
  }
  return { tone: "ok", text: payload.freshness_label || "IR data approved" };
}

export function monthDayLabel(value, now = new Date(), timeZone = "UTC") {
  const label = catalystDateLabel(value, { now, timeZone });
  return label && label !== "—" ? label : "";
}

export function stockFreshnessText(payload, state, ticker = "", now = new Date(), timeZone = "UTC", review = "all") {
  const badge = freshnessBadge(payload, state, ticker, review);
  if (state !== "ready" || !payload) return badge.text;
  const extra = [payload.run_status, monthDayLabel(payload.generated_at, now, timeZone)].filter(Boolean);
  return extra.length ? `${badge.text} · ${extra.join(" · ")}` : badge.text;
}

function ratioChange(latest, prior) {
  if (!latest?.reported || !prior?.reported || prior.value == null || Number(prior.value) === 0) return null;
  return (Number(latest.value) - Number(prior.value)) / Math.abs(Number(prior.value));
}

function applyPeriod(card, options, ticker) {
  if (!options.period || card.panelType === "text" || card.panelType === "table" || card.unavailable) return card;
  if (!card.series?.length) return card;
  const kept = quartersInPeriod(card.series, options.period, {
    ticker, history: options.history, asOf: options.asOf || "",
  });
  const reported = kept.filter((point) => point.reported);
  if (!reported.length) {
    return {
      ...card,
      series: [],
      emptyPeriod: true,
      latest: "not reported",
      latestPeriod: "",
      periodEnd: "",
      qoq: null,
      yoy: null,
      qoqValue: null,
      yoyValue: null,
    };
  }
  const latest = reported.at(-1);
  const same = latest.fiscal_period === card.latestPeriod;
  const priorQ = card.series.find((point) => point.fiscal_period === shiftQuarter(latest.fiscal_period, 1));
  const priorY = card.series.find((point) => point.fiscal_period === shiftQuarter(latest.fiscal_period, 4));
  const qoqValue = same ? card.qoqValue : ratioChange(latest, priorQ);
  const yoyValue = same ? card.yoyValue : ratioChange(latest, priorY);
  return {
    ...card,
    series: kept,
    emptyPeriod: false,
    latest: formatMetricValue(latest.value, card.unit),
    latestPeriod: latest.fiscal_period,
    periodEnd: latest.period_end || "",
    qoq: same ? card.qoq : formatChange(qoqValue),
    yoy: same ? card.yoy : formatChange(yoyValue),
    qoqValue,
    yoyValue,
  };
}

function presentMetric(metric, ticker = "") {
  const series = fillQuarterGaps(metric.series || [], ticker);
  const revisedFrom = metric.revised_from || "";
  if (revisedFrom) {
    series.forEach((point) => {
      if (point.fiscal_period >= revisedFrom) point.revised = true;
    });
  }
  const reported = series.filter((point) => point.reported);
  const latest = metric.latest?.reported === false ? null : metric.latest;
  const fiscal = latest?.fiscal_period || reported.at(-1)?.fiscal_period || "";
  const matched = series.find((point) => point.fiscal_period === fiscal);
  const pending = isPendingMetric(metric);
  const capex = metric.metric_id === "capex";
  return {
    id: metric.metric_id,
    name: capex ? "CapEx" : (metric.display_name || metric.metric_id),
    detailName: capex ? (metric.display_name || "Capital expenditures") : "",
    unit: metric.unit || "",
    category: metric.category || "operating",
    panelType: panelType(metric),
    series,
    latest: latest?.value == null ? "not reported" : formatMetricValue(latest.value, metric.unit),
    latestPeriod: fiscal,
    periodEnd: latest?.period_end || matched?.period_end || fiscalQuarterEnd(fiscal, ticker),
    qoq: formatChange(metric.qoq),
    yoy: formatChange(metric.yoy),
    qoqValue: metric.qoq,
    yoyValue: metric.yoy,
    sourceUrl: safeHttpUrl(metric.provenance?.source_url),
    sourceTitle: metric.provenance?.source_title || metric.provenance?.source_kind || "Source",
    published: metric.provenance?.published_date || "",
    confidence: metric.provenance?.confidence == null ? "" : `confidence ${Number(metric.provenance.confidence).toFixed(2)}`,
    approval: pending ? "proposed" : (metric.provenance?.approval_state || metric.approval_state || "approved"),
    pending,
    capex,
    rows: Array.isArray(metric.rows) ? metric.rows : (Array.isArray(metric.table) ? metric.table : []),
    text: metric.text || metric.narrative || "",
    definition: metric.definition || "",
    revisedFrom,
    revisionNote: metric.revision_note || "",
    xbrl: metric.xbrl?.status === "mismatch" ? "XBRL mismatch" : metric.xbrl?.status === "match" ? "XBRL reconciled" : "",
    unavailable: false,
  };
}

function edgarCards(chartData, ticker = "") {
  const rows = chartData?.fundamentals || [];
  return EDGAR_METRICS.map((metric) => {
    const series = rows
      .filter((row) => row.series_id === metric.id && row.fiscal_quarter && row.value != null)
      .map((row) => ({
        fiscal_period: row.fiscal_quarter,
        period_end: row.period_end || fiscalQuarterEnd(row.fiscal_quarter, ticker),
        value: row.value,
        reported: true,
        source_id: row.source_id,
        date: row.date,
      }))
      .sort((a, b) => String(a.fiscal_period).localeCompare(String(b.fiscal_period)));
    if (!series.length) return null;
    const filled = fillQuarterGaps(series, ticker);
    const latest = [...series].sort((a, b) => String(a.date || a.fiscal_period).localeCompare(String(b.date || b.fiscal_period))).at(-1);
    const prior = filled.find((point) => point.fiscal_period === shiftQuarter(latest.fiscal_period, 1));
    const qoq = prior?.reported && prior.value ? (latest.value - prior.value) / Math.abs(prior.value) : null;
    return {
      id: `edgar:${metric.id}`,
      name: metric.name,
      unit: metric.unit,
      category: "fundamentals",
      series: filled,
      latest: formatMetricValue(latest.value, metric.unit),
      latestPeriod: latest.fiscal_period,
      periodEnd: latest.period_end || fiscalQuarterEnd(latest.fiscal_period, ticker),
      qoq: formatChange(qoq),
      yoy: null,
      qoqValue: qoq,
      yoyValue: null,
      sourceUrl: "",
      sourceTitle: latest.source_id || metric.source,
      published: latest.date || "",
      confidence: "reviewed",
      approval: "edgar",
      pending: false,
      capex: false,
      panelType: "chart",
      rows: [],
      text: "",
      definition: "",
      revisedFrom: "",
      revisionNote: "",
      xbrl: metric.source === "SEC XBRL" ? "SEC XBRL" : "",
      unavailable: false,
      edgar: true,
    };
  }).filter(Boolean);
}

function presentUnavailable(item) {
  return {
    id: item.metric_id,
    name: item.display_name || item.metric_id,
    detailName: "",
    unit: item.unit || "",
    category: item.category || "operating",
    panelType: "text",
    series: [],
    latest: "not reported",
    latestPeriod: "",
    periodEnd: "",
    qoq: null,
    yoy: null,
    qoqValue: null,
    yoyValue: null,
    sourceUrl: safeHttpUrl(item.source_url),
    sourceTitle: item.reason || "Unavailable",
    published: "",
    confidence: "",
    approval: "unavailable",
    pending: isPendingMetric(item),
    capex: item.metric_id === "capex",
    rows: [],
    text: item.reason || "Not reported yet.",
    definition: "",
    revisedFrom: "",
    revisionNote: "",
    xbrl: "",
    unavailable: true,
  };
}

export function stockPanels(payload, chartData, options = {}) {
  const review = options.review === "approved" ? "approved" : "all";
  const ticker = options.ticker || payload?.ticker || "";
  const chosen = metricsForReview(payload, review).map((metric) => presentMetric(metric, ticker));
  const byId = new Map(chosen.map((metric) => [metric.id, metric]));
  chosen.forEach((metric) => {
    if (!metric.capex) return;
    metric.context = ["operating_cash_flow", "free_cash_flow"]
      .map((id) => byId.get(id))
      .filter(Boolean)
      .map((item) => ({
        name: item.detailName || item.name,
        latest: item.latest,
        latestPeriod: item.latestPeriod,
        pending: item.pending,
      }));
  });
  const operating = chosen.filter((metric) => metric.category === "operating");
  const guidance = chosen.filter((metric) => metric.category === "guidance");
  const irFundamentals = chosen.filter((metric) => metric.category === "fundamentals" || metric.capex);
  const covered = new Set(irFundamentals.map((metric) => metric.id));
  const edgar = edgarCards(chartData, ticker).filter((card) => !covered.has(String(card.id).replace(/^edgar:/, "")));
  const fundamentals = irFundamentals.length ? [...irFundamentals, ...edgar] : edgar;
  const unavailable = (payload?.unavailable || [])
    .filter((item) => !isRejectedMetric(item))
    .filter((item) => review === "all" || !isPendingMetric(item))
    .filter((item) => item?.approved !== false || isPendingMetric(item))
    .map(presentUnavailable);
  const rank = (metric) => (metric.pending ? 2 : 0) + (metric.capex ? 0 : 1);
  const sortCards = (cards) => cards.slice().sort((a, b) => rank(a) - rank(b));
  const limit = (cards) => (options.period ? cards.map((card) => applyPeriod(card, options, ticker)) : cards);
  return {
    operating: limit(sortCards([...operating, ...unavailable.filter((item) => item.category !== "fundamentals" && item.category !== "guidance")])),
    fundamentals: limit(sortCards(fundamentals)),
    guidance: limit(sortCards([...guidance, ...unavailable.filter((item) => item.category === "guidance")])),
    fundamentalsSource: irFundamentals.length ? "ir" : fundamentals.length ? "edgar" : "none",
    pendingCount: chosen.filter((metric) => metric.pending).length,
    approvedCount: chosen.filter((metric) => !metric.pending).length,
  };
}

export function callsCopy() {
  return {
    guidance: "Show pending review includes proposed series on this page. Approved only hides those panels and list rows.",
    transcripts: "Quarterly call transcripts are not collected. They are added only where the company hosts them or the license explicitly allows it.",
  };
}

function notePoints(card) {
  const points = [];
  const text = String(card?.text || "").trim();
  if (text) points.push(text);
  if (card?.latest && card.latest !== "not reported") {
    const line = `${card.name || "Note"}: ${card.latest}`;
    if (!points.includes(line)) points.push(line);
  }
  for (const row of card?.rows || []) {
    if (typeof row === "string" && row.trim()) points.push(row.trim());
    else if (row && typeof row === "object") {
      const point = String(row.point || row.text || row.note || row.title || "").trim();
      if (point) points.push(point);
    }
  }
  return points;
}

function quarterLabel(value) {
  const match = QUARTER.exec(String(value || "").trim());
  return match ? `Q${match[2]} ${match[1]}` : String(value || "").trim();
}

/** Guidance cards as quarterly notes, latest quarter first. The underlying metric fields stay on the cards. */
export function quarterlyNoteEntries(guidance) {
  const cards = [...(guidance || [])].filter(Boolean).sort((a, b) => (
    String(b.latestPeriod || "").localeCompare(String(a.latestPeriod || ""))
    || String(b.periodEnd || b.published || "").localeCompare(String(a.periodEnd || a.published || ""))
  ));
  return cards.map((card) => ({
    id: card.id || "",
    quarter: quarterLabel(card.latestPeriod),
    date: String(card.periodEnd || card.published || "").trim(),
    points: notePoints(card),
    sourceUrl: card.sourceUrl || "",
    sourceTitle: card.sourceTitle || "Source",
    pending: Boolean(card.pending),
  })).filter((note) => note.quarter || note.points.length || note.sourceUrl);
}

export function emptyKpiCopy() {
  return "No operating metrics collected yet.";
}
