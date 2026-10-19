const TICKER = /^[A-Za-z][A-Za-z0-9.\-]{0,9}$/;
const SECTION_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$/;
const STOCK_TABS = "overview|kpis|fundamentals|calls|deep-dive";
// Optional `/<tab>` and `/<tab>/<section>` stay inside the company route.
// A bare `#section` is not a stock hash: the hash is the router, and replacing
// `#stock/<T>` leaves the page.
const STOCK_HASH = new RegExp(
  `^#stock\\/([A-Za-z][A-Za-z0-9.\\-]{0,9})(?:\\/(${STOCK_TABS})(?:\\/([A-Za-z0-9][A-Za-z0-9_-]{0,80}))?)?$`,
);

function stockMatch(hash) {
  return STOCK_HASH.exec(String(hash || ""));
}

export function parseStockHash(hash) {
  const match = stockMatch(hash);
  return match ? match[1].toUpperCase() : null;
}

/** Company tab in `#stock/<T>/<tab>`, or "" when the hash is only the company route. */
export function parseStockTab(hash) {
  return stockMatch(hash)?.[2] || "";
}

/** Bookmark id in `#stock/<T>/deep-dive/<section>`, or "" for a company-tab hash. */
export function parseStockSection(hash) {
  const match = stockMatch(hash);
  return match?.[2] === "deep-dive" ? (match[3] || "") : "";
}

/** Bookmark id in `#stock/<T>/<tab>/<section>` for Overview, KPIs, Fundamentals, and Calls. */
export function parseStockPanel(hash) {
  const match = stockMatch(hash);
  if (!match?.[2] || match[2] === "deep-dive") return "";
  return match[3] || "";
}

export function stockHash(ticker, sectionId = "") {
  const symbol = String(ticker || "").trim().toUpperCase();
  if (!TICKER.test(symbol)) return null;
  const section = String(sectionId || "").trim();
  if (!section || !SECTION_ID.test(section)) return `#stock/${symbol}`;
  return `#stock/${symbol}/deep-dive/${section}`;
}

/** `#stock/<T>` for Overview, `#stock/<T>/<tab>` for the other company tabs. */
export function stockTabHash(ticker, tab = "") {
  const symbol = String(ticker || "").trim().toUpperCase();
  if (!TICKER.test(symbol)) return null;
  const id = String(tab || "").trim();
  if (!id || id === "overview" || !STOCK_TABS.split("|").includes(id)) return `#stock/${symbol}`;
  return `#stock/${symbol}/${id}`;
}

/** Section bookmark on a company tab. Deep dive bookmarks keep `stockHash`. */
export function stockPanelHash(ticker, tab, sectionId = "") {
  const id = String(tab || "overview").trim();
  if (id === "deep-dive") return stockHash(ticker, sectionId);
  const symbol = String(ticker || "").trim().toUpperCase();
  if (!TICKER.test(symbol)) return null;
  const section = String(sectionId || "").trim();
  if (!section || !SECTION_ID.test(section)) return stockTabHash(symbol, id);
  const prefix = id === "overview" ? "overview" : id;
  if (!STOCK_TABS.split("|").includes(prefix)) return `#stock/${symbol}`;
  return `#stock/${symbol}/${prefix}/${section}`;
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
