import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { classifyMetric } from "./statement-model.js";
import { renderYearlyFundamentals, yearlyCell, yearlyFundamentalsModel } from "./yearly-fundamentals.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const modelSource = await readFile(new URL("./yearly-fundamentals.js", import.meta.url), "utf8");
const statementSource = await readFile(new URL("./statement-model.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

function metric(id, name, unit, values, extra = {}) {
  return {
    metric_id: id,
    display_name: name,
    unit,
    category: extra.category || "fundamentals",
    approval_state: extra.approval || "approved",
    approved: (extra.approval || "approved") === "approved",
    series: Object.entries(values).map(([period, value]) => ({
      fiscal_period: period,
      value,
      reported: true,
    })),
  };
}

function element(tag) {
  const node = {
    tagName: String(tag).toUpperCase(),
    className: "",
    textContent: "",
    children: [],
    style: {},
    dataset: {},
    attributes: {},
    listeners: {},
  };
  node.childNodes = node.children;
  node.append = (...kids) => {
    for (const kid of kids) {
      if (kid == null || kid === false) continue;
      if (typeof kid === "string") {
        node.children.push({ textContent: kid, children: [] });
        continue;
      }
      kid.parentElement = node;
      node.children.push(kid);
    }
  };
  node.setAttribute = (key, value) => {
    node.attributes[key] = String(value);
    if (key === "class") node.className = String(value);
  };
  node.getAttribute = (key) => node.attributes[key];
  node.addEventListener = (type, fn) => {
    node.listeners[type] = node.listeners[type] || [];
    node.listeners[type].push(fn);
  };
  node.replaceWith = (next) => {
    node.replacedWith = next;
  };
  return node;
}

function fakeDocument() {
  return {
    createElement: (tag) => element(tag),
    createElementNS: (_ns, tag) => element(tag),
    createTextNode: (text) => ({ textContent: String(text), children: [] }),
  };
}

function textOf(node) {
  const parts = [];
  const walk = (item) => {
    if (item?.textContent) parts.push(item.textContent);
    for (const child of item?.children || []) walk(child);
  };
  walk(node);
  return parts.join(" ");
}

test("classification follows the line, not a ticker list", () => {
  assert.equal(classifyMetric({ metric_id: "revenue_gaap", display_name: "Revenue", unit: "USD" }), "revenue");
  assert.equal(classifyMetric({ metric_id: "services_revenue", display_name: "Services and other", unit: "USD" }), "segment_revenue");
  assert.equal(classifyMetric({ metric_id: "research_and_development", display_name: "R&D", unit: "USD" }), "rd");
  assert.equal(classifyMetric({ metric_id: "sga", display_name: "Selling, general and administrative", unit: "USD" }), "sga");
  assert.equal(classifyMetric({ metric_id: "restructuring", display_name: "Restructuring", unit: "USD" }), "restructuring");
  assert.equal(classifyMetric({ metric_id: "interest_income", display_name: "Interest income", unit: "USD" }), "interest_income");
  assert.equal(classifyMetric({ metric_id: "interest_expense", display_name: "Interest expense", unit: "USD" }), "interest_expense");
  assert.equal(classifyMetric({ metric_id: "other_income", display_name: "Other income", unit: "USD" }), "other_income");
  assert.equal(classifyMetric({ metric_id: "income_tax", display_name: "Income tax", unit: "USD" }), "tax");
  assert.equal(classifyMetric({ metric_id: "net_income_gaap", display_name: "Net income", unit: "USD" }), "net_profit");
  assert.equal(classifyMetric({ metric_id: "adjusted_ebitda", display_name: "Adjusted EBITDA", unit: "USD" }), "ebitda");
  assert.equal(classifyMetric({ metric_id: "automotive_leasing", display_name: "Automotive leasing", unit: "USD" }), "segment_revenue");
  assert.equal(classifyMetric({ metric_id: "vehicles_operating_lease", display_name: "Vehicles in operating lease", unit: "vehicles", category: "operating" }), "segment_other");
  assert.equal(classifyMetric({ metric_id: "automotive_gross_profit", display_name: "Segment gross profit", unit: "USD" }), "");
  assert.doesNotMatch(`${modelSource}\n${statementSource}`, /TSLA|SPCX|NVDA|AAPL/);
});

test("an empty company shows the common rows and no invented numbers", () => {
  const model = yearlyFundamentalsModel("ACME", { metrics: [] }, null);
  assert.equal(model.empty, true);
  assert.deepEqual(model.years, []);
  assert.deepEqual(model.rows.map((row) => row.label), [
    "Revenue", "Total revenue", "Net profit", "Model adj. EBITDA", "P/E (assumption)", "Market cap", "Stock price",
  ]);
  assert.equal(model.rows.every((row) => row.cells.length === 0), true);
  assert.match(model.notes.join(" "), /No P\/E assumption/);
  const view = renderYearlyFundamentals(model, fakeDocument());
  assert.match(textOf(view), /No yearly figures are on file/);
  assert.doesNotMatch(textOf(view), /\$\d/);
});

test("market cap is net profit times P/E and price divides by shares", () => {
  const payload = {
    metrics: [
      metric("revenue_gaap", "Revenue", "USD", { "2025Q1": 40, "2025Q2": 60 }),
      metric("net_income_gaap", "Net income", "USD", { "2025Q1": 10, "2025Q2": 15 }),
      metric("adjusted_ebitda", "Adjusted EBITDA", "USD", { "2025Q1": 12, "2025Q2": 18 }, { approval: "proposed" }),
      metric("cloud_revenue", "Cloud revenue", "USD", { "2025Q1": 5, "2025Q2": 7 }),
      metric("fsd_subscriptions", "FSD subscriptions", "subscribers", { "2025Q2": 2.65e6 }, { category: "operating" }),
      metric("fsd_revenue", "FSD revenue", "USD", { "2025Q1": 1.5e9, "2025Q2": 1.8e9 }),
    ],
    assumptions: { pe: { 2025: 20 } },
  };
  const chart = {
    fundamentals: [
      { series_id: "shares_outstanding", fiscal_quarter: "2025Q2", value: 5, unit: "shares" },
    ],
  };
  const model = yearlyFundamentalsModel("ACME", payload, chart);
  assert.deepEqual(model.years.map((year) => year.label), ["FY2025"]);
  assert.equal(yearlyCell(model, "revenue", 2025).value, 100);
  assert.equal(yearlyCell(model, "total_revenue", 2025).value, 100);
  assert.equal(yearlyCell(model, "net_profit", 2025).value, 25);
  assert.equal(yearlyCell(model, "pe", 2025).text, "20x");
  assert.equal(yearlyCell(model, "market_cap", 2025).value, 500);
  assert.equal(yearlyCell(model, "price", 2025).value, 100);
  const cloud = model.rows.find((row) => row.id === "cloud_revenue");
  assert.equal(cloud.label, "Cloud revenue");
  assert.equal(cloud.cells[0].text, "$12");
  const paired = model.rows.find((row) => row.label.includes("FSD"));
  assert.match(paired.cells[0].text, /2\.65M \/ \$3\.3B/);
  assert.equal(model.rows.some((row) => row.id === "robotaxi_fleet"), false);
  assert.equal(model.pending, true);
  const view = renderYearlyFundamentals(model, fakeDocument());
  assert.match(textOf(view), /Pending review/);
  assert.match(view.querySelector ? "" : view.children.map((child) => child.className).join(" "), /yearly/);
});

test("a second company does not inherit another company's segments", () => {
  const payload = {
    metrics: [
      metric("revenue_gaap", "Revenue", "USD", { "2024Q1": 8, "2024Q2": 8, "2024Q3": 8, "2024Q4": 8 }),
      metric("net_income_gaap", "Net income", "USD", { "2024Q1": 1, "2024Q2": 1, "2024Q3": 1, "2024Q4": 1 }),
      metric("advertising_revenue", "Advertising", "USD", { "2024Q1": 3, "2024Q2": 3, "2024Q3": 3, "2024Q4": 3 }),
    ],
  };
  const model = yearlyFundamentalsModel("BETA", payload, { fundamentals: [] });
  assert.equal(model.ticker, "BETA");
  assert.equal(model.rows.some((row) => /cloud|fsd|deliver/i.test(row.label)), false);
  assert.equal(model.rows.some((row) => row.label === "Advertising"), true);
  assert.equal(yearlyCell(model, "market_cap", 2024).text, "—");
  assert.equal(yearlyCell(model, "price", 2024).text, "—");
});

test("placeholder forecast fills only missing quarters and stays labeled", () => {
  const payload = {
    metrics: [
      metric("revenue_gaap", "Revenue", "USD", { "2024Q1": 10, "2024Q2": 10, "2024Q3": 10, "2024Q4": 10 }),
    ],
    deep_dive: {
      forecast: {
        segments: [{
          id: "total",
          label: "Total",
          metrics: [{
            field: "revenue",
            metric_id: "revenue_gaap",
            unit: "USD",
            approval_state: "proposed",
            actuals: [{ period: "2024Q4", value: 99, reported: true }],
            forecast: [
              { period: "2025Q1", value: 10, reported: true, model_status: "placeholder" },
              { period: "2025Q2", value: null, reported: false },
            ],
          }],
        }],
      },
    },
  };
  const model = yearlyFundamentalsModel("ACME", payload, null);
  assert.equal(yearlyCell(model, "revenue", 2024).value, 40);
  assert.equal(yearlyCell(model, "revenue", 2024).estimate, false);
  assert.equal(yearlyCell(model, "revenue", 2025).value, 10);
  assert.equal(yearlyCell(model, "revenue", 2025).estimate, true);
  assert.equal(yearlyCell(model, "revenue", 2025).pending, true);
  assert.match(model.subtitle, /Placeholder forecast/);
  assert.equal(model.years.at(-1).label, "FY2025E");
});

test("the table keeps the latest six fiscal years", () => {
  const values = {};
  for (let year = 2018; year <= 2026; year += 1) values[`${year}Q4`] = year;
  const model = yearlyFundamentalsModel("ACME", {
    metrics: [metric("revenue_gaap", "Revenue", "USD", values)],
  }, null);
  assert.deepEqual(model.years.map((year) => year.year), [2021, 2022, 2023, 2024, 2025, 2026]);
});

test("the Analysis tab mounts both panels and funds get a note", () => {
  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  const mount = app.slice(app.indexOf("function renderAnalysisFundamentals"), app.indexOf("function renderStockPage"));
  assert.match(mount, /Fundamentals and revenue flow aren't available for funds; see the Fund tab\./);
  assert.match(mount, /selectStockTab\(FUND_TAB_ID\)/);
  assert.doesNotMatch(mount, /if \(fund\) return null/);
  assert.match(page, /renderAnalysisFundamentals\(session\.selected, payload, chartData, fundTicker\)/);
  assert.match(page, /fund: fundTicker/);
  assert.doesNotMatch(mount, /TSLA|SPCX|NVDA|AAPL/);
  assert.match(app, /Yearly Fundamentals/);
  assert.match(mount, /wrap\.append\(mountRevenueFlow\(ticker, payload, chartData\), yearly\)/);
  const stack = styles.match(/\.analysis-fundamentals\[data-stock-panel="analysis"\]\s*\{[^}]*\}/)[0];
  assert.match(stack, /grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.doesNotMatch(stack, /minmax\(320px/);
  assert.doesNotMatch(styles, /@media[^{]+\{[^@]*\.analysis-fundamentals[^{]*grid-template-columns/s);
  assert.doesNotMatch(styles, /@media[^{]+\{[^@]*\.yearly-scroll[^{]*overflow/s);
  const scroll = styles.match(/\.yearly-scroll\s*\{[^}]*\}/)[0];
  assert.match(scroll, /overflow-x:\s*auto/);
  const label = styles.match(/\.yearly-table \.yearly-label\s*\{[^}]*\}/)[0];
  assert.match(label, /position:\s*sticky/);
  assert.match(label, /left:\s*0/);
  const chart = styles.match(/\.flow-chart\s*\{[^}]*\}/)[0];
  assert.match(chart, /width:\s*100%/);
  assert.match(chart, /max-width:\s*100%/);
  assert.doesNotMatch(chart, /min-width:\s*720px/);
});
