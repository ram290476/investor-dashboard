import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { curvePull, layoutRevenueFlow, renderRevenueFlow, resolveSelection, revenueFlowModel, ribbonPath } from "./revenue-flow.js";

const source = await readFile(new URL("./revenue-flow.js", import.meta.url), "utf8");

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

function year(id, name, value, quarters = [1, 2, 3, 4]) {
  const values = {};
  for (const quarter of quarters) values[`2025Q${quarter}`] = value;
  return metric(id, name, "USD", values);
}

function element(tag) {
  const node = {
    tagName: String(tag).toUpperCase(),
    className: "",
    textContent: "",
    children: [],
    dataset: {},
    attributes: {},
    listeners: {},
    style: {},
  };
  node.childNodes = node.children;
  node.append = (...kids) => {
    for (const kid of kids) {
      if (kid == null) continue;
      kid.parentElement = node;
      node.children.push(kid);
    }
  };
  node.setAttribute = (key, value) => {
    node.attributes[key] = String(value);
    if (key === "class") node.className = String(value);
    if (key.startsWith("data-")) {
      const prop = key.slice(5).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
      node.dataset[prop] = String(value);
    }
  };
  node.getAttribute = (key) => node.attributes[key];
  node.addEventListener = (type, fn) => {
    node.listeners[type] = node.listeners[type] || [];
    node.listeners[type].push(fn);
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

function walk(node, visit) {
  visit(node);
  for (const child of node.children || []) walk(child, visit);
}

function byAttr(node, key, value) {
  const found = [];
  walk(node, (item) => {
    if (item?.getAttribute?.(key) === value || item?.dataset?.[key] === value) found.push(item);
  });
  return found;
}

const full = {
  metrics: [
    year("revenue_gaap", "Revenue", 25),
    year("cloud_revenue", "Cloud", 10),
    year("ads_revenue", "Advertising", 15),
    year("cost_of_revenue", "Cost of revenue", 10),
    year("hosting_cost", "Cost of hosting", 6),
    year("support_cost", "Cost of support", 3),
    year("gross_profit", "Gross profit", 15),
    year("operating_income", "Operating income", 8),
    year("operating_expenses", "Operating expenses", 7),
    year("research_and_development", "Research and development", 4),
    year("sga", "Selling, general and administrative", 2),
    year("restructuring", "Restructuring", 1),
    year("interest_income", "Interest income", 3),
    year("interest_expense", "Interest expense", 2),
    year("other_income", "Other income", 1),
    year("pretax_income", "Income before tax", 10),
    year("income_tax", "Income tax", 4),
    year("net_income_gaap", "Net income", 6),
    metric("total_deliveries", "Deliveries", "vehicles", { "2025Q1": 100, "2025Q2": 120, "2025Q3": 80, "2025Q4": 100 }, { category: "operating" }),
    metric("total_production", "Production", "vehicles", { "2025Q1": 90, "2025Q2": 90, "2025Q3": 90, "2025Q4": 90 }, { category: "operating" }),
  ],
};

test("right-side ribbons are cubic beziers with a long horizontal pull", () => {
  const path = ribbonPath(10, 40, 210, 220, 24, 18, true);
  assert.match(path, / C /);
  assert.equal(curvePull(true), 0.9);
  assert.equal(curvePull(false), 0.38);
  const width = 200;
  const dx = Math.max(84, width * curvePull(true));
  assert.ok(dx / width >= 0.9);
  assert.match(path, new RegExp(`C ${10 + dx} `));
  assert.doesNotMatch(source, /TSLA|SPCX|NVDA|AAPL/);
});

test("the flow uses every reported line and balances with Other", () => {
  const model = revenueFlowModel("ACME", full, null, {});
  assert.equal(model.selection.year, 2025);
  assert.equal(model.selection.quarter, null);
  assert.equal(model.periods[0].quarters.join(""), "1234");
  const ids = model.nodes.map((node) => node.id);
  for (const id of ["cloud_revenue", "ads_revenue", "hosting_cost", "support_cost", "research_and_development", "sga", "restructuring", "interest_income", "interest_expense", "other_income", "income_tax", "net_income_gaap"]) {
    assert.equal(ids.includes(id), true, id);
  }
  assert.equal(ids.includes("robotaxi_fleet"), false);
  const costGap = model.balances.find((row) => row.parent === "cost_of_revenue");
  assert.equal(costGap.gap, 4);
  assert.equal(model.nodes.some((node) => node.id === "other:cost_of_revenue" && node.label === "Other"), true);
  const revenueBalance = model.balances.find((row) => row.parent === "revenue");
  assert.equal(revenueBalance.gap, 0);
  assert.equal(model.nodes.some((node) => node.id === "other:revenue"), false);
  assert.equal(model.cards.map((card) => card.label).sort().join(","), "Deliveries,Production");
  const layout = layoutRevenueFlow(model);
  const strong = layout.links.filter((link) => link.strong);
  assert.ok(strong.length >= 4);
  assert.ok(strong.every((link) => link.d.includes(" C ")));
  const curved = strong.find((link) => link.from === "gross_profit" || link.to === "research_and_development" || link.to === "net_income_gaap");
  assert.ok(curved);
  const rightSide = [
    ["gross_profit", "operating_income"],
    ["gross_profit", "operating_expenses"],
    ["operating_expenses", "research_and_development"],
    ["operating_expenses", "sga"],
    ["pretax_income", "income_tax"],
    ["pretax_income", "net_income_gaap"],
  ];
  for (const [from, to] of rightSide) {
    const link = layout.links.find((item) => item.from === from && item.to === to);
    assert.ok(link, `${from} -> ${to}`);
    const dx = Math.abs(link.x2 - link.x1);
    const dy = Math.abs(link.y2 - link.y1);
    assert.ok(dx >= 180, `${from} -> ${to} gap ${dx}`);
    const ratio = dy / dx;
    assert.ok(ratio >= 0.75 && ratio <= 1.35, `${from} -> ${to} rise ${ratio}`);
    assert.equal(link.strong, true);
    const pull = curvePull(true);
    const control = Math.max(84, dx * pull);
    assert.ok(control / dx >= 0.85, `${from} -> ${to} control ${control}`);
  }
  const gentle = layout.links.filter((link) => !link.strong);
  assert.ok(gentle.length >= 1);
  const view = renderRevenueFlow(model, fakeDocument(), {});
  const paths = [];
  walk(view, (item) => {
    if (item.getAttribute?.("data-curve")) paths.push(item);
  });
  assert.ok(paths.some((path) => path.getAttribute("data-curve") === "strong" && path.getAttribute("d").includes("C")));
  assert.ok(byAttr(view, "quarter", "year").length >= 2);
  assert.equal(byAttr(view, "quarter", "1").length, 1);
});

test("a quarter compares with the same quarter last year and can return to the year", () => {
  const payload = {
    metrics: [
      metric("revenue_gaap", "Revenue", "USD", {
        "2024Q1": 10, "2024Q2": 20, "2024Q3": 30, "2024Q4": 40,
        "2025Q1": 12, "2025Q2": 18,
      }),
      metric("net_income_gaap", "Net income", "USD", {
        "2024Q1": 1, "2024Q2": 2, "2025Q1": 2, "2025Q2": 1,
      }),
      metric("total_deliveries", "Deliveries", "vehicles", { "2025Q1": 50 }, { category: "operating" }),
    ],
  };
  const latest = revenueFlowModel("BETA", payload, null, {});
  assert.equal(latest.ticker, "BETA");
  assert.equal(latest.selection.year, 2025);
  assert.equal(latest.selection.quarter, null);
  assert.deepEqual(latest.periods.map((period) => period.quarters), [[1, 2, 3, 4], [1, 2]]);
  assert.equal(latest.nodes.find((node) => node.id === "revenue").value, 30);
  assert.match(latest.subtitle, /Q1–Q2 FY2025 actual/);
  assert.match(latest.subtitle, /Q1–Q2 FY2024/);
  const quarter = revenueFlowModel("BETA", payload, null, { year: 2025, quarter: 1 });
  assert.equal(quarter.header, "BETA FY2025 Q1");
  assert.equal(quarter.nodes.find((node) => node.id === "revenue").value, 12);
  assert.match(quarter.nodes.find((node) => node.id === "revenue").changeText, /▲20%/);
  assert.match(quarter.subtitle, /Q1 FY2025 actual · changes vs\. Q1 FY2024/);
  assert.equal(quarter.cards.length, 1);
  assert.equal(quarter.cards[0].valueText, "50");
  const yearView = revenueFlowModel("BETA", payload, null, { year: 2025, quarter: null });
  assert.equal(yearView.selection.quarter, null);
  const skipped = revenueFlowModel("BETA", payload, null, { year: 2025, quarter: 4 });
  assert.equal(skipped.selection.quarter, null);
  assert.equal(resolveSelection(latest.periods, {}).year, 2025);
  let picked = null;
  const view = renderRevenueFlow(quarter, fakeDocument(), {
    onSelect(year, quarterId) { picked = [year, quarterId]; },
  });
  const fullYear = [];
  walk(view, (item) => {
    if (item.textContent === "Full year") fullYear.push(item);
  });
  fullYear[0].listeners.click[0]();
  assert.deepEqual(picked, [2025, null]);
  const q2 = [];
  walk(view, (item) => {
    if (item.textContent === "Q2") q2.push(item);
  });
  q2[0].listeners.click[0]();
  assert.deepEqual(picked, [2025, 2]);
});

test("missing lines are omitted and gross profit is not invented", () => {
  const payload = {
    metrics: [
      year("revenue_gaap", "Revenue", 25),
      year("cost_of_revenue", "Cost of revenue", 10),
      year("restructuring", "Restructuring", 1),
    ],
  };
  const model = revenueFlowModel("GAMMA", payload, null, { year: 2025 });
  const ids = model.nodes.map((node) => node.id);
  assert.equal(ids.includes("gross_profit"), false);
  assert.equal(ids.includes("research_and_development"), false);
  assert.equal(ids.includes("sga"), false);
  assert.equal(ids.includes("restructuring"), true);
  assert.equal(ids.includes("other:revenue"), true);
  assert.equal(model.cards.length, 0);
  assert.equal(model.nodes.find((node) => node.id === "other:revenue").value, 60);
});

test("no income-statement periods leave an empty state", () => {
  const model = revenueFlowModel("DELTA", { metrics: [] }, null, {});
  assert.equal(model.empty, true);
  assert.deepEqual(model.periods, []);
  const view = renderRevenueFlow(model, fakeDocument(), {});
  const text = [];
  walk(view, (item) => {
    if (item.textContent) text.push(item.textContent);
  });
  assert.match(text.join(" "), /No income-statement/);
  assert.equal(text.some((item) => item === "Q1"), false);
});
