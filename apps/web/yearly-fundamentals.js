/**
 * Yearly Fundamentals table for the Analysis tab.
 *
 * Common rows are always present. Segment rows appear only when that ticker
 * has the series. Market cap is net profit times the P/E assumption. Stock
 * price is market cap divided by shares outstanding. Missing inputs stay blank.
 */

import {
  alignedValue,
  collectStatement,
  formatScaled,
  periodSlice,
  yearsWithData,
} from "./statement-model.js";

const YEAR_LIMIT = 6;

const SEGMENT_ROLES = new Set(["segment_revenue", "segment_other", "deliveries", "production"]);

const STEM_SUFFIXES = new Set([
  "revenue", "sales", "rev", "subscribers", "subscriptions", "subscriber", "subscription",
  "subs", "units", "unit", "fleet", "count", "deliveries", "delivery", "production",
]);

function scenarioLabel(payload) {
  const candidates = [
    payload?.yearly_scenario?.label,
    payload?.yearly_scenario?.subtitle,
    payload?.scenario?.label,
    payload?.assumptions?.scenario,
  ];
  for (const value of candidates) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (text) return text;
  }
  return "";
}

function stem(id) {
  const parts = String(id || "").toLowerCase().replace(/^segment_/, "").split("_").filter(Boolean);
  while (parts.length > 1 && STEM_SUFFIXES.has(parts.at(-1))) parts.pop();
  return parts.join("_") || String(id || "").toLowerCase();
}

function cellFor(metric, year) {
  const slice = periodSlice(metric, year, null);
  if (!slice) return { text: "—", value: null, estimate: false, pending: false };
  const money = String(metric.unit || "").toLowerCase().startsWith("usd") || metric.role === "segment_revenue";
  const multiple = metric.role === "pe";
  return {
    text: formatScaled(slice.value, { money, multiple }),
    value: slice.value,
    estimate: slice.estimate,
    pending: slice.pending,
  };
}

function blankCell() {
  return { text: "—", value: null, estimate: false, pending: false };
}

function row(id, label, kind, cells, extra = {}) {
  return {
    id,
    label,
    kind,
    emphasis: extra.emphasis || "",
    pending: cells.some((item) => item.pending),
    cells,
  };
}

function metricByRole(metrics, role) {
  return metrics.find((metric) => metric.role === role) || null;
}

function buildSegmentRows(metrics, years) {
  const segments = metrics.filter((metric) => SEGMENT_ROLES.has(metric.role) && metric.points.length);
  const groups = new Map();
  for (const metric of segments) {
    const key = stem(metric.id);
    const group = groups.get(key) || [];
    group.push(metric);
    groups.set(key, group);
  }
  const rows = [];
  const used = new Set();
  for (const metric of segments) {
    const key = stem(metric.id);
    if (used.has(key)) continue;
    used.add(key);
    const group = groups.get(key) || [];
    const money = group.filter((item) => String(item.unit || "").toLowerCase().startsWith("usd") || item.role === "segment_revenue");
    const counts = group.filter((item) => !money.includes(item));
    if (money.length === 1 && counts.length === 1) {
      const count = counts[0];
      const revenue = money[0];
      const cells = years.map((year) => {
        const countCell = cellFor(count, year.year);
        const revenueCell = cellFor(revenue, year.year);
        const parts = [countCell.text !== "—" ? countCell.text : "", revenueCell.text !== "—" ? revenueCell.text : ""].filter(Boolean);
        return {
          text: parts.length ? parts.join(" / ") : "—",
          value: revenueCell.value ?? countCell.value,
          estimate: countCell.estimate || revenueCell.estimate,
          pending: countCell.pending || revenueCell.pending,
        };
      });
      if (cells.every((item) => item.text === "—")) continue;
      rows.push(row(`${count.id}+${revenue.id}`, `${count.name} / rev`, "segment", cells));
      continue;
    }
    for (const item of group) {
      const cells = years.map((year) => cellFor(item, year.year));
      if (cells.every((cell) => cell.text === "—")) continue;
      rows.push(row(item.id, item.name, "segment", cells));
    }
  }
  return rows;
}

function valueCell(slice, { money = false, multiple = false } = {}) {
  if (!slice) return blankCell();
  return {
    text: formatScaled(slice.value, { money, multiple }),
    value: slice.value,
    estimate: slice.estimate,
    pending: slice.pending,
  };
}

function buildCommonRows(metrics, years, derivedTotal) {
  const revenue = metricByRole(metrics, "revenue");
  const profit = metricByRole(metrics, "net_profit");
  const ebitda = metricByRole(metrics, "ebitda");
  const pe = metricByRole(metrics, "pe");
  const shares = metricByRole(metrics, "shares");
  const revenueCells = years.map((year) => valueCell(periodSlice(revenue, year.year, null), { money: true }));
  const totalCells = years.map((year, index) => {
    const reported = revenueCells[index];
    if (reported.value != null) return reported;
    const sum = derivedTotal.get(year.year);
    if (!sum) return blankCell();
    return {
      text: formatScaled(sum.value, { money: true }),
      value: sum.value,
      estimate: sum.estimate,
      pending: sum.pending,
      derived: true,
    };
  });
  const profitCells = years.map((year) => valueCell(periodSlice(profit, year.year, null), { money: true }));
  const ebitdaCells = years.map((year) => valueCell(periodSlice(ebitda, year.year, null), { money: true }));
  const peCells = years.map((year) => valueCell(periodSlice(pe, year.year, null), { multiple: true }));
  const capCells = years.map((year, index) => {
    const earnings = profitCells[index].value;
    const multiple = peCells[index].value;
    if (!Number.isFinite(earnings) || !Number.isFinite(multiple)) return blankCell();
    const value = earnings * multiple;
    return {
      text: formatScaled(value, { money: true }),
      value,
      estimate: profitCells[index].estimate || peCells[index].estimate,
      pending: profitCells[index].pending || peCells[index].pending,
      derived: true,
    };
  });
  const priceCells = years.map((year, index) => {
    const cap = capCells[index].value;
    const shareSlice = periodSlice(shares, year.year, null);
    if (!Number.isFinite(cap) || !shareSlice || !Number.isFinite(shareSlice.value) || shareSlice.value === 0) return blankCell();
    const value = cap / shareSlice.value;
    return {
      text: formatScaled(value, { money: true }),
      value,
      estimate: capCells[index].estimate || shareSlice.estimate,
      pending: capCells[index].pending || shareSlice.pending,
      derived: true,
    };
  });
  return [
    row("revenue", "Revenue", "revenue", revenueCells),
    row("total_revenue", "Total revenue", "total", totalCells, { emphasis: "total" }),
    row("net_profit", "Net profit", "profit", profitCells, { emphasis: "profit" }),
    row("ebitda", "Model adj. EBITDA", "ebitda", ebitdaCells),
    row("pe", "P/E (assumption)", "pe", peCells, { emphasis: "pe" }),
    row("market_cap", "Market cap", "market_cap", capCells),
    row("price", "Stock price", "price", priceCells, { emphasis: "price" }),
  ];
}

function segmentRevenueTotal(metrics, year) {
  const parts = metrics.filter((metric) => metric.role === "segment_revenue");
  if (!parts.length) return null;
  let value = 0;
  let seen = false;
  let estimate = false;
  let pending = false;
  for (const metric of parts) {
    const slice = periodSlice(metric, year, null);
    if (!slice) continue;
    value += slice.value;
    seen = true;
    estimate = estimate || slice.estimate;
    pending = pending || slice.pending;
  }
  return seen ? { value, estimate, pending } : null;
}

function subtitleFor(payload, years, metrics) {
  const named = scenarioLabel(payload);
  if (named) return named;
  if (!years.length) return "No yearly scenario is on file";
  const estimate = years.some((year) => year.estimate);
  const placeholder = metrics.some((metric) => metric.points.some((point) => point.source === "placeholder-forecast"));
  if (placeholder && estimate) return "Placeholder forecast — pending review";
  if (estimate) return "Includes labeled estimates — pending review";
  return "Reported fiscal years";
}

function columnEstimate(rows, index) {
  return rows.some((item) => item.kind !== "pe" && item.cells[index]?.estimate);
}

export function yearlyFundamentalsModel(ticker, payload, chartData) {
  const statement = collectStatement(payload, chartData);
  const financials = statement.metrics.filter((metric) => (
    metric.role !== "pe" && metric.role !== "shares" && metric.points.length
  ));
  const yearNumbers = yearsWithData(financials.length ? financials : statement.metrics, YEAR_LIMIT);
  const segmentRows = buildSegmentRows(statement.metrics, yearNumbers.map((year) => ({ year })));
  const derivedTotal = new Map(yearNumbers.map((year) => [year, segmentRevenueTotal(statement.metrics, year)]));
  const common = buildCommonRows(statement.metrics, yearNumbers.map((year) => ({ year })), derivedTotal);
  const body = [...segmentRows, ...common];
  const years = yearNumbers.map((year, index) => ({
    year,
    label: `FY${year}${columnEstimate(body, index) ? "E" : ""}`,
    estimate: columnEstimate(body, index),
  }));
  const pending = body.some((item) => item.pending);
  const derived = common.some((item) => item.id === "total_revenue" && item.cells.some((cell) => cell.derived && cell.value != null))
    && !metricByRole(statement.metrics, "revenue");
  const notes = [];
  if (pending) notes.push("Company metrics are pending review.");
  if (!metricByRole(statement.metrics, "pe")) notes.push("No P/E assumption is on file, so market cap and stock price stay blank.");
  if (derived) notes.push("Total revenue sums reported segment revenue because a consolidated total is not on file.");
  const placeholder = statement.metrics.some((metric) => metric.points.some((point) => point.source === "placeholder-forecast"));
  if (placeholder) notes.push("Forward quarters are the placeholder forecast, not a reviewed projection.");
  return {
    ticker: String(ticker || payload?.ticker || "").toUpperCase(),
    subtitle: subtitleFor(payload, years, statement.metrics),
    years,
    rows: body,
    empty: years.length === 0,
    pending,
    notes,
  };
}

function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text != null && text !== "") node.textContent = text;
  return node;
}

export function renderYearlyFundamentals(model, doc = document) {
  const panel = el(doc, "section", "panel yearly-fundamentals");
  panel.setAttribute("aria-label", `${model.ticker || "Company"} yearly fundamentals`);
  const header = el(doc, "div", "yearly-heading");
  const title = el(doc, "h2", "yearly-ticker", model.ticker || "—");
  header.append(title);
  const subtitle = el(doc, "p", "yearly-subtitle", model.subtitle || "");
  header.append(subtitle);
  if (model.pending) {
    const badge = el(doc, "span", "review-badge", "Pending review");
    header.append(badge);
  }
  panel.append(header);

  if (model.empty) {
    panel.append(el(doc, "p", "yearly-empty", "No yearly figures are on file for this ticker. Nothing is estimated here."));
  }

  const wrap = el(doc, "div", "yearly-scroll");
  const table = el(doc, "table", "yearly-table");
  const caption = el(doc, "caption", "yearly-caption", "Yearly fundamentals");
  table.append(caption);
  const head = el(doc, "thead");
  const headRow = el(doc, "tr");
  headRow.append(el(doc, "th", "yearly-label", "Metric"));
  for (const year of model.years) {
    const cell = el(doc, "th", year.estimate ? "yearly-year is-estimate" : "yearly-year", year.label);
    cell.scope = "col";
    headRow.append(cell);
  }
  if (!model.years.length) headRow.append(el(doc, "th", "yearly-year", "—"));
  head.append(headRow);
  const body = el(doc, "tbody");
  for (const item of model.rows) {
    const tr = el(doc, "tr", `yearly-row yearly-row-${item.kind}${item.emphasis ? ` is-${item.emphasis}` : ""}`);
    tr.dataset.row = item.id;
    const label = el(doc, "th", "yearly-label", item.label);
    label.scope = "row";
    if (item.kind === "ebitda") label.title = "Model adjusted EBITDA";
    tr.append(label);
    const cells = item.cells.length ? item.cells : [blankCell()];
    cells.forEach((cell) => {
      const td = el(doc, "td", cell.pending ? "is-pending" : "", cell.text);
      if (cell.pending) td.title = "Pending review";
      if (cell.estimate) td.dataset.estimate = "true";
      tr.append(td);
    });
    body.append(tr);
  }
  table.append(head, body);
  wrap.append(table);
  panel.append(wrap);
  if (model.notes?.length) panel.append(el(doc, "p", "yearly-note", model.notes.join(" ")));
  return panel;
}

export function yearlyCell(model, rowId, year) {
  const line = model.rows.find((item) => item.id === rowId);
  const index = model.years.findIndex((item) => item.year === year);
  if (!line || index < 0) return null;
  return line.cells[index] || null;
}

export { alignedValue };
