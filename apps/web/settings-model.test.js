import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_PINNED,
  MAX_TICKERS,
  PIN_LIMIT_MESSAGE,
  addTicker,
  canRemove,
  emailFromIdToken,
  fallbackSelection,
  historyState,
  moveTicker,
  parseSettingsHash,
  refreshSummary,
  removeTicker,
  restoreTicker,
  stripOrder,
  tabAfterKey,
  togglePin,
  validateNewTicker,
} from "./settings-model.js";

const prefs = (tickers, pinned = []) => ({ tickers, pinned, display: { time_zone: "America/New_York" }, version: 3 });

test("deep links open the matching tab and unknown tabs fall back to My tickers", () => {
  assert.equal(parseSettingsHash("#settings/theme"), "theme");
  assert.equal(parseSettingsHash("#settings/profile"), "profile");
  assert.equal(parseSettingsHash("#settings/refresh"), "refresh");
  assert.equal(parseSettingsHash("#settings/tickers"), "tickers");
  assert.equal(parseSettingsHash("#settings/nope"), "tickers");
  assert.equal(parseSettingsHash("#settings"), "tickers");
  assert.equal(parseSettingsHash(""), null);
  assert.equal(parseSettingsHash("#other"), null);
});

test("tab keys wrap with arrows and jump with Home/End", () => {
  assert.equal(tabAfterKey("tickers", "ArrowRight"), "theme");
  assert.equal(tabAfterKey("profile", "ArrowRight"), "refresh");
  assert.equal(tabAfterKey("tickers", "ArrowLeft"), "refresh");
  assert.equal(tabAfterKey("theme", "Home"), "tickers");
  assert.equal(tabAfterKey("tickers", "End"), "refresh");
  assert.equal(tabAfterKey("refresh", "ArrowRight"), "tickers");
  assert.equal(tabAfterKey("tickers", "a"), null);
});

test("new tickers are validated for format, duplicates and the cap", () => {
  assert.deepEqual(validateNewTicker(" nvda ", prefs(["TSLA"])), { ticker: "NVDA" });
  assert.match(validateNewTicker("", prefs(["TSLA"])).error, /Enter a ticker/);
  assert.match(validateNewTicker("1ABC", prefs(["TSLA"])).error, /not a valid/);
  assert.equal(validateNewTicker("tsla", prefs(["TSLA"])).error, "TSLA is already in My tickers.");
  const full = prefs(Array.from({ length: MAX_TICKERS }, (_, i) => `T${i}`));
  assert.equal(validateNewTicker("NVDA", full).error, `At most ${MAX_TICKERS} tickers.`);
});

test("added tickers go to the end, unpinned, without mutating the input", () => {
  const before = prefs(["TSLA", "SPCX"], ["TSLA"]);
  const after = addTicker(before, "NVDA");
  assert.deepEqual(after.tickers, ["TSLA", "SPCX", "NVDA"]);
  assert.deepEqual(after.pinned, ["TSLA"]);
  assert.deepEqual(before.tickers, ["TSLA", "SPCX"]);
});

test("chart settings are deep-copied when preferences are edited", () => {
  const before = {
    ...prefs(["TSLA"]),
    chart_settings: { TSLA: { overlays: ["MA20"], lanes: ["VOL", "PRESS"] } },
  };
  const after = addTicker(before, "SPCX");
  after.chart_settings.TSLA.overlays.push("SPY");
  assert.deepEqual(before.chart_settings.TSLA.overlays, ["MA20"]);
  assert.deepEqual(after.chart_settings.TSLA.overlays, ["MA20", "SPY"]);
});

test("a seventh pin is blocked; unpinning frees a slot", () => {
  const six = ["A", "B", "C", "D", "E", "F"];
  const full = prefs([...six, "G"], six);
  assert.equal(togglePin(full, "G").error, PIN_LIMIT_MESSAGE);
  const unpinned = togglePin(full, "C");
  assert.equal(unpinned.pinned, false);
  assert.equal(unpinned.prefs.pinned.length, MAX_PINNED - 1);
  const pinned = togglePin(unpinned.prefs, "G");
  assert.deepEqual(pinned.prefs.pinned, ["A", "B", "D", "E", "F", "G"]);
});

test("reorder moves one step and stops at the ends", () => {
  const p = prefs(["TSLA", "SPCX", "NVDA"]);
  const moved = moveTicker(p, "NVDA", -1);
  assert.deepEqual(moved.prefs.tickers, ["TSLA", "NVDA", "SPCX"]);
  assert.equal(moved.position, 2);
  assert.equal(moved.total, 3);
  assert.equal(moveTicker(p, "TSLA", -1), null);
  assert.equal(moveTicker(p, "NVDA", 1), null);
  assert.equal(moveTicker(p, "XXX", 1), null);
});

test("remove unpins, keeps the last ticker, and undo restores position and pin", () => {
  const p = prefs(["TSLA", "SPCX", "NVDA"], ["SPCX", "TSLA"]);
  const removed = removeTicker(p, "TSLA");
  assert.deepEqual(removed.prefs.tickers, ["SPCX", "NVDA"]);
  assert.deepEqual(removed.prefs.pinned, ["SPCX"]);
  const restored = restoreTicker(removed.prefs, removed.undo);
  assert.deepEqual(restored.tickers, ["TSLA", "SPCX", "NVDA"]);
  assert.deepEqual(restored.pinned, ["SPCX", "TSLA"]);
  assert.equal(canRemove(prefs(["TSLA"])), false);
  assert.match(removeTicker(prefs(["TSLA"]), "TSLA").error, /at least one/);
});

test("the strip shows pinned tickers first in pin order", () => {
  assert.deepEqual(stripOrder(prefs(["A", "B", "C", "D"], ["C", "A"])), { pinned: ["C", "A"], others: ["B", "D"] });
  assert.deepEqual(stripOrder(prefs(["A"], ["GONE"])), { pinned: [], others: ["A"] });
});

test("a removed selection falls back to the first pin, then the first ticker", () => {
  assert.equal(fallbackSelection(prefs(["A", "B"], ["B"]), "A"), "A");
  assert.equal(fallbackSelection(prefs(["A", "B"], ["B"]), "GONE"), "B");
  assert.equal(fallbackSelection(prefs(["A", "B"]), null), "A");
});

test("the email claim is read from the ID token payload", () => {
  const payload = Buffer.from(JSON.stringify({ email: "investor@example.com", sub: "abc" })).toString("base64url");
  assert.equal(emailFromIdToken(`h.${payload}.s`), "investor@example.com");
  assert.equal(emailFromIdToken(undefined), null);
  assert.equal(emailFromIdToken("not-a-jwt"), null);
});

test("history state reports loading until a new ticker has price history", () => {
  assert.equal(historyState(undefined, false), "unknown");
  assert.equal(historyState(undefined, true), "loading");
  assert.equal(historyState({ price_history: [{ date: "2026-10-06", close: 1 }] }, true), "ready");
});

test("refresh summary counts ok jobs and flags partial or failed ones", () => {
  const status = {
    jobs: [
      { job: "H1", status: "ok" },
      { job: "H2", status: "ok" },
      { job: "D5", status: "partial" },
      { job: "D1", status: "never_run" },
    ],
  };
  assert.deepEqual(refreshSummary(status), { ok: 2, issues: 1, tone: "partial", text: "2 jobs ok · 1 need attention" });
  assert.equal(refreshSummary(null).text, "no refresh data yet");
  assert.equal(refreshSummary({ jobs: [{ status: "ok" }] }).tone, "ok");
});
