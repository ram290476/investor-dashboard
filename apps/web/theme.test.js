import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { applyTheme, contrastRatio, DEFAULT_THEME, mixColor, overlayColor, THEMES, themeFor, themeProperties } from "./theme.js";
import { OVERLAYS } from "./chart-overlays.js";
import { CATALYST_CATEGORIES } from "./roadmap.js";

const LANE_HUES = ["--lane-volume", "--lane-si", "--lane-opt"];

function lab(hex) {
  const channels = [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16) / 255);
  const linear = channels.map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
  let x = linear[0] * 0.4124564 + linear[1] * 0.3575761 + linear[2] * 0.1804375;
  let y = linear[0] * 0.2126729 + linear[1] * 0.7151522 + linear[2] * 0.0721750;
  let z = linear[0] * 0.0193339 + linear[1] * 0.1191920 + linear[2] * 0.9503041;
  x /= 0.95047;
  z /= 1.08883;
  const f = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (t * 24389 / 27 + 16) / 116);
  const fx = f(x);
  const fy = f(y);
  const fz = f(z);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

function deltaE(a, b) {
  const [l1, a1, b1] = lab(a);
  const [l2, a2, b2] = lab(b);
  return Math.hypot(l1 - l2, a1 - a2, b1 - b2);
}

test("production palettes match all six existing design references", () => {
  const reference = JSON.parse(readFileSync(new URL("../../docs/design-roadmap/themes/themes.json", import.meta.url)));
  assert.equal(THEMES.length, 6);
  for (const theme of THEMES) {
    const design = reference.themes.find((item) => item.id === theme.id);
    assert.deepEqual(theme.tokens, design.tokens);
    assert.equal(theme.scheme, design.scheme);
  }
});

test("missing themes default to Industrial Dark and invalid preferences are explicit errors", () => {
  assert.equal(themeFor().id, DEFAULT_THEME);
  assert.throws(() => themeFor("not-a-theme"), /Unsupported theme/);
  assert.throws(() => themeProperties(DEFAULT_THEME, "pink"), /Unsupported up\/down palette/);
});

test("every theme and direction palette meets text and non-text contrast on rendered surfaces", () => {
  for (const theme of THEMES) {
    for (const palette of ["theme", "green-red", "red-green", "blue-orange"]) {
      const p = themeProperties(theme.id, palette);
      const backgrounds = ["--page", "--surface", "--surface-raised", "--hover", "--selected"].map((key) => p[key]);
      for (const key of ["--text", "--secondary", "--muted", "--subtle", "--blue", "--green", "--red", "--amber", "--error"]) {
        for (const bg of backgrounds) assert.ok(contrastRatio(p[key], bg) >= 4.5, `${theme.id}/${palette}/${key} on ${bg}`);
      }
      for (const key of ["--price", "--control-line", ...Array.from({ length: 6 }, (_, i) => `--series-${i + 1}`)]) {
        for (const bg of backgrounds) assert.ok(contrastRatio(p[key], bg) >= 3, `${theme.id}/${key} on ${bg}`);
      }
      for (const key of ["--lane-volume", "--lane-si", "--lane-opt", "--lane-press"]) {
        for (const bg of [p["--surface"], p["--surface-raised"]]) {
          assert.ok(contrastRatio(p[key], bg) >= 3, `${theme.id}/${palette}/${key} on ${bg}`);
        }
      }
      for (const key of ["--green", "--red"]) {
        const badge = mixColor(p["--surface"], p[key], 0.12);
        assert.ok(contrastRatio(p[key], badge) >= 4.5, `${theme.id}/${palette} direction badge`);
      }
    }
  }
});

test("direction palette overrides do not change chart or category colors", () => {
  for (const theme of THEMES) {
    const normal = themeProperties(theme.id);
    const reverse = themeProperties(theme.id, "red-green");
    assert.equal(normal["--green"], reverse["--red"]);
    assert.equal(normal["--red"], reverse["--green"]);
    assert.equal(normal["--price"], reverse["--price"]);
    assert.equal(normal["--series-1"], reverse["--series-1"]);
    for (const key of ["--lane-volume", "--lane-si", "--lane-opt", "--lane-press"]) {
      assert.equal(normal[key], reverse[key], `${theme.id} ${key}`);
      assert.equal(normal[key], themeProperties(theme.id, "blue-orange")[key], `${theme.id} ${key} palette`);
    }
  }
  const industrial = themeProperties("industrial-dark", "blue-orange");
  assert.notEqual(industrial["--lane-volume"], industrial["--red"]);
});

test("lane hues stay distinct from overlay and catalyst series in every theme", () => {
  for (const theme of THEMES) {
    assert.equal(theme.tokens["lane-volume"], theme.tokens.down, `${theme.id} volume uses the theme down token`);
    const properties = themeProperties(theme.id);
    assert.equal(properties["--lane-press"], properties["--secondary"], `${theme.id} pressure line`);
    const series = [
      ...Array.from({ length: 6 }, (_, slot) => properties[`--series-${slot + 1}`]),
      ...Array.from({ length: 6 }, (_, slot) => overlayColor(slot, theme.id)),
      ...CATALYST_CATEGORIES.map((category) => overlayColor(category.slot, theme.id)),
    ];
    for (const key of LANE_HUES) {
      for (const other of series) {
        const distance = deltaE(properties[key], other);
        assert.ok(distance >= 25, `${theme.id} ${key} ΔE ${distance.toFixed(1)} vs ${other}`);
      }
      for (const background of [properties["--surface"], properties["--surface-raised"]]) {
        assert.ok(contrastRatio(properties[key], background) >= 3, `${theme.id} ${key}`);
      }
    }
    for (let left = 0; left < LANE_HUES.length; left += 1) {
      for (let right = left + 1; right < LANE_HUES.length; right += 1) {
        const distance = deltaE(properties[LANE_HUES[left]], properties[LANE_HUES[right]]);
        assert.ok(distance >= 25, `${theme.id} ${LANE_HUES[left]} vs ${LANE_HUES[right]} ΔE ${distance.toFixed(1)}`);
      }
    }
  }
});

test("all overlay colors are theme-aware and readable on the chart background", () => {
  const colors = OVERLAYS.map(overlay => overlay.color);
  assert.ok(colors.length > 20);
  for (const theme of THEMES) {
    const p = themeProperties(theme.id);
    assert.equal(new Set(colors.map(slot => overlayColor(slot, theme.id))).size, colors.length, `${theme.id} has duplicate overlay colors`);
    const fill = mixColor(p["--surface"], p["--price"], 0.08);
    assert.ok(contrastRatio(p["--price"], fill) >= 3, `${theme.id} price on area fill`);
    for (const color of colors) {
      for (const background of [p["--surface"], p["--surface-raised"], fill]) {
        assert.ok(contrastRatio(overlayColor(color, theme.id), background) >= 3, `${theme.id}/${color}`);
      }
    }
  }
  assert.notEqual(overlayColor(0, "industrial-dark"), overlayColor(0, "terminal-amber"));
});

test("catalyst marker colors match across themes and stay readable on chip surfaces", () => {
  for (const theme of THEMES) {
    const surface = themeProperties(theme.id);
    const backgrounds = [surface["--surface"], surface["--surface-raised"], surface["--selected"], surface["--page"]];
    const colors = CATALYST_CATEGORIES.map((category) => overlayColor(category.slot, theme.id));
    colors.forEach((color, index) => {
      for (const background of backgrounds) {
        assert.ok(contrastRatio(color, background) >= 3, `${theme.id} ${CATALYST_CATEGORIES[index].id} on ${background}`);
      }
    });
  }
});

test("theme application targets the document, updates metadata, and changes only styling", () => {
  const values = new Map();
  const metadata = new Map();
  const doc = {
    documentElement: { dataset: {}, style: { setProperty: (key, value) => values.set(key, value) } },
    querySelector: (selector) => ({ setAttribute: (_key, value) => metadata.set(selector, value) }),
  };
  const display = { theme: "clean-light", updown_palette: "blue-orange", chart_period: "1Y" };
  const before = structuredClone(display);
  applyTheme(display, doc);
  assert.deepEqual(display, before);
  assert.equal(doc.documentElement.dataset.theme, "clean-light");
  assert.equal(doc.documentElement.style.colorScheme, "light");
  assert.equal(values.get("--page"), "#F3F5F8");
  assert.equal(metadata.get('meta[name="theme-color"]'), "#F3F5F8");
  applyTheme({}, doc);
  assert.equal(doc.documentElement.dataset.theme, DEFAULT_THEME);
});
