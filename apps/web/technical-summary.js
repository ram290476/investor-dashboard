/**
 * Technical Summary panel (#191): view model and data interface.
 *
 * The panel renders whatever a provider returns in the TechnicalSummary shape
 * below. The live provider is `liveTechnicalSummary` (#196): dashboard_build
 * computes every indicator from real daily closes and publishes it on
 * serving/chart_data/<T>.json as `technical_summary`. `exampleTechnicalSummary`
 * remains for tests and design captures only; the "Example data" badge shows
 * only when a feed is explicitly `example: true` (#193).
 *
 * Each horizon may carry `macro` (#198): macro and pricing-impact signal groups
 * (context only; they do not change the vote counts) and a net roll-up line.
 *
 * TechnicalSummary = {
 *   ticker, example: boolean, asOf: string (display text), delayed: boolean,
 *   horizons: [{
 *     id: "short" | "intermediate" | "long", label, window,
 *     requiredBars, availableBars,          // drives "Not enough data"
 *     summary: {buy, neutral, sell},        // Summary vote counts
 *     groups: [{id: "ma" | "osc", label, buy, neutral, sell}],
 *     indicators: [{name, value, vote: 1 | 0 | -1}],
 *     explanation, support: number[], resistance: number[], eventRisk: string,
 *     macro?: {net, summary, events: [{name, date}], groups: [{id, label, status, effect, relation,
 *       confidence, reason, signals: [{label, value, direction, effect, relation, confidence, note, status}]}]},
 *   }]
 * }
 */

export const HORIZONS = [
  { id: "short", label: "Short-term", window: "Next week", requiredBars: 35 },
  { id: "intermediate", label: "Intermediate-term", window: "3 weeks – 1 month", requiredBars: 60 },
  { id: "long", label: "Long-term", window: "Next 3 months", requiredBars: 200 },
];

export const DISCLAIMER =
  "Technical summary for informational and educational purposes only. Not investment advice or a recommendation to buy or sell any security. Ratings are derived mechanically from technical indicators, reflect past price data, and do not guarantee future results.";

export const CONSENSUS_NOTE =
  "Percentages are the share of indicator signals that are bullish vs bearish (neutral excluded), not probabilities of a price move.";

export const METHOD_NOTE =
  "Each indicator votes Buy (+1), Neutral (0) or Sell (-1); the score is the mean. Strong Sell < -0.5 ≤ Sell < -0.1 ≤ Neutral ≤ 0.1 < Buy ≤ 0.5 < Strong Buy. Short-term: price vs 10/20-day MAs, RSI(14), Stochastic(14,3,3), MACD(12,26,9). Intermediate: price vs 50-day SMA, MA crossovers, ADX(14), Bollinger(20,2), OBV. Long-term: price vs 100/150/200-day SMA, 50/200 and 100/200 crosses, 3M/6M performance. Computed from daily closes at the last bar close; Stochastic, ADX and support/resistance use close-only forms (no intraday high/low is collected). Macro & pricing signals are context only and do not change the rating.";

const finiteCount = (value) => (Number.isFinite(value) && value >= 0 ? value : 0);

/** Mean vote in -1..+1, or null when there are no votes. */
export function voteScore(counts) {
  const buy = finiteCount(counts?.buy);
  const neutral = finiteCount(counts?.neutral);
  const sell = finiteCount(counts?.sell);
  const total = buy + neutral + sell;
  return total ? (buy - sell) / total : null;
}

/** TradingView thresholds. */
export function ratingLabel(score) {
  if (!Number.isFinite(score)) return null;
  if (score < -0.5) return "Strong Sell";
  if (score < -0.1) return "Sell";
  if (score <= 0.1) return "Neutral";
  if (score <= 0.5) return "Buy";
  return "Strong Buy";
}

export function ratingTone(label) {
  if (label === "Buy" || label === "Strong Buy") return "buy";
  if (label === "Sell" || label === "Strong Sell") return "sell";
  return "neutral";
}

/** Bullish share of directional votes; bullish + bearish always sum to 100. */
export function consensus(counts) {
  const buy = finiteCount(counts?.buy);
  const sell = finiteCount(counts?.sell);
  if (!buy && !sell) return null;
  const bullish = Math.round((buy / (buy + sell)) * 100);
  return { bullish, bearish: 100 - bullish };
}

export function formatScore(score) {
  if (!Number.isFinite(score)) return "--";
  const rounded = Math.round(score * 100) / 100;
  if (rounded === 0) return "0.00";
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}`;
}

export function countsText(counts) {
  return `Buy ${finiteCount(counts?.buy)} · Neutral ${finiteCount(counts?.neutral)} · Sell ${finiteCount(counts?.sell)}`;
}

function groupView(group) {
  const score = voteScore(group);
  const rating = ratingLabel(score);
  return {
    id: group.id,
    label: group.label,
    rating: rating || "--",
    tone: ratingTone(rating),
    counts: `${finiteCount(group.buy)} / ${finiteCount(group.neutral)} / ${finiteCount(group.sell)}`,
    score: formatScore(score),
  };
}

/** Turns one provider horizon into display rows; never throws on missing data. */
const EFFECT_TONE = { tailwind: "buy", headwind: "sell" };

function effectText(effect) {
  return effect === "tailwind" ? "Tailwind" : effect === "headwind" ? "Headwind" : "Neutral";
}

const RELATION_TEXT = { supports: "Supports", contradicts: "Contradicts", neutral: "Neutral" };

/** True when a live macro/pricing signal is neutral on both effect and relation to the rating. */
export function neutralSignal(signal) {
  if (!signal || signal.status === "no_data") return false;
  if (noConfidenceChange(signal)) return true;
  return !EFFECT_TONE[signal.effect] && (RELATION_TEXT[signal.relation] || "Neutral") === "Neutral";
}

// macro_signals._relation() writes `confidence` = "No change to confidence" exactly when
// the signal's `impact` is 0 (#212). Either marks a signal that does not move the rating.
export const NO_CONFIDENCE_CHANGE = "No change to confidence";

export function noConfidenceChange(item) {
  if (!item || item.status === "no_data") return false;
  return String(item.confidence || "").trim().toLowerCase().startsWith(NO_CONFIDENCE_CHANGE.toLowerCase())
    || item.impact === 0;
}

/** Display rows for one horizon's macro & pricing-impact block; null when absent. */
export function macroView(macro) {
  if (!macro || !Array.isArray(macro.groups)) return null;
  const tone = macro.net === "net tailwind" ? "buy" : macro.net === "net headwind" ? "sell" : "neutral";
  const groups = macro.groups.map((group) => {
    const live = group.status === "live";
    return {
      id: group.id,
      label: group.label,
      live,
      effect: live ? effectText(group.effect) : "No data",
      tone: live ? EFFECT_TONE[group.effect] || "neutral" : "neutral",
      relation: live ? RELATION_TEXT[group.relation] || "Neutral" : "—",
      confidence: live ? group.confidence || "" : group.reason || "No source collected yet.",
      events: (group.events || []).map((event) => `${event.name} ${event.date}`),
      // Only directional signals (#210): drop a signal whose effect and "vs rating" are
      // both neutral. A no-data signal is not neutral and stays listed as missing.
      signals: (group.signals || []).filter((signal) => !neutralSignal(signal)).map((signal) => {
        const noData = signal.status === "no_data";
        return {
          label: signal.label,
          value: noData ? "No data" : signal.value ?? "--",
          direction: noData ? "—" : signal.direction || "—",
          effect: noData ? "No data" : effectText(signal.effect),
          tone: noData ? "neutral" : EFFECT_TONE[signal.effect] || "neutral",
          relation: noData ? "—" : RELATION_TEXT[signal.relation] || "Neutral",
          note: noData ? signal.note || "No source collected yet." : signal.confidence || signal.note || "",
          title: signal.note || "",
        };
      }),
    };
  });
  // #212: drop a group whose own confidence is "No change to confidence" once none of
  // its signals remain; no-data groups stay so missing sources are still named.
  const kept = groups.filter((group, index) => group.signals.length
    || !group.live || !noConfidenceChange(macro.groups[index]));
  const directional = kept.some((group) => group.signals.some((signal) => signal.effect !== "No data"));
  return {
    tone,
    summary: macro.summary || "Macro/pricing signals: No data for this horizon.",
    events: (macro.events || []).map((event) => `${event.name} ${event.date}`),
    groups: kept,
    empty: directional ? "" : "No directional macro signals",
  };
}

export function horizonView(horizon, ticker = "", unavailable = "") {
  const base = HORIZONS.find((item) => item.id === horizon?.id) || {};
  const label = horizon?.label || base.label || "Horizon";
  const window = horizon?.window || base.window || "";
  const required = horizon?.requiredBars ?? base.requiredBars ?? 0;
  const available = horizon?.availableBars;
  const score = voteScore(horizon?.summary);
  const rating = ratingLabel(score);
  if (unavailable) {
    return { id: horizon?.id || base.id, label, window, enoughData: false, message: unavailable, macro: null };
  }
  if ((Number.isFinite(available) && available < required) || !rating) {
    const have = Number.isFinite(available) ? ` (${ticker || "this ticker"} has ${available})` : "";
    return {
      id: horizon?.id || base.id,
      label,
      window,
      enoughData: false,
      message: `Not enough data — needs ≥ ${required} trading days of history${have}.`,
      macro: macroView(horizon?.macro),
    };
  }
  const split = consensus(horizon.summary);
  return {
    id: horizon.id,
    label,
    window,
    enoughData: true,
    rating,
    tone: ratingTone(rating),
    score: formatScore(score),
    bullish: split?.bullish ?? 50,
    bearish: split?.bearish ?? 50,
    consensusText: split ? `${split.bullish}% of signals bullish` : "No directional signals",
    bearishText: split ? `${split.bearish}% bearish` : "",
    counts: countsText(horizon.summary),
    explanation: horizon.explanation || "",
    eventRisk: horizon.eventRisk || "",
    groups: [
      ...(horizon.groups || []).map(groupView),
      { ...groupView({ id: "summary", label: "Summary", ...horizon.summary }) },
    ],
    // Only bullish or bearish indicators are listed (#207). Neutral votes still count in
    // horizon.summary, so the tallies, split and verdict above are unchanged.
    indicators: (horizon.indicators || []).filter((item) => item.vote > 0 || item.vote < 0).map((item) => ({
      name: item.name,
      value: item.value ?? "--",
      vote: item.vote > 0 ? "Buy" : "Sell",
      tone: item.vote > 0 ? "buy" : "sell",
    })),
    indicatorsNote: (horizon.indicators || []).length && !(horizon.indicators || []).some((item) => item.vote > 0 || item.vote < 0)
      ? "No directional signals"
      : "",
    support: (horizon.support || []).filter(Number.isFinite).map((value) => value.toFixed(2)),
    resistance: (horizon.resistance || []).filter(Number.isFinite).map((value) => value.toFixed(2)),
    macro: macroView(horizon.macro),
  };
}

/** Low agreement when horizons with data point in opposite directions or one is Neutral against a directional one. */
export function agreementNote(rows) {
  const tones = rows.filter((row) => row.enoughData).map((row) => row.tone);
  if (tones.length < 2) return "";
  const distinct = new Set(tones);
  if (distinct.size === 1) return "";
  const names = rows.filter((row) => row.enoughData).map((row) => `${row.label.toLowerCase()} (${row.rating})`);
  return `Low agreement: ${names.join(", ")} do not align.`;
}

export function technicalSummaryView(summary) {
  const ticker = summary?.ticker || "";
  const unavailable = typeof summary?.unavailable === "string" ? summary.unavailable : "";
  const rows = (summary?.horizons?.length ? summary.horizons : HORIZONS)
    .map((horizon) => horizonView(horizon, ticker, unavailable));
  const agreement = agreementNote(rows);
  return {
    ticker,
    // #193: the badge only appears when a feed is explicitly marked as example data.
    example: summary?.example === true,
    asOf: summary?.asOf ? `As of ${summary.asOf}${summary.delayed === false ? "" : " · data delayed"}` : "As of --",
    rows: rows.map((row, index) => ({ ...row, lowAgreement: Boolean(agreement) && index === 0 && row.enoughData })),
    agreement,
    disclaimer: DISCLAIMER,
    consensusNote: CONSENSUS_NOTE,
    method: METHOD_NOTE,
  };
}

/**
 * LIVE provider (#196): the serving-layer `technical_summary` on the ticker's chart_data
 * document. While chart data loads, or when a build predates the feed, every horizon shows
 * a plain status line instead of a rating. Never falls back to example values.
 */
export function liveTechnicalSummary(ticker, chartData, state = "ready") {
  const symbol = String(ticker || "");
  const served = chartData?.technical_summary;
  if (served && Array.isArray(served.horizons)) return { ...served, ticker: served.ticker || symbol };
  const unavailable = state === "loading"
    ? "Loading live indicators…"
    : `Live indicators are not published for ${symbol || "this ticker"} yet.`;
  return { ticker: symbol, example: false, asOf: null, unavailable, horizons: HORIZONS.map((base) => ({ ...base })) };
}

const EXAMPLE_FULL = [
  {
    id: "short",
    summary: { buy: 7, neutral: 3, sell: 4 },
    groups: [
      { id: "ma", label: "Moving Averages", buy: 5, neutral: 1, sell: 2 },
      { id: "osc", label: "Oscillators", buy: 2, neutral: 2, sell: 2 },
    ],
    indicators: [
      { name: "Price vs EMA 20", value: "101.4 > 98.7", vote: 1 },
      { name: "MACD (12,26,9)", value: "0.84 > 0.61", vote: 1 },
      { name: "RSI (14)", value: "58", vote: 0 },
      { name: "Stochastic (14,3,3)", value: "83", vote: -1 },
    ],
    explanation: "Price above 20-day EMA, MACD above signal, RSI 58 (not overbought).",
    support: [96.2, 93.8],
    resistance: [104.5, 108.1],
  },
  {
    id: "intermediate",
    summary: { buy: 6, neutral: 4, sell: 4 },
    groups: [
      { id: "ma", label: "Moving Averages", buy: 4, neutral: 2, sell: 2 },
      { id: "osc", label: "Oscillators", buy: 2, neutral: 2, sell: 2 },
    ],
    explanation: "Price above 50-day SMA; ADX 24 confirms a moderate uptrend.",
    eventRisk: "Earnings in 9 days (inside this horizon) — event risk.",
    support: [93.8, 90.4],
    resistance: [108.1, 112.0],
  },
  {
    id: "long",
    summary: { buy: 9, neutral: 2, sell: 2 },
    groups: [
      { id: "ma", label: "Moving Averages", buy: 7, neutral: 1, sell: 0 },
      { id: "osc", label: "Oscillators", buy: 2, neutral: 1, sell: 2 },
    ],
    explanation: "Price above 200-day SMA; 50-day above 200-day (golden cross); 3M performance +11% vs SPY +4%.",
    support: [88.5, 82.0],
    resistance: [112.0, 120.0],
  },
];

const EXAMPLE_LIMITED = [
  {
    id: "short",
    summary: { buy: 6, neutral: 4, sell: 7 },
    groups: [
      { id: "ma", label: "Moving Averages", buy: 4, neutral: 2, sell: 3 },
      { id: "osc", label: "Oscillators", buy: 2, neutral: 2, sell: 4 },
    ],
    explanation: "Mixed signals: price above 20-day EMA but MACD below signal and RSI 47.",
    support: [96.2, 93.8],
    resistance: [104.5, 108.1],
  },
  {
    id: "intermediate",
    summary: { buy: 4, neutral: 3, sell: 7 },
    groups: [
      { id: "ma", label: "Moving Averages", buy: 2, neutral: 2, sell: 4 },
      { id: "osc", label: "Oscillators", buy: 2, neutral: 1, sell: 3 },
    ],
    explanation: "Price below 50-day SMA; ADX 18 (weak trend).",
    support: [93.8, 90.4],
    resistance: [108.1, 112.0],
  },
  { id: "long" },
];

/**
 * EXAMPLE provider. Values are illustrative and identical for every ticker;
 * only the ticker name and real history length (for "Not enough data") vary.
 * Replace with a serving-layer feed returning the same shape.
 */
export function exampleTechnicalSummary(ticker, { history = [] } = {}) {
  const bars = Array.isArray(history) ? history.length : 0;
  const limited = bars > 0 && bars < 200;
  const rows = limited ? EXAMPLE_LIMITED : EXAMPLE_FULL;
  return {
    ticker: String(ticker || ""),
    example: true,
    asOf: "Oct 13, 2026 4:00 PM ET",
    delayed: true,
    horizons: HORIZONS.map((base, index) => ({
      ...base,
      ...rows[index],
      availableBars: limited ? bars : undefined,
    })),
  };
}
