import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const fn = (name) => app.slice(app.indexOf(`function ${name}(`), app.indexOf("\n}\n", app.indexOf(`function ${name}(`)) + 3);

test("company page chart starts with every toggle off, including Volume and catalysts (#212)", () => {
  const state = fn("companyChartState");
  assert.match(state, /session\.route\?\.page !== "stock"\) return null/);
  assert.match(state, /\{ ticker, overlays: \[\], lanes: \[\], catalysts: \[\] \}/);
  const route = { page: "stock" };
  const session = { route, selected: "AMZN", companyChart: null, catalystCategories: ["earnings"] };
  const chartSettingsFor = () => ({ overlays: ["sma50"], lanes: ["volume", "short_interest", "macro", "options"] });
  const api = new Function("session", "chartSettingsFor",
    `${state}${fn("chartSettingsView")}${fn("activeCatalysts")}${fn("setActiveCatalysts")}
    return { chartSettingsView, activeCatalysts, setActiveCatalysts };`)(session, chartSettingsFor);
  const view = api.chartSettingsView({}, "AMZN");
  assert.deepEqual([view.overlays, view.lanes, api.activeCatalysts()], [[], [], []]);
  api.setActiveCatalysts(["earnings"]);
  assert.deepEqual(session.catalystCategories, ["earnings"], "dashboard catalysts untouched");
  assert.deepEqual(api.chartSettingsView({}, "MSFT").lanes, [], "a new company resets to all off");
  session.companyChart = null;
  for (const tab of ["summary", "trends"]) {
    session.stockTab = tab;
    assert.deepEqual(api.chartSettingsView({}, "AMZN").lanes, ["volume", "short_interest", "macro", "options"], `${tab} keeps the saved chart`);
  }
  for (const tab of ["analysis", "overview", "technical"]) {
    session.stockTab = tab;
    session.companyChart = null;
    assert.deepEqual(api.chartSettingsView({}, "AMZN").lanes, [], `${tab} starts with toggles off`);
  }
  session.stockTab = "";
  route.page = "dashboard";
  assert.deepEqual(api.chartSettingsView({}, "AMZN").lanes, ["volume", "short_interest", "macro", "options"],
    "dashboard and research keep saved settings");
});

test("company page toggles never save preferences and every read goes through the view (#212)", () => {
  assert.doesNotMatch(app, /chartSettingsFor\(session\.prefs,/);
  const update = fn("updateChartSettings");
  assert.ok(update.indexOf("companyChartState(ticker)") < update.indexOf("savePrefs"));
  assert.match(update, /if \(company\) \{[\s\S]*?return;\n  \}/);
  assert.match(fn("navigateToStock"), /session\.companyChart = null;/);
  assert.doesNotMatch(app.replace(fn("activeCatalysts"), "").replace(fn("setActiveCatalysts"), ""),
    /session\.catalystCategories(\.|\s*=)/);
});
