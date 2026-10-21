import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { contrastRatio, THEMES, themeProperties } from "./theme.js";
import {
  approvedMetrics,
  callsCopy,
  emptyKpiCopy,
  fillQuarterGaps,
  fiscalQuarterEnd,
  formatMetricValue,
  freshnessBadge,
  monthDayLabel,
  nextStockTab,
  panelType,
  pendingMetrics,
  stockFreshnessText,
  safeHttpUrl,
  stockPanels,
  STOCK_TABS,
  companyName,
} from "./stock-page.js";

const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

test("proposed metrics stay labeled pending and missing quarters say not reported", () => {
  const payload = {
    metrics: [
      { metric_id: "optimus", approved: false, approval_state: "proposed", display_name: "Optimus", latest: { value: 9 } },
      {
        metric_id: "tesla_semi",
        approved: true,
        approval_state: "approved",
        display_name: "Tesla Semi",
        unit: "vehicles",
        category: "operating",
        latest: { fiscal_period: "2025Q4", value: 4, reported: true },
        qoq: 1,
        yoy: null,
        series: [
          { fiscal_period: "2025Q1", value: 2, reported: true },
          { fiscal_period: "2025Q4", value: 4, reported: true },
        ],
        provenance: {
          source_url: "https://ir.example/update",
          source_title: "Fixture deck",
          published_date: "2026-01-29",
          confidence: 0.95,
          approval_state: "approved",
        },
      },
    ],
  };
  assert.deepEqual(approvedMetrics(payload).map((metric) => metric.metric_id), ["tesla_semi"]);
  assert.deepEqual(pendingMetrics(payload).map((metric) => metric.metric_id), ["optimus"]);
  const panels = stockPanels(payload, { fundamentals: [] });
  const card = panels.operating[0];
  assert.equal(card.name, "Tesla Semi");
  assert.equal(card.pending, false);
  assert.equal(panels.operating[1].id, "optimus");
  assert.equal(panels.operating[1].pending, true);
  assert.equal(panels.operating[1].approval, "proposed");
  const approvedOnly = stockPanels(payload, { fundamentals: [] }, { review: "approved" });
  assert.equal(approvedOnly.operating.some((item) => item.id === "optimus"), false);
  assert.equal(card.series.find((point) => point.fiscal_period === "2025Q2").reported, false);
  assert.equal(formatMetricValue(null, "vehicles"), "not reported");
  assert.equal(card.qoq, "+100.0%");
  assert.equal(card.yoy, null);
  assert.equal(card.sourceUrl, "https://ir.example/update");
  assert.equal(safeHttpUrl("javascript:alert(1)"), "");
  assert.equal(panels.fundamentalsSource, "none");
});

test("panel type follows the registry and the shape of collected values", () => {
  assert.equal(panelType({ panel_type: "kpi", series: [{ value: 1, reported: true }, { value: 2, reported: true }] }), "kpi");
  assert.equal(panelType({ rows: [{ segment: "Auto", value: 1 }] }), "table");
  assert.equal(panelType({ text: "Factory note" }), "text");
  assert.equal(panelType({ narrative: "Starlink note", series: [{ value: 1, reported: true }] }), "text");
  assert.equal(panelType({ latest: { value: 4, reported: true }, series: [] }), "kpi");
  assert.equal(panelType({
    series: [
      { fiscal_period: "2025Q1", value: 1, reported: true },
      { fiscal_period: "2025Q2", value: 2, reported: true },
    ],
  }), "chart");
  const table = stockPanels({
    metrics: [{
      metric_id: "segments",
      approved: false,
      approval_state: "proposed",
      display_name: "Segments",
      category: "operating",
      panel_type: "table",
      rows: [{ name: "Automotive", value: 1 }],
    }],
  }, null);
  assert.equal(table.operating[0].panelType, "table");
  assert.deepEqual(table.operating[0].rows, [{ name: "Automotive", value: 1 }]);
  assert.equal(table.operating[0].pending, true);
  const text = stockPanels({
    metrics: [{
      metric_id: "factories",
      approved: true,
      approval_state: "approved",
      display_name: "Factories",
      category: "operating",
      panel_type: "text",
      text: "Gigafactory note",
    }],
  }, null);
  assert.equal(text.operating[0].panelType, "text");
  assert.equal(text.operating[0].text, "Gigafactory note");
  assert.equal(text.operating[0].pending, false);
});

test("collected XBRL metrics and CapEx render as pending charts with revision context", () => {
  const ids = [
    "revenue_gaap",
    "net_income_gaap",
    "operating_income",
    "research_and_development",
    "sga",
    "eps_diluted",
    "deferred_revenue",
    "capex",
    "operating_cash_flow",
    "cash_and_investments",
  ];
  const names = {
    capex: "Capital expenditures",
    operating_cash_flow: "Operating cash flow",
    free_cash_flow: "Free cash flow",
  };
  const series = [
    { fiscal_period: "2024Q4", value: 8, reported: true },
    { fiscal_period: "2025Q1", value: 9, reported: true },
    { fiscal_period: "2025Q2", value: 10, reported: true },
  ];
  const metrics = [...ids, "free_cash_flow"].map((id) => ({
    metric_id: id,
    approved: false,
    approval_state: "proposed",
    display_name: names[id] || id,
    unit: "USD",
    category: "fundamentals",
    latest: { fiscal_period: "2025Q2", value: 10, reported: true },
    series,
    revised_from: id === "capex" ? "2025Q1" : "",
    revision_note: id === "capex" ? "Capex uses a revised definition from 2025Q1." : "",
    provenance: { source_kind: "xbrl", source_url: "https://www.sec.gov/example", confidence: 0.95, approval_state: "proposed" },
  }));
  const payload = { metrics, freshness_label: "Pending review", run_status: "ok" };
  const panels = stockPanels(payload, { fundamentals: [] });
  assert.equal(panels.fundamentalsSource, "ir");
  assert.equal(panels.fundamentals[0].id, "capex");
  assert.equal(panels.fundamentals.every((card) => card.pending), true);
  for (const id of ids) {
    const card = panels.fundamentals.find((item) => item.id === id);
    assert.equal(card.panelType, "chart", id);
    assert.equal(card.pending, true, id);
  }
  const capex = panels.fundamentals[0];
  assert.equal(capex.name, "CapEx");
  assert.equal(capex.detailName, "Capital expenditures");
  assert.equal(capex.series.find((point) => point.fiscal_period === "2024Q4").revised, false);
  assert.equal(capex.series.find((point) => point.fiscal_period === "2025Q1").revised, true);
  assert.match(capex.revisionNote, /2025Q1/);
  assert.deepEqual(capex.context.map((item) => item.name), ["Operating cash flow", "Free cash flow"]);
  assert.equal(capex.context.every((item) => item.pending), true);
  const badge = freshnessBadge(payload, "ready");
  assert.equal(badge.tone, "pending");
  assert.equal(badge.text, "Pending review");
  assert.equal(freshnessBadge(payload, "ready", "TSLA", "approved").text, "No approved company metrics");
  const hidden = stockPanels(payload, { fundamentals: [] }, { review: "approved" });
  assert.equal(hidden.pendingCount, 0);
  assert.equal(hidden.fundamentals.length, 0);
  const cardSource = app.slice(app.indexOf("function renderMetricCard"), app.indexOf("async function submitMetricReview"));
  assert.doesNotMatch(cardSource, /review-badge|renderReviewBadge|Pending review|stock-metric-pending/);
  const metricsSource = app.slice(app.indexOf("function renderCompanyMetrics"), app.indexOf("function appendMetricGroups"));
  assert.match(metricsSource, /renderReviewBadge\(\)/);
  assert.match(metricsSource, /Pending review/);
  assert.match(app.slice(app.indexOf("function renderReviewBadge"), app.indexOf("function renderMetricTable")), /review-badge/);
  assert.match(app, /stock-metric-capex/);
  assert.match(app, /stock-bar-revised-mark", "Rev"/);
  assert.match(styles, /\.stock-bar-revised\s*\{[^}]*repeating-linear-gradient/);
  const mixed = stockPanels({
    metrics: [
      {
        metric_id: "capex",
        approved: false,
        approval_state: "proposed",
        display_name: "Capital expenditures",
        unit: "USD",
        category: "fundamentals",
        latest: { fiscal_period: "2025Q2", value: 3, reported: true },
        series,
      },
      {
        metric_id: "revenue_gaap",
        approved: true,
        approval_state: "approved",
        display_name: "Revenue",
        unit: "USD",
        category: "fundamentals",
        latest: { fiscal_period: "2025Q2", value: 10, reported: true },
        series,
        provenance: { source_kind: "xbrl", approval_state: "approved" },
      },
    ],
  }, { fundamentals: [] });
  assert.deepEqual(mixed.fundamentals.map((card) => card.id), ["revenue_gaap", "capex"]);
  assert.equal(mixed.fundamentals[0].pending, false);
  assert.equal(mixed.fundamentals[1].pending, true);
});

test("company metric errors stay distinct from an empty catalog", () => {
  assert.equal(freshnessBadge(null, "forbidden", "TSLA").text, "Add TSLA to your watchlist to see company metrics");
  assert.equal(freshnessBadge(null, "missing").text, "No company-specific metrics discovered");
  assert.equal(freshnessBadge(null, "error").text, "Company metrics could not be loaded. Retry");
  const ready = freshnessBadge({
    freshness_label: "IR data approved",
    run_status: "ok",
    generated_at: "2026-10-08T12:00:00Z",
    metrics: [{ metric_id: "tesla_semi", approved: true, approval_state: "approved" }],
  }, "ready");
  assert.equal(ready.tone, "ok");
  const now = new Date("2026-10-10T15:00:00Z");
  assert.equal(stockFreshnessText({
    freshness_label: "IR data approved",
    run_status: "ok",
    generated_at: "2026-10-08T12:00:00Z",
    metrics: [{ metric_id: "tesla_semi", approved: true, approval_state: "approved" }],
  }, "ready", "TSLA", now, "America/New_York"), "IR data approved · ok · Oct 8");
  assert.equal(monthDayLabel("2026-10-02", now, "America/New_York"), "Oct 2");
  assert.equal(monthDayLabel("2025-10-02", now, "UTC"), "Oct 2, 2025");
});

test("a partial payload keeps a stale badge and does not invent a zero", () => {
  const badge = freshnessBadge({
    stale: true,
    run_status: "partial",
    freshness_label: "Partial run · previous approved values kept",
    metrics: [{ metric_id: "optimus", approved: true, approval_state: "approved", latest: { value: 4, reported: true }, series: [] }],
  }, "ready");
  assert.equal(badge.tone, "partial");
  assert.match(badge.text, /previous approved values kept/);
  assert.equal(formatMetricValue(0, "vehicles") === "not reported", false);
  assert.doesNotMatch(emptyKpiCopy(), /0/);
});

test("edgar fundamentals fill the page only when no approved IR fundamentals exist", () => {
  const chart = {
    fundamentals: [
      { series_id: "revenue_gaap", fiscal_quarter: "2025Q3", value: 25e9, unit: "USD", date: "2025-10-20", source_id: "DS-11" },
      { series_id: "revenue_gaap", fiscal_quarter: "2025Q4", value: 28e9, unit: "USD", date: "2026-01-29", source_id: "DS-11" },
    ],
  };
  const panels = stockPanels({ metrics: [] }, chart);
  assert.equal(panels.fundamentalsSource, "edgar");
  assert.equal(panels.fundamentals[0].name, "Revenue");
  assert.equal(panels.fundamentals[0].sourceTitle, "DS-11");
  assert.match(panels.fundamentals[0].latest, /\$/);
  const withIr = stockPanels({
    metrics: [{
      metric_id: "revenue_gaap",
      approved: true,
      approval_state: "approved",
      display_name: "Revenue",
      unit: "USD",
      category: "fundamentals",
      latest: { fiscal_period: "2025Q4", value: 28e9, reported: true },
      series: [{ fiscal_period: "2025Q4", value: 28e9, reported: true }],
      provenance: { source_url: "https://ir.example/letter", source_title: "Update", published_date: "2026-01-29", confidence: 0.95, approval_state: "approved" },
      xbrl: { status: "mismatch" },
    }],
  }, chart);
  assert.equal(withIr.fundamentalsSource, "ir");
  assert.equal(withIr.fundamentals.length, 1);
  assert.equal(withIr.fundamentals[0].xbrl, "XBRL mismatch");
});

test("review mode shows or hides operating, guidance, and CapEx panels together", () => {
  const series = [
    { fiscal_period: "2025Q1", value: 1, reported: true },
    { fiscal_period: "2025Q2", value: 2, reported: true },
  ];
  const metric = (id, category, approved) => ({
    metric_id: id,
    approved,
    approval_state: approved ? "approved" : "proposed",
    display_name: id,
    unit: "USD",
    category,
    latest: { fiscal_period: "2025Q2", value: 2, reported: true },
    series,
  });
  const payload = {
    metrics: [
      metric("data_center_revenue", "operating", true),
      metric("robotaxi_rides", "operating", false),
      metric("outlook", "guidance", false),
      metric("capex", "fundamentals", false),
      metric("revenue_gaap", "fundamentals", true),
    ],
  };
  const showingPending = stockPanels(payload, { fundamentals: [] }, { review: "all" });
  assert.deepEqual(showingPending.operating.map((card) => card.id), ["data_center_revenue", "robotaxi_rides"]);
  assert.equal(showingPending.guidance.some((card) => card.id === "outlook" && card.pending), true);
  assert.equal(showingPending.fundamentals.some((card) => card.id === "capex" && card.pending), true);
  assert.equal(showingPending.fundamentals.some((card) => card.id === "revenue_gaap" && card.pending === false), true);
  const approvedOnly = stockPanels(payload, { fundamentals: [] }, { review: "approved" });
  assert.deepEqual(approvedOnly.operating.map((card) => card.id), ["data_center_revenue"]);
  assert.equal(approvedOnly.guidance.length, 0);
  assert.equal(approvedOnly.fundamentals.some((card) => card.id === "capex"), false);
  assert.equal(approvedOnly.fundamentals.some((card) => card.id === "revenue_gaap"), true);
  const pendingOnly = stockPanels({
    metrics: [
      metric("robotaxi_rides", "operating", false),
      metric("outlook", "guidance", false),
      metric("capex", "fundamentals", false),
    ],
  }, { fundamentals: [] }, { review: "approved" });
  assert.equal(pendingOnly.operating.length, 0);
  assert.equal(pendingOnly.guidance.length, 0);
  assert.equal(pendingOnly.fundamentals.length, 0);
  const groups = app.slice(app.indexOf("function appendMetricGroups"), app.indexOf("function renderStockPage"));
  assert.match(groups, /if \(!cards\.length\)[\s\S]*node\("p", "data-state", empty\)/);
});

test("stock tabs move by keyboard and calls stay unlicensed", () => {
  assert.equal(nextStockTab("summary", "ArrowRight"), "analysis");
  assert.equal(nextStockTab("trends", "ArrowRight"), "analysis");
  assert.equal(nextStockTab("analysis", "ArrowRight"), "fundamentals");
  assert.equal(nextStockTab("overview", "ArrowRight"), "fundamentals");
  assert.equal(nextStockTab("technical", "ArrowRight"), "fundamentals");
  assert.equal(nextStockTab("fundamentals", "ArrowRight"), "kpis");
  assert.equal(nextStockTab("summary", "End"), "kpis");
  assert.equal(nextStockTab("kpis", "ArrowRight"), "summary");
  assert.equal(nextStockTab("kpis", "Home"), "summary");
  assert.deepEqual(STOCK_TABS.map((tab) => tab.id), ["summary", "analysis", "fundamentals", "kpis"]);
  assert.deepEqual(STOCK_TABS.map((tab) => tab.label), ["Overview", "Analysis", "Fundamentals", "KPIs"]);
  assert.equal(STOCK_TABS.find((tab) => tab.id === "summary").label, "Overview");
  assert.equal(STOCK_TABS.find((tab) => tab.id === "analysis").label, "Analysis");
  assert.equal(STOCK_TABS.length, 4);
  assert.match(callsCopy().transcripts, /not collected/);
  const gap = fillQuarterGaps([
    { fiscal_period: "2025Q1", value: 1, reported: true },
    { fiscal_period: "2025Q2", value: null, reported: false },
  ]);
  assert.equal(gap[1].reported, false);
  assert.equal(gap[1].value, null);
});

test("stock page panels are two-up on desktop and one-up below 1024px", () => {
  assert.match(styles, /\.stock-metric-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(styles, /@media \(max-width: 1023px\)[\s\S]*?\.stock-metric-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(styles, /\.stock-chart\s*\{[^}]*grid-template-columns:\s*auto minmax\(0,\s*1fr\)/);
  assert.match(styles, /\.stock-chart\s*\{[^}]*width:\s*100%/);
  assert.match(styles, /\.stock-bars\s*\{[^}]*grid-column:\s*2/);
  assert.match(styles, /\.stock-quarters\s*\{[^}]*grid-column:\s*2/);
  assert.match(styles, /\.stock-bar\s*\{[^}]*min-width:\s*0/);
  assert.match(styles, /\.stock-metric[\s\S]*var\(--/);
  assert.doesNotMatch(styles, /\.stock-metric[^{]*\{[^}]*#[0-9a-fA-F]{3,8}/);
  assert.match(app, /class="stock-chart"|node\("div", "stock-chart"\)/);
  assert.match(app, /node\("div", "stock-axis stock-quarters"\)/);
  const card = app.slice(app.indexOf("function renderMetricCard"), app.indexOf("function renderStockPage"));
  assert.doesNotMatch(card, /stock-provenance|stock-xbrl|DS-11/);
});

test("company header shows only the company name and no Back to dashboard button", () => {
  const header = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  const heading = header.slice(header.indexOf('node("section", "panel stock-heading")'), header.indexOf('const page = node("main", "stock-page")'));
  assert.equal(heading.indexOf("Back to dashboard"), -1);
  assert.doesNotMatch(heading, /stock-dashboard-link/);
  assert.match(heading, /node\("h2", "", companyName\(session\.selected, payload, tickerData\)\)/);
  assert.doesNotMatch(heading, /\$\{session\.selected\}/);
  assert.match(heading, /copy\.append\(title\)/);
  assert.match(heading, /heading\.append\(copy\)/);
  assert.doesNotMatch(heading, /heading\.append\(copy, back\)/);
  assert.doesNotMatch(heading, /stock-price|stock-freshness|overview-meta|formatPrice|monthDay|session\.selected\} ·/);
  assert.match(styles, /\.stock-heading\s*\{[^}]*display:\s*grid/);
  assert.match(styles, /\.stock-heading\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) auto/);
  assert.match(styles, /\.stock-heading\s*\{[^}]*align-items:\s*center/);
  assert.match(styles, /\.stock-heading\s*\{[^}]*padding-block:\s*var\(--space-3\)/);
  assert.match(styles, /\.stock-heading\s*\{[^}]*padding-inline:\s*calc\(var\(--space-2\) \* 2\)/);
  assert.match(styles, /\.stock-heading\s*\{[^}]*margin-block:\s*var\(--space-3\)/);
  assert.match(styles, /--space-3:\s*12px/);
  assert.match(styles, /\.stock-page\s*\{[^}]*gap:\s*var\(--space-3\)/);
  assert.match(styles, /\.overview\s*\{[^}]*padding:\s*14px 16px 12px/);
  assert.match(styles, /\.stock-section\s*\{[^}]*padding:\s*14px 16px 16px/);
  const mobileAt = styles.lastIndexOf(".stock-heading {");
  const mobileHeading = styles.slice(mobileAt, styles.indexOf(".stock-page-tabs {", mobileAt));
  assert.match(mobileHeading, /align-items:\s*center/);
  assert.doesNotMatch(mobileHeading, /padding-inline:\s*0/);
  assert.doesNotMatch(mobileHeading, /padding-block:\s*0/);
  assert.doesNotMatch(mobileHeading, /margin-block:\s*0/);
  assert.doesNotMatch(styles, /\.stock-dashboard-link/);
  assert.doesNotMatch(styles, /\.stock-heading\s*\{[^}]*align-items:\s*start/);
  assert.doesNotMatch(styles, /\.stock-heading-copy\s*\{[^}]*flex-basis:\s*100%/);
});

test("company header uses the company name and falls back to the ticker only when no name exists", () => {
  assert.equal(companyName("TSLA", { company_name: "Tesla, Inc." }), "Tesla, Inc.");
  assert.equal(companyName("TSLA", { company_name: "Tesla" }), "Tesla");
  assert.equal(companyName("NVDA", null, { company_name: "NVIDIA Corporation" }), "NVIDIA Corporation");
  assert.equal(companyName("NVDA", { entityName: "NVIDIA CORP" }), "NVIDIA CORP");
  assert.equal(companyName("NVDA", { company_name: "NVDA" }, { name: "NVIDIA" }), "NVIDIA");
  assert.equal(companyName("TSLA", null, null), "Tesla, Inc.");
  assert.equal(companyName("SPCX", {}, {}), "SpaceX");
  assert.equal(companyName("ABCD", { company_name: "ABCD" }, { name: "ABCD" }), "ABCD");
  assert.equal(companyName("ABCD", null, {}), "ABCD");
});

function operatingSeries(periods, extra = {}) {
  return {
    metrics: [{
      metric_id: "data_center_revenue",
      approved: true,
      approval_state: "approved",
      display_name: "Data center revenue",
      unit: "USD",
      category: "operating",
      latest: { fiscal_period: periods.at(-1).fiscal_period, value: periods.at(-1).value, reported: true },
      series: periods,
    }],
  };
}

function fiscalPoints(ticker, fromYear, fromQuarter, toYear, toQuarter) {
  const points = [];
  let year = fromYear;
  let quarter = fromQuarter;
  let value = 1;
  while (year < toYear || (year === toYear && quarter <= toQuarter)) {
    points.push({ fiscal_period: `${year}Q${quarter}`, value, reported: true });
    value += 1;
    quarter += 1;
    if (quarter === 5) {
      year += 1;
      quarter = 1;
    }
  }
  return points.map((point) => ({ ...point, ticker }));
}

test("NVDA and MRVL quarter ends use the fiscal calendar, not the label year", () => {
  assert.equal(fiscalQuarterEnd("2027Q2", "NVDA"), "2026-07-26");
  assert.equal(fiscalQuarterEnd("2026Q1", "NVDA"), "2025-04-27");
  assert.equal(fiscalQuarterEnd("2026Q4", "NVDA"), "2026-01-25");
  assert.equal(fiscalQuarterEnd("2026Q1", "MRVL"), "2025-05-03");
  assert.equal(fiscalQuarterEnd("2027Q1", "MRVL"), "2026-05-02");
  assert.equal(fiscalQuarterEnd("2027Q2", "MRVL"), "2026-08-01");
  assert.equal(fiscalQuarterEnd("2026Q2", "TSLA"), "2026-06-30");
});

test("company metric panels follow the chart period by period-end date", () => {
  const asOf = "2026-10-11";
  const nvda = fiscalPoints("NVDA", 2026, 1, 2027, 3).map(({ fiscal_period, value, reported }) => ({
    fiscal_period, value, reported,
  }));
  const oneYear = stockPanels(operatingSeries(nvda), null, { ticker: "NVDA", period: "1Y", asOf });
  const kept = oneYear.operating[0].series.filter((point) => point.reported).map((point) => point.fiscal_period);
  assert.deepEqual(kept, ["2026Q3", "2026Q4", "2027Q1", "2027Q2"]);
  assert.equal(oneYear.operating[0].series.find((point) => point.fiscal_period === "2027Q2").period_end, "2026-07-26");

  const overridden = stockPanels(operatingSeries([
    { fiscal_period: "2024Q1", period_end: "2027-06-30", value: 1, reported: true },
    { fiscal_period: "2024Q2", period_end: "2026-06-30", value: 2, reported: true },
  ]), null, { ticker: "NVDA", period: "1Y", asOf });
  assert.deepEqual(
    overridden.operating[0].series.filter((point) => point.reported).map((point) => point.fiscal_period),
    ["2024Q2"],
  );

  const latest = stockPanels(operatingSeries(nvda), null, { ticker: "NVDA", period: "1M", asOf });
  assert.deepEqual(latest.operating[0].series.map((point) => point.fiscal_period), ["2027Q3"]);

  const fiveYearPoints = fiscalPoints("NVDA", 2021, 1, 2027, 2).map(({ fiscal_period, value, reported }) => ({
    fiscal_period, value, reported,
  }));
  const fiveYear = stockPanels(operatingSeries(fiveYearPoints), null, { ticker: "NVDA", period: "5Y", asOf });
  const fiveKept = fiveYear.operating[0].series.filter((point) => point.reported);
  assert.equal(fiveKept.length, 20);
  assert.equal(fiveKept[0].fiscal_period, "2022Q3");
  assert.equal(fiveKept.at(-1).fiscal_period, "2027Q2");

  const empty = stockPanels(operatingSeries([
    { fiscal_period: "2026Q4", period_end: "2026-01-25", value: 4, reported: true },
  ]), null, { ticker: "NVDA", period: "3M", asOf });
  assert.equal(empty.operating[0].emptyPeriod, true);
  assert.deepEqual(empty.operating[0].series, []);
  assert.equal(empty.operating[0].latest, "not reported");
});

test("stock page text and up/down colors stay AA on all six themes", () => {
  for (const theme of THEMES) {
    const properties = themeProperties(theme.id, "green-red");
    assert.ok(contrastRatio(properties["--text"], properties["--surface"]) >= 4.5, theme.id);
    assert.ok(contrastRatio(properties["--green"], properties["--surface"]) >= 4.5, `${theme.id} up`);
    assert.ok(contrastRatio(properties["--red"], properties["--surface"]) >= 4.5, `${theme.id} down`);
  }
});

test("company description comes from the stock payload, then dashboard data, and is empty when absent", async () => {
  const { companyDescription } = await import("./stock-page.js");
  assert.equal(companyDescription({ company_description: "  Designs  GPUs. " }, { company_description: "x" }), "Designs GPUs.");
  assert.equal(companyDescription({}, { company_description: "Builds EV chargers." }), "Builds EV chargers.");
  assert.equal(companyDescription(null, { company_description: null }), "");
});

test("company page order: Technical Summary, then quote headers on KPIs and Fundamentals (#279)", () => {
  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  assert.match(page, /quoteFor\("kpis"\)/);
  assert.match(page, /quoteFor\("fundamentals"\)/);
  assert.doesNotMatch(page, /renderChartPanel\("company", session\.selected\)/);
  const order = ["renderTechnicalSummary(session.selected, history)", 'quoteFor("kpis")', 'section("kpis"', 'quoteFor("fundamentals")', 'section(\n      "fundamentals"', "renderCompanyMetrics(charts, pageState)"]
    .map((piece) => page.indexOf(piece, page.indexOf("page.append(")));
  assert.ok(order.every((at, index) => at > 0 && (index === 0 || at > order[index - 1])), order.join(","));
  assert.ok(page.indexOf("root.append(heading)") < page.indexOf("root.append(page)"));
  assert.match(page, /page\.prepend\(tabs\)/);
  assert.doesNotMatch(page, /page\.append\(tabs\)/);
  assert.match(page, /aria-orientation", "horizontal"/);
  assert.match(page, /stock-tab-deep/);
  assert.match(page, /parseStockSection\(location\.hash\)/);
  const barAt = styles.indexOf(".stock-page-tabs {");
  const bar = styles.slice(barAt, styles.indexOf("}", barAt) + 1);
  assert.match(bar, /display:\s*flex/);
  assert.match(bar, /flex-wrap:\s*wrap/);
  assert.match(bar, /overflow-x:\s*clip/);
  assert.doesNotMatch(bar, /overflow-x:\s*auto/);
  assert.doesNotMatch(bar, /display:\s*none/);
  assert.doesNotMatch(styles, /\.stock-page-tabs\s*\{[^}]*grid-template-columns:\s*repeat\(4/);
  assert.match(styles, /\.stock-tab-deep\[aria-selected="true"\]/);
  assert.match(styles, /\.stock-page \[data-stock-panel\]\s*\{[^}]*display:\s*none/);
  assert.match(app, /async function submitMetricReview/);
  assert.match(app, /"Approved only"/);
});
