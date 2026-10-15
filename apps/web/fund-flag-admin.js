// Admin tab in Account settings (issue 223): reset the write-once is_fund flag for a watchlist
// ticker via POST /admin/fund-flag/<T>/reset. Lives outside the company page on purpose, so it
// stays reachable after a reset. The caller decides who is an admin (same check as the metric
// Approve buttons; never the demo account).

export const RESET_LABEL = "Reset fund flag";
export const NOT_STAMPED = "Not stamped";

export function typeLabel(isFund) {
  if (isFund === true) return "Fund";
  if (isFund === false) return "Stock";
  return NOT_STAMPED;
}

/** Rows for the picker. A ticker reset in this session shows Not stamped until the next collection. */
export function flagRows(rows = [], outcomes = {}) {
  return rows.map((row) => {
    const cleared = outcomes[row.ticker]?.state === "done" || outcomes[row.ticker]?.state === "missing";
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
    const pick = el(document, "td", "ffa-col-pick");
    const radio = el(document, "input");
    radio.type = "radio";
    radio.name = "fund-flag-ticker";
    radio.value = row.ticker;
    radio.checked = row.ticker === selected;
    radio.dataset.key = `fund-flag-${row.ticker}`;
    radio.setAttribute("aria-label", `${row.ticker}, ${row.type}`);
    radio.addEventListener("change", () => handlers.onSelect?.(row.ticker));
    pick.append(radio);
    const type = el(document, "td", `ffa-col-type ffa-type-${row.type === NOT_STAMPED ? "none" : row.type.toLowerCase()}`, row.type);
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
  button.disabled = outcome?.state === "saving";
  button.addEventListener("click", () => handlers.onReset?.(selected));
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
