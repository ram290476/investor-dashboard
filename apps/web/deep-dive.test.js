import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  confidenceLabel,
  formatDeepValue,
  hasDeepDive,
  renderDeepDive,
  trendTone,
} from "./deep-dive.js";
import { nextStockTab, stockTabs, STOCK_TABS } from "./stock-page.js";

const source = await readFile(new URL("./deep-dive.js", import.meta.url), "utf8");
const geo = await readFile(new URL("./geo.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const ci = await readFile(new URL("../../.github/workflows/ci.yml", import.meta.url), "utf8");
const deepStyles = styles.slice(styles.indexOf("Deep dive is a fifth company-page tab"));

function element(tag) {
  const node = {
    tagName: String(tag).toUpperCase(),
    className: "",
    textContent: "",
    children: [],
    style: {},
    dataset: {},
    hidden: false,
    attributes: {},
    colSpan: 1,
    listeners: {},
  };
  node.childNodes = node.children;
  node.classList = {
    add(...names) {
      const parts = node.className.split(/\s+/).filter(Boolean);
      for (const name of names) if (!parts.includes(name)) parts.push(name);
      node.className = parts.join(" ");
    },
  };
  node.append = (...kids) => {
    for (const kid of kids) if (kid != null) node.children.push(kid);
  };
  node.setAttribute = (key, value) => {
    node.attributes[key] = String(value);
  };
  node.getAttribute = (key) => node.attributes[key];
  node.addEventListener = (type, fn) => {
    node.listeners[type] = node.listeners[type] || [];
    node.listeners[type].push(fn);
  };
  node.click = () => (node.listeners.click || []).forEach((fn) => fn());
  node.replaceChildren = (...kids) => {
    node.children.splice(0, node.children.length, ...kids.filter((kid) => kid != null));
  };
  return node;
}

function fakeDocument(phone = false) {
  return {
    createElement: (tag) => element(tag),
    createElementNS: (_ns, tag) => element(tag),
    createTextNode: (text) => ({ textContent: String(text), children: [] }),
    defaultView: { matchMedia: () => ({ matches: phone }) },
  };
}

function walk(node, visit) {
  visit(node);
  for (const child of node.children || []) walk(child, visit);
}

function textOf(node) {
  const parts = [];
  walk(node, (item) => {
    if (item.textContent) parts.push(item.textContent);
  });
  return parts.join(" ").replace(/\s+/g, " ").trim();
}

function byClass(node, className) {
  const found = [];
  walk(node, (item) => {
    if (String(item.className || "").split(/\s+/).includes(className)) found.push(item);
  });
  return found;
}

const sample = {
  deep_dive: {
    title: "Deep dive",
    disclaimer: "Figures appear when a source has been collected. Pending values stay labeled.",
    sections: [
      {
        id: "scoreboard",
        title: "Scoreboard",
        placement: "primary",
        panels: [{
          id: "tiles",
          kind: "scoreboard",
          title: "Scoreboard",
          width: "wide",
          kpis: [
            { label: "Fleet", unit: "count", value: null, empty_label: "Not yet disclosed", approval_state: "proposed" },
            {
              label: "Inventory days",
              unit: "count",
              value: 12,
              approval_state: "proposed",
              confidence: "low",
              inverse: true,
              qoq: -0.1,
              sparkline: [3, 2, 1],
            },
          ],
        }],
      },
      {
        id: "forecast",
        title: "Forecast",
        placement: "primary",
        panels: [{
          id: "forecast",
          kind: "forecast-band",
          title: "Revenue and earnings",
          width: "wide",
          forecast: {
            disclaimer: "Seasonal copy of the same quarter a year earlier. Not a forecast model.",
            consensus_note: "No licensed consensus source is configured.",
            prior_projection_note: "No prior projection is stored.",
            segments: [{
              id: "total",
              label: "Total",
              metrics: [{
                field: "revenue",
                unit: "USD",
                actuals: [{ period: "2025Q3", value: 20, reported: true }],
                forecast: [{ period: "2026Q3", value: 20, reported: true, band_low: null, band_high: null }],
              }, {
                field: "earnings",
                unit: "per_share",
                actuals: [],
                forecast: [{ period: "2026Q3", value: null, reported: false }],
                empty_label: "Not yet disclosed",
              }],
            }],
          },
        }],
      },
      {
        id: "places",
        title: "Places",
        placement: "primary",
        panels: [{
          id: "sites",
          kind: "map-sites",
          title: "Chargers",
          width: "half",
          map: "sites",
          markers: [],
        }, {
          id: "modes",
          kind: "trend-bars",
          title: "Incidents",
          width: "half",
          modes: [{
            id: "ads",
            label: "ADS",
            series: [{ label: "Counts", role: "bar", points: [] }],
            notes: ["Narratives may be redacted."],
          }],
        }],
      },
      {
        id: "later",
        title: "Energy news",
        placement: "secondary",
        panels: [{
          id: "news",
          kind: "table",
          title: "Energy news",
          width: "wide",
          empty_label: "Not yet collected",
          table: { columns: [{ id: "headline", label: "Headline" }], rows: [] },
        }, {
          id: "miles",
          kind: "milestone-timeline",
          title: "Milestones",
          width: "wide",
          milestones: [{ id: "pilot", label: "Pilot fleet", reported: false, empty_label: "Not yet disclosed", approval_state: "proposed" }],
        }],
      },
    ],
  },
};

test("deep dive helpers stay ticker-agnostic and label empty or placeholder values", () => {
  assert.equal(hasDeepDive({}), false);
  assert.equal(hasDeepDive(sample), true);
  assert.equal(formatDeepValue(1.2, "per_share"), "1.20");
  assert.equal(formatDeepValue(null, "USD"), "");
  assert.equal(confidenceLabel("low"), "Low confidence");
  assert.equal(confidenceLabel("high"), "");
  assert.equal(confidenceLabel("placeholder"), "Placeholder");
  assert.equal(trendTone(-0.1, true), "up");
  assert.equal(trendTone(-0.1, false), "down");
  assert.equal(trendTone(0, true), "flat");
  assert.equal(STOCK_TABS.length, 4);
  assert.equal(stockTabs(null).length, 4);
  assert.equal(stockTabs(sample).at(-1).id, "deep-dive");
  assert.equal(stockTabs(sample).at(-1).label, "Deep dive");
  assert.equal(nextStockTab("calls", "ArrowRight", stockTabs(sample)), "deep-dive");
  assert.equal(nextStockTab("overview", "End", stockTabs(sample)), "deep-dive");
  assert.equal(nextStockTab("deep-dive", "ArrowRight", stockTabs(sample)), "overview");
});

test("rendered deep dive shows pending review, empty copy, and a placeholder forecast", () => {
  const view = renderDeepDive(sample, fakeDocument(false));
  const text = textOf(view);
  assert.match(text, /Figures appear when a source has been collected/);
  assert.match(text, /Not yet disclosed/);
  assert.match(text, /Pending review/);
  assert.match(text, /Low confidence/);
  assert.match(text, /Improved/);
  assert.match(text, /Not a real model/);
  assert.match(text, /Not a forecast model/);
  assert.match(text, /No licensed consensus source is configured/);
  assert.match(text, /Site locations are not collected/);
  assert.match(text, /Not yet collected/);
  assert.match(text, /Narratives may be redacted/);
  assert.equal(byClass(view, "review-badge").length > 0, true);
  assert.equal(byClass(view, "deep-dive-model-flag")[0].textContent, "Not a real model");
  const secondary = byClass(view, "deep-dive-secondary-body")[0];
  assert.equal(secondary.hidden, false);
  assert.equal(byClass(view, "deep-dive-secondary-toggle")[0].getAttribute("aria-expanded"), "true");
  const phone = renderDeepDive(sample, fakeDocument(true));
  assert.equal(byClass(phone, "deep-dive-secondary-body")[0].hidden, true);
  const empty = renderDeepDive({}, fakeDocument(false));
  assert.match(textOf(empty), /No deep dive is configured/);
  assert.doesNotMatch(text, /EXAMPLE|TSLA|Tesla/);
});

test("deep dive sources name no ticker and ship no example observations", () => {
  for (const file of [source, geo, deepStyles]) {
    assert.doesNotMatch(file, /TSLA|Tesla|NVDA|SPCX/);
    assert.doesNotMatch(file, /EXAMPLE/);
    assert.doesNotMatch(file, /api[_-]?key/i);
  }
  assert.match(source, /Not a real model/);
  assert.match(source, /Pending review/);
  assert.match(geo, /not observations/);
  assert.doesNotMatch(deepStyles, /#[0-9a-fA-F]{3,8}/);
  assert.match(deepStyles, /var\(--surface\)/);
  assert.match(deepStyles, /data-has-deep-dive/);
  assert.match(app, /stockTabs\(payload\)/);
  assert.match(app, /renderDeepDive\(payload\)/);
  assert.match(app, /hasDeepDive\(payload\)/);
  assert.match(ci, /node --check apps\/web\/deep-dive\.js/);
  assert.match(ci, /node --check apps\/web\/geo\.js/);
});
