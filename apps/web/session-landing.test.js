import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { accountMenuModel, canApproveMetrics } from "./settings-model.js";
import { freshCatalystSelection, sessionStockLanding, STOCK_SESSION_KEY } from "./session-landing.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const account = await readFile(new URL("./account-settings.js", import.meta.url), "utf8");

function accessToken(claims) {
  const payload = Buffer.from(JSON.stringify(claims)).toString("base64url");
  return `header.${payload}.sig`;
}

test("the first load of a session lands on Overview and rewrites a deep link", () => {
  for (const hashTab of ["overview", "technical", "trends", "summary", "analysis", "fundamentals", "kpis", "deep-dive", "admin", ""]) {
    const landing = sessionStockLanding({ sessionStarted: false, hashTab, routePage: "stock" });
    assert.equal(landing.tab, "summary");
    assert.equal(landing.markSession, true);
    assert.equal(landing.rewriteLanding, true);
  }
  const dashboard = sessionStockLanding({ sessionStarted: false, hashTab: "", routePage: "dashboard" });
  assert.deepEqual(dashboard, { tab: "summary", markSession: true, rewriteLanding: true });
});

test("a later load in the same session keeps the tab in the URL", () => {
  const fromOverview = sessionStockLanding({ sessionStarted: true, hashTab: "overview", routePage: "stock" });
  assert.deepEqual(fromOverview, { tab: "analysis", markSession: false, rewriteLanding: false });
  const fromTechnical = sessionStockLanding({ sessionStarted: true, hashTab: "technical", routePage: "stock" });
  assert.deepEqual(fromTechnical, { tab: "analysis", markSession: false, rewriteLanding: false });
  const analysis = sessionStockLanding({ sessionStarted: true, hashTab: "analysis", routePage: "stock" });
  assert.equal(analysis.tab, "analysis");
  const fromTrends = sessionStockLanding({ sessionStarted: true, hashTab: "trends", routePage: "stock" });
  assert.deepEqual(fromTrends, { tab: "summary", markSession: false, rewriteLanding: false });
  const overview = sessionStockLanding({ sessionStarted: true, hashTab: "summary", routePage: "stock" });
  assert.equal(overview.tab, "summary");
  const bare = sessionStockLanding({ sessionStarted: true, hashTab: "", routePage: "stock" });
  assert.equal(bare.tab, "analysis");
  assert.equal(bare.rewriteLanding, false);
  const home = sessionStockLanding({ sessionStarted: true, hashTab: "", routePage: "dashboard" });
  assert.equal(home.tab, "summary");
  assert.equal(home.rewriteLanding, false);
  const saved = sessionStockLanding({ sessionStarted: true, hashTab: "deep-dive", routePage: "stock" });
  assert.equal(saved.tab, "research");
  assert.equal(saved.rewriteLanding, false);
  const named = sessionStockLanding({ sessionStarted: true, hashTab: "research", routePage: "stock" });
  assert.equal(named.tab, "research");
});

test("research and other routes do not consume the first-load flag", () => {
  const research = sessionStockLanding({ sessionStarted: false, hashTab: "", routePage: "research" });
  assert.deepEqual(research, { tab: "", markSession: false, rewriteLanding: false });
  const missing = sessionStockLanding({ sessionStarted: false, routePage: "not-found" });
  assert.equal(missing.markSession, false);
});

test("catalyst markers start cleared and are not a stored preference", () => {
  assert.deepEqual(freshCatalystSelection(), { categories: [], focusId: null });
  assert.deepEqual(freshCatalystSelection(), freshCatalystSelection());
  assert.match(app, /const openedCatalysts = freshCatalystSelection\(\)/);
  assert.match(app, /catalystCategories: openedCatalysts\.categories/);
  assert.match(app, /focusCatalyst: openedCatalysts\.focusId/);
  assert.doesNotMatch(app, /catalystCategories: CATALYST_CATEGORIES\.map/);
  assert.doesNotMatch(app, /localPrefs\.(get|set)Item\([^)]*catalyst/);
  assert.doesNotMatch(app, /localPrefs\.(get|set)Item\(STOCK_SESSION_KEY/);
  assert.doesNotMatch(app, /tokenStorage\.(get|set)Item\(STOCK_SESSION_KEY/);
});

test("first load is detected with sessionStorage and not localStorage", () => {
  assert.equal(STOCK_SESSION_KEY, "investor-dashboard.stock-session");
  assert.match(app, /const stockSession = createSafeStorage\("sessionStorage"\);/);
  assert.doesNotMatch(app, /stockSession = createSafeStorage\("sessionStorage", \{[^}]*cookieFallback/);
  assert.doesNotMatch(app, /stockSession = createSafeStorage\("localStorage"/);
  assert.match(app, /stockSession\.getItem\(STOCK_SESSION_KEY\) === "1"/);
  assert.match(app, /stockSession\.setItem\(STOCK_SESSION_KEY, "1"\)/);
  assert.match(app, /session\.focusCatalyst = null/);
  const render = app.slice(app.indexOf("function renderDashboardNow"), app.indexOf("async function refreshData"));
  const landingAt = render.indexOf("applySessionStockLanding()");
  const trendsAt = render.indexOf("openTrendsLanding()");
  assert.ok(landingAt >= 0 && trendsAt > landingAt, "session landing runs before the dashboard Overview fallback");
  const landing = app.slice(app.indexOf("function applySessionStockLanding"), app.indexOf("function openTrendsLanding"));
  assert.match(landing, /rewriteLanding/);
  assert.match(landing, /stockTabHash\(ticker, landing\.tab\)/);
  assert.doesNotMatch(landing, /rewriteOverview|stockTabHash\(ticker, "overview"\)/);
  assert.doesNotMatch(app, /\b(window\.)?(sessionStorage|localStorage)\s*\./);
});

test("the account menu renames My tickers and adds Admin Settings only for admins", () => {
  assert.deepEqual(accountMenuModel(false).map((item) => item.label), [
    "Manage Watchlist",
    "Theme & display",
    "Profile & time zone",
    "Sign out",
  ]);
  assert.equal(accountMenuModel(false).some((item) => item.id === "admin"), false);
  const admin = accountMenuModel(true);
  assert.equal(admin.at(-2).id, "admin");
  assert.equal(admin.at(-2).label, "Admin Settings");
  assert.equal(admin.at(-1).id, "sign-out");
  assert.equal(admin.some((item) => item.label === "My tickers"), false);
  assert.match(account, /accountMenuModel\(isAdmin\(\)\)/);
  assert.match(account, /open\(ADMIN_TAB\.id, "account"\)/);
  const demo = "ram.vellamsetti@gmail.com";
  const demoToken = accessToken({ "cognito:groups": ["admin"], username: demo });
  assert.equal(canApproveMetrics(demoToken, demo), false);
  assert.equal(accountMenuModel(canApproveMetrics(demoToken, demo)).some((item) => item.id === "admin"), false);
  const owner = "owner@example.com";
  const ownerToken = accessToken({ "cognito:groups": ["admin"], username: owner });
  assert.equal(accountMenuModel(canApproveMetrics(ownerToken, owner)).some((item) => item.label === "Admin Settings"), true);
  assert.match(app, /isAdmin: \(\) => canApproveMetrics\(session\.accessToken, session\.email\)/);
});
