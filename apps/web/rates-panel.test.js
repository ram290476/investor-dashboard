import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { contrastRatio, THEMES, themeProperties } from "./theme.js";
import {
  curvePaths,
  curveRegime,
  curveView,
  finiteNumber,
  fomcView,
  formatBp,
  policyPathView,
  ratesPanelBlocks,
} from "./rates-panel.js";

const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

function tenor(label, yieldNow, yieldAgo, change) {
  return { tenor: label, yield: yieldNow, yield_1m: yieldAgo, chg_1m_bp: change };
}

test("regime uses the 2s10s change and the 10Y level, and stays blank when an input is missing", () => {
  assert.equal(curveRegime([tenor("2Y", 3.4, 3.8), tenor("10Y", 4.0, 4.2)]), "bull steepening");
  assert.equal(curveRegime([tenor("2Y", 3.8, 3.9), tenor("10Y", 4.3, 4.1)]), "bear steepening");
  assert.equal(curveRegime([tenor("2Y", 3.7, 3.8), tenor("10Y", 4.0, 4.3)]), "bull flattening");
  assert.equal(curveRegime([tenor("2Y", 4.1, 3.8), tenor("10Y", 4.4, 4.3)]), "bear flattening");
  assert.equal(curveRegime([tenor("2Y", 4.0, 4.0), tenor("10Y", 4.2, 4.2)]), null);
  assert.equal(curveRegime([tenor("2Y", 3.8, null), tenor("10Y", 4.3, 4.1)]), null);
  assert.equal(curveRegime([tenor("10Y", 4.3, 4.1)]), null);
});

test("missing curve, FOMC and policy blocks name the source and do not invent zeros", () => {
  const blocks = ratesPanelBlocks(null, "ready");
  for (const block of [blocks.curve, blocks.fomc, blocks.policy]) {
    assert.equal(block.kind, "unavailable");
    assert.match(block.message, /Unavailable/);
    assert.match(block.message, /no attempt recorded/);
    assert.equal(finiteNumber(block.message), null);
    assert.doesNotMatch(block.message, /\b0(\.00)?%/);
  }
  assert.match(blocks.curve.message, /FRED/);
  assert.match(blocks.fomc.message, /Kalshi/);
  assert.match(blocks.policy.message, /source not selected/);
  assert.match(blocks.policy.message, /has not been selected/);
  assert.equal(blocks.curve.todayPath, "");
  assert.equal(blocks.fomc.segments.length, 0);
  assert.equal(blocks.policy.bars.length, 0);
  assert.equal(formatBp(null), "--");
  assert.equal(formatBp(0), "0");
});

test("loading and error states stay distinct from an empty curve", () => {
  assert.equal(curveView(null, "loading").kind, "loading");
  assert.match(curveView(null, "error").message, /could not be loaded/);
  assert.equal(fomcView({ status: { fomc: { source: "Kalshi", state: "error", last_attempt: "2026-10-08T11:00:00Z", detail: "provider_policy" } } }, "ready").kind, "error");
  assert.match(policyPathView(null, "loading").message, /Loading implied policy path/);
});

test("a served curve draws today and one month ago and skips a missing tenor", () => {
  const tenors = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"].map((label, index) => (
    tenor(label, label === "3Y" ? null : 3.5 + index * 0.08, label === "3Y" ? null : 3.4 + index * 0.07, label === "3Y" ? null : 8)
  ));
  const view = curveView({
    curve: { date: "2026-10-08", ref_date_1m: "2026-09-08", regime: "bear steepening", tenors },
  }, "ready");
  assert.equal(view.kind, "ready");
  assert.equal(view.regime, "bear steepening");
  assert.match(view.todayPath, /^M/);
  assert.match(view.agoPath, /^M/);
  assert.equal(view.tenors.find((row) => row.tenor === "3Y").bpText, "--");
  assert.equal(view.tenors.find((row) => row.tenor === "3Y").yieldText, "--");
  const paths = curvePaths(tenors);
  assert.doesNotMatch(paths.today, /NaN/);
  assert.equal(view.tenors.length, 11);
});

test("FOMC segments keep their own width and a short policy path is not drawn", () => {
  const fomc = fomcView({
    fomc: {
      meeting_date: "2026-10-28",
      outcomes: [{ label: "Cut", prob: 0.62 }, { label: "Hold", prob: 0.31 }, { label: "Hike", prob: null }],
      history_14d: [{ date: "2026-10-01", cut: 0.55 }, { date: "2026-10-08", cut: 0.62 }],
    },
  }, "ready");
  assert.equal(fomc.kind, "ready");
  assert.deepEqual(fomc.segments.map((row) => row.width), ["62%", "31%"]);
  assert.equal(fomc.history.length, 2);
  const policy = policyPathView({ policy_path: [{ meeting: "Oct", implied_rate: 4.1, chg_1m_bp: -12 }] }, "ready");
  assert.equal(policy.kind, "unavailable");
  assert.equal(policy.bars.length, 0);
});

test("rates block styles use theme tokens and stack under 640px", () => {
  const start = styles.indexOf(".rates-curve");
  assert.ok(start > 0);
  const block = styles.slice(start, start + 1800);
  assert.equal(block.includes("#"), false);
  assert.match(styles, /\.rates-fomc-row\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) 96px/);
  assert.match(styles, /max-width:\s*640px[\s\S]*\.rates-fomc-row\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  for (const theme of THEMES) {
    const properties = themeProperties(theme.id);
    assert.ok(contrastRatio(properties["--text"], properties["--surface"]) >= 4.5, theme.id);
    assert.ok(contrastRatio(properties["--price"], properties["--surface"]) >= 3, theme.id);
  }
});
