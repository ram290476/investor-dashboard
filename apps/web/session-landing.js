// First stock-page load in a browser tab.
// sessionStorage (not localStorage) marks that this tab has already loaded, so a
// refresh can keep the tab named in the URL. A new tab has empty sessionStorage
// and lands on Trends again. Catalyst markers are not part of that flag:
// they start cleared and are rebuilt empty on every document load.

export const STOCK_SESSION_KEY = "investor-dashboard.stock-session";

/** Markers off, nothing selected. A page refresh builds this again from scratch. */
export function freshCatalystSelection() {
  return { categories: [], focusId: null };
}

/**
 * Tab decision for a stock or dashboard route.
 * The first load of a tab session is Trends, even when the URL names another tab.
 * After the session flag is set, a stock URL keeps its tab and a bare dashboard
 * route still opens Trends. A bare stock hash stays on Technical.
 * Research and other routes do not consume the first-load flag.
 */
export function sessionStockLanding({ sessionStarted = false, hashTab = "", routePage = "" } = {}) {
  const page = String(routePage || "");
  const stockSurface = page === "stock" || page === "dashboard";
  const tab = String(hashTab || "");
  if (!stockSurface) return { tab, markSession: false, rewriteLanding: false };
  if (!sessionStarted) return { tab: "trends", markSession: true, rewriteLanding: true };
  if (page === "dashboard") return { tab: tab || "trends", markSession: false, rewriteLanding: false };
  return { tab: tab || "overview", markSession: false, rewriteLanding: false };
}
