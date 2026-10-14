import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  DISCLAIMER, agreementNote, consensus, exampleTechnicalSummary, formatScore, horizonView, ratingLabel,
  technicalSummaryView, voteScore,
} from "./technical-summary.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const longHistory = Array.from({ length: 260 }, (_, i) => ({ date: `d${i}`, close: 100 }));

test("rating labels follow the -1..+1 TradingView thresholds", () => {
  assert.equal(ratingLabel(-0.51), "Strong Sell");
  assert.equal(ratingLabel(-0.5), "Sell");
  assert.equal(ratingLabel(-0.1), "Neutral");
  assert.equal(ratingLabel(0.1), "Neutral");
  assert.equal(ratingLabel(0.11), "Buy");
  assert.equal(ratingLabel(0.5), "Buy");
  assert.equal(ratingLabel(0.51), "Strong Buy");
  assert.equal(ratingLabel(null), null);
  assert.equal(voteScore({ buy: 0, neutral: 0, sell: 0 }), null);
  assert.equal(formatScore(voteScore({ buy: 7, neutral: 3, sell: 4 })), "+0.21");
});

test("bullish and bearish shares exclude neutral votes and sum to 100", () => {
  assert.deepEqual(consensus({ buy: 7, neutral: 3, sell: 4 }), { bullish: 64, bearish: 36 });
  assert.deepEqual(consensus({ buy: 9, neutral: 2, sell: 2 }), { bullish: 82, bearish: 18 });
  assert.equal(consensus({ buy: 0, neutral: 5, sell: 0 }), null);
  for (let buy = 0; buy < 12; buy += 1) {
    const split = consensus({ buy, sell: 11 - buy });
    assert.equal(split.bullish + split.bearish, 100);
  }
});

test("example data matches the mockups and is labelled as example for any ticker", () => {
  for (const ticker of ["EXAMPLE", "NVDA", "ANY"]) {
    const view = technicalSummaryView(exampleTechnicalSummary(ticker, { history: longHistory }));
    assert.equal(view.example, true);
    assert.equal(view.ticker, ticker);
    assert.deepEqual(view.rows.map((row) => row.rating), ["Buy", "Buy", "Strong Buy"]);
    assert.deepEqual(view.rows.map((row) => row.bullish), [64, 60, 82]);
    assert.equal(view.rows[0].counts, "Buy 7 · Neutral 3 · Sell 4");
    assert.equal(view.rows[0].consensusText, "64% of signals bullish");
    assert.deepEqual(view.rows[0].groups.map((g) => [g.label, g.rating, g.score]),
      [["Moving Averages", "Buy", "+0.38"], ["Oscillators", "Neutral", "0.00"], ["Summary", "Buy", "+0.21"]]);
    assert.deepEqual(view.rows[0].support, ["96.20", "93.80"]);
    assert.match(view.rows[1].eventRisk, /Earnings/);
    assert.equal(view.agreement, "");
    assert.match(view.asOf, /^As of .* data delayed$/);
    assert.equal(view.disclaimer, DISCLAIMER);
  }
});

test("short history shows Not enough data per horizon and flags low agreement", () => {
  const history = Array.from({ length: 142 }, () => ({ close: 1 }));
  const view = technicalSummaryView(exampleTechnicalSummary("NEWCO", { history }));
  assert.equal(view.rows[2].enoughData, false);
  assert.match(view.rows[2].message, /Not enough data — needs ≥ 200 trading days of history \(NEWCO has 142\)/);
  assert.deepEqual(view.rows.slice(0, 2).map((row) => [row.rating, row.bullish]), [["Neutral", 46], ["Sell", 36]]);
  assert.equal(view.rows[0].lowAgreement, true);
  assert.match(view.agreement, /Low agreement/);
  const tiny = technicalSummaryView(exampleTechnicalSummary("IPO", { history: [{ close: 1 }] }));
  assert.ok(tiny.rows.every((row) => !row.enoughData));
});

test("missing provider data never throws", () => {
  assert.equal(horizonView(undefined).enoughData, false);
  const view = technicalSummaryView(null);
  assert.equal(view.rows.length, 3);
  assert.ok(view.rows.every((row) => !row.enoughData));
  assert.equal(agreementNote([]), "");
  assert.equal(technicalSummaryView({ example: false, horizons: [] }).example, false);
});

test("the panel sits directly below the chart, uses theme tokens and has no hard-coded tickers", () => {
  assert.match(app, /page\.append\(\s*chart,\s*renderTechnicalSummary\(session\.selected, history\),/);
  const render = app.slice(app.indexOf("function technicalSummaryFor"), app.indexOf("function renderStockPage"));
  assert.match(render, /Example data/);
  assert.match(render, /view\.disclaimer/);
  assert.doesNotMatch(render, /"(TSLA|SPCX|NVDA)"/);
  const css = styles.slice(styles.indexOf(".technical-summary {"));
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/);
  assert.match(css, /var\(--green\)/);
});
