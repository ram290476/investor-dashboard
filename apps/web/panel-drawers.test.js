import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { drawerControls, withPanelState } from "./settings-model.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

test("collapsed drawer controls hide the body and a toggle brings it back", () => {
  const closed = drawerControls("catalyst-calendar", false);
  assert.equal(closed.ariaExpanded, "false");
  assert.equal(closed.bodyHidden, true);
  assert.equal(closed.ariaControls, "drawer-body-catalyst-calendar");
  const opened = drawerControls("catalyst-calendar", true);
  assert.equal(opened.ariaExpanded, "true");
  assert.equal(opened.bodyHidden, false);
  assert.equal(opened.ariaControls, closed.ariaControls);
  assert.deepEqual(withPanelState({ "catalyst-calendar": "closed" }, "catalyst-calendar", true), {
    "catalyst-calendar": "open",
  });
});

test("bottom panels are drawers and the old nested disclosures are gone", () => {
  for (const id of ["rates", "inflation", "market-comparison", "moving-averages", "volatility", "dollar-oil", "tariffs", "correlation", "catalyst-calendar", "company", "contracts", "about-data"]) {
    assert.match(app, new RegExp(`["']${id}["']`));
  }
  assert.match(app, /aria-expanded/);
  assert.match(app, /aria-controls/);
  assert.match(app, /button\.type = "button"/);
  assert.match(app, /body\.hidden = controls\.bodyHidden/);
  assert.match(app, /restoreDrawerFocus/);
  assert.doesNotMatch(app, /rememberDrawer|openDrawers/);
  assert.doesNotMatch(app, /signal-drawer panel-body/);
  assert.match(app, /rememberInner\([\s\S]*"release-calendar"\)/);
  assert.match(app, /rememberInner\([\s\S]*"all-drivers"\)/);
});

test("drawer chrome uses theme tokens and does not animate when motion is reduced", () => {
  const block = styles.slice(styles.indexOf(".panel-drawer .drawer-header"), styles.indexOf(".drawer-body[hidden]") + 40);
  assert.equal(block.includes("#"), false);
  for (const token of ["--line", "--text", "--hover", "--secondary", "--muted"]) {
    assert.match(block, new RegExp(`var\\(${token}\\)`));
  }
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.drawer-chevron\s*\{[^}]*transition:\s*none/);
  assert.match(styles, /\.macro-panel-grid\s*\{[^}]*align-items:\s*start/);
});
