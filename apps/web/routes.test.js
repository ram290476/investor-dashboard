import assert from "node:assert/strict";
import test from "node:test";
import { legacyMainSection, parseStockHash, parseStockPanel, parseStockSection, parseStockTab, redirectStockHash, routeFromLocation, routeFromPath, routeStateForLocation, routeStateForPath, stockHash, stockPanelHash, stockTabHash, tickerResearchPath } from "./routes.js";

test("dashboard and Cognito callback paths resolve to the dashboard", () => {
  assert.deepEqual(routeFromPath("/"), { page: "dashboard" });
  assert.deepEqual(routeFromPath("/index.html"), { page: "dashboard" });
  assert.deepEqual(routeFromPath("/auth/callback"), { page: "dashboard" });
});

test("any watchlist ticker has a research path", () => {
  assert.deepEqual(routeFromPath("/ticker/TSLA"), { page: "research", ticker: "TSLA" });
  assert.deepEqual(routeFromPath("/ticker/SPCX/"), { page: "research", ticker: "SPCX" });
  assert.deepEqual(routeFromPath("/ticker/tsla"), { page: "research", ticker: "TSLA" });
  assert.deepEqual(routeFromPath("/ticker/NVDA"), { page: "research", ticker: "NVDA" });
  assert.deepEqual(routeFromPath("/ticker/brk.b"), { page: "research", ticker: "BRK.B" });
  assert.equal(tickerResearchPath("tsla"), "/ticker/TSLA");
  assert.equal(tickerResearchPath("SPCX"), "/ticker/SPCX");
  assert.equal(tickerResearchPath("NVDA"), "/ticker/NVDA");
  assert.equal(tickerResearchPath(""), null);
  assert.equal(tickerResearchPath("has space"), null);
});

test("unsupported paths do not fall back to a different ticker", () => {
  assert.deepEqual(routeFromPath("/research/TSLA"), { page: "not-found" });
  assert.deepEqual(routeFromPath("/ticker/"), { page: "not-found" });
});

test("stock pages open from a hash or a /stock path for any watchlist ticker", () => {
  assert.equal(parseStockHash("#stock/tsla"), "TSLA");
  assert.equal(parseStockHash("#settings/theme"), null);
  assert.equal(stockHash("nvda"), "#stock/NVDA");
  assert.deepEqual(routeFromLocation("/", "#stock/TSLA"), { page: "stock", ticker: "TSLA" });
  assert.deepEqual(routeFromLocation("/stock/nvda", ""), { page: "stock", ticker: "NVDA" });
  assert.deepEqual(routeFromLocation("/ticker/TSLA", ""), { page: "research", ticker: "TSLA" });
  assert.deepEqual(
    routeStateForLocation("/", "#stock/AAPL", "SPY", "QQQ"),
    {
      route: { page: "stock", ticker: "AAPL" },
      dashboardSelection: "SPY",
      selected: "AAPL",
    },
  );
});

test("a deep-dive bookmark hash stays on that company's route", () => {
  assert.equal(parseStockHash("#stock/NVDA/deep-dive/forecast"), "NVDA");
  assert.equal(parseStockSection("#stock/nvda/deep-dive/forecast"), "forecast");
  assert.equal(parseStockSection("#stock/AAPL"), "");
  assert.equal(parseStockHash("#deep-dive-forecast"), null);
  assert.equal(parseStockSection("#deep-dive-forecast"), "");
  assert.equal(stockHash("abcd", "scoreboard"), "#stock/ABCD/deep-dive/scoreboard");
  assert.equal(stockHash("ABCD", ""), "#stock/ABCD");
  assert.equal(stockHash("ABCD", "../overview"), "#stock/ABCD");
  assert.deepEqual(routeFromLocation("/", "#stock/BRK.B/deep-dive/fleet"), { page: "stock", ticker: "BRK.B" });
  assert.deepEqual(
    routeStateForLocation("/", "#stock/QQQ/deep-dive/scoreboard", "SPY", "DIA"),
    {
      route: { page: "stock", ticker: "QQQ" },
      dashboardSelection: "SPY",
      selected: "QQQ",
    },
  );
});

test("company tab hashes stay on that company's route and restore the section", () => {
  assert.equal(parseStockHash("#stock/nvda/kpis"), "NVDA");
  assert.equal(parseStockTab("#stock/NVDA/kpis/revenue_gaap"), "kpis");
  assert.equal(parseStockPanel("#stock/nvda/kpis/revenue_gaap"), "revenue_gaap");
  assert.equal(parseStockSection("#stock/NVDA/kpis/revenue_gaap"), "");
  assert.equal(parseStockTab("#stock/AAPL"), "");
  assert.equal(parseStockPanel("#stock/AAPL"), "");
  assert.equal(parseStockTab("#stock/MSFT/deep-dive"), "deep-dive");
  assert.equal(parseStockSection("#stock/MSFT/deep-dive"), "");
  assert.equal(parseStockHash("#stock/NVDA/nope"), null);
  assert.equal(stockTabHash("abcd", "overview"), "#stock/ABCD");
  assert.equal(stockTabHash("abcd", "trends"), "#stock/ABCD/trends");
  assert.equal(stockTabHash("abcd", "kpis"), "#stock/ABCD/kpis");
  assert.equal(stockTabHash("abcd", "deep-dive"), "#stock/ABCD/deep-dive");
  assert.equal(stockTabHash("abcd", "calls"), "#stock/ABCD");
  assert.equal(stockTabHash("abcd", "admin"), "#stock/ABCD/admin");
  assert.equal(parseStockTab("#stock/NVDA/admin"), "admin");
  assert.equal(parseStockPanel("#stock/NVDA/admin/metrics"), "metrics");
  assert.equal(parseStockHash("#stock/tsla/admin"), "TSLA");
  assert.equal(stockPanelHash("abcd", "admin", "metrics"), "#stock/ABCD/admin/metrics");
  assert.equal(stockPanelHash("abcd", "overview", "price"), "#stock/ABCD/overview/price");
  assert.equal(stockPanelHash("abcd", "trends", "news"), "#stock/ABCD/trends/news");
  assert.equal(stockPanelHash("abcd", "calls", "notes"), "#stock/ABCD");
  assert.equal(stockPanelHash("abcd", "deep-dive", "forecast"), "#stock/ABCD/deep-dive/forecast");
  assert.equal(stockPanelHash("abcd", "kpis", "../x"), "#stock/ABCD/kpis");
  assert.equal(parseStockTab("#stock/NVDA/trends/price"), "trends");
  assert.equal(parseStockPanel("#stock/NVDA/trends/price"), "price");
  assert.equal(parseStockPanel("#stock/NVDA/calls/notes"), "");
  assert.deepEqual(routeFromLocation("/", "#stock/BRK.B/fundamentals/revenue"), { page: "stock", ticker: "BRK.B" });
});

test("legacy main-page and calls hashes redirect to the company tab that replaced them", () => {
  assert.equal(legacyMainSection("#news"), "news");
  assert.equal(legacyMainSection("#rates"), "rates");
  assert.equal(legacyMainSection("#mobile-panel-chart"), "price");
  assert.equal(legacyMainSection("#settings"), "");
  assert.equal(legacyMainSection("#stock/NVDA"), "");
  assert.equal(redirectStockHash("#news", "nvda"), "#stock/NVDA/trends/news");
  assert.equal(redirectStockHash("#filings", "brk.b"), "#stock/BRK.B/trends/filings");
  assert.equal(redirectStockHash("#stock/nvda/calls"), "#stock/NVDA");
  assert.equal(redirectStockHash("#stock/NVDA/calls/transcripts"), "#stock/NVDA");
  assert.equal(redirectStockHash("#stock/NVDA/overview/quarterly-notes"), "#stock/NVDA");
  assert.equal(redirectStockHash("#stock/NVDA/trends/price", "NVDA"), "");
  assert.equal(redirectStockHash("#settings/theme", "NVDA"), "");
  assert.equal(redirectStockHash("#news", ""), "");
});

test("research navigation preserves and restores the dashboard ticker", () => {
  assert.deepEqual(
    routeStateForPath("/ticker/TSLA", "SPY", "QQQ"),
    {
      route: { page: "research", ticker: "TSLA" },
      dashboardSelection: "SPY",
      selected: "TSLA",
    },
  );
  assert.deepEqual(
    routeStateForPath("/", "SPY", "TSLA"),
    {
      route: { page: "dashboard" },
      dashboardSelection: "SPY",
      selected: "SPY",
    },
  );
});
