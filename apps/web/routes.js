const TICKER = /^[A-Za-z][A-Za-z0-9.\-]{0,9}$/;
const SECTION_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$/;
// Calls stays in the matcher so old bookmarks can be redirected. It is not a tab.
// Admin is a real tab for signed-in admins. The page refuses it for everyone else.
// Legacy tab ids stay in the matcher. canonicalStockTab maps them on read.
// `summary` is Overview (formerly trends). `analysis` is Analysis (formerly Technical).
export const OVERVIEW_TAB_ID = "summary";
export const ANALYSIS_TAB_ID = "analysis";
export const FUND_TAB_ID = "fund";
export const RESEARCH_TAB_ID = "research";
const LEGACY_RESEARCH_TAB = "deep-dive";
const LEGACY_STOCK_TABS = Object.freeze({
  trends: OVERVIEW_TAB_ID,
  overview: ANALYSIS_TAB_ID,
  technical: ANALYSIS_TAB_ID,
  [LEGACY_RESEARCH_TAB]: RESEARCH_TAB_ID,
});
const STOCK_TABS = "summary|analysis|trends|overview|technical|fundamentals|kpis|research|deep-dive|calls|admin|fund";
const LIVE_TABS = [OVERVIEW_TAB_ID, ANALYSIS_TAB_ID, FUND_TAB_ID, "fundamentals", "kpis", RESEARCH_TAB_ID, "admin"];

/**
 * Live tab id for a saved tab id or hash segment.
 * `trends` is Overview. `overview` (pre-#281) and `technical` are Analysis.
 * `deep-dive` is Research. A bare company hash has no tab id; callers map that separately.
 */
export function canonicalStockTab(tab) {
  const id = String(tab || "").trim();
  return LEGACY_STOCK_TABS[id] || id;
}

function isResearchTab(tab) {
  const id = String(tab || "").trim();
  return id === RESEARCH_TAB_ID || id === LEGACY_RESEARCH_TAB;
}

// Bare hashes the main page used for its panels. Each one lands on Overview (id `summary`).
// `#technical` is that old section bookmark, not the Analysis tab. `#stock/<T>/technical` is Analysis.
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
  return canonicalStockTab(stockMatch(hash)?.[2] || "");
}

/** Bookmark id in `#stock/<T>/research/<section>`, or "" for a company-tab hash. */
export function parseStockSection(hash) {
  const match = stockMatch(hash);
  return isResearchTab(match?.[2]) ? (match[3] || "") : "";
}

/** Bookmark id in `#stock/<T>/<tab>/<section>` for Overview, Analysis, Fund, Fundamentals, KPIs, and Admin. */
export function parseStockPanel(hash) {
  const match = stockMatch(hash);
  if (!match?.[2] || isResearchTab(match[2]) || match[2] === "calls") return "";
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
 * Replacement hash for a legacy Calls, quarterly-notes, or main-page bookmark.
 * "" when the hash should be left alone. Calls and a removed quarterly-notes bookmark land on Analysis.
 * A bare main-page hash needs the selected ticker. Legacy tab hashes are left in the address bar.
 */
export function redirectStockHash(hash, ticker = "") {
  const match = stockMatch(hash);
  const tab = match?.[2] || "";
  const section = match?.[3] || "";
  if (tab === "calls" || (section === "quarterly-notes" && !isResearchTab(tab))) {
    return stockTabHash(match[1], ANALYSIS_TAB_ID);
  }
  const sectionId = legacyMainSection(hash);
  if (!sectionId) return "";
  return stockPanelHash(ticker, OVERVIEW_TAB_ID, sectionId) || "";
}

export function stockHash(ticker, sectionId = "") {
  const symbol = String(ticker || "").trim().toUpperCase();
  if (!TICKER.test(symbol)) return null;
  const section = String(sectionId || "").trim();
  if (!section || !SECTION_ID.test(section)) return `#stock/${symbol}`;
  return `#stock/${symbol}/${RESEARCH_TAB_ID}/${section}`;
}

/** `#stock/<T>/<tab>` for a live tab. A bare `#stock/<T>` is the legacy Analysis hash. */
export function stockTabHash(ticker, tab = "") {
  const symbol = String(ticker || "").trim().toUpperCase();
  if (!TICKER.test(symbol)) return null;
  const id = canonicalStockTab(tab);
  if (id === "calls") return `#stock/${symbol}/${ANALYSIS_TAB_ID}`;
  if (!id || !LIVE_TABS.includes(id)) return `#stock/${symbol}`;
  return `#stock/${symbol}/${id}`;
}

/** Section bookmark on a company tab. Research bookmarks keep `stockHash`. */
export function stockPanelHash(ticker, tab, sectionId = "") {
  const id = canonicalStockTab(tab || ANALYSIS_TAB_ID);
  if (id === RESEARCH_TAB_ID) return stockHash(ticker, sectionId);
  const symbol = String(ticker || "").trim().toUpperCase();
  if (!TICKER.test(symbol)) return null;
  if (id === "calls") return stockTabHash(symbol, ANALYSIS_TAB_ID);
  const section = String(sectionId || "").trim();
  if (!section || !SECTION_ID.test(section)) return stockTabHash(symbol, id);
  if (!LIVE_TABS.includes(id)) return `#stock/${symbol}`;
  return `#stock/${symbol}/${id}/${section}`;
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
