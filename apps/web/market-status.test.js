import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { contrastRatio, THEMES, themeProperties } from "./theme.js";
import { clockDelay, headerDate, liveSession, marketLabel } from "./market-status.js";

const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const account = await readFile(new URL("./account-settings.js", import.meta.url), "utf8");

const weekend = {
  status: "closed",
  next_open: "2026-10-12T13:30:00+00:00",
  next_close: "2026-10-12T20:00:00+00:00",
  as_of: "2026-10-10T16:00:00+00:00",
};
const regular = {
  status: "open",
  next_close: "2026-10-07T20:00:00+00:00",
  next_open: "2026-10-08T13:30:00+00:00",
  as_of: "2026-10-07T15:00:00+00:00",
};
const pre = {
  status: "pre",
  next_open: "2026-10-07T13:30:00+00:00",
  next_close: "2026-10-07T20:00:00+00:00",
  as_of: "2026-10-07T12:00:00+00:00",
};
const early = {
  status: "open",
  next_close: "2026-11-27T18:00:00+00:00",
  next_open: "2026-11-30T14:30:00+00:00",
  as_of: "2026-11-27T17:00:00+00:00",
};

test("weekend, holiday, and early-close labels use the viewer's time zone", () => {
  const saturday = new Date("2026-10-10T16:00:00Z");
  assert.equal(marketLabel(weekend, saturday, "America/Los_Angeles").text, "NYSE closed · opens Mon 06:30 PT");
  assert.equal(marketLabel(weekend, saturday, "America/New_York").text, "NYSE closed · opens Mon 09:30 ET");
  assert.equal(marketLabel(weekend, saturday, "America/Los_Angeles").compact, "Closed");

  const midday = new Date("2026-10-07T15:00:00Z");
  assert.equal(marketLabel(regular, midday, "America/Los_Angeles").text, "NYSE open · closes 13:00 PT");
  assert.equal(marketLabel(pre, new Date("2026-10-07T12:00:00Z"), "America/Los_Angeles").text, "NYSE pre-market · opens 06:30 PT");

  const blackFriday = new Date("2026-11-27T17:00:00Z");
  assert.equal(marketLabel(early, blackFriday, "America/Los_Angeles").text, "NYSE open · closes 10:00 PT");
  assert.equal(headerDate(new Date("2026-10-09T20:00:00Z"), "America/Los_Angeles"), "Fri Oct 9 · PT");
  assert.equal(headerDate(new Date("2026-10-09T20:00:00Z"), "America/New_York"), "Fri Oct 9 · ET");
});

test("the label crosses open and close without a new snapshot", () => {
  const beforeClose = new Date("2026-10-07T19:59:00Z");
  const atClose = new Date("2026-10-07T20:00:00Z");
  assert.equal(liveSession(regular, beforeClose).status, "open");
  assert.equal(marketLabel(regular, atClose, "America/Los_Angeles").text, "NYSE after-hours · opens Thu 06:30 PT");
  assert.equal(marketLabel(regular, atClose, "America/Los_Angeles").compact, "Post");

  const beforeOpen = new Date("2026-10-12T13:29:00Z");
  const atOpen = new Date("2026-10-12T13:30:00Z");
  assert.equal(marketLabel(weekend, beforeOpen, "America/New_York").status, "closed");
  assert.equal(marketLabel(weekend, atOpen, "America/New_York").text, "NYSE open · closes 16:00 ET");

  const beforeEarly = new Date("2026-11-27T17:59:00Z");
  const atEarly = new Date("2026-11-27T18:00:00Z");
  assert.equal(marketLabel(early, beforeEarly, "America/New_York").status, "open");
  assert.match(marketLabel(early, atEarly, "America/New_York").text, /^NYSE after-hours · opens/);
});

test("a missing market block stays unavailable and the clock waits at most a minute", () => {
  const blank = marketLabel(null, new Date("2026-10-10T16:00:00Z"), "America/Los_Angeles");
  assert.equal(blank.status, "unavailable");
  assert.equal(blank.text, "Market status unavailable");
  assert.equal(blank.compact, "Unavailable");
  assert.equal(blank.dot, "");
  assert.equal(marketLabel({ status: "open" }, new Date(), "UTC").status, "unavailable");
  assert.equal(clockDelay(Date.parse("2026-10-07T20:00:00Z"), Date.parse("2026-10-07T19:59:30Z")), 30_000);
  assert.equal(clockDelay(Date.parse("2026-10-12T13:30:00Z"), Date.parse("2026-10-10T16:00:00Z")), 60_000);
  assert.equal(clockDelay(null, Date.now()), 60_000);
});

test("header clock styles use theme tokens and stay one row, including the phone compact status", () => {
  const block = styles.slice(styles.indexOf("/* header market status (#90) */"), styles.indexOf("/* end header market status (#90) */"));
  assert.match(block, /\.header-date[\s\S]*color:\s*var\(--muted\)/);
  assert.match(block, /\.account-jobs\.ok[\s\S]*color:\s*var\(--green\)/);
  assert.match(block, /\.account-jobs\.partial[\s\S]*color:\s*var\(--amber\)/);
  assert.match(block, /\.market-compact[\s\S]*display:\s*none/);
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}/);
  const phone = styles.slice(styles.indexOf("@media (max-width: 640px)"), styles.indexOf("@media (max-width: 930px)"));
  assert.match(phone, /\.header-date,\s*\n\s*\.market-label,\s*\n\s*\.schedule-when/);
  assert.match(phone, /\.market-compact\s*\{[^}]*display:\s*inline/);
  assert.match(styles, /\.app-header\s*\{[^}]*flex-wrap:\s*nowrap/);
  for (const theme of THEMES) {
    const props = themeProperties(theme.id);
    assert.ok(contrastRatio(props["--text"], props["--page"]) >= 4.5);
    assert.ok(contrastRatio(props["--muted"], props["--page"]) >= 4.5);
    assert.ok(contrastRatio(props["--green"], props["--page"]) >= 4.5);
    assert.ok(contrastRatio(props["--amber"], props["--page"]) >= 3);
  }
});

test("the header paints text nodes and keeps the account and data names", () => {
  const paint = app.slice(app.indexOf("function paintHeaderClock"), app.indexOf("function armMarketTimer"));
  assert.doesNotMatch(paint, /renderDashboard/);
  assert.match(paint, /textContent/);
  assert.match(app, /data-market-status/);
  assert.match(app, /aria-label", view\.text/);
  assert.match(app, /open collection schedules and data refresh status/);
  assert.match(account, /account-jobs/);
  assert.match(account, /aria-label", `Signed In · open account menu · data refresh: \$\{summary\.text\}`/);
  assert.match(account, /Private workspace/);
  assert.doesNotMatch(app, /Private workspace/);
});
