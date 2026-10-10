// View model for the per-stock page. Approved metrics stay primary.
// Proposed metrics render separately, labeled Pending review. Text is plain.
import { catalystDateLabel } from "./roadmap.js";

export const LONG_PRESS_MS = 550;

export const STOCK_TABS = [
  { id: "overview", label: "Overview" },
  { id: "kpis", label: "KPIs" },
  { id: "fundamentals", label: "Fundamentals" },
  { id: "calls", label: "Calls" },
];

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

export function companyName(ticker, payload) {
  return payload?.company_name || COMPANY_NAMES[ticker] || ticker;
}

export function nextStockTab(current, key) {
  const ids = STOCK_TABS.map((tab) => tab.id);
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

export function fillQuarterGaps(series) {
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

function presentMetric(metric) {
  const series = fillQuarterGaps(metric.series || []);
  const revisedFrom = metric.revised_from || "";
  if (revisedFrom) {
    series.forEach((point) => {
      if (point.fiscal_period >= revisedFrom) point.revised = true;
    });
  }
  const reported = series.filter((point) => point.reported);
  const latest = metric.latest?.reported === false ? null : metric.latest;
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
    latestPeriod: latest?.fiscal_period || reported.at(-1)?.fiscal_period || "",
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

function edgarCards(chartData) {
  const rows = chartData?.fundamentals || [];
  return EDGAR_METRICS.map((metric) => {
    const series = rows
      .filter((row) => row.series_id === metric.id && row.fiscal_quarter && row.value != null)
      .map((row) => ({
        fiscal_period: row.fiscal_quarter,
        value: row.value,
        reported: true,
        source_id: row.source_id,
        date: row.date,
      }))
      .sort((a, b) => String(a.fiscal_period).localeCompare(String(b.fiscal_period)));
    if (!series.length) return null;
    const filled = fillQuarterGaps(series);
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
  const chosen = metricsForReview(payload, review).map(presentMetric);
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
  const edgar = edgarCards(chartData).filter((card) => !covered.has(String(card.id).replace(/^edgar:/, "")));
  const fundamentals = irFundamentals.length ? [...irFundamentals, ...edgar] : edgar;
  const unavailable = (payload?.unavailable || [])
    .filter((item) => !isRejectedMetric(item))
    .filter((item) => review === "all" || !isPendingMetric(item))
    .filter((item) => item?.approved !== false || isPendingMetric(item))
    .map(presentUnavailable);
  const rank = (metric) => (metric.capex ? 0 : 1);
  const sortCards = (cards) => cards.slice().sort((a, b) => rank(a) - rank(b));
  return {
    operating: sortCards([...operating, ...unavailable.filter((item) => item.category !== "fundamentals" && item.category !== "guidance")]),
    fundamentals: sortCards(fundamentals),
    guidance: sortCards([...guidance, ...unavailable.filter((item) => item.category === "guidance")]),
    fundamentalsSource: irFundamentals.length ? "ir" : fundamentals.length ? "edgar" : "none",
    pendingCount: chosen.filter((metric) => metric.pending).length,
    approvedCount: chosen.filter((metric) => !metric.pending).length,
  };
}

export function callsCopy() {
  return {
    guidance: "Approved outlook is shown as primary. Proposed outlook is labeled Pending review and can be hidden with Approved only.",
    transcripts: "Quarterly call transcripts are not collected. They are added only where the company hosts them or the license explicitly allows it.",
  };
}

export function emptyKpiCopy() {
  return "No operating metrics collected yet.";
}
