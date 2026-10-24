// Admin tab in Account settings (issue 223): reset the write-once is_fund flag for a watchlist
// ticker via POST /admin/fund-flag/<T>/reset. Lives outside the company page on purpose, so it
// stays reachable after a reset. The caller decides who is an admin (same check as the metric
// Approve buttons; never the demo account).

export const RESET_LABEL = "Reset fund flag";
export const NOT_STAMPED = "Not stamped";
export const TYPE_LOADING = "Loading…";

export function typeLabel(isFund) {
  if (isFund === true) return "Fund";
  if (isFund === false) return "Stock";
  return NOT_STAMPED;
}

/**
 * One Admin Type cell. While the dashboard is still loading, the type is unknown.
 * A missing is_fund falls back to instrument_kind === "fund" so a known fund is not "Not stamped".
 * The write-once stamp still wins when it is present.
 */
export function adminFundFlagRow(ticker, record, dashboardState = "ready") {
  if (dashboardState === "loading") return { ticker, loading: true };
  if (typeof record?.is_fund === "boolean") return { ticker, is_fund: record.is_fund };
  if (record?.instrument_kind === "fund") return { ticker, is_fund: true };
  return { ticker };
}

/** Rows for the picker. A ticker reset in this session shows Not stamped until the next collection. */
export function flagRows(rows = [], outcomes = {}) {
  return rows.map((row) => {
    const cleared = outcomes[row.ticker]?.state === "done" || outcomes[row.ticker]?.state === "missing";
    if (row.loading && !cleared) return { ticker: row.ticker, loading: true, type: TYPE_LOADING };
    const isFund = cleared ? undefined : row.is_fund;
    return { ticker: row.ticker, is_fund: isFund, type: typeLabel(isFund) };
  });
}

export function resetConfirmText(ticker) {
  return `Clear the stored fund flag for ${ticker}? The next collection will re-stamp it.`;
}

export function resetOutcome(error) {
  if (!error) return { state: "done" };
  if (error.status === 404) return { state: "missing" };
  if (error.status === 403) return { state: "forbidden" };
  return { state: "error", message: error.message };
}

export function resetStatus(ticker, outcome) {
  switch (outcome?.state) {
    case "saving": return { tone: "", text: `Resetting ${ticker}…` };
    case "done": return { tone: "ok", text: `Fund flag cleared for ${ticker}. The next collection will re-stamp it.` };
    case "missing": return { tone: "warn", text: `No flag stored for ${ticker}.` };
    case "forbidden": return { tone: "error", text: "Only an admin can reset the fund flag." };
    case "error": return { tone: "error", text: outcome.message || `The fund flag for ${ticker} could not be reset.` };
    default: return null;
  }
}

function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function typeTone(row) {
  if (row.loading) return "loading";
  if (row.type === "Fund") return "fund";
  if (row.type === "Stock") return "stock";
  return "none";
}

function paintStatus(section, ticker, outcome) {
  const actions = section.querySelector(".ffa-actions");
  if (!actions) return;
  const view = resetStatus(ticker, outcome);
  const existing = section.querySelector(".ffa-status");
  if (!view) {
    existing?.remove();
    return;
  }
  const line = existing || el(section.ownerDocument, "p");
  line.className = `ffa-status${view.tone ? ` is-${view.tone}` : ""}`;
  line.textContent = view.text;
  line.setAttribute("role", view.tone === "error" ? "alert" : "status");
  if (!existing) actions.append(line);
}

/**
 * Move the selection without rebuilding the table. The settings panel and the
 * table scroller keep their scroll positions because the nodes stay put.
 */
export function selectFundFlagRow(section, ticker, outcome) {
  if (!section || !ticker) return;
  for (const tr of section.querySelectorAll(".ffa-row")) {
    const on = tr.dataset.ticker === ticker;
    tr.classList.toggle("is-selected", on);
    if (on) tr.setAttribute("aria-selected", "true");
    else tr.removeAttribute("aria-selected");
    const radio = tr.querySelector('input[type="radio"]');
    if (radio) radio.checked = on;
  }
  const button = section.querySelector(".ffa-reset");
  if (button) {
    button.textContent = `${RESET_LABEL} for ${ticker}`;
    button.dataset.ticker = ticker;
    button.disabled = outcome?.state === "saving";
  }
  paintStatus(section, ticker, outcome);
}

/**
 * Refresh Type cells from the latest rows without rebuilding the table.
 * Returns false when the ticker set changed, so the caller can rebuild and
 * restore scroll. A cleared stamp is left as paintFundFlagOutcome wrote it.
 */
export function syncFundFlagTable(section, sourceRows, outcomes = {}) {
  if (!section) return false;
  const rows = flagRows(sourceRows, outcomes);
  const existing = [...section.querySelectorAll(".ffa-row")];
  if (!rows.length && !existing.length) return true;
  if (existing.length !== rows.length) return false;
  for (let index = 0; index < rows.length; index += 1) {
    if (existing[index].dataset.ticker !== rows[index].ticker) return false;
  }
  for (const row of rows) {
    const tr = existing.find((item) => item.dataset.ticker === row.ticker);
    const type = tr?.querySelector(".ffa-col-type");
    if (!type) return false;
    const cleared = outcomes[row.ticker]?.state === "done" || outcomes[row.ticker]?.state === "missing";
    if (cleared) continue;
    type.textContent = row.type;
    type.className = `ffa-col-type ffa-type-${typeTone(row)}`;
    const radio = tr.querySelector('input[type="radio"]');
    if (radio) radio.setAttribute("aria-label", `${row.ticker}, ${row.type}`);
  }
  return true;
}

/** Status line and, once a reset clears the stamp, that row's Type cell. The table stays. */
export function paintFundFlagOutcome(section, ticker, outcome) {
  if (!section || !ticker) return;
  const cleared = outcome?.state === "done" || outcome?.state === "missing";
  const tr = [...section.querySelectorAll(".ffa-row")].find((row) => row.dataset.ticker === ticker);
  if (tr && cleared) {
    const type = tr.querySelector(".ffa-col-type");
    if (type) {
      type.textContent = NOT_STAMPED;
      type.className = "ffa-col-type ffa-type-none";
    }
    const radio = tr.querySelector('input[type="radio"]');
    if (radio) radio.setAttribute("aria-label", `${ticker}, ${NOT_STAMPED}`);
  }
  const button = section.querySelector(".ffa-reset");
  if (button?.dataset.ticker === ticker) button.disabled = outcome?.state === "saving";
  const selected = [...section.querySelectorAll(".ffa-row")].find((row) => row.classList.contains("is-selected"));
  if (!selected || selected.dataset.ticker === ticker) paintStatus(section, ticker, outcome);
}

/**
 * state: { rows: [{ticker, is_fund}], selected, outcomes: {T: {state}} }
 * handlers: { onSelect(ticker), onReset(ticker) }
 */
export function renderFundFlagAdmin(state, document = globalThis.document, handlers = {}) {
  const section = el(document, "section", "settings-section fund-flag-admin");
  section.append(el(document, "h3", "", "Fund flag"));
  section.append(el(document, "p", "settings-help",
    "Each ticker's fund/stock type is stamped once by the first collection. Reset clears it so the next collection stamps it again."));
  const rows = flagRows(state.rows, state.outcomes);
  if (!rows.length) {
    section.append(el(document, "p", "data-state", "Add tickers to your watchlist to manage their fund flag."));
    return section;
  }
  const selected = rows.some((r) => r.ticker === state.selected) ? state.selected : rows[0].ticker;
  const table = el(document, "table", "fund-flag-table");
  const head = el(document, "thead");
  const headRow = el(document, "tr");
  for (const [label, cls] of [["", "ffa-col-pick"], ["Ticker", "ffa-col-ticker"], ["Type", "ffa-col-type"]]) {
    const th = el(document, "th", cls, label);
    th.scope = "col";
    headRow.append(th);
  }
  head.append(headRow);
  const body = el(document, "tbody");
  for (const row of rows) {
    const tr = el(document, "tr", row.ticker === selected ? "ffa-row is-selected" : "ffa-row");
    tr.dataset.ticker = row.ticker;
    if (row.ticker === selected) tr.setAttribute("aria-selected", "true");
    const pick = el(document, "td", "ffa-col-pick");
    const radio = el(document, "input");
    radio.type = "radio";
    radio.name = "fund-flag-ticker";
    radio.value = row.ticker;
    radio.checked = row.ticker === selected;
    radio.dataset.key = `fund-flag-${row.ticker}`;
    radio.setAttribute("aria-label", `${row.ticker}, ${row.type}`);
    radio.addEventListener("change", () => {
      // Update the existing rows. Rebuilding the section would drop the table and panel scroll.
      selectFundFlagRow(section, row.ticker, state.outcomes?.[row.ticker]);
      handlers.onSelect?.(row.ticker);
    });
    pick.append(radio);
    const type = el(document, "td", `ffa-col-type ffa-type-${typeTone(row)}`, row.type);
    tr.append(pick, el(document, "td", "ffa-col-ticker", row.ticker), type);
    body.append(tr);
  }
  table.append(head, body);
  const wrap = el(document, "div", "fund-flag-table-wrap");
  wrap.append(table);
  section.append(wrap);

  const actions = el(document, "div", "ffa-actions");
  const outcome = state.outcomes?.[selected];
  const button = el(document, "button", "button-link ffa-reset", `${RESET_LABEL} for ${selected}`);
  button.type = "button";
  button.dataset.key = "fund-flag-reset";
  button.dataset.ticker = selected;
  button.disabled = outcome?.state === "saving";
  button.addEventListener("click", () => handlers.onReset?.(button.dataset.ticker));
  actions.append(button);
  const status = resetStatus(selected, outcome);
  if (status) {
    const line = el(document, "p", `ffa-status${status.tone ? ` is-${status.tone}` : ""}`, status.text);
    line.setAttribute("role", status.tone === "error" ? "alert" : "status");
    actions.append(line);
  }
  section.append(actions);
  return section;
}
