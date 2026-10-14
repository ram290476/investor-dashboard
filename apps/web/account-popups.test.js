import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parseSettingsHash, SETTINGS_TABS } from "./settings-model.js";

const account = await readFile(new URL("./account-settings.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

test("the account menu no longer offers All refresh jobs", () => {
  assert.equal(account.includes("All refresh jobs"), false);
  assert.equal(account.includes("job schedule · status"), false);
  assert.match(account, /Data refresh:/);
});

test("data refresh is its own dialog and is not a settings tab", () => {
  assert.deepEqual(SETTINGS_TABS.map((tab) => tab.id), ["tickers", "theme", "profile"]);
  assert.equal(parseSettingsHash("#settings/refresh"), "refresh");
  assert.equal(parseSettingsHash("#settings/theme"), "theme");
  assert.match(account, /settings-dialog refresh-dialog/);
  assert.match(account, /function openRefresh/);
  assert.equal(account.includes('activeTab === "refresh"'), false);
  assert.match(styles, /\.refresh-dialog\s*\{[^}]*width:\s*min\(1120px,\s*100%\)/);
  assert.match(styles, /\.refresh-dialog\s*\{[^}]*max-height:\s*min\(920px/);
});
