const RESEARCH_TICKERS = new Set(["SPCX", "TSLA"]);
const TICKER = /^[A-Za-z][A-Za-z0-9.\-]{0,9}$/;

export function parseStockHash(hash) {
  const match = /^#stock\/([A-Za-z][A-Za-z0-9.\-]{0,9})$/.exec(String(hash || ""));
  return match ? match[1].toUpperCase() : null;
}

export function stockHash(ticker) {
  const symbol = String(ticker || "").trim().toUpperCase();
  return TICKER.test(symbol) ? `#stock/${symbol}` : null;
}

export function routeFromPath(pathname) {
  const path = String(pathname || "/");
  if (path === "/" || path === "/index.html" || path === "/auth/callback") {
    return { page: "dashboard" };
  }

  const stock = /^\/stock\/([A-Za-z][A-Za-z0-9.\-]{0,9})\/?$/i.exec(path);
  if (stock) return { page: "stock", ticker: stock[1].toUpperCase() };

  const match = /^\/ticker\/(SPCX|TSLA)\/?$/i.exec(path);
  if (match) return { page: "research", ticker: match[1].toUpperCase() };
  return { page: "not-found" };
}

export function routeFromLocation(pathname, hash) {
  const fromHash = parseStockHash(hash);
  if (fromHash) return { page: "stock", ticker: fromHash };
  return routeFromPath(pathname);
}

export function routeStateForPath(pathname, dashboardSelection, selected) {
  return routeStateForLocation(pathname, "", dashboardSelection, selected);
}

export function routeStateForLocation(pathname, hash, dashboardSelection, selected) {
  const route = routeFromLocation(pathname, hash);
  if (route.page === "research" || route.page === "stock") {
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
