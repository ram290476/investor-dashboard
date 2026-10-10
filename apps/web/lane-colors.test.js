import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { CHART_LANES } from "./chart-overlays.js";
import { THEMES, themeProperties } from "./theme.js";

const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

function rule(selector) {
  const start = styles.indexOf(`${selector} {`);
  assert.ok(start >= 0, selector);
  return styles.slice(start, styles.indexOf("}", start));
}

function declaration(block, property) {
  const match = block.match(new RegExp(`${property}:\\s*([^;]+);`));
  assert.ok(match, `${property} in ${block.slice(0, 40)}`);
  return match[1].trim();
}

function resolve(value, properties, laneColor) {
  const vars = { ...properties, "--lane-color": laneColor };
  let current = value;
  for (let step = 0; step < 4; step += 1) {
    const next = current.replace(/var\((--[\w-]+)\)/g, (_, name) => vars[name] ?? `var(${name})`);
    if (next === current) return current;
    current = next;
  }
  return current;
}

test("each lane toggle swatch and chart mark resolve to the same color", () => {
  const volumeFill = declaration(rule(".lane-volume-bar"), "fill");
  const lineStroke = declaration(rule(".lane-line"), "stroke");
  const swatchBorder = declaration(rule(".lane-swatch"), "border");
  const swatchOn = declaration(rule('.lane-toggle[aria-pressed="true"] .lane-swatch'), "background");
  assert.equal(volumeFill, "var(--lane-color)");
  assert.equal(lineStroke, "var(--lane-color)");
  assert.match(swatchBorder, /var\(--lane-color\)/);
  assert.equal(swatchOn, "var(--lane-color)");
  assert.equal(declaration(rule(".lane-pressure-fill.up"), "fill"), "var(--green)");
  assert.equal(declaration(rule(".lane-pressure-fill.down"), "fill"), "var(--red)");
  assert.match(rule(".lane-swatch-pressure"), /var\(--green\)/);
  assert.match(rule(".lane-swatch-pressure"), /var\(--red\)/);
  assert.match(rule('.lane-toggle[aria-pressed="true"] .lane-swatch-pressure'), /var\(--green\)/);
  assert.match(rule('.lane-toggle[aria-pressed="true"] .lane-swatch-pressure'), /var\(--red\)/);
  assert.equal(declaration(rule(".lane-zero-line,\n.lane-reference-line"), "stroke"), "var(--control-line)");
  assert.match(rule(".lane-pressure-fill.up"), /opacity:\s*0\.35/);

  const renderLane = app.slice(app.indexOf("function renderLane"), app.indexOf("function renderChartLanes"));
  const renderControls = app.slice(app.indexOf("function renderLaneControls"), app.indexOf("function legendKey"));
  const laneColorCall = /setProperty\("--lane-color", `var\(\$\{lane\.colorVar\}\)`\)/g;
  assert.equal(renderLane.match(laneColorCall).length, 1);
  assert.equal(renderControls.match(laneColorCall).length, 1);
  assert.match(renderControls, /aria-pressed/);
  assert.match(renderControls, /lane-swatch/);
  assert.match(renderControls, /aria-hidden", "true"/);
  assert.match(renderControls, /document\.createTextNode\(lane\.label\)/);
  assert.match(renderLane, /lane-pressure-fill \$\{tone\}/);
  assert.match(renderLane, /lane-reference-line/);
  assert.match(renderControls, /Hide all/);
  assert.doesNotMatch(renderControls.slice(renderControls.indexOf("Hide all")), /lane-swatch/);

  for (const theme of THEMES) {
    const properties = themeProperties(theme.id, "blue-orange");
    for (const lane of CHART_LANES) {
      const laneColor = properties[lane.colorVar];
      const mark = resolve(lane.id === "VOL" ? volumeFill : lineStroke, properties, laneColor);
      const swatch = resolve(swatchBorder, properties, laneColor);
      assert.equal(mark, laneColor, `${theme.id} ${lane.id} mark`);
      assert.ok(swatch.includes(laneColor), `${theme.id} ${lane.id} swatch ${swatch}`);
      if (lane.id !== "PRESS") {
        assert.equal(resolve(swatchOn, properties, laneColor), laneColor, `${theme.id} ${lane.id} on swatch`);
      }
    }
    assert.equal(resolve("var(--green)", properties, ""), properties["--green"]);
    assert.equal(resolve("var(--red)", properties, ""), properties["--red"]);
  }
});

test("lane color rules use tokens rather than hard-coded chart colors", () => {
  const block = styles.slice(styles.indexOf(".lane-volume-bar {"), styles.indexOf(".lane-empty {"));
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}/);
  assert.doesNotMatch(rule(".lane-swatch"), /#[0-9a-fA-F]{3,8}/);
  assert.doesNotMatch(rule('.lane-toggle[data-lane][aria-pressed="true"]'), /#[0-9a-fA-F]{3,8}/);
});
