/**
 * Income-statement lines for the Analysis tab.
 *
 * Values come from the company document, SEC fundamentals on the chart payload,
 * and the existing placeholder forecast. A missing line stays missing. Nothing
 * here fills a gap with a guessed number. Proposed company metrics stay pending.
 */

const PERIOD = /^(\d{4})Q([1-4])$/;

const FLOW_ROLES = new Set([
  "revenue",
  "segment_revenue",
  "cost_of_sales",
  "gross_profit",
  "opex",
  "rd",
  "sga",
  "restructuring",
  "other_opex",
  "operating_profit",
  "interest_income",
  "interest_expense",
  "interest_other",
  "other_income",
  "pretax",
  "tax",
  "net_profit",
]);

const SINGLETON_ROLES = new Set([
  "revenue",
  "gross_profit",
  "net_profit",
  "operating_profit",
  "ebitda",
  "pretax",
  "opex",
  "tax",
  "pe",
  "shares",
]);

const SINGLETON_RANK = {
  revenue: ["revenue_gaap", "total_revenues", "total_revenue", "revenue"],
  gross_profit: ["gross_profit", "gross_profit_gaap"],
  net_profit: ["net_income_gaap", "net_income", "net_profit", "net_loss"],
  operating_profit: ["operating_income", "operating_profit"],
  ebitda: ["adjusted_ebitda", "model_adjusted_ebitda", "ebitda"],
  pretax: ["pretax_income", "income_before_tax", "earnings_before_tax", "pretax"],
  opex: ["operating_expenses", "operating_expense"],
  tax: ["income_tax_expense", "income_tax", "tax_expense"],
  pe: ["pe_assumption", "pe", "price_to_earnings"],
  shares: ["shares_outstanding"],
};

const EDGAR_UNITS = {
  revenue_gaap: "USD",
  gross_profit_gaap: "USD",
  gross_margin_gaap: "ratio",
  shares_outstanding: "shares",
  public_float_usd: "USD",
  deliveries: "vehicles",
  fsd_subscribers: "subscribers",
};

const EXPENSE_ROLES = new Set([
  "cost_of_sales",
  "opex",
  "rd",
  "sga",
  "restructuring",
  "other_opex",
  "tax",
  "interest_expense",
]);

export function isFlowRole(role) {
  return FLOW_ROLES.has(role);
}

export function parseFiscalPeriod(value) {
  const match = PERIOD.exec(String(value || "").replace(/\s+/g, ""));
  if (!match) return null;
  return { period: `${match[1]}Q${match[2]}`, year: Number(match[1]), quarter: Number(match[2]) };
}

function rejected(metric) {
  const state = metric?.approval_state || metric?.status;
  return state === "rejected" || metric?.approved === false && state === "rejected";
}

function pendingMetric(metric) {
  if (!metric || rejected(metric)) return false;
  const state = metric.approval_state || metric.status;
  return state === "proposed";
}

export function classifyMetric(metric) {
  if (!metric || rejected(metric)) return "";
  const id = String(metric.metric_id || metric.id || "").toLowerCase();
  const name = String(metric.display_name || metric.name || "").toLowerCase().trim();
  const blob = `${id} ${name}`;
  const unit = String(metric.unit || "").toLowerCase();
  const usd = unit === "usd" || unit.startsWith("usd");

  if (/gross_margin|operating_margin|net_margin|\bmargin\b/.test(blob) && !/^gross_profit(_gaap)?$/.test(id)) return "";
  if (/deferred_revenue|\binventory\b|cash_and|capex|capital_expenditure|operating_cash|free_cash|share_repurchase|dividends_paid|public_float|days_sales|days_payable|eps_diluted|eps_basic/.test(blob)) return "";
  if (unit === "statement" || metric.category === "guidance") return "";
  if (unit.includes("share") && !/shares_outstanding/.test(id) && !/\bp\/e\b|price_to_earnings|pe_assumption/.test(blob)) return "";

  if (/(^|_)pe_assumption$|^pe$|price_to_earnings|price\/earnings|\bp\/e\b/.test(blob)) return "pe";
  if (/shares_outstanding/.test(id)) return "shares";
  if (/net_income|net_profit|net_loss/.test(id) || /\bnet (income|profit|loss)\b/.test(name)) return "net_profit";
  if (/ebitda/.test(blob)) return "ebitda";
  if (/^gross_profit$|^gross_profit_gaap$/.test(id) || name === "gross profit") return "gross_profit";
  if (/cost_of_revenue|cost_of_sales|cost_of_goods|(^|_)cogs$/.test(id) || /cost of /.test(name)) return "cost_of_sales";
  if (/research_and_development|(^|_)r_and_d($|_)|(^|_)rd($|_)/.test(id) || /research and development|\br&d\b/.test(name)) return "rd";
  if (/(^|_)sga($|_)|selling_general|general_and_administrative/.test(id) || /\bsg&a\b|selling, general|sales & admin|sales and administrative|general and administrative/.test(name)) return "sga";
  if (/restructur/.test(blob)) return "restructuring";
  if ((/^operating_expenses?$/.test(id) || name === "operating expenses") && !/other/.test(blob)) return "opex";
  if (/interest_income/.test(id) || name.includes("interest income")) return "interest_income";
  if (/interest_expense/.test(id) || name.includes("interest expense")) return "interest_expense";
  if (/interest/.test(blob) && /other/.test(blob)) return "interest_other";
  if (/other_income|other_expense|nonoperating|non_operating/.test(id) || /other income|other expense/.test(name)) return "other_income";
  if (/income_tax|tax_expense|provision_for/.test(blob) || /income tax|tax expense/.test(name)) return "tax";
  if (/operating_income|operating_profit|income_from_operations/.test(id) || /operating income|operating profit|income from operations|loss from operations/.test(name)) return "operating_profit";
  if (/pretax|pre_tax|income_before_tax|earnings_before_tax/.test(id) || /pre-tax|pretax|income before tax|before income taxes/.test(name)) return "pretax";
  if (/^revenue_gaap$|^total_revenues?$|^revenue$/.test(id) || name === "revenue" || name === "total revenue" || name === "total revenues") return "revenue";
  if (/regulatory.?credit/.test(blob) && (usd || !unit)) return "segment_revenue";
  if (/revenue|sales/.test(blob) && !/cost|gross|deferred|margin/.test(blob) && (usd || !unit || metric.category === "fundamentals")) return "segment_revenue";
  if (/(^|_)leasing$/.test(id) && (usd || !unit) && !/count|vehicle|liabilit|expense|receivable/.test(blob)) return "segment_revenue";
  if (/deliver/.test(blob)) return "deliveries";
  if (/produc/.test(blob) && !/capacity/.test(blob)) return "production";
  if (metric.category === "operating") return "segment_other";
  if ((usd || !unit) && /expense/.test(blob)) return "other_opex";
  if ((usd || !unit) && /\b(income|gain)\b/.test(blob)) return "other_income";
  return "";
}

export function isStockMetric(metric) {
  if (!metric) return false;
  if (metric.role === "shares" || metric.role === "pe") return true;
  const blob = `${metric.id} ${metric.name}`.toLowerCase();
  if (/deliver|produc|deploy|revenue|sales|income|expense|profit|ebitda|tax|cost/.test(blob)) return false;
  return /subscriber|subscription|fleet|outstanding|station|connector|installed|capacity|employee|headcount/.test(blob);
}

export function isExpenseRole(role) {
  return EXPENSE_ROLES.has(role);
}

/** Signed contribution to pretax. A positive expense reduces pretax. */
export function signedBridge(role, value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  if (role === "interest_expense") return number > 0 ? -number : number;
  return number;
}

function humanName(id) {
  const text = String(id || "").replace(/_/g, " ").trim();
  if (!text) return "Line";
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function singletonScore(metric) {
  const rank = SINGLETON_RANK[metric.role] || [];
  const index = rank.indexOf(metric.id);
  const rankScore = index === -1 ? rank.length + 5 : index;
  return rankScore * 1000 - metric.points.length;
}

function blankMetric(raw, role) {
  const id = String(raw.metric_id || raw.id || role);
  return {
    id,
    name: String(raw.display_name || raw.name || humanName(id)),
    unit: String(raw.unit || ""),
    role,
    category: raw.category || "",
    pending: pendingMetric(raw),
    source: raw.source || "company",
    points: [],
  };
}

function addPoint(metric, fiscalPeriod, value, flags) {
  const parsed = parseFiscalPeriod(fiscalPeriod);
  const number = Number(value);
  if (!parsed || !Number.isFinite(number)) return;
  if (metric.points.some((point) => point.period === parsed.period)) return;
  metric.points.push({
    period: parsed.period,
    year: parsed.year,
    quarter: parsed.quarter,
    value: number,
    estimate: Boolean(flags.estimate),
    pending: Boolean(flags.pending || metric.pending),
    source: flags.source || metric.source || "",
    periodEnd: String(flags.periodEnd || ""),
  });
}

function addSeriesPoint(metric, point, flags) {
  if (!point || point.reported === false) return;
  addPoint(metric, point.fiscal_period || point.period, point.value, {
    ...flags,
    periodEnd: point.period_end || point.periodEnd || flags.periodEnd || "",
  });
}

function takeSingletons(metrics) {
  const kept = [];
  const groups = new Map();
  for (const metric of metrics) {
    if (!SINGLETON_ROLES.has(metric.role)) {
      kept.push(metric);
      continue;
    }
    const group = groups.get(metric.role) || [];
    group.push(metric);
    groups.set(metric.role, group);
  }
  for (const group of groups.values()) {
    group.sort((a, b) => singletonScore(a) - singletonScore(b));
    const winner = group[0];
    for (const other of group.slice(1)) {
      for (const point of other.points) {
        addPoint(winner, point.period, point.value, point);
      }
    }
    winner.points.sort((a, b) => a.year - b.year || a.quarter - b.quarter);
    kept.push(winner);
  }
  return kept;
}

function assumptionPe(payload) {
  const block = payload?.assumptions?.pe || payload?.yearly_assumptions?.pe;
  if (!block || typeof block !== "object" || Array.isArray(block)) return [];
  return Object.entries(block).map(([year, value]) => ({ year: Number(year), value: Number(value) }))
    .filter((row) => Number.isFinite(row.year) && Number.isFinite(row.value));
}

/**
 * Every usable line for one ticker. Company rows win a quarter; the chart and
 * the placeholder forecast only fill quarters that are still empty.
 */
export function collectStatement(payload, chartData) {
  const metrics = [];
  const byId = new Map();
  const ensure = (raw, role) => {
    const id = String(raw.metric_id || raw.id || role);
    const existing = byId.get(id);
    if (existing) return existing;
    const metric = blankMetric(raw, role);
    metric.stock = isStockMetric(metric);
    byId.set(id, metric);
    metrics.push(metric);
    return metric;
  };

  for (const raw of payload?.metrics || []) {
    const role = classifyMetric(raw);
    if (!role) continue;
    const metric = ensure(raw, role);
    const flags = { pending: pendingMetric(raw), estimate: Boolean(raw.estimate), source: raw.provenance?.source_kind || "company" };
    for (const point of raw.series || []) addSeriesPoint(metric, point, flags);
    addSeriesPoint(metric, raw.latest, flags);
  }

  for (const row of chartData?.fundamentals || []) {
    const raw = {
      metric_id: row.series_id || row.metric_id,
      display_name: row.display_name || row.name || "",
      unit: row.unit || EDGAR_UNITS[row.series_id] || "",
      category: "fundamentals",
      approval_state: "approved",
      approved: true,
    };
    if (!raw.display_name) raw.display_name = humanName(raw.metric_id);
    const role = classifyMetric(raw);
    if (!role) continue;
    const metric = ensure(raw, role);
    addSeriesPoint(metric, {
      fiscal_period: row.fiscal_quarter || row.fiscal_period,
      value: row.value,
      reported: row.reported !== false,
    }, { pending: false, estimate: false, source: row.source_id || "edgar" });
  }

  const forecast = payload?.deep_dive?.forecast;
  for (const segment of forecast?.segments || []) {
    if (!segment || typeof segment !== "object") continue;
    for (const series of segment.metrics || []) {
      const id = series?.metric_id;
      if (!id) continue;
      const raw = {
        metric_id: id,
        display_name: byId.get(id)?.name || segment.label || humanName(id),
        unit: series.unit || "USD",
        category: "fundamentals",
        approval_state: series.approval_state || "proposed",
      };
      const role = classifyMetric(raw);
      if (!role) continue;
      const metric = ensure(raw, role);
      const actualFlags = {
        pending: series.approval_state === "proposed" || series.approved === false,
        estimate: false,
        source: "forecast-actual",
      };
      for (const point of series.actuals || []) addSeriesPoint(metric, point, actualFlags);
      for (const point of series.forecast || []) {
        if (point?.value == null) continue;
        addSeriesPoint(metric, { ...point, reported: point.reported !== false && point.value != null }, {
          pending: true,
          estimate: true,
          source: "placeholder-forecast",
        });
      }
    }
  }

  if (![...byId.values()].some((metric) => metric.role === "pe")) {
    const points = assumptionPe(payload);
    if (points.length) {
      const metric = ensure({
        metric_id: "pe_assumption",
        display_name: "P/E (assumption)",
        unit: "multiple",
        approval_state: "proposed",
        source: "assumption",
      }, "pe");
      for (const point of points) {
        addPoint(metric, `${point.year}Q4`, point.value, { pending: true, estimate: false, source: "assumption" });
      }
    }
  }

  const chosen = takeSingletons(metrics).map((metric) => {
    metric.stock = isStockMetric(metric);
    metric.points.sort((a, b) => a.year - b.year || a.quarter - b.quarter);
    return metric;
  });
  return { metrics: chosen };
}

export function periodSlice(metric, year, quarter) {
  const points = (metric?.points || []).filter((point) => point.year === year && (quarter == null || point.quarter === quarter));
  if (!points.length) return null;
  const ordered = points.slice().sort((a, b) => a.quarter - b.quarter);
  if (quarter != null || metric.stock) {
    const chosen = ordered.at(-1);
    return {
      value: chosen.value,
      estimate: Boolean(chosen.estimate),
      pending: Boolean(chosen.pending),
      quarters: [chosen.quarter],
      estimatedQuarters: chosen.estimate ? [chosen.quarter] : [],
      periodEnd: chosen.periodEnd || "",
    };
  }
  return {
    value: ordered.reduce((sum, point) => sum + point.value, 0),
    estimate: ordered.some((point) => point.estimate),
    pending: ordered.some((point) => point.pending),
    quarters: ordered.map((point) => point.quarter),
    estimatedQuarters: ordered.filter((point) => point.estimate).map((point) => point.quarter),
    periodEnd: ordered.at(-1)?.periodEnd || "",
  };
}

/** Same quarters in another year. Missing any quarter means the comparison is unavailable. */
export function alignedValue(metric, year, quarters) {
  if (!metric || !quarters?.length) return null;
  if (metric.stock) {
    const slice = periodSlice(metric, year, null);
    if (!slice || !quarters.includes(slice.quarters[0])) return null;
    return slice;
  }
  const points = quarters.map((quarter) => (metric.points || []).find((point) => point.year === year && point.quarter === quarter));
  if (points.some((point) => !point)) return null;
  return {
    value: points.reduce((sum, point) => sum + point.value, 0),
    estimate: points.some((point) => point.estimate),
    pending: points.some((point) => point.pending),
    quarters: quarters.slice(),
    estimatedQuarters: points.filter((point) => point.estimate).map((point) => point.quarter),
    periodEnd: points.at(-1)?.periodEnd || "",
  };
}

export function changeRatio(current, prior) {
  if (!current || !prior || !Number.isFinite(current.value) || !Number.isFinite(prior.value) || prior.value === 0) return null;
  return (current.value - prior.value) / Math.abs(prior.value);
}

export function formatScaled(value, { money = false, multiple = false } = {}) {
  if (!Number.isFinite(value)) return "—";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (multiple) {
    const digits = abs >= 10 ? 0 : 1;
    return `${sign}${trimScaled(abs.toFixed(digits), digits)}x`;
  }
  const steps = [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "k"]];
  for (const [div, suffix] of steps) {
    if (abs >= div) {
      const scaled = abs / div;
      const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2;
      const body = trimScaled(scaled.toFixed(digits), digits);
      return money ? `${sign}$${body}${suffix}` : `${sign}${body}${suffix}`;
    }
  }
  if (money) {
    if (abs >= 100) return `${sign}$${Math.round(abs).toLocaleString("en-US")}`;
    return `${sign}$${trimScaled(abs.toFixed(2), 2)}`;
  }
  return `${sign}${Math.round(abs).toLocaleString("en-US")}`;
}

function trimScaled(text, digits) {
  if (digits !== 2) return text;
  return text.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

export function formatChangePct(ratio) {
  if (!Number.isFinite(ratio)) return "";
  const pct = ratio * 100;
  const abs = Math.abs(pct);
  const digits = Math.abs(abs - Math.round(abs)) < 0.05 ? 0 : 1;
  return `${pct >= 0 ? "▲" : "▼"}${abs.toFixed(digits)}%`;
}

export function formatPts(delta) {
  if (!Number.isFinite(delta)) return "";
  const pts = delta * 100;
  const abs = Math.abs(pts);
  return `${pts >= 0 ? "▲" : "▼"}${abs.toFixed(1)} pts`;
}

export function formatMargin(ratio) {
  if (!Number.isFinite(ratio)) return "";
  return `${(ratio * 100).toFixed(1)}% margin`;
}

export function changeTone(ratio) {
  if (!Number.isFinite(ratio) || ratio === 0) return "flat";
  return ratio > 0 ? "up" : "down";
}

export function compactQuarters(quarters) {
  const list = [...new Set(quarters || [])].filter((quarter) => quarter >= 1 && quarter <= 4).sort((a, b) => a - b);
  if (!list.length) return "";
  const ranges = [];
  let start = list[0];
  let end = list[0];
  for (const quarter of list.slice(1)) {
    if (quarter === end + 1) end = quarter;
    else {
      ranges.push(start === end ? `Q${start}` : `Q${start}–Q${end}`);
      start = quarter;
      end = quarter;
    }
  }
  ranges.push(start === end ? `Q${start}` : `Q${start}–Q${end}`);
  return ranges.join(", ");
}

export function flowMetrics(statement) {
  return (statement?.metrics || []).filter((metric) => isFlowRole(metric.role));
}

export function yearsWithData(metrics, limit = 6) {
  const years = new Set();
  for (const metric of metrics || []) {
    for (const point of metric.points || []) years.add(point.year);
  }
  return [...years].sort((a, b) => a - b).slice(-limit);
}

export function quartersWithData(metrics, year) {
  const quarters = new Set();
  for (const metric of metrics || []) {
    for (const point of metric.points || []) {
      if (point.year === year) quarters.add(point.quarter);
    }
  }
  return [...quarters].sort((a, b) => a - b);
}
