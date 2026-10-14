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
  assert.doesNotMatch(app, /rememberInner\([\s\S]*"release-calendar"\)/);
  assert.match(app, /title: "Inflation surprise"/);
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
  assert.match(styles, /\.macro-panel-grid\s*\{[^}]*gap:\s*var\(--space-3\)/);
  assert.match(styles, /\.side-column\s*\{[^}]*display:\s*grid/);
  assert.match(styles, /\.side-column\s*\{[^}]*align-content:\s*start/);
  assert.match(styles, /\.side-column\s*\{[^}]*gap:\s*var\(--space-3\)/);
  assert.match(styles, /\.dashboard-grid\s*\{[^}]*gap:\s*var\(--space-3\)/);
  assert.match(styles, /--space-3:\s*12px/);
  assert.doesNotMatch(styles, /\.side-column\s*\{[^}]*margin(?:-top|-bottom)?:\s*-/);
  assert.doesNotMatch(styles, /\.side-column\s*\{[^}]*position:\s*absolute/);
  const narrow = styles.slice(styles.indexOf("@media (max-width: 930px)"), styles.indexOf("/* Data refresh"));
  assert.match(narrow, /\.side-column\s*\{[^}]*gap:\s*var\(--space-3\)/);
  assert.doesNotMatch(narrow, /\.side-column\s*\{[^}]*gap:\s*0/);
  const phone = styles.slice(styles.lastIndexOf("@media (max-width: 640px)", styles.indexOf(".dashboard-grid .primary-column")), styles.indexOf("@media (max-width: 930px)"));
  assert.match(phone, /\.dashboard-grid \.side-column\s*\{[^}]*display:\s*contents/);
});

test("middle panels pack open drawers into balanced columns and collapsed headers two per row (#204)", () => {
  const pack = app.slice(app.indexOf("function packMiddlePanels"));
  const body = pack.slice(0, pack.indexOf("\n}\n"));
  assert.match(body, /middle-panels-open/);
  assert.match(body, /middle-panels-collapsed/);
  assert.match(body, /classList\.contains\("is-open"\)/);
  assert.match(body, /catalyst-calendar/);
  // Panels are moved, never rebuilt: contents, mobile tabs and saved state are untouched.
  assert.doesNotMatch(body, /renderDrawer\(|dataset\.mobilePanel\s*=|setPanelOpen/);
  assert.match(app, /grid\.append\(\.\.\.packMiddlePanels\(\[/);
  assert.match(app, /root\.querySelector\(`\[data-drawer-toggle="\$\{id\}"\]`\)\?\.focus\(\)/);
  assert.match(styles, /\.middle-panels-open\s*\{[^}]*columns:\s*2/);
  assert.match(styles, /\.middle-panels-open > \.panel\s*\{[^}]*break-inside:\s*avoid/);
  assert.match(styles, /\.middle-panels-collapsed\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/);
  assert.doesNotMatch(styles, /\.macro-panel-grid > :last-child/);
  const phone = styles.slice(styles.lastIndexOf("@media (max-width: 640px)", styles.indexOf(".middle-panels-open {\n    columns: 1")));
  assert.match(phone, /\.middle-panels-open\s*\{\s*columns:\s*1/);
  // The right-hand column is out of scope.
  assert.match(app, /side\.append\(\.\.\.\[renderDrivers\(tickerData\), news, filings\]\.filter\(Boolean\)\)/);
});
