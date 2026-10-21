import { ANALYSIS_TAB_ID, OVERVIEW_TAB_ID, canonicalStockTab } from "./routes.js";

// First stock-page load in a browser tab.
// sessionStorage (not localStorage) marks that this tab has already loaded, so a
// refresh can keep the tab named in the URL. A new tab has empty sessionStorage
// and lands on Overview again. Catalyst markers are not part of that flag:
// they start cleared and are rebuilt empty on every document load.

export const STOCK_SESSION_KEY = "investor-dashboard.stock-session";

/** Markers off, nothing selected. A page refresh builds this again from scratch. */
export function freshCatalystSelection() {
  return { categories: [], focusId: null };
}

/**
 * Tab decision for a stock or dashboard route.
 * The first load of a tab session is Overview (tab id `summary`), even when the URL names another tab.
 * After the session flag is set, a stock URL keeps its tab and a bare dashboard
 * route still opens Overview. A bare stock hash stays on Analysis (tab id `analysis`).
 * Saved `trends` opens Overview. Saved `overview` and `technical` open Analysis.
 * The /ticker research route and other routes do not consume the first-load flag.
 * A saved `deep-dive` tab id is the Research tab.
 */
export function sessionStockLanding({ sessionStarted = false, hashTab = "", routePage = "" } = {}) {
  const page = String(routePage || "");
  const stockSurface = page === "stock" || page === "dashboard";
  const tab = canonicalStockTab(hashTab);
  if (!stockSurface) return { tab, markSession: false, rewriteLanding: false };
  if (!sessionStarted) return { tab: OVERVIEW_TAB_ID, markSession: true, rewriteLanding: true };
  if (page === "dashboard") return { tab: tab || OVERVIEW_TAB_ID, markSession: false, rewriteLanding: false };
  return { tab: tab || ANALYSIS_TAB_ID, markSession: false, rewriteLanding: false };
}
