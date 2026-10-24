// Overview right-hand column (macro signals, news, filings).
// The choice is a layout preference, not a per-ticker setting.

export const OVERVIEW_SIDE_KEY = "investor-dashboard.overview-side";

export function readOverviewSideCollapsed(storage) {
  try {
    return storage?.getItem(OVERVIEW_SIDE_KEY) === "collapsed";
  } catch {
    return false;
  }
}

export function writeOverviewSideCollapsed(storage, collapsed) {
  try {
    storage?.setItem(OVERVIEW_SIDE_KEY, collapsed ? "collapsed" : "expanded");
  } catch {
    // Storage can be unavailable. The choice still applies until reload.
  }
}

export function applyOverviewSideColumn(layout, side, button, collapsed) {
  const closed = Boolean(collapsed);
  if (layout?.dataset) layout.dataset.sideCollapsed = closed ? "true" : "false";
  if (side) {
    if (!side.id) side.id = "overview-side-column";
    side.hidden = closed;
  }
  if (!button) return;
  button.type = "button";
  button.setAttribute("aria-expanded", closed ? "false" : "true");
  button.setAttribute("aria-controls", side?.id || "overview-side-column");
  const label = closed ? "Show macro signals" : "Hide macro signals";
  button.textContent = label;
  button.setAttribute("aria-label", closed ? "Show macro signals column" : "Hide macro signals column");
}

/** The price chart is width 100% of the primary column. Flush layout after the grid changes. */
export function resizeOverviewCharts(root, view = globalThis) {
  const run = () => {
    const charts = typeof root?.querySelectorAll === "function"
      ? root.querySelectorAll(".price-chart, .lane-chart")
      : [];
    for (const chart of charts) {
      if (chart?.style) chart.style.width = "100%";
      if (typeof chart?.getBoundingClientRect === "function") chart.getBoundingClientRect();
    }
    if (typeof view?.dispatchEvent === "function" && typeof view.Event === "function") {
      view.dispatchEvent(new view.Event("resize"));
    }
  };
  if (typeof view?.requestAnimationFrame === "function") view.requestAnimationFrame(run);
  else run();
}
