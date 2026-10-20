// One price chart panel for every page (#203). The dashboard, research and company
// pages render the same panel — period chips, trend pill, overlays, under-chart lanes,
// lane controls, catalyst filters, crosshair and legend. Only these page options differ.

export const CHART_PANEL_PAGES = Object.freeze(["dashboard", "research", "company"]);

const TICKER = /^[A-Z][A-Z0-9.\-]{0,9}$/;

/**
 * Options for the shared panel on one page.
 * companyLink: show "Open company page" (the dashboard only; the company page is the target).
 * stockPanel: company-page tab the quote header belongs to. Callers set Fundamentals and KPIs.
 * mobilePanel / id: dashboard phone tab wiring.
 */
export function chartPanelOptions(page, ticker) {
  const context = CHART_PANEL_PAGES.includes(page) ? page : "dashboard";
  const symbol = String(ticker || "").trim().toUpperCase();
  return Object.freeze({
    page: context,
    ticker: TICKER.test(symbol) ? symbol : "",
    companyLink: context === "dashboard",
    stockPanel: context === "company" ? "overview" : "",
    mobilePanel: context === "dashboard" ? "chart" : "",
    id: context === "dashboard" ? "mobile-panel-chart" : "",
  });
}
