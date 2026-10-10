import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { SPARK_WINDOW, sparklineModel, sparklineSummary } from "./sparkline.js";

const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const account = await readFile(new URL("./account-settings.js", import.meta.url), "utf8");

function series(count, start, step) {
  return Array.from({ length: count }, (_, index) => ({ close: start + index * step }));
}

test("a rising, falling, and flat window report direction and skip gaps", () => {
  const rising = sparklineModel([
    ...series(SPARK_WINDOW - 1, 100, 0),
    { close: 103.2 },
  ]);
  assert.equal(rising.direction, "positive");
  assert.equal(rising.sessions, SPARK_WINDOW);
  assert.equal(sparklineSummary(rising), "up 3.2% over 1M");
  assert.equal(rising.points.split(" ").length, SPARK_WINDOW);

  const falling = sparklineModel(series(SPARK_WINDOW, 200, -1));
  assert.equal(falling.direction, "negative");
  assert.match(sparklineSummary(falling), /^down [\d.]+% over 1M$/);

  const flat = sparklineModel(series(8, 50, 0));
  assert.equal(flat.direction, "flat");
  assert.equal(flat.change, 0);
  assert.equal(sparklineSummary(flat), "unchanged over 8 sessions");
  const ys = flat.points.split(" ").map((pair) => pair.split(",")[1]);
  assert.equal(new Set(ys).size, 1);

  const gapped = sparklineModel([
    { close: 10 },
    { close: null, adj_close: null },
    { close: "" },
    { adj_close: 12 },
  ]);
  assert.equal(gapped.sessions, 2);
  assert.equal(gapped.points.split(" ").length, 2);
  assert.equal(gapped.direction, "positive");
  assert.equal(sparklineSummary(gapped), "up 20% over 2 sessions");
  assert.equal(sparklineModel([{ close: 4 }]).points, "");
  assert.equal(sparklineSummary(sparklineModel([{ close: 0 }, { close: 5 }])), "");
});

test("the same snapshot reuses the sparkline path", () => {
  const history = series(5, 10, 1);
  const first = sparklineModel(history, "2026-10-09|TSLA");
  const second = sparklineModel(history.map((row) => ({ close: 1 })), "2026-10-09|TSLA");
  assert.equal(first, second);
  assert.notEqual(sparklineModel(history, "2026-10-10|TSLA"), first);
});

test("the strip and My tickers share one helper, and the chip colors are theme tokens", () => {
  assert.match(app, /from "\.\/sparkline\.js"/);
  assert.match(account, /from "\.\/sparkline\.js"/);
  assert.doesNotMatch(account, /function sparkline/);
  const block = styles.slice(styles.indexOf("/* watchlist sparklines (#92) */"), styles.indexOf("/* end watchlist sparklines (#92) */"));
  assert.match(block, /\.watchlist \.spark[\s\S]*width:\s*60px/);
  assert.match(block, /\.watchlist \.spark[\s\S]*height:\s*18px/);
  assert.match(styles, /\.spark\.positive[\s\S]*stroke:\s*var\(--green\)/);
  assert.match(styles, /\.spark\.negative[\s\S]*stroke:\s*var\(--red\)/);
  assert.match(block, /\.spark\.flat[\s\S]*stroke:\s*var\(--muted\)/);
  assert.match(block, /grid-template-columns:\s*minmax\(0, 1fr\) 44px/);
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}/);
  assert.match(app, /sparklineSummary/);
  assert.match(app, /aria-hidden/);
});
