import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

test("Follow overlays, Show all, and Hidden panels share one toolbar row", () => {
  const toolbar = app.slice(app.indexOf("function renderPanelToolbar"), app.indexOf("function renderPanelMode"));
  assert.match(toolbar, /node\("div", "panel-toolbar"\)/);
  assert.match(toolbar, /bar\.append\(mode, hidden\)/);
  assert.match(app, /primary\.append\(renderPanelToolbar\("chart"\)\)/);
  assert.match(app, /Follow overlays/);
  assert.match(app, /Show all/);
  assert.match(app, /Hidden panels/);
  assert.match(styles, /\.panel-toolbar\s*\{[^}]*display:\s*flex/);
  assert.match(styles, /\.panel-toolbar\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(styles, /\.panel-toolbar\s*\{[^}]*align-items:\s*center/);
  assert.match(styles, /\.panel-toolbar \.hidden-panels\s*\{[^}]*margin-left:\s*auto/);
  assert.match(styles, /\.panel-mode-option\s*\{[^}]*height:\s*var\(--control-height\)/);
  assert.match(styles, /\.panel-mode-option\s*\{[^}]*border:\s*1px solid var\(--control-line\)/);
  assert.match(styles, /\.hidden-panels-toggle\s*\{[^}]*height:\s*var\(--control-height\)/);
  assert.match(styles, /\.hidden-panels-toggle\s*\{[^}]*min-height:\s*var\(--control-height\)/);
  const phoneAt = styles.indexOf('.dashboard-grid[data-mobile-view="chart"] .panel-toolbar');
  assert.ok(phoneAt > styles.indexOf("@media (max-width: 640px)"));
  const phone = styles.slice(phoneAt, phoneAt + 500);
  assert.match(phone, /display:\s*flex/);
  assert.match(phone, /flex-wrap:\s*wrap/);
  assert.match(phone, /margin-left:\s*auto/);
});
