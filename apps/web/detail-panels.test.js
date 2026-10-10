import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  activeOverlayGroups,
  normalizePanelMode,
  panelVisible,
  panelsRevealedByOverlay,
} from "./chart-overlays.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const prefs = await readFile(new URL("../../services/data-jobs/src/functions/prefs_api/prefs_api.py", import.meta.url), "utf8");

test("follow mode shows a card only for an active overlay group", () => {
  assert.equal(normalizePanelMode(undefined), "follow");
  assert.equal(normalizePanelMode("all"), "all");
  assert.equal(normalizePanelMode("other"), "follow");
  const groups = activeOverlayGroups(["SPY", "MA50", "DGS10", "NOPE"]);
  assert.deepEqual(groups, ["Market", "Moving averages", "Rates"]);
  assert.equal(panelVisible("market-comparison", { mode: "follow", groups }), true);
  assert.equal(panelVisible("moving-averages", { mode: "follow", groups }), true);
  assert.equal(panelVisible("rates", { mode: "follow", groups }), true);
  assert.equal(panelVisible("inflation", { mode: "follow", groups }), false);
  assert.equal(panelVisible("volatility", { mode: "follow", groups }), false);
  assert.equal(panelVisible("company", { mode: "follow", groups: [] }), false);
  assert.equal(panelVisible("news", { mode: "follow", groups: ["Sentiment"] }), true);
  assert.equal(panelVisible("correlation", { mode: "follow", groups: [] }), true);
  assert.equal(panelVisible("catalyst-calendar", { mode: "follow", groups: [] }), true);
});

test("risk overlays reveal both volatility and dollar and oil", () => {
  assert.deepEqual(panelsRevealedByOverlay("VIXCLS"), ["volatility", "dollar-oil"]);
  assert.deepEqual(panelsRevealedByOverlay("DTWEXBGS"), ["volatility", "dollar-oil"]);
  assert.deepEqual(panelsRevealedByOverlay("DCOILWTICO"), ["volatility", "dollar-oil"]);
  const groups = ["Risk"];
  assert.equal(panelVisible("volatility", { mode: "follow", groups }), true);
  assert.equal(panelVisible("dollar-oil", { mode: "follow", groups }), true);
  assert.equal(panelVisible("tariffs", { mode: "follow", groups }), false);
});

test("show all reveals mapped cards and a dismissal still hides them", () => {
  assert.equal(panelVisible("inflation", { mode: "all", groups: [] }), true);
  assert.equal(panelVisible("rates", { mode: "follow", state: "dismissed", groups: ["Rates"] }), false);
  assert.equal(panelVisible("rates", { mode: "all", state: "dismissed", groups: ["Rates"] }), false);
  assert.equal(panelVisible("correlation", { mode: "all", state: "dismissed" }), false);
});

test("detail cards expose a named dismiss control and the hidden-panels menu", () => {
  assert.match(app, /aria-label", `Hide \$\{title\}`/);
  assert.match(app, /Hidden panels/);
  assert.match(app, /No panels for the active overlays/);
  assert.match(app, /Follow overlays/);
  assert.match(app, /panel_mode/);
  assert.match(app, /"news"/);
  assert.match(prefs, /"news"/);
  assert.match(prefs, /"dismissed"/);
  assert.match(prefs, /panel_mode/);
});
