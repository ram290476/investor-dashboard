// 13F summary panel for a fund's manager (issue 223). Pure view model plus a DOM renderer.
// Data: GET /fund/<T> -> managers[].summary_13f (built from SEC 13F-HR, previous quarter and
// SEC-derived issuer sectors by the backend). No tickers, sectors or managers are hard-coded.
import { formatDate, formatPct, formatUsd } from "./fund-holdings.js";

export const TOP_NAMES = 10;
export const CHANGE_ROWS = 5;
export const EMPTY_13F_COPY = "No 13F filing is available for this fund's manager yet.";
export const NO_PREVIOUS_COPY = "The previous quarter's 13F is not available, so changes can't be shown yet.";

export function managerSummaries(fundDoc) {
  return (fundDoc?.managers || []).filter((m) => m?.summary_13f);
}

export function quarterLabel(summary, manager = {}) {
  const parts = [];
  if (summary?.period_end) parts.push(`Quarter ended ${formatDate(summary.period_end)}`);
  if (manager.filed_at) parts.push(`filed ${formatDate(manager.filed_at)}`);
  return parts.length ? `${parts.join(" · ")} · SEC 13F` : "SEC 13F";
}

export function factsLine(summary) {
  const out = [];
  if (Number.isFinite(Number(summary?.value_total_usd))) out.push(`${formatUsd(summary.value_total_usd)} reported value`);
  if (Number.isFinite(Number(summary?.positions))) out.push(`${summary.positions} positions`);
  return out;
}

export function topNames(summary, n = TOP_NAMES) {
  return (summary?.top || []).slice(0, n).map((row) => ({
    label: row.ticker || row.name || row.cusip || "Unnamed holding",
    name: row.name || "",
    ticker: row.ticker || null,
    pct: Number(row.pct_portfolio) || 0,
    value_usd: row.value_usd,
  }));
}

export function sectorRows(summary) {
  return (summary?.sectors || []).map((s) => ({ sector: s.sector || "Other", pct: Number(s.pct_portfolio) || 0,
    value_usd: s.value_usd, positions: s.positions }));
}

function changeLabel(row) {
  return row.ticker || row.name || row.cusip || "Unnamed holding";
}

export function changeGroups(summary, n = CHANGE_ROWS) {
  const ch = summary?.changes;
  if (!ch) return null;
  const counts = ch.counts || {};
  const shares = (r) => (Number.isFinite(Number(r.shares_change_pct)) ? `${r.shares_change_pct > 0 ? "+" : ""}${Number(r.shares_change_pct).toFixed(0)}% shares` : "");
  return [
    { key: "new_buys", title: "New buys", count: counts.new_buys ?? ch.new_buys?.length ?? 0, tone: "up",
      rows: (ch.new_buys || []).slice(0, n).map((r) => ({ label: changeLabel(r), name: r.name, detail: formatUsd(r.value_usd) })) },
    { key: "full_sells", title: "Full sells", count: counts.full_sells ?? ch.full_sells?.length ?? 0, tone: "down",
      rows: (ch.full_sells || []).slice(0, n).map((r) => ({ label: changeLabel(r), name: r.name, detail: `was ${formatUsd(r.value_prev_usd)}` })) },
    { key: "increases", title: "Biggest increases", count: counts.increased ?? ch.increases?.length ?? 0, tone: "up",
      rows: (ch.increases || []).slice(0, n).map((r) => ({ label: changeLabel(r), name: r.name, detail: `+${formatUsd(r.trade_value_usd)}`, sub: shares(r) })) },
    { key: "decreases", title: "Biggest decreases", count: counts.decreased ?? ch.decreases?.length ?? 0, tone: "down",
      rows: (ch.decreases || []).slice(0, n).map((r) => ({ label: changeLabel(r), name: r.name, detail: `−${formatUsd(Math.abs(r.trade_value_usd))}`, sub: shares(r) })) },
  ];
}

function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function barRow(document, label, title, pct, max, valueText) {
  const li = el(document, "li", "m13f-bar-row");
  const name = el(document, "span", "m13f-bar-label", label);
  if (title && title !== label) name.title = title;
  const track = el(document, "span", "m13f-bar-track");
  const bar = el(document, "span", "m13f-bar");
  bar.style.width = `${Math.max(2, (pct / Math.max(max, 1e-9)) * 100)}%`;
  bar.setAttribute("aria-hidden", "true");
  track.append(bar);
  li.append(name, track, el(document, "span", "m13f-bar-value", valueText));
  return li;
}

// Renders one panel per manager with a 13F summary; null when the fund document has none and
// it is not loading (the fund panel already explains an empty fund).
export function renderManager13fPanels(fundDoc, loadState, document = globalThis.document) {
  if (loadState !== "ready") return [];
  const managers = managerSummaries(fundDoc);
  if (!managers.length) {
    if (!(fundDoc?.managers || []).length) return [];
    const panel = el(document, "section", "panel manager-13f");
    panel.dataset.stockPanel = "overview";
    const header = el(document, "div", "panel-header");
    header.append(el(document, "h2", "", "13F summary"));
    panel.append(header, el(document, "p", "data-state m13f-empty", EMPTY_13F_COPY));
    return [panel];
  }
  return managers.map((manager) => {
    const summary = manager.summary_13f;
    const panel = el(document, "section", "panel manager-13f");
    panel.id = `stock-panel-13f-${manager.cik}`;
    panel.dataset.stockPanel = "overview";
    const header = el(document, "div", "panel-header");
    const titleBlock = el(document, "div");
    titleBlock.append(el(document, "h2", "", `13F summary · ${manager.name || "Fund manager"}`));
    titleBlock.append(el(document, "p", "panel-subtitle m13f-asof", quarterLabel(summary, manager)));
    header.append(titleBlock);
    panel.append(header);
    const facts = factsLine(summary);
    if (facts.length) panel.append(el(document, "p", "m13f-facts", facts.join(" · ")));

    const grid = el(document, "div", "m13f-grid");
    const top = topNames(summary);
    const topBlock = el(document, "div", "m13f-block");
    topBlock.append(el(document, "h3", "m13f-heading", "Top names by weight"));
    const topList = el(document, "ol", "m13f-bars");
    const topMax = Math.max(0, ...top.map((r) => r.pct));
    for (const row of top) topList.append(barRow(document, row.label, row.name, row.pct, topMax, formatPct(row.pct, 1)));
    topBlock.append(topList);

    const sectors = sectorRows(summary);
    const sectorBlock = el(document, "div", "m13f-block");
    sectorBlock.append(el(document, "h3", "m13f-heading", "Sectors"));
    const sectorList = el(document, "ul", "m13f-bars");
    const sectorMax = Math.max(0, ...sectors.map((r) => r.pct));
    for (const row of sectors) {
      sectorList.append(barRow(document, row.sector, `${row.positions} positions`, row.pct, sectorMax, formatPct(row.pct, 1)));
    }
    sectorBlock.append(sectorList);
    if (summary.sector_source) sectorBlock.append(el(document, "p", "m13f-source", "Sector: SEC filer office of each issuer; unmatched issuers are Other."));
    grid.append(topBlock, sectorBlock);
    panel.append(grid);

    const changesBlock = el(document, "div", "m13f-changes");
    const heading = summary.previous_period_end
      ? `Changes since ${formatDate(summary.previous_period_end)}` : "Quarter-over-quarter changes";
    changesBlock.append(el(document, "h3", "m13f-heading", heading));
    const groups = changeGroups(summary);
    if (!groups) {
      changesBlock.append(el(document, "p", "data-state m13f-no-prev", NO_PREVIOUS_COPY));
    } else {
      const cols = el(document, "div", "m13f-change-grid");
      for (const group of groups) {
        const col = el(document, "div", `m13f-change m13f-${group.tone}`);
        col.dataset.change = group.key;
        col.append(el(document, "h4", "m13f-change-title", `${group.title} (${group.count})`));
        const list = el(document, "ul", "m13f-change-list");
        if (!group.rows.length) list.append(el(document, "li", "m13f-none", "None"));
        for (const row of group.rows) {
          const li = el(document, "li");
          const name = el(document, "span", "m13f-change-name", row.label);
          if (row.name && row.name !== row.label) name.title = row.name;
          const detail = el(document, "span", "m13f-change-detail", row.detail);
          li.append(name, detail);
          if (row.sub) li.append(el(document, "span", "m13f-change-sub", row.sub));
          list.append(li);
        }
        col.append(list);
        cols.append(col);
      }
      changesBlock.append(cols);
    }
    panel.append(changesBlock);
    return panel;
  });
}
