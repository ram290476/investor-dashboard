// One price chart panel for every page (#203). The Overview tab (the dashboard chart) and research
// render the full panel — period chips, trend pill, overlays, under-chart lanes, lane
// controls, catalyst filters, crosshair and legend. Fundamentals and KPIs pass stockPanel
// and keep only the quote header. The Analysis tab does not render this panel.

export const CHART_PANEL_PAGES = Object.freeze(["dashboard", "research", "company"]);

const TICKER = /^[A-Z][A-Z0-9.\-]{0,9}$/;

/**
 * Options for the shared panel on one page.
 * companyLink: show "Open company page" (the dashboard only; the company page is the target).
 * stockPanel: Fundamentals and KPIs get only this quote header. The company value "overview"
 * is the full-chart placement, not the Analysis tab id. Overview keeps the dashboard chart.
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
