const TICKER = /^[A-Za-z][A-Za-z0-9.\-]{0,9}$/;
const SECTION_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$/;
// Optional `/deep-dive/<section>` stays inside the company route. A bare `#section`
// is not a stock hash: the hash is the router, and replacing `#stock/<T>` leaves the page.
const STOCK_HASH = /^#stock\/([A-Za-z][A-Za-z0-9.\-]{0,9})(?:\/deep-dive\/([A-Za-z0-9][A-Za-z0-9_-]{0,80}))?$/;

export function parseStockHash(hash) {
  const match = STOCK_HASH.exec(String(hash || ""));
  return match ? match[1].toUpperCase() : null;
}

/** Bookmark id in `#stock/<T>/deep-dive/<section>`, or "" when the hash is only the company route. */
export function parseStockSection(hash) {
  const match = STOCK_HASH.exec(String(hash || ""));
  return match?.[2] || "";
}

export function stockHash(ticker, sectionId = "") {
  const symbol = String(ticker || "").trim().toUpperCase();
  if (!TICKER.test(symbol)) return null;
  const section = String(sectionId || "").trim();
  if (!section || !SECTION_ID.test(section)) return `#stock/${symbol}`;
  return `#stock/${symbol}/deep-dive/${section}`;
}

export function routeFromPath(pathname) {
  const path = String(pathname || "/");
  if (path === "/" || path === "/index.html" || path === "/auth/callback") {
    return { page: "dashboard" };
  }

  const stock = /^\/stock\/([A-Za-z][A-Za-z0-9.\-]{0,9})\/?$/i.exec(path);
  if (stock) return { page: "stock", ticker: stock[1].toUpperCase() };

  const research = /^\/ticker\/([A-Za-z][A-Za-z0-9.\-]{0,9})\/?$/i.exec(path);
  if (research) return { page: "research", ticker: research[1].toUpperCase() };
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
  return TICKER.test(symbol) ? `/ticker/${symbol}` : null;
}
