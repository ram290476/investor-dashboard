import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { fundamentalSummary } from "./chart-overlays.js";
import { legendItems } from "./chart-legend.js";
import { fiscalAxis } from "./chart-scale.js";
import {
  companyMetricLine,
  formatConsumerDate,
  formatMetricPeriod,
  formatPanelStamp,
  metricBarTitle,
  readablePanelText,
  renderedMetricPanelText,
} from "./panel-dates.js";
import { curveView } from "./rates-panel.js";
import { callsCopy, stockPanels } from "./stock-page.js";
import { trendSentence, trendTitle } from "./trend-state.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const ISO_DATE = /\d{4}-\d{2}-\d{2}/;

test("consumer dates keep the year and quarters stay quarter labels", () => {
  assert.equal(formatConsumerDate("2026-10-11"), "Oct 11, 2026");
  assert.equal(formatConsumerDate("2026-10-11T15:04:00Z"), "Oct 11, 2026");
  assert.equal(formatConsumerDate(" 2026-01-02 "), "Jan 2, 2026");
  assert.equal(formatQuarter("2026Q2"), "Q2 2026");
  const period = formatMetricPeriod("2026Q2", "2026-06-30");
  assert.equal(period.label, "Q2 2026");
  assert.equal(period.title, "Jun 30, 2026");
  assert.equal(formatPanelStamp("2026-10-11T14:30:00Z", "1D"), "Oct 11, 2026 14:30");
  assert.equal(formatPanelStamp("2026-10-11", "1Y"), "Oct 11, 2026");
  assert.equal(readablePanelText("filed 2026-07-15 for 2026Q3"), "filed Jul 15, 2026 for Q3 2026");
  assert.equal(metricBarTitle(
    { fiscal_period: "2026Q2", period_end: "2026-06-30", reported: false },
  ), "Q2 2026 not reported · Jun 30, 2026");
});

test("rendered metric and dashboard panel text has no ISO dates", () => {
  const published = {
    source_url: "https://ir.example/update",
    source_title: "Quarterly update",
    published_date: "2026-08-27",
    confidence: 0.9,
    approval_state: "approved",
  };
  const point = (fiscalPeriod, value, periodEnd) => ({
    fiscal_period: fiscalPeriod,
    period_end: periodEnd,
    value,
    reported: true,
  });
  const payload = {
    metrics: [
      {
        metric_id: "adjusted_ebitda",
        approved: true,
        display_name: "Adjusted EBITDA",
        unit: "USD",
        category: "operating",
        panel_type: "chart",
        latest: { fiscal_period: "2026Q2", value: 1200, reported: true, period_end: "2026-06-30" },
        series: [point("2026Q1", 900, "2026-03-31"), point("2026Q2", 1200, "2026-06-30")],
        provenance: published,
      },
      {
        metric_id: "energy_generation",
        approved: true,
        display_name: "Energy generation",
        unit: "GWh",
        category: "operating",
        panel_type: "chart",
        latest: { fiscal_period: "2026Q3", value: 40, reported: true, period_end: "2026-09-30" },
        series: [point("2026Q2", 30, "2026-06-30"), point("2026Q3", 40, "2026-09-30"), { fiscal_period: "2026Q4", period_end: "2026-12-31", value: null, reported: false }],
        provenance: published,
      },
      {
        metric_id: "storage_revenue",
        approved: true,
        display_name: "Storage revenue",
        unit: "USD",
        category: "operating",
        panel_type: "kpi",
        latest: { fiscal_period: "2026Q2", value: 500, reported: true, period_end: "2026-06-30" },
        series: [point("2026Q2", 500, "2026-06-30")],
        provenance: { ...published, published_date: "2026-10-11" },
      },
      {
        metric_id: "segments",
        approved: true,
        display_name: "Segment mix",
        category: "operating",
        panel_type: "table",
        rows: [{
          segment: "Storage",
          fiscal_period: "2026Q2",
          period_end: "2026-06-30",
          note: "filed 2026-07-15",
        }],
        provenance: published,
      },
      {
        metric_id: "outlook",
        approved: true,
        display_name: "Outlook",
        category: "guidance",
        panel_type: "text",
        text: "Storage revenue through 2026-09-30 for 2026Q3.",
        provenance: published,
      },
    ],
  };
  const panels = stockPanels(payload, {
    fundamentals: [{
      series_id: "revenue_gaap",
      fiscal_quarter: "2025Q4",
      value: 28e9,
      unit: "USD",
      date: "2026-01-29",
      period_end: "2025-12-31",
      source_id: "DS-11",
    }],
  });
  const ebitda = panels.operating.find((card) => card.id === "adjusted_ebitda");
  const storage = panels.operating.find((card) => card.id === "storage_revenue");
  assert.equal(companyMetricLine(ebitda).text.includes("Q2 2026"), true);
  assert.equal(companyMetricLine(ebitda).title, "Jun 30, 2026");
  assert.equal(companyMetricLine(storage).title, "Jun 30, 2026");
  const axis = fiscalAxis(ebitda.series.map((item) => item.fiscal_period));
  const curve = curveView({
    curve: {
      date: "2026-10-11",
      ref_date_1m: "2026-09-11",
      regime: "bear steepening",
      tenors: [{ tenor: "2Y", yield: 3.8, yield_1m: 3.7, chg_1m_bp: 10 }, { tenor: "10Y", yield: 4.2, yield_1m: 4.1, chg_1m_bp: 10 }],
    },
  }, "ready");
  const failed = curveView({
    status: { curve: { source: "FRED", state: "error", last_attempt: "2026-10-08T11:00:00Z", detail: "provider_policy" } },
  }, "ready");
  const trend = {
    state: "uptrend", days: 9, since: "2026-09-20", fromStart: true, asOf: "2026-10-11",
    close: 120, ma20: 110, ma50: 100, vsMa20: 0.05,
  };
  const legend = legendItems({
    ticker: "NVDA",
    prices: [1, 2],
    overlays: [{
      definition: { id: "NEWS:SENTIMENT", label: "7-day news sentiment", kind: "sentiment" },
      values: [0.2],
      observedOn: "2026-10-11",
    }],
  }).map((item) => `${item.text} ${item.title || ""}`).join("\n");
  const calls = callsCopy();
  const rendered = [
    renderedMetricPanelText(panels),
    calls.guidance,
    calls.transcripts,
    axis.ticks.map((tick) => tick.label).join(" "),
    axis.years.map((band) => band.year).join(" "),
    fundamentalSummary("FUNDAMENTAL:revenue_gaap", {
      fundamentals: [{ series_id: "revenue_gaap", date: "2026-01-29", value: 27e9, unit: "USD", fiscal_quarter: "2025Q4" }],
    }, "2026-02-01"),
    legend,
    curve.dates,
    curve.ariaLabel,
    curve.note,
    failed.message,
    trendTitle(trend),
    trendSentence(trend),
    trendSentence({ historyStarts: "2026-06-12", needed: 50, sessionsAvailable: 34, vsMa20: 0.01 }),
    `As of ${formatPanelStamp("2026-10-11")}`,
    formatPanelStamp("2026-10-11T14:30:00Z", "1D"),
    readablePanelText("2026Q1 agency award"),
  ].join("\n");

  assert.doesNotMatch(rendered, ISO_DATE);
  assert.match(rendered, /Q2 2026/);
  assert.match(rendered, /Oct 11, 2026/);
  assert.match(rendered, /Jun 30, 2026/);
  assert.match(rendered, /Aug 27, 2026/);
  assert.match(rendered, /Sep 30, 2026/);
  assert.match(rendered, /Q3 2026/);
  assert.match(rendered, /Q4 2025/);
  assert.match(rendered, /Adjusted EBITDA/);
  assert.match(rendered, /Energy generation/);
  assert.match(rendered, /Storage revenue/);
  assert.doesNotMatch(rendered, /Quarterly notes/);
  assert.match(rendered, /Company metrics/);
});

test("metric panel renderers call the shared date formatter", () => {
  const card = app.slice(app.indexOf("function renderMetricCard"), app.indexOf("function focusMetricReviewError"));
  const table = app.slice(app.indexOf("function renderMetricTable"), app.indexOf("function renderMetricCard"));
  const list = app.slice(app.indexOf("function renderCompanyMetrics"), app.indexOf("function appendMetricGroups"));
  assert.match(card, /metricBarTitle\(/);
  assert.match(card, /metricLatestCopy\(/);
  assert.match(card, /metricSourceBits\(/);
  assert.match(card, /readablePanelText\(/);
  assert.match(table, /tableCellCopy\(/);
  assert.match(card, /formatMetricPeriod\(/);
  assert.doesNotMatch(card, /\$\{point\.fiscal_period\}/);
  assert.doesNotMatch(card, /metric\.published/);
  assert.match(list, /companyMetricLine\(/);
  assert.doesNotMatch(list, /latestPeriod/);
  assert.match(app, /function snapshotLabel/);
  assert.doesNotMatch(app, /Snapshot \$\{session\.dashboard/);
  assert.match(app, /const asOf = formatPanelStamp\(/);
  assert.match(app, /formatPanelStamp\(stamp, periodId\)/);
  assert.doesNotMatch(app, /String\(event\.date\)\.slice\(0, 10\)/);
});

function formatQuarter(value) {
  return formatMetricPeriod(value, "").label;
}
