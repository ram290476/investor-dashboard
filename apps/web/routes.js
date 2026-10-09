const RESEARCH_TICKERS = new Set(["SPCX", "TSLA"]);

export function routeFromPath(pathname) {
  const path = String(pathname || "/");
  if (path === "/" || path === "/index.html" || path === "/auth/callback") {
    return { page: "dashboard" };
  }

  const match = /^\/ticker\/(SPCX|TSLA)\/?$/i.exec(path);
  if (match) return { page: "research", ticker: match[1].toUpperCase() };
  return { page: "not-found" };
}

export function routeStateForPath(pathname, dashboardSelection, selected) {
  const route = routeFromPath(pathname);
  if (route.page === "research") {
    return {
      route,
      dashboardSelection: dashboardSelection || selected,
      selected: route.ticker,
    };
  }
  if (route.page === "dashboard") {
    const ticker = dashboardSelection || selected;
    return { route, dashboardSelection: ticker, selected: ticker };
  }
  return { route, dashboardSelection, selected };
}

export function tickerResearchPath(ticker) {
  const symbol = String(ticker || "").trim().toUpperCase();
  return RESEARCH_TICKERS.has(symbol) ? `/ticker/${symbol}` : null;
}
