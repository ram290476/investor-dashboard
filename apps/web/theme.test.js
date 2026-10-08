import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { applyTheme, contrastRatio, DEFAULT_THEME, mixColor, overlayColor, THEMES, themeFor, themeProperties } from "./theme.js";

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
  }
});

test("all overlay colors are theme-aware and readable on the chart background", () => {
  const source = readFileSync(new URL("./chart-overlays.js", import.meta.url), "utf8");
  const colors = [...source.matchAll(/color: "(#[a-fA-F0-9]{6})"/g)].map((match) => match[1]);
  assert.ok(colors.length > 20);
  for (const theme of THEMES) {
    const p = themeProperties(theme.id);
    const fill = mixColor(p["--surface"], p["--price"], 0.08);
    assert.ok(contrastRatio(p["--price"], fill) >= 3, `${theme.id} price on area fill`);
    for (const color of colors) {
      for (const background of [p["--surface"], p["--surface-raised"], fill]) {
        assert.ok(contrastRatio(overlayColor(color, theme.id), background) >= 3, `${theme.id}/${color}`);
      }
    }
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
