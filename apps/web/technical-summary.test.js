import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  DISCLAIMER, agreementNote, consensus, exampleTechnicalSummary, formatScore, horizonView, liveTechnicalSummary,
  macroView, noConfidenceChange, ratingLabel, technicalSummaryView, voteScore,
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

const liveFeed = {
  ticker: "ABC",
  example: false,
  as_of_date: "2026-10-13",
  asOf: "Oct 13, 2026 4:00 PM ET",
  delayed: true,
  horizons: [
    {
      id: "short", label: "Short-term", window: "Next week", requiredBars: 35, availableBars: 300,
      summary: { buy: 5, neutral: 1, sell: 2 },
      groups: [{ id: "ma", label: "Moving Averages", buy: 3, neutral: 0, sell: 1 },
        { id: "osc", label: "Oscillators", buy: 2, neutral: 1, sell: 1 }],
      indicators: [{ name: "RSI (14)", value: "61", vote: 0 }],
      explanation: "Price above SMA 10.", support: [10.5], resistance: [12.25],
      eventRisk: "Scheduled inside this horizon: CPI 2026-10-14 — event risk.",
      macro: {
        net: "net headwind", summary: "Macro/pricing signals: net headwind for this short-term Buy (2 headwind; 0 tailwind; 1 neutral; No data: Credit spreads).",
        events: [{ name: "CPI", date: "2026-10-14", group: "inflation" }],
        groups: [
          { id: "rates", label: "Rates & Fed", status: "live", effect: "headwind", relation: "contradicts",
            confidence: "Contradicts the Buy; lowers confidence", events: [],
            signals: [{ label: "10Y Treasury yield", value: "5.24%", direction: "rising", effect: "headwind",
              relation: "contradicts", confidence: "Contradicts the Buy; lowers confidence", note: "10Y rising" },
            { label: "FOMC path", status: "no_data", note: "Not live yet." }] },
          { id: "credit", label: "Credit spreads", status: "no_data", reason: "No credit-spread series is collected yet.",
            signals: [], events: [] },
        ],
      },
    },
    { id: "intermediate", requiredBars: 60, availableBars: 300, summary: { buy: 1, neutral: 1, sell: 5 } },
    { id: "long", requiredBars: 200, availableBars: 120, macro: { net: null, summary: "Macro/pricing signals: No data for this horizon.", groups: [], events: [] } },
  ],
};

test("live feed renders without the example badge and keeps disclaimer, as-of and low agreement (#193, #196)", () => {
  const view = technicalSummaryView(liveTechnicalSummary("ABC", { technical_summary: liveFeed }));
  assert.equal(view.example, false);
  assert.equal(view.asOf, "As of Oct 13, 2026 4:00 PM ET · data delayed");
  assert.deepEqual(view.rows.map((row) => row.rating), ["Buy", "Strong Sell", undefined]);
  assert.match(view.rows[2].message, /Not enough data — needs ≥ 200 trading days of history \(ABC has 120\)/);
  assert.match(view.agreement, /Low agreement/);
  assert.equal(view.rows[0].lowAgreement, true);
  assert.equal(view.disclaimer, DISCLAIMER);
  assert.equal(technicalSummaryView({ horizons: [] }).example, false, "badge needs an explicit example flag");
  assert.equal(technicalSummaryView(exampleTechnicalSummary("X")).example, true);
});

test("live provider never falls back to example values", () => {
  const loading = technicalSummaryView(liveTechnicalSummary("ABC", null, "loading"));
  assert.equal(loading.example, false);
  assert.ok(loading.rows.every((row) => !row.enoughData && row.message === "Loading live indicators…"));
  const missing = technicalSummaryView(liveTechnicalSummary("ABC", { price_history: [] }, "ready"));
  assert.ok(missing.rows.every((row) => /not published for ABC/.test(row.message)));
  assert.equal(missing.asOf, "As of --");
});

test("macro view shows direction, effect, relation, confidence, events, No data and a roll-up (#198)", () => {
  const macro = macroView(liveFeed.horizons[0].macro);
  assert.equal(macro.tone, "sell");
  assert.deepEqual(macro.events, ["CPI 2026-10-14"]);
  assert.match(macro.summary, /^Macro\/pricing signals: net headwind/);
  const [rates, credit] = macro.groups;
  assert.deepEqual([rates.effect, rates.relation, rates.tone], ["Headwind", "Contradicts", "sell"]);
  assert.deepEqual([rates.signals[0].direction, rates.signals[0].effect], ["rising", "Headwind"]);
  assert.deepEqual([rates.signals[1].value, rates.signals[1].effect], ["No data", "No data"]);
  assert.deepEqual([credit.live, credit.effect], [false, "No data"]);
  assert.match(credit.confidence, /credit-spread/);
  assert.equal(macroView(undefined), null);
  const view = technicalSummaryView(liveFeed);
  assert.equal(view.rows[2].macro.summary, "Macro/pricing signals: No data for this horizon.");
});

test("the company page reads the live feed and renders the macro roll-up", () => {
  assert.match(app, /import \{ liveTechnicalSummary, technicalSummaryView \} from "\.\/technical-summary\.js";/);
  assert.doesNotMatch(app, /exampleTechnicalSummary/);
  const render = app.slice(app.indexOf("function technicalSummaryFor"), app.indexOf("function renderStockPage"));
  assert.match(render, /liveTechnicalSummary\(ticker, session\.chartData\[ticker\]/);
  assert.match(render, /technical-macro-summary/);
  assert.match(render, /Macro & pricing signals/);
});

test("each horizon lists only bullish or bearish indicators; neutrals still count (#207)", () => {
  const horizon = {
    id: "short", label: "Short-term", window: "Next week", requiredBars: 35, availableBars: 300,
    summary: { buy: 1, neutral: 2, sell: 1 },
    groups: [],
    indicators: [
      { name: "RSI (14)", value: "50", vote: 0 },
      { name: "Price vs SMA 10", value: "10 > 9", vote: 1 },
      { name: "MACD", value: "-0.1", vote: -1 },
      { name: "Stochastic", value: "48", vote: 0 },
    ],
  };
  const row = horizonView(horizon);
  assert.deepEqual(row.indicators.map((item) => [item.name, item.vote, item.tone]),
    [["Price vs SMA 10", "Buy", "buy"], ["MACD", "Sell", "sell"]]);
  assert.equal(row.indicators.some((item) => item.vote === "Neutral"), false);
  assert.equal(row.indicatorsNote, "");
  // Tallies and the verdict still include the two neutral votes.
  assert.equal(row.counts, "Buy 1 · Neutral 2 · Sell 1");
  assert.equal(row.rating, horizonView({ ...horizon, indicators: [] }).rating);

  const flat = horizonView({ ...horizon, summary: { buy: 0, neutral: 2, sell: 0 },
    indicators: horizon.indicators.filter((item) => item.vote === 0) });
  assert.deepEqual(flat.indicators, []);
  assert.equal(flat.indicatorsNote, "No directional signals");
  assert.equal(flat.counts, "Buy 0 · Neutral 2 · Sell 0");
  assert.equal(horizonView({ ...horizon, indicators: [] }).indicatorsNote, "");

  const render = app.slice(app.indexOf("function renderTechnicalSummary"), app.indexOf("function renderStockPage"));
  assert.match(render, /else if \(row\.indicatorsNote\)/);
  assert.match(render, /technical-no-signals/);
});

test("macro and pricing signals hide only signals neutral on both effect and vs rating (#210)", () => {
  const signal = (label, effect, relation, extra = {}) => ({ label, effect, relation, status: "live", value: "1", direction: "↑", ...extra });
  const view = macroView({
    net: "net headwind",
    summary: "Macro/pricing signals: net headwind.",
    groups: [{
      id: "rates", label: "Rates & Fed", status: "live", effect: "neutral", relation: "neutral",
      signals: [
        signal("2Y yield", "neutral", "neutral"),
        signal("10Y yield", "headwind", "neutral"),
        signal("Real yield", "neutral", "contradicts"),
        signal("Fed path", "tailwind", "supports"),
        signal("SOFR", "neutral", undefined),
        signal("Credit spreads", undefined, undefined, { status: "no_data" }),
      ],
    }],
  });
  assert.deepEqual(view.groups[0].signals.map((item) => item.label), ["10Y yield", "Real yield", "Fed path", "Credit spreads"]);
  assert.equal(view.groups[0].signals.some((item) => item.effect === "Neutral" && item.relation === "Neutral"), false);
  // The group row itself and the horizon summary are unchanged.
  assert.equal(view.groups.length, 1);
  assert.equal(view.groups[0].effect, "Neutral");
  assert.equal(view.tone, "sell");
});

test("panel layout follows the issue 191 design: counts and chevron in the row, full-width breakdown band (#210)", () => {
  const render = app.slice(app.indexOf("function renderTechnicalSummary"), app.indexOf("function renderStockPage"));
  assert.match(render, /node\("span", "technical-chevron", "▸"\)/);
  assert.match(render, /const expanded = node\("div", "technical-expanded"\)/);
  assert.match(render, /item\.append\(name, ratingCell, body, details, expanded\)/);
  assert.match(render, /expanded\.append\(groups\)/);
  assert.match(styles, /\.technical-row\s*\{[^}]*grid-template-columns:\s*minmax\(150px, 200px\) 130px minmax\(0, 1fr\) 180px/);
  assert.match(styles, /\.technical-row\s*\{[^}]*padding:\s*14px 18px/);
  assert.match(styles, /\.technical-row:has\(> \.technical-details\[open\]\) > \.technical-expanded\s*\{[^}]*display:\s*block/);
  assert.match(styles, /\.technical-expanded\s*\{[^}]*grid-column:\s*1 \/ -1[^}]*background:\s*var\(--surface-raised\)/);
  assert.match(styles, /\.technical-details\[open\] \.technical-chevron\s*\{[^}]*rotate\(90deg\)/);
  const css = styles.slice(styles.indexOf(".technical-summary {"));
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/);
});

test("macro signals with no change to confidence are dropped (#212)", () => {
  const sig = (label, extra) => ({ label, value: "1", direction: "rising", effect: "headwind", relation: "contradicts",
    confidence: "Contradicts the Buy; lowers confidence", impact: -1, ...extra });
  const macro = macroView({ net: "net headwind", events: [], groups: [
    { id: "rates", label: "Rates & Fed", status: "live", effect: "headwind", relation: "contradicts", confidence: "x",
      signals: [sig("10Y"), sig("2Y", { confidence: "No change to confidence", relation: "supports" }),
        sig("30Y", { impact: 0, confidence: "" }), { label: "FOMC path", status: "no_data", note: "n/a" }] },
    { id: "usd", label: "USD", status: "live", effect: "neutral", relation: "neutral",
      confidence: "No change to confidence", signals: [sig("DXY", { confidence: "no change to confidence" })] },
    { id: "credit", label: "Credit", status: "no_data", reason: "none", signals: [] },
  ] });
  assert.deepEqual(macro.groups.map((g) => g.id), ["rates", "credit"]);
  assert.deepEqual(macro.groups[0].signals.map((s) => s.label), ["10Y", "FOMC path"]);
  assert.equal(macro.empty, "");
  assert.equal(noConfidenceChange({ status: "no_data", confidence: "No change to confidence" }), false);
  const quiet = macroView({ groups: [{ id: "usd", label: "USD", status: "live", confidence: "No change to confidence",
    signals: [sig("DXY", { impact: 0 })] }, { id: "credit", label: "Credit", status: "no_data", signals: [] }] });
  assert.equal(quiet.empty, "No directional macro signals");
  assert.deepEqual(quiet.groups.map((g) => g.id), ["credit"]);
});

test("every horizon renders the macro section with the empty line (#212)", () => {
  assert.match(app, /if \(macro\.empty\) details\.append\(node\("p", "technical-note technical-no-signals", macro\.empty\)\);\s*else details\.append\(table\);/);
  assert.equal((app.match(/= renderMacroSignals\(row\)/g) || []).length, 2, "rated and not-enough-data rows both render it");
  const feed = { ...liveFeed, horizons: liveFeed.horizons.map((h) => ({ ...h, macro: liveFeed.horizons[0].macro })) };
  const view = technicalSummaryView(feed);
  assert.deepEqual(view.rows.map((row) => Boolean(row.macro)), [true, true, true]);
});
