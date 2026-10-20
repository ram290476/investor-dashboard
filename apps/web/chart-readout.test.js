import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { overlayPointValue, overlayReadoutItems } from "./chart-legend.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const usd = (v) => `$${v.toFixed(2)}`;

test("overlay values at the crosshair date follow each overlay kind (#218)", () => {
  assert.equal(overlayPointValue({ definition: { kind: "average" }, values: [null, 101.5, 102] }, 1, usd), "$101.50");
  assert.equal(overlayPointValue({ definition: { kind: "average" }, values: [null, 101.5] }, 0, usd), "—");
  assert.equal(overlayPointValue({ definition: { kind: "market" }, values: [400, 410, 420] }, 1), "410.00 (+2.5%)");
  assert.equal(overlayPointValue({ definition: { kind: "macro" }, unit: "%", values: [4.1, null, null] }, 2), "4.10%",
    "sparse series carry the latest observation on or before the date");
  assert.equal(overlayPointValue({ definition: { kind: "macro" }, unit: "%", values: [null, 4.2] }, 0), "—");
  assert.equal(overlayPointValue({ definition: { kind: "fundamental" }, unit: "ratio", values: [0.183] }, 0), "18.3%");
  assert.equal(overlayPointValue({ definition: { kind: "fundamental" }, unit: "USD", values: [25.2e9] }, 0), "$25.2B");
  assert.equal(overlayPointValue({ definition: { kind: "fundamental" }, unit: "sh", values: [3.2e9] }, 0), "3.2B sh");
  assert.equal(overlayPointValue({ definition: { kind: "sentiment" }, values: [0.25] }, 5), "0.25");
  assert.equal(overlayPointValue({ definition: null, values: [1] }, 0), "—");
});

test("readout items carry the overlay label and colour in the selected order (#218)", () => {
  const items = overlayReadoutItems([
    { definition: { id: "MA20", short: "20-day", label: "20-day average", kind: "average" }, color: "var(--a)", values: [10] },
    { definition: null, values: [1] },
    { definition: { id: "DGS10", label: "10Y yield", kind: "macro" }, unit: "%", color: "var(--b)", values: [4.25] },
  ], 0, usd);
  assert.deepEqual(items, [
    { id: "MA20", label: "20-day", color: "var(--a)", text: "$10.00" },
    { id: "DGS10", label: "10Y yield", color: "var(--b)", text: "4.25%" },
  ]);
});

test("the chart engine still builds a crosshair readout (#218)", () => {
  assert.match(app, /function attachSharedCrosshair\(/);
  assert.match(app, /renderReadout\(readout, crosshairText\(bars\[index\], index, periodId, lanes\), overlayReadoutItems\(overlays, index, formatPrice\)\)/);
  assert.match(app, /label\.style\.color = item\.color;/);
  const panel = app.slice(app.indexOf("function renderPricePanel"), app.indexOf("function renderContracts"));
  assert.doesNotMatch(panel, /visibleOverlays|attachSharedCrosshair|chart-legend/);
});

test("overlay controls still restore focus without scrolling (#218)", () => {
  assert.match(styles, /\.chart-controls \{[^}]*position: sticky;[^}]*top: 0;/);
  assert.match(app, /function renderDashboard\(\) \{\n  if \(session\.ended\) return;\n  const before = chartControlsTop\(\);\n  renderDashboardNow\(\);\n  keepChartControlsPinned\(before\);\n\}/);
  assert.match(app, /window\.scrollBy\(0, after - before\)/);
  for (const selector of ["data-overlay-tab=", "data-overlay=\"\\$\\{overlay.id", "\\.overlay-all", "lane-toggle\\[data-lane="]) {
    assert.match(app, new RegExp(`${selector}[^\\n]*\\?\\.focus\\(\\{ preventScroll: true \\}\\)`), selector);
  }
  const panel = app.slice(app.indexOf("function renderPricePanel"), app.indexOf("function renderContracts"));
  assert.doesNotMatch(panel, /catalyst-filters|chart-controls|chart-stack/);
});

test("the price panel does not render the chart stack under the period selection", () => {
  const panel = app.slice(app.indexOf("function renderPricePanel"), app.indexOf("function renderContracts"));
  assert.doesNotMatch(panel, /chart-stack|pinReadoutBelowControls|renderOverlayControls/);
  assert.match(app, /function pinReadoutBelowControls/);
  assert.match(styles, /\.chart-readout \{[^}]*position: sticky;[^}]*top: var\(--chart-controls-height, 0px\);[^}]*background: var\(--surface\);/);
});
