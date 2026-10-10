import assert from "node:assert/strict";
import test from "node:test";
import { parseStockHash, routeFromLocation, routeFromPath, routeStateForLocation, routeStateForPath, stockHash, tickerResearchPath } from "./routes.js";

test("dashboard and Cognito callback paths resolve to the dashboard", () => {
  assert.deepEqual(routeFromPath("/"), { page: "dashboard" });
  assert.deepEqual(routeFromPath("/index.html"), { page: "dashboard" });
  assert.deepEqual(routeFromPath("/auth/callback"), { page: "dashboard" });
});

test("TSLA and SPCX research paths support direct entry and refresh", () => {
  assert.deepEqual(routeFromPath("/ticker/TSLA"), { page: "research", ticker: "TSLA" });
  assert.deepEqual(routeFromPath("/ticker/SPCX/"), { page: "research", ticker: "SPCX" });
  assert.deepEqual(routeFromPath("/ticker/tsla"), { page: "research", ticker: "TSLA" });
  assert.equal(tickerResearchPath("tsla"), "/ticker/TSLA");
  assert.equal(tickerResearchPath("SPCX"), "/ticker/SPCX");
  assert.equal(tickerResearchPath("NVDA"), null);
});

test("unsupported paths do not fall back to a different ticker", () => {
  assert.deepEqual(routeFromPath("/ticker/NVDA"), { page: "not-found" });
  assert.deepEqual(routeFromPath("/research/TSLA"), { page: "not-found" });
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
