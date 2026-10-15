/**
 * Fund holdings panel (#223) for the company page.
 *
 * Data: GET /fund/<T> (serving/funds/<T>.json, schema_version 1), written by the dashboard build.
 * The panel exists only when the dashboard snapshot marks the ticker `instrument_kind: "fund"`;
 * operating companies get no DOM node and no fetch.
 *
 * FundDocument = {ticker, status, fund_name, holdings_source: "sec_nport" | "yahoo_top_holdings" | null,
 *   holdings_as_of, holdings_note, form, accession, filing_url, period_end, filed_at, net_assets_usd,
 *   net_assets_source, holdings_count, profile{source, fetched_at, expense_ratio, category, family, ...},
 *   holdings[{ticker, name, name_filed, title, cusip, isin, value_usd, pct_nav, payoff, asset_cat, is_private}]}
 */

export const TOP_N = 10;
export const UNNAMED = "Unnamed holding";
export const EMPTY_COPY =
  "Holdings not published yet. N-PORT filings are public quarterly, about 60 days after the period ends.";
export const ERROR_COPY = "Holdings could not be loaded.";
export const DAILY_UNAVAILABLE_NOTE = "Daily holdings unavailable; showing latest SEC filing.";
const BLANK_NAMES = new Set(["", "N/A", "NA", "NONE", "NULL", "-", "--", "UNKNOWN"]);
const SOURCE_LABELS = { sec_nport: "SEC N-PORT", yahoo_top_holdings: "Yahoo Finance", ark_daily: "ARK daily holdings" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// The write-once is_fund stamp (#223) decides; instrument_kind is the fallback for tickers the
// collector has not stamped yet. A disagreement is surfaced, never resolved here.
export function isFund(tickerData) {
  if (typeof tickerData?.is_fund === "boolean") return tickerData.is_fund;
  return tickerData?.instrument_kind === "fund";
}

export const CONFLICT_NOTES = {
  is_fund_stamped_true_but_now_operating: "Listed as a fund, but SEC data now matches an operating company.",
  is_fund_stamped_false_but_nport_fund: "SEC fund filings were found for this ticker; classification is under review.",
};

export function classificationNote(tickerData) {
  const conflict = tickerData?.fund_classification_conflict;
  return conflict ? (CONFLICT_NOTES[conflict] || "Fund classification is under review.") : null;
}

function blank(value) {
  return value === null || value === undefined || BLANK_NAMES.has(String(value).trim().toUpperCase());
}

/** Filed name, else issuer, else title, else "Unnamed holding" (filings sometimes say "N/A"). */
export function holdingName(row = {}) {
  for (const field of ["name", "issuer", "issuer_name", "title"]) {
    if (!blank(row[field])) return String(row[field]).trim();
  }
  return UNNAMED;
}

export function formatDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
  if (!match) return "";
  return `${MONTHS[Number(match[2]) - 1]} ${Number(match[3])}, ${match[1]}`;
}

export function formatUsd(value) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  const [div, suffix] = abs >= 1e12 ? [1e12, "T"] : abs >= 1e9 ? [1e9, "B"] : abs >= 1e6 ? [1e6, "M"]
    : abs >= 1e3 ? [1e3, "K"] : [1, ""];
  const digits = div === 1 ? 0 : abs / div >= 100 ? 0 : 1;
  return `$${(n / div).toFixed(digits)}${suffix}`;
}

export function formatPct(value, digits = 2) {
  const n = Number(value);
  return value === null || value === undefined || !Number.isFinite(n) ? "—" : `${n.toFixed(digits)}%`;
}

/** Header label: the as-of line always names the source. */
export function asOfLabel(doc) {
  if (!doc) return "";
  const source = doc.holdings_source;
  if (source === "sec_nport") {
    const filed = doc.filed_at ? `, filed ${formatDate(doc.filed_at)}` : "";
    return `As of ${formatDate(doc.holdings_as_of || doc.period_end)} · SEC N-PORT${filed}`;
  }
  if (source === "yahoo_top_holdings") {
    return `Top holdings via Yahoo Finance · ${formatDate(doc.holdings_as_of)}`.replace(/ · $/, "");
  }
  if (source) return `As of ${formatDate(doc.holdings_as_of)} · ${SOURCE_LABELS[source] || source}`;
  return "";
}

/** Small note shown when a fresher source is unavailable. */
export function fallbackNote(doc) {
  if (!doc) return "";
  if (doc.holdings_note) return doc.holdings_note;
  return doc.holdings_source === "sec_nport" ? DAILY_UNAVAILABLE_NOTE : "";
}

export function factsLine(doc) {
  if (!doc) return [];
  const parts = [];
  if (doc.net_assets_usd !== null && doc.net_assets_usd !== undefined) {
    const source = SOURCE_LABELS[doc.net_assets_source] || (doc.net_assets_source === "yahoo" ? "Yahoo Finance" : "");
    parts.push(`Net assets ${formatUsd(doc.net_assets_usd)}${source ? ` (${source})` : ""}`);
  }
  const count = Array.isArray(doc.holdings) ? doc.holdings.length : 0;
  if (count) parts.push(`${count} holding${count === 1 ? "" : "s"}`);
  const profile = doc.profile || {};
  if (profile.expense_ratio !== null && profile.expense_ratio !== undefined) {
    parts.push(`Expense ratio ${formatPct(Number(profile.expense_ratio) * 100)}`);
  }
  if (profile.category) parts.push(profile.category);
  if (profile.family) parts.push(profile.family);
  return parts;
}

export function panelState(doc, loadState) {
  if (loadState === "error") return "error";
  if (loadState === "missing") return "empty";
  if (loadState !== "ready") return "loading";
  return Array.isArray(doc?.holdings) && doc.holdings.length ? "ready" : "empty";
}

const SORTERS = {
  pct: (row) => Number(row.pct_nav ?? -Infinity),
  value: (row) => Number(row.value_usd ?? -Infinity),
  name: (row) => holdingName(row).toLowerCase(),
  ticker: (row) => String(row.ticker || "").toLowerCase(),
};

export function defaultUiState() {
  return { expanded: false, sort: "pct", direction: "desc", query: "", onlyWatchlist: false };
}

/**
 * Rows to show. Collapsed: top 10 by % NAV regardless of sort. Expanded: every holding, sorted.
 * A search (or the watchlist filter) covers all holdings and suspends the collapse.
 */
export function visibleRows(holdings = [], ui = defaultUiState(), watchlist = []) {
  const watch = new Set((watchlist || []).map((t) => String(t).toUpperCase()));
  const all = (holdings || []).map((row, index) => ({
    ...row, displayName: holdingName(row), watched: Boolean(row.ticker) && watch.has(String(row.ticker).toUpperCase()),
    index,
  }));
  const query = String(ui.query || "").trim().toLowerCase();
  let rows = all;
  if (query) {
    rows = rows.filter((row) => [row.displayName, row.ticker, row.cusip, row.isin, row.name]
      .some((value) => value && String(value).toLowerCase().includes(query)));
  }
  if (ui.onlyWatchlist) rows = rows.filter((row) => row.watched);
  const filtered = Boolean(query) || Boolean(ui.onlyWatchlist);
  const byPct = (a, b) => SORTERS.pct(b) - SORTERS.pct(a) || a.index - b.index;
  if (!ui.expanded && !filtered) {
    return { rows: [...rows].sort(byPct).slice(0, TOP_N), total: all.length, matched: rows.length, filtered };
  }
  const key = SORTERS[ui.sort] ? ui.sort : "pct";
  const sign = ui.direction === "asc" ? 1 : -1;
  const sorted = [...rows].sort((a, b) => {
    const x = SORTERS[key](a);
    const y = SORTERS[key](b);
    const cmp = typeof x === "string" ? x.localeCompare(y) : x - y;
    return cmp * sign || a.index - b.index;
  });
  return { rows: sorted, total: all.length, matched: rows.length, filtered };
}

export function toggleLabel(ui, total) {
  return ui.expanded ? `Show top ${TOP_N}` : `Show all ${total} holdings`;
}

export function nextSort(ui, key) {
  if (ui.sort === key) return { ...ui, direction: ui.direction === "desc" ? "asc" : "desc", expanded: true };
  return { ...ui, sort: key, direction: key === "name" || key === "ticker" ? "asc" : "desc", expanded: true };
}

// ---------------- DOM ----------------

function el(doc, tag, className, text) {
  const item = doc.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = text;
  return item;
}

/**
 * Render the panel. `state` is {doc, loadState, ui, watchlist}; `ui` is mutated in place so the
 * caller keeps it across dashboard re-renders. Interactions re-render only this panel.
 */
export function renderFundHoldingsPanel(state, document = globalThis.document) {
  const panel = el(document, "section", "panel fund-holdings");
  panel.id = "stock-panel-fund";
  panel.dataset.stockPanel = "overview";
  const ui = state.ui || (state.ui = defaultUiState());

  const draw = (focus) => {
    panel.replaceChildren();
    const fund = state.doc;
    const status = panelState(fund, state.loadState);
    panel.dataset.fundState = status;
    const header = el(document, "div", "panel-header fund-header");
    const titleBlock = el(document, "div");
    titleBlock.append(el(document, "h2", "", "Fund holdings"));
    const asOf = asOfLabel(fund);
    if (status === "ready" && asOf) {
      const label = el(document, "p", "panel-subtitle fund-asof", asOf);
      if (fund.filing_url && fund.holdings_source === "sec_nport") {
        const link = el(document, "a", "fund-edgar-link", "EDGAR");
        link.href = fund.filing_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        label.append(" · ", link);
      }
      titleBlock.append(label);
    }
    header.append(titleBlock);
    panel.append(header);
    if (state.conflictNote) panel.append(el(document, "p", "fund-note fund-conflict", state.conflictNote));

    if (status === "loading") {
      panel.append(el(document, "p", "data-state", "Loading holdings…"));
      return;
    }
    if (status === "error") {
      const message = el(document, "p", "data-state error fund-error", ERROR_COPY);
      message.setAttribute("role", "alert");
      panel.append(message);
      return;
    }
    if (status === "empty") {
      panel.append(el(document, "p", "data-state fund-empty", EMPTY_COPY));
      return;
    }
    const facts = factsLine(fund);
    if (facts.length) {
      const line = el(document, "p", "fund-facts", facts.join(" · "));
      if (fund.profile?.source === "yahoo") line.title = "Fund facts: Yahoo Finance";
      panel.append(line);
    }
    const note = fallbackNote(fund);
    if (note) panel.append(el(document, "p", "fund-note", note));

    const controls = el(document, "div", "fund-controls");
    const search = el(document, "input", "fund-search");
    search.type = "search";
    search.placeholder = "Search name, ticker or CUSIP";
    search.setAttribute("aria-label", "Search holdings");
    search.value = ui.query;
    search.addEventListener("input", () => {
      ui.query = search.value;
      draw("search");
    });
    const watchLabel = el(document, "label", "fund-watch-toggle");
    const watch = el(document, "input");
    watch.type = "checkbox";
    watch.checked = ui.onlyWatchlist;
    watch.addEventListener("change", () => {
      ui.onlyWatchlist = watch.checked;
      draw("watch");
    });
    watchLabel.append(watch, " Only my watchlist");
    controls.append(search, watchLabel);
    panel.append(controls);

    const view = visibleRows(fund.holdings, ui, state.watchlist);
    if (view.filtered) {
      panel.append(el(document, "p", "fund-match-count", `${view.matched} of ${view.total}`));
    }
    const maxPct = Math.max(1e-9, ...view.rows.map((row) => Number(row.pct_nav) || 0));
    const table = el(document, "table", "fund-table");
    const head = el(document, "thead");
    const headRow = el(document, "tr");
    for (const [key, label, className] of [["ticker", "Ticker", "fund-col-ticker"], ["name", "Name", "fund-col-name"],
      ["value", "Market value", "fund-col-value"], ["pct", "% of net assets", "fund-col-pct"]]) {
      const th = el(document, "th", className);
      th.scope = "col";
      const active = (ui.expanded || view.filtered) && ui.sort === key;
      th.setAttribute("aria-sort", active ? (ui.direction === "asc" ? "ascending" : "descending") : "none");
      const button = el(document, "button", "fund-sort", label);
      button.type = "button";
      button.dataset.sort = key;
      button.addEventListener("click", () => {
        Object.assign(ui, nextSort(ui, key));
        draw(`sort:${key}`);
      });
      th.append(button);
      headRow.append(th);
    }
    head.append(headRow);
    const body = el(document, "tbody");
    for (const row of view.rows) {
      const tr = el(document, "tr", row.watched ? "fund-row is-watched" : "fund-row");
      const tickerCell = el(document, "td", "fund-col-ticker");
      tickerCell.append(row.watched ? `★ ${row.ticker}` : (row.ticker || "--"));
      const nameCell = el(document, "td", "fund-col-name");
      nameCell.append(el(document, "span", "fund-name", row.displayName));
      if (!row.ticker && row.cusip) nameCell.append(el(document, "span", "fund-cusip", row.cusip));
      if (row.payoff === "Short") nameCell.append(el(document, "span", "fund-badge", "Short"));
      if (row.is_private) nameCell.append(el(document, "span", "fund-badge", "Private"));
      const valueCell = el(document, "td", "fund-col-value", formatUsd(row.value_usd));
      const pctCell = el(document, "td", "fund-col-pct");
      const bar = el(document, "span", "fund-bar");
      bar.style.width = `${Math.max(2, (Number(row.pct_nav) || 0) / maxPct * 100)}%`;
      bar.setAttribute("aria-hidden", "true");
      pctCell.append(el(document, "span", "fund-pct", formatPct(row.pct_nav)), bar,
        el(document, "span", "fund-value-under", formatUsd(row.value_usd)));
      tr.append(tickerCell, nameCell, valueCell, pctCell);
      body.append(tr);
    }
    table.append(head, body);
    const wrap = el(document, "div", "fund-table-wrap");
    wrap.append(table);
    panel.append(wrap);

    if (!view.filtered && view.total > TOP_N) {
      const toggle = el(document, "button", "button-link fund-toggle", toggleLabel(ui, view.total));
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", String(ui.expanded));
      toggle.setAttribute("aria-controls", "stock-panel-fund");
      toggle.addEventListener("click", () => {
        ui.expanded = !ui.expanded;
        if (!ui.expanded) Object.assign(ui, { sort: "pct", direction: "desc" });
        draw("toggle");
      });
      panel.append(toggle);
    }
    if (focus === "toggle") panel.querySelector(".fund-toggle")?.focus();
    else if (focus === "search") {
      const input = panel.querySelector(".fund-search");
      input?.focus();
      input?.setSelectionRange?.(input.value.length, input.value.length);
    } else if (focus === "watch") panel.querySelector(".fund-watch-toggle input")?.focus();
    else if (focus?.startsWith("sort:")) panel.querySelector(`[data-sort="${focus.slice(5)}"]`)?.focus();
  };

  draw();
  return panel;
}

/** #227: automatic retry delay after the Nth consecutive failed GET /fund load: 5 s doubling, capped at 5 min. */
export function fundRetryDelayMs(attempts) {
  const n = Math.max(1, Math.floor(Number(attempts) || 1));
  return Math.min(5000 * 2 ** (n - 1), 300000);
}
