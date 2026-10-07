// Pure helpers for the Account settings dialog (issue #36). No DOM access, so node --test covers them.
// Limits mirror services/data-jobs/src/functions/prefs_api/prefs_api.py.

export const MAX_PINNED = 6;
export const MAX_TICKERS = 50;
export const SYMBOL_RE = /^[A-Z][A-Z0-9.-]{0,9}$/;
export const PIN_LIMIT_MESSAGE = `Pin limit reached (${MAX_PINNED}). Unpin one first.`;

export const SETTINGS_TABS = [
  { id: "tickers", label: "My tickers" },
  { id: "theme", label: "Theme & display" },
  { id: "profile", label: "Profile & time zone" },
];

export const PALETTES = [
  { id: "green-red", label: "Green / Red" },
  { id: "red-green", label: "Red / Green" },
  { id: "blue-orange", label: "Blue / Orange" },
];

export const TIME_ZONE_PICKS = [
  { id: "America/Los_Angeles", label: "PT" },
  { id: "America/New_York", label: "ET" },
];

function clone(prefs) {
  return {
    ...prefs,
    tickers: [...(prefs.tickers || [])],
    pinned: [...(prefs.pinned || [])],
    display: { ...(prefs.display || {}) },
  };
}

/** "#settings/theme" -> "theme"; unknown tabs fall back to My tickers; other hashes -> null. */
export function parseSettingsHash(hash) {
  const match = /^#settings(?:\/([a-z-]*))?$/.exec(String(hash || ""));
  if (!match) return null;
  return SETTINGS_TABS.some((tab) => tab.id === match[1]) ? match[1] : "tickers";
}

export function settingsHash(tabId) {
  return `#settings/${tabId}`;
}

/** Next tab for the WAI-ARIA tabs keyboard pattern (arrows wrap, Home/End jump). */
export function tabAfterKey(currentId, key) {
  const ids = SETTINGS_TABS.map((tab) => tab.id);
  const index = Math.max(0, ids.indexOf(currentId));
  if (key === "ArrowRight") return ids[(index + 1) % ids.length];
  if (key === "ArrowLeft") return ids[(index - 1 + ids.length) % ids.length];
  if (key === "Home") return ids[0];
  if (key === "End") return ids.at(-1);
  return null;
}

export function validateNewTicker(raw, prefs) {
  const ticker = String(raw || "").trim().toUpperCase();
  if (!ticker) return { error: "Enter a ticker symbol." };
  if (!SYMBOL_RE.test(ticker)) return { error: `${ticker} is not a valid ticker symbol.` };
  if ((prefs.tickers || []).includes(ticker)) return { error: `${ticker} is already in My tickers.` };
  if ((prefs.tickers || []).length >= MAX_TICKERS) return { error: `At most ${MAX_TICKERS} tickers.` };
  return { ticker };
}

/** New tickers go to the end, unpinned. */
export function addTicker(prefs, ticker) {
  const next = clone(prefs);
  next.tickers.push(ticker);
  return next;
}

export function canRemove(prefs) {
  return (prefs.tickers || []).length > 1;
}

/** Remove a ticker (and its pin). Returns what Undo needs to put it back where it was. */
export function removeTicker(prefs, ticker) {
  if (!canRemove(prefs)) return { error: "Keep at least one ticker." };
  const next = clone(prefs);
  const index = next.tickers.indexOf(ticker);
  if (index < 0) return { error: `${ticker} is not in My tickers.` };
  next.tickers.splice(index, 1);
  const pinIndex = next.pinned.indexOf(ticker);
  if (pinIndex >= 0) next.pinned.splice(pinIndex, 1);
  return { prefs: next, undo: { ticker, index, pinIndex } };
}

export function restoreTicker(prefs, undo) {
  const next = clone(prefs);
  if (next.tickers.includes(undo.ticker)) return next;
  next.tickers.splice(Math.min(undo.index, next.tickers.length), 0, undo.ticker);
  if (undo.pinIndex >= 0 && next.pinned.length < MAX_PINNED) {
    next.pinned.splice(Math.min(undo.pinIndex, next.pinned.length), 0, undo.ticker);
  }
  return next;
}

export function togglePin(prefs, ticker) {
  const next = clone(prefs);
  const at = next.pinned.indexOf(ticker);
  if (at >= 0) {
    next.pinned.splice(at, 1);
    return { prefs: next, pinned: false };
  }
  if (next.pinned.length >= MAX_PINNED) return { error: PIN_LIMIT_MESSAGE };
  next.pinned.push(ticker);
  return { prefs: next, pinned: true };
}

/** Move one step up (-1) or down (+1) in tickers order. Null when it can't move. */
export function moveTicker(prefs, ticker, delta) {
  const index = (prefs.tickers || []).indexOf(ticker);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= prefs.tickers.length) return null;
  const next = clone(prefs);
  [next.tickers[index], next.tickers[target]] = [next.tickers[target], next.tickers[index]];
  return { prefs: next, position: target + 1, total: next.tickers.length };
}

/** Header strip order: pinned tickers first in pin order, then the rest in tickers order. */
export function stripOrder(prefs) {
  const tickers = prefs.tickers || [];
  const pinned = (prefs.pinned || []).filter((ticker) => tickers.includes(ticker));
  return { pinned, others: tickers.filter((ticker) => !pinned.includes(ticker)) };
}

/** Keep the selection if it still exists; otherwise pinned[0], then tickers[0]. */
export function fallbackSelection(prefs, selected) {
  if (selected && (prefs.tickers || []).includes(selected)) return selected;
  return prefs.pinned?.[0] || prefs.tickers?.[0] || null;
}

/** Email claim from a Cognito ID token (display only; the API never trusts this). */
export function emailFromIdToken(idToken) {
  try {
    const payload = String(idToken || "").split(".")[1];
    if (!payload) return null;
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(payload.length / 4) * 4, "=");
    const claims = JSON.parse(globalThis.atob(base64));
    return typeof claims.email === "string" ? claims.email : null;
  } catch {
    return null;
  }
}

/** Per-ticker history status for a just-added ticker (#33 adds a richer pipeline status later). */
export function historyState(record, dashboardLoaded) {
  if (!dashboardLoaded) return "unknown";
  return (record?.price_history || []).length ? "ready" : "loading";
}

export function timeZoneLabel(zone) {
  return TIME_ZONE_PICKS.find((pick) => pick.id === zone)?.label || zone;
}

/** Data refresh summary for the account button and menu. */
export function refreshSummary(status) {
  const jobs = (status?.jobs || []).filter((job) => job.status && job.status !== "never_run");
  const ok = jobs.filter((job) => job.status === "ok").length;
  const issues = jobs.filter((job) => job.status === "partial" || job.status === "failed").length;
  const tone = !jobs.length ? "" : issues ? "partial" : "ok";
  const text = !jobs.length ? "no refresh data yet" : `${ok} job${ok === 1 ? "" : "s"} ok${issues ? ` · ${issues} need attention` : ""}`;
  return { ok, issues, tone, text };
}
