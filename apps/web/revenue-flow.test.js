import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { curvePull, flowTextWidth, latestDataQuarter, layoutRevenueFlow, renderRevenueFlow, resolveSelection, revenueFlowModel, ribbonPath } from "./revenue-flow.js";

const source = await readFile(new URL("./revenue-flow.js", import.meta.url), "utf8");
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

function periodControls(view) {
  const year = byAttr(view, "control", "year")[0];
  const quarter = byAttr(view, "control", "quarter")[0];
  const optionsOf = (select) => (select?.children || []).filter((item) => item.tagName === "OPTION").map((option) => ({
    value: option.value,
    label: option.textContent,
    selected: Boolean(option.selected),
  }));
  return { year, quarter, yearOptions: optionsOf(year), quarterOptions: optionsOf(quarter) };
}

function choose(select, value) {
  select.value = value;
  for (const option of select.children || []) option.selected = option.value === value;
  select.listeners.change[0]();
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
  const model = revenueFlowModel("ACME", full, null, { year: 2025 });
  assert.equal(model.selection.year, 2025);
  assert.equal(model.selection.quarter, null);
  assert.equal(model.periods[0].quarters.join(""), "1234");
  const ids = model.nodes.map((node) => node.id);
  for (const id of ["cloud_revenue", "ads_revenue", "hosting_cost", "support_cost", "research_and_development", "sga", "restructuring", "interest_net", "income_tax", "net_income_gaap"]) {
    assert.equal(ids.includes(id), true, id);
  }
  assert.equal(ids.includes("interest_income"), false);
  assert.equal(ids.includes("robotaxi_fleet"), false);
  const interest = model.nodes.find((node) => node.id === "interest_net");
  assert.match(interest.label, /Interest/);
  assert.equal(interest.value, 8);
  assert.deepEqual(interest.parts, ["interest_income", "interest_expense", "other_income"]);
  const costGap = model.balances.find((row) => row.parent === "cost_of_revenue");
  assert.equal(costGap.gap, 4);
  assert.equal(model.nodes.some((node) => node.id === "other:cost_of_revenue" && node.label === "Other"), true);
  const revenueBalance = model.balances.find((row) => row.parent === "revenue");
  assert.equal(revenueBalance.gap, 0);
  assert.equal(model.nodes.some((node) => node.id === "other:revenue"), false);
  assert.equal(model.cards.map((card) => card.label).sort().join(","), "Deliveries,Production");
  const layout = layoutRevenueFlow(model, { width: 2400 });
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
  assert.ok(layout.nodes.every((node) => node.w <= 12));
  const costDrop = layout.links.find((link) => link.from === "revenue" && link.to === "cost_of_revenue");
  assert.ok(costDrop);
  assert.ok(costDrop.y2 > costDrop.y1 + 24, `cost drop ${costDrop.y2 - costDrop.y1}`);
  for (const [from, to] of rightSide) {
    const link = layout.links.find((item) => item.from === from && item.to === to);
    assert.ok(link, `${from} -> ${to}`);
    const dx = Math.abs(link.x2 - link.x1);
    assert.ok(dx >= 160, `${from} -> ${to} gap ${dx}`);
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
  const picker = periodControls(view);
  assert.deepEqual(picker.yearOptions.map((item) => item.value), ["2025"]);
  assert.equal(picker.yearOptions.find((item) => item.selected).label, "FY2025");
  assert.deepEqual(picker.quarterOptions.map((item) => item.label), ["Full Year", "Q1", "Q2", "Q3", "Q4"]);
  assert.equal(picker.quarterOptions.find((item) => item.selected).value, "year");
  assert.match(model.footnote, /Source: company filings and SEC XBRL/);
  assert.doesNotMatch(source, /flow-period/);
  assert.doesNotMatch(source, /DillonLoomis|@Dillon/);
  const classes = [];
  walk(view, (item) => {
    if (item.className) classes.push(item.className);
  });
  assert.ok(classes.some((name) => String(name).includes("flow-footer")));
  assert.ok(classes.some((name) => String(name).includes("flow-cards")));
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
  const latest = revenueFlowModel("BETA", payload, null, { year: 2025 });
  assert.equal(latest.ticker, "BETA");
  assert.equal(latest.selection.year, 2025);
  assert.equal(latest.selection.quarter, null);
  assert.deepEqual(latest.periods.map((period) => period.quarters), [[1, 2, 3, 4], [1, 2]]);
  assert.equal(latest.nodes.find((node) => node.id === "revenue").value, 30);
  assert.match(latest.subtitle, /Where the money went · Q1–Q2 FY2025 · ▲▼ changes vs\. Q1–Q2 FY2024/);
  const quarter = revenueFlowModel("BETA", payload, null, { year: 2025, quarter: 1 });
  assert.equal(quarter.header, "BETA FY2025 Q1");
  assert.equal(quarter.nodes.find((node) => node.id === "revenue").value, 12);
  assert.match(quarter.nodes.find((node) => node.id === "revenue").changeText, /▲20%/);
  assert.match(quarter.subtitle, /Where the money went · Jan–Mar 2025 · ▲▼ changes vs\. Q1 2024/);
  assert.equal(quarter.cards.length, 1);
  assert.equal(quarter.cards[0].valueText, "50");
  const yearView = revenueFlowModel("BETA", payload, null, { year: 2025, quarter: null });
  assert.equal(yearView.selection.quarter, null);
  const skipped = revenueFlowModel("BETA", payload, null, { year: 2025, quarter: 4 });
  assert.equal(skipped.selection.quarter, null);
  assert.deepEqual(resolveSelection(latest.periods, {}), { year: 2025, quarter: 2 });
  let picked = null;
  const view = renderRevenueFlow(quarter, fakeDocument(), {
    onSelect(year, quarterId) { picked = [year, quarterId]; },
  });
  const picker = periodControls(view);
  assert.deepEqual(picker.quarterOptions.map((item) => item.label), ["Full Year", "Q1", "Q2"]);
  assert.equal(picker.quarterOptions.find((item) => item.selected).label, "Q1");
  choose(picker.quarter, "year");
  assert.deepEqual(picked, [2025, null]);
  choose(picker.quarter, "2");
  assert.deepEqual(picked, [2025, 2]);
  choose(picker.year, "2024");
  assert.deepEqual(picked, [2024, 1]);
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

test("year and quarter dropdowns list only periods with data", () => {
  const payload = {
    metrics: [
      metric("revenue_gaap", "Revenue", "USD", {
        "2024Q1": 8,
        "2025Q1": 10, "2025Q2": 10, "2025Q3": 10, "2025Q4": 10,
        "2026Q1": 11, "2026Q3": 14,
      }),
      metric("net_income_gaap", "Net income", "USD", {
        "2025Q3": 2,
        "2026Q3": 3,
      }),
    ],
  };
  const latest = revenueFlowModel("ACME", payload, null, { year: 2026 });
  assert.equal(latest.selection.year, 2026);
  assert.equal(latest.selection.quarter, null);
  assert.equal(latest.header, "ACME FY2026");
  assert.equal(latest.nodes.find((node) => node.id === "revenue").value, 25);
  assert.match(latest.nodes.find((node) => node.id === "revenue").changeText, /▲25%/);
  assert.match(latest.subtitle, /Where the money went · Q1, Q3 FY2026 · ▲▼ changes vs\. Q1, Q3 FY2025/);

  const quarter = revenueFlowModel("ACME", payload, null, { year: 2026, quarter: 3 });
  assert.equal(quarter.header, "ACME FY2026 Q3");
  assert.equal(quarter.nodes.find((node) => node.id === "revenue").value, 14);
  assert.match(quarter.nodes.find((node) => node.id === "revenue").changeText, /▲40%/);
  assert.match(quarter.nodes.find((node) => node.id === "net_income_gaap").changeText, /▲50%/);
  assert.match(quarter.subtitle, /Where the money went · Jul–Sep 2026 · ▲▼ changes vs\. Q3 2025/);

  const missing = revenueFlowModel("ACME", payload, null, { year: 2026, quarter: 2 });
  assert.equal(missing.selection.quarter, null);
  assert.equal(missing.nodes.find((node) => node.id === "revenue").value, 25);
  const dropped = revenueFlowModel("ACME", payload, null, { year: 2024, quarter: 3 });
  assert.deepEqual(dropped.selection, { year: 2024, quarter: null });
  assert.equal(dropped.nodes.find((node) => node.id === "revenue").value, 8);

  let picked = null;
  const view = renderRevenueFlow(latest, fakeDocument(), {
    onSelect(year, quarterId) { picked = [year, quarterId]; },
  });
  const picker = periodControls(view);
  assert.deepEqual(picker.yearOptions.map((item) => item.label), ["FY2024", "FY2025", "FY2026"]);
  assert.equal(picker.yearOptions.find((item) => item.selected).value, "2026");
  assert.deepEqual(picker.quarterOptions.map((item) => item.label), ["Full Year", "Q1", "Q3"]);
  assert.equal(picker.quarterOptions.find((item) => item.selected).label, "Full Year");
  choose(picker.quarter, "3");
  assert.deepEqual(picked, [2026, 3]);

  const quarterView = renderRevenueFlow(quarter, fakeDocument(), {
    onSelect(year, quarterId) { picked = [year, quarterId]; },
  });
  const quarterPicker = periodControls(quarterView);
  assert.equal(quarterPicker.quarterOptions.find((item) => item.selected).label, "Q3");
  choose(quarterPicker.year, "2025");
  assert.deepEqual(picked, [2025, 3]);
  choose(quarterPicker.year, "2024");
  assert.deepEqual(picked, [2024, null]);
  choose(quarterPicker.quarter, "year");
  assert.deepEqual(picked, [2026, null]);
});

test("auto sales, credits and leasing roll into Total auto", () => {
  const payload = {
    metrics: [
      year("automotive_sales", "Automotive sales", 20),
      year("automotive_regulatory_credits", "Automotive regulatory credits", 2),
      year("regulatory_credits_revenue", "Regulatory credits revenue", 2),
      year("automotive_leasing", "Automotive leasing", 3),
      year("automotive_revenue", "Automotive revenue", 25),
      year("energy_revenue", "Energy generation and storage revenue", 8),
      year("services_revenue", "Services and other revenue", 5),
      year("revenue_gaap", "Revenue", 40),
    ],
  };
  const model = revenueFlowModel("ACME", payload, null, { year: 2025 });
  const labels = model.nodes.filter((node) => node.column === "segments" && node.role === "segment_revenue").map((node) => node.label);
  assert.deepEqual(labels, ["Car sales", "Regulatory credits", "Leasing", "Energy", "Services & other"]);
  assert.equal(model.nodes.some((node) => node.id === "regulatory_credits_revenue"), false);
  const total = model.nodes.find((node) => node.id === "automotive_revenue");
  assert.equal(total.label, "Total auto");
  assert.equal(total.column, "rollup");
  const layout = layoutRevenueFlow(model);
  for (const id of ["automotive_sales", "automotive_regulatory_credits", "automotive_leasing"]) {
    const link = layout.links.find((item) => item.from === id);
    assert.equal(link.to, "automotive_revenue", id);
    assert.equal(layout.links.some((item) => item.from === id && item.to === "revenue"), false);
  }
  assert.ok(layout.links.some((item) => item.from === "automotive_revenue" && item.to === "revenue"));
  assert.ok(layout.links.some((item) => item.from === "energy_revenue" && item.to === "revenue"));
  const other = model.nodes.find((node) => node.id === "other:revenue-in");
  assert.equal(other.value, 8);
});

test("a reported total smaller than its segments is flagged and not rewritten", () => {
  const payload = {
    metrics: [
      year("revenue_gaap", "Revenue", 25),
      year("cloud_revenue", "Cloud", 20),
      year("ads_revenue", "Advertising", 15),
    ],
  };
  const model = revenueFlowModel("ACME", payload, null, { year: 2025 });
  assert.equal(model.nodes.find((node) => node.id === "revenue").value, 100);
  assert.equal(model.nodes.some((node) => node.value < 0), false);
  assert.equal(model.nodes.some((node) => node.id === "other:revenue-in"), false);
  assert.match(model.footnote, /difference is not filled/);
  assert.equal(model.nodes.find((node) => node.id === "revenue").note, "Reported total differs from segments");
});

test("a quarter with no prior year shows a dash and names the months", () => {
  const payload = {
    metrics: [
      metric("revenue_gaap", "Revenue", "USD", { "2026Q3": 14 }),
      metric("gross_profit", "Gross profit", "USD", { "2026Q3": 4 }),
    ],
  };
  const model = revenueFlowModel("ACME", payload, null, { year: 2026, quarter: 3 });
  const revenue = model.nodes.find((node) => node.id === "revenue");
  assert.equal(revenue.changeText, "—");
  assert.match(model.subtitle, /Where the money went · Jul–Sep 2026$/);
  assert.doesNotMatch(model.subtitle, /changes vs/);
  const gross = model.nodes.find((node) => node.id === "gross_profit");
  assert.match(gross.marginText, /28\.6% margin/);
  assert.doesNotMatch(gross.marginText, /pts/);
});

test("a placeholder quarter does not flatten the year-over-year change", () => {
  const payload = {
    metrics: [
      metric("revenue_gaap", "Revenue", "USD", {
        "2025Q1": 10, "2025Q2": 10, "2025Q3": 10, "2025Q4": 10,
        "2026Q1": 20, "2026Q2": 20, "2026Q3": 20,
      }),
    ],
    deep_dive: {
      forecast: {
        segments: [{
          label: "Revenue",
          metrics: [{
            metric_id: "revenue_gaap",
            unit: "USD",
            approval_state: "approved",
            forecast: [{ fiscal_period: "2026Q4", value: 10, reported: true }],
          }],
        }],
      },
    },
  };
  const model = revenueFlowModel("ACME", payload, null, { year: 2026 });
  const revenue = model.nodes.find((node) => node.id === "revenue");
  assert.equal(revenue.value, 70);
  assert.match(revenue.changeText, /▲100%/);
  assert.match(model.subtitle, /Where the money went · Q1–Q3 FY2026 \+ Q4 placeholder · ▲▼ changes vs\. Q1–Q3 FY2025/);
});

function fitsContainer(model, width) {
  const layout = layoutRevenueFlow(model, { width });
  assert.ok(layout.width <= width, `layout ${layout.width} > ${width}`);
  assert.equal(layout.width, width);
  for (const node of layout.nodes) {
    assert.ok(node.x >= -0.1, node.id);
    assert.ok(node.x + node.w <= layout.width + 0.1, `${node.id} right ${node.x + node.w}`);
    for (const row of node.rows) {
      const text = row.change ? `${row.text} ${row.change}` : row.text;
      assert.ok(flowTextWidth(text, row.font) <= node.budget + 0.5, `${node.label} “${text}”`);
    }
    for (const line of node.noteLines || []) {
      assert.ok(flowTextWidth(line, 12) <= node.budget + 0.5, line);
    }
  }
  for (const link of layout.links) {
    assert.ok(link.x1 >= -0.1 && link.x2 >= -0.1, `${link.from}->${link.to}`);
    assert.ok(link.x1 <= layout.width + 0.1 && link.x2 <= layout.width + 0.1, `${link.from}->${link.to}`);
  }
  return layout;
}

test("the chart width stays inside the panel for the year and a quarter", () => {
  const year = revenueFlowModel("ACME", full, null, { year: 2025 });
  const quarter = revenueFlowModel("ACME", full, null, { year: 2025, quarter: 1 });
  for (const width of [1024, 1280, 1440]) {
    fitsContainer(year, width);
    fitsContainer(quarter, width);
  }
  const view = renderRevenueFlow(year, fakeDocument(), { width: 1280 });
  let chart = null;
  walk(view, (item) => {
    if (String(item.className || "").split(/\s+/).includes("flow-chart")) chart = item;
  });
  assert.equal(chart.getAttribute("width"), "100%");
  const boxWidth = Number(chart.getAttribute("viewBox").split(/\s+/)[2]);
  assert.ok(boxWidth <= 1280);
  const sankey = styles.slice(styles.indexOf("/* Revenue flow matches"));
  assert.doesNotMatch(sankey, /#[0-9a-fA-F]{3,8}\b/);
  const scroll = sankey.match(/\.revenue-flow \.flow-scroll\s*\{[^}]*\}/)[0];
  assert.match(scroll, /overflow:\s*hidden/);
  assert.doesNotMatch(scroll, /overflow-x:\s*auto/);
  const chartRule = sankey.match(/\.revenue-flow \.flow-chart\s*\{[^}]*\}/)[0];
  assert.match(chartRule, /width:\s*100%/);
  assert.match(chartRule, /max-width:\s*100%/);
  assert.match(chartRule, /min-width:\s*0/);
  const name = sankey.match(/\.revenue-flow \.flow-name\s*\{[^}]*\}/)[0];
  const amount = sankey.match(/\.revenue-flow \.flow-amount\s*\{[^}]*\}/)[0];
  const cardLabel = sankey.match(/\.revenue-flow \.flow-card-label\s*\{[^}]*\}/)[0];
  const cardValue = sankey.match(/\.revenue-flow \.flow-card-value\s*\{[^}]*\}/)[0];
  const cardChange = sankey.match(/\.revenue-flow \.flow-card-value \.flow-change\s*\{[^}]*\}/)[0];
  for (const rule of [name, amount, cardLabel, cardValue, cardChange]) {
    const size = Number(rule.match(/font-size:\s*(\d+(?:\.\d+)?)px/)[1]);
    assert.ok(size >= 12, rule);
  }
  assert.ok(Number(cardValue.match(/font-size:\s*(\d+)/)[1]) < 28);
});

test("source notes sit in a keyboard-reachable tooltip instead of the footer line", () => {
  const model = revenueFlowModel("ACME", full, null, {});
  const view = renderRevenueFlow(model, fakeDocument(), { width: 1024 });
  let tip = null;
  let button = null;
  const footnotes = [];
  walk(view, (item) => {
    if (item.getAttribute?.("role") === "tooltip") tip = item;
    if (String(item.className || "").split(/\s+/).includes("flow-source-button")) button = item;
    if (String(item.className || "").split(/\s+/).includes("flow-footnote")) footnotes.push(item);
  });
  assert.equal(footnotes.length, 0);
  assert.match(tip.textContent, /Source: company filings and SEC XBRL/);
  assert.equal(button.tagName, "BUTTON");
  assert.equal(button.getAttribute("type"), "button");
  assert.equal(button.getAttribute("aria-describedby"), tip.getAttribute("id"));
  assert.equal(button.getAttribute("aria-label"), "Source notes");
  let nodeTitle = "";
  walk(view, (item) => {
    if (item.tagName === "TITLE" && /Gross profit/.test(item.textContent || "")) nodeTitle = item.textContent;
  });
  assert.match(nodeTitle, /Gross profit/);
  assert.match(nodeTitle, /margin/);
});

test("an empty selection uses the latest reported quarter for that ticker", () => {
  const withForecast = (periods, forecast) => ({
    metrics: [metric("revenue_gaap", "Revenue", "USD", periods)],
    deep_dive: {
      forecast: {
        segments: [{
          label: "Revenue",
          metrics: [{
            metric_id: "revenue_gaap",
            unit: "USD",
            approval_state: "approved",
            forecast,
          }],
        }],
      },
    },
  });
  const first = revenueFlowModel("ACME", withForecast({
    "2026Q1": 10, "2026Q2": 11, "2026Q3": 12,
  }, [
    { fiscal_period: "2027Q1", value: 13, reported: true },
  ]), null, {});
  assert.deepEqual(first.periods.at(-1).activeQuarters, []);
  assert.deepEqual(first.periods.at(-1).quarters, [1]);
  assert.deepEqual(first.selection, { year: 2026, quarter: 3 });
  assert.equal(first.header, "ACME FY2026 Q3");
  assert.equal(first.nodes.find((node) => node.id === "revenue").value, 12);
  const firstView = renderRevenueFlow(first, fakeDocument(), {});
  const firstPicker = periodControls(firstView);
  assert.equal(firstPicker.yearOptions.find((item) => item.selected).value, "2026");
  assert.equal(firstPicker.quarterOptions.find((item) => item.selected).label, "Q3");

  const second = revenueFlowModel("BETA", withForecast({
    "2025Q4": 8, "2027Q2": 9,
  }, [
    { fiscal_period: "2028Q1", value: 10, reported: true },
  ]), null, {});
  assert.deepEqual(second.selection, { year: 2027, quarter: 2 });
  assert.deepEqual(latestDataQuarter(second.periods), { year: 2027, quarter: 2 });
  assert.notDeepEqual(first.selection, second.selection);

  const fullYear = revenueFlowModel("ACME", withForecast({ "2026Q3": 12 }, []), null, { year: 2026, quarter: null });
  assert.deepEqual(fullYear.selection, { year: 2026, quarter: null });
  const missingQuarter = revenueFlowModel("BETA", withForecast({ "2027Q2": 9 }, []), null, { year: 2027, quarter: 4 });
  assert.deepEqual(missingQuarter.selection, { year: 2027, quarter: null });
  const staleYear = revenueFlowModel("BETA", withForecast({ "2027Q2": 9 }, []), null, { year: 2030, quarter: 1 });
  assert.deepEqual(staleYear.selection, { year: 2027, quarter: 2 });

  const estimatesOnly = revenueFlowModel("GAMMA", {
    metrics: [],
    deep_dive: {
      forecast: {
        segments: [{
          label: "Revenue",
          metrics: [{
            metric_id: "revenue_gaap",
            display_name: "Revenue",
            unit: "USD",
            approval_state: "approved",
            forecast: [{ fiscal_period: "2027Q1", value: 5, reported: true }],
          }],
        }],
      },
    },
  }, null, {});
  assert.deepEqual(estimatesOnly.selection, { year: 2027, quarter: 1 });
  assert.equal(estimatesOnly.empty, false);

  const none = revenueFlowModel("DELTA", { metrics: [] }, null, {});
  assert.deepEqual(none.selection, { year: null, quarter: null });
  assert.equal(latestDataQuarter([]), null);
  assert.deepEqual(resolveSelection(null, {}), { year: null, quarter: null });
  assert.doesNotMatch(source, /TSLA|SPCX|NVDA|AAPL/);
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
  assert.equal(periodControls(view).year, undefined);
  assert.equal(text.some((item) => item === "Q1" || item === "Full Year"), false);
});
