const TICKER = /^[A-Za-z][A-Za-z0-9.\-]{0,9}$/;
const SECTION_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$/;
// Calls stays in the matcher so old bookmarks can be redirected. It is not a tab.
// Admin is a real tab for signed-in admins. The page refuses it for everyone else.
const STOCK_TABS = "trends|overview|fundamentals|kpis|deep-dive|calls|admin";
const LIVE_TABS = ["trends", "overview", "fundamentals", "kpis", "deep-dive", "admin"];

// Bare hashes the main page used for its panels. Each one lands on the Trends section.
export const MAIN_PAGE_SECTIONS = Object.freeze({
  chart: "price",
  price: "price",
  "mobile-panel-chart": "price",
  technical: "technical",
  "technical-summary": "technical",
  signals: "drivers",
  drivers: "drivers",
  "mobile-panel-signals": "drivers",
  calendar: "catalyst-calendar",
  "catalyst-calendar": "catalyst-calendar",
  "mobile-panel-calendar": "catalyst-calendar",
  more: "news",
  news: "news",
  "mobile-panel-more": "news",
  filings: "filings",
  "filings-events": "filings",
  rates: "rates",
  inflation: "inflation",
  correlation: "correlation",
  "market-comparison": "market-comparison",
  "moving-averages": "moving-averages",
  volatility: "volatility",
  "dollar-oil": "dollar-oil",
  tariffs: "tariffs",
  contracts: "contracts",
  "about-data": "about-data",
  about: "about-data",
});
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

/** Bookmark id in `#stock/<T>/<tab>/<section>` for Trends, Overview, Fundamentals, KPIs, and Admin. */
export function parseStockPanel(hash) {
  const match = stockMatch(hash);
  if (!match?.[2] || match[2] === "deep-dive" || match[2] === "calls") return "";
  return match[3] || "";
}

/** Section id for a bare main-page hash such as `#news` or `#rates`. "" for stock and settings hashes. */
export function legacyMainSection(hash) {
  const match = /^#([A-Za-z0-9][A-Za-z0-9_-]{0,80})$/.exec(String(hash || ""));
  if (!match) return "";
  const id = match[1].toLowerCase();
  if (id === "settings" || id === "stock") return "";
  return MAIN_PAGE_SECTIONS[id] || "";
}

/**
 * Replacement hash for a legacy Calls or main-page bookmark.
 * "" when the hash should be left alone. Calls always lands on Overview quarterly notes.
 * A bare main-page hash needs the selected ticker.
 */
export function redirectStockHash(hash, ticker = "") {
  const match = stockMatch(hash);
  if (match?.[2] === "calls") return stockPanelHash(match[1], "overview", "quarterly-notes");
  const section = legacyMainSection(hash);
  if (!section) return "";
  return stockPanelHash(ticker, "trends", section) || "";
}

export function stockHash(ticker, sectionId = "") {
  const symbol = String(ticker || "").trim().toUpperCase();
  if (!TICKER.test(symbol)) return null;
  const section = String(sectionId || "").trim();
  if (!section || !SECTION_ID.test(section)) return `#stock/${symbol}`;
  return `#stock/${symbol}/deep-dive/${section}`;
}

/** `#stock/<T>` for Overview, `#stock/<T>/<tab>` for Trends and the other company tabs. */
export function stockTabHash(ticker, tab = "") {
  const symbol = String(ticker || "").trim().toUpperCase();
  if (!TICKER.test(symbol)) return null;
  const id = String(tab || "").trim();
  if (id === "calls") return stockPanelHash(symbol, "overview", "quarterly-notes");
  if (!id || id === "overview" || !LIVE_TABS.includes(id)) return `#stock/${symbol}`;
  return `#stock/${symbol}/${id}`;
}

/** Section bookmark on a company tab. Deep dive bookmarks keep `stockHash`. */
export function stockPanelHash(ticker, tab, sectionId = "") {
  const id = String(tab || "overview").trim();
  if (id === "deep-dive") return stockHash(ticker, sectionId);
  const symbol = String(ticker || "").trim().toUpperCase();
  if (!TICKER.test(symbol)) return null;
  if (id === "calls") return stockPanelHash(symbol, "overview", "quarterly-notes");
  const section = String(sectionId || "").trim();
  if (!section || !SECTION_ID.test(section)) return stockTabHash(symbol, id);
  const prefix = id === "overview" ? "overview" : id;
  if (!LIVE_TABS.includes(prefix)) return `#stock/${symbol}`;
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
