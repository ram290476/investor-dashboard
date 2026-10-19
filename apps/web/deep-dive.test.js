import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  confidenceLabel,
  formatDeepValue,
  hasDeepDive,
  renderDeepDive,
  revealDeepDiveSection,
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
    for (const kid of kids) {
      if (kid == null) continue;
      kid.parentElement = node;
      kid.parentNode = node;
      node.children.push(kid);
    }
  };
  node.setAttribute = (key, value) => {
    node.attributes[key] = String(value);
  };
  node.getAttribute = (key) => node.attributes[key];
  node.removeAttribute = (key) => {
    delete node.attributes[key];
  };
  node.scrollIntoView = (options) => {
    node.scrolled = options;
  };
  node.focus = (options) => {
    node.focused = options;
  };
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

test("deep dive bookmarks scroll in page and keep the company hash", () => {
  let booked = "";
  const view = renderDeepDive(sample, fakeDocument(false), {
    ticker: "abcd",
    currentSection: "forecast",
    onBookmark: (id, href) => {
      booked = `${id}|${href}`;
    },
  });
  const chips = byClass(view, "deep-dive-jump-chip");
  assert.equal(chips.length, sample.deep_dive.sections.length);
  assert.equal(byClass(view, "deep-dive-jump-label")[0].textContent, "On this page");
  assert.equal(byClass(view, "deep-dive-jump")[0].getAttribute("aria-labelledby"), "deep-dive-jump-label");
  for (const chip of chips) {
    assert.equal(chip.tagName, "A");
    assert.equal(String(chip.className).includes("overlay-chip"), false);
    assert.equal(chip.getAttribute("role"), undefined);
    assert.doesNotMatch(chip.href, /^#deep-dive-/);
    assert.match(chip.href, /^#stock\/ABCD\/deep-dive\//);
  }
  const forecast = chips.find((chip) => chip.dataset.sectionId === "forecast");
  assert.equal(forecast.href, "#stock/ABCD/deep-dive/forecast");
  assert.equal(forecast.getAttribute("aria-current"), "location");
  const modified = {
    button: 0,
    metaKey: true,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    preventDefault() { this.defaultPrevented = true; },
  };
  forecast.listeners.click[0](modified);
  assert.equal(modified.defaultPrevented, undefined);

  const later = chips.find((chip) => chip.dataset.sectionId === "later");
  const event = {
    button: 0,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    preventDefault() { this.defaultPrevented = true; },
  };
  later.listeners.click[0](event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(booked, "later|#stock/ABCD/deep-dive/later");
  assert.equal(later.getAttribute("aria-current"), "location");
  assert.equal(forecast.getAttribute("aria-current"), undefined);
  const section = findId(view, "deep-dive-later");
  assert.equal(section.scrolled.behavior, "smooth");
  assert.equal(section.scrolled.block, "start");
  assert.equal(section.tabIndex, -1);
  assert.equal(section.focused.preventScroll, true);
  const body = byClass(view, "deep-dive-secondary-body")[0];
  assert.equal(body.hidden, false);
  assert.equal(byClass(view, "deep-dive-secondary-toggle")[0].getAttribute("aria-expanded"), "true");
  assert.equal(revealDeepDiveSection(view, "missing"), null);

  const phone = renderDeepDive(sample, fakeDocument(true), { ticker: "zzzz" });
  assert.equal(byClass(phone, "deep-dive-secondary-body")[0].hidden, true);
  const phoneLater = byClass(phone, "deep-dive-jump-chip").find((chip) => chip.dataset.sectionId === "later");
  phoneLater.listeners.click[0]({
    button: 0,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    preventDefault() {},
  });
  assert.equal(byClass(phone, "deep-dive-secondary-body")[0].hidden, false);
  assert.equal(phoneLater.href, "#stock/ZZZZ/deep-dive/later");
});

function findId(node, id) {
  if (node?.id === id) return node;
  for (const child of node?.children || []) {
    const found = findId(child, id);
    if (found) return found;
  }
  return null;
}

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
  assert.match(app, /renderDeepDive\(payload/);
  assert.match(app, /openDeepDiveBookmark/);
  assert.match(app, /revealDeepDiveSection\(/);
  assert.match(source, /scrollIntoView/);
  assert.match(source, /preventDefault/);
  assert.match(source, /stockHash\(/);
  assert.doesNotMatch(source, /#deep-dive-\$\{/);
  assert.doesNotMatch(source, /overlay-chip deep-dive-jump-chip/);
  assert.match(app, /hasDeepDive\(payload\)/);
  assert.match(ci, /node --check apps\/web\/deep-dive\.js/);
  assert.match(ci, /node --check apps\/web\/geo\.js/);
});

test("a second company page uses the same panels for launches, forecast, and voice labels", () => {
  const payload = {
    deep_dive: {
      title: "Deep dive",
      disclaimer: "Figures appear when a source has been collected.",
      sections: [
        {
          id: "forecast",
          title: "Revenue and earnings forecast",
          placement: "primary",
          panels: [{
            id: "forecast",
            kind: "forecast-band",
            title: "Revenue and earnings forecast",
            width: "wide",
            forecast: {
              disclaimer: "Seasonal copy of the same quarter a year earlier. Not a forecast model.",
              consensus_note: "No licensed consensus source is configured.",
              segments: [
                { id: "launch", label: "Launch", metrics: [{ field: "revenue", unit: "USD", actuals: [], forecast: [] }] },
                { id: "total", label: "Total", metrics: [
                  { field: "revenue", unit: "USD", actuals: [], forecast: [{ period: "next", value: null, reported: false }] },
                  { field: "earnings", unit: "per_share", actuals: [], forecast: [{ period: "next", value: null, reported: false }] },
                ] },
              ],
            },
          }],
        },
        {
          id: "launches",
          title: "Launches",
          placement: "primary",
          panels: [{
            id: "schedule",
            kind: "table",
            title: "Launch schedule",
            width: "wide",
            empty_label: "Not yet disclosed",
            table: {
              columns: [
                { id: "date", label: "Date" },
                { id: "vehicle", label: "Vehicle" },
                { id: "mission", label: "Mission / customer" },
                { id: "orbit", label: "Orbit" },
                { id: "outcome", label: "Outcome" },
                { id: "booster", label: "Booster reuse" },
              ],
              rows: [],
            },
          }],
        },
        {
          id: "projected",
          title: "Projected launches",
          placement: "primary",
          panels: [{
            id: "projected",
            kind: "trend-bars",
            title: "Projected launches",
            width: "wide",
            source_note: "The placeholder does not project launch counts.",
            series: [],
          }],
        },
        {
          id: "terraform",
          title: "Terraform",
          placement: "primary",
          panels: [{
            id: "terraform",
            kind: "milestone-timeline",
            title: "Terraform progress",
            subtitle: "Voice label. The program name is unconfirmed.",
            width: "wide",
            source_note: "Not HashiCorp Terraform.",
            milestones: [{ id: "goal", label: "Stated goal", reported: false, empty_label: "Not yet disclosed" }],
          }],
        },
        {
          id: "starmind",
          title: "Starmind",
          placement: "primary",
          panels: [{
            id: "starmind",
            kind: "trend-bars",
            title: "Production and deployments",
            subtitle: "Voice label. The program name is unconfirmed and may be Starshield.",
            width: "wide",
            series: [],
          }],
        },
      ],
    },
  };
  const view = renderDeepDive(payload, fakeDocument(false));
  const text = textOf(view);
  assert.match(text, /Not a real model/);
  assert.match(text, /Not yet disclosed/);
  assert.match(text, /Launch schedule/);
  assert.match(text, /Mission \/ customer/);
  assert.match(text, /Booster reuse/);
  assert.match(text, /Voice label\. The program name is unconfirmed/);
  assert.match(text, /may be Starshield/);
  assert.match(text, /Not HashiCorp Terraform/);
  assert.match(text, /does not project launch counts/);
  assert.equal(byClass(view, "deep-dive-secondary").length, 0);
  assert.doesNotMatch(text, /EXAMPLE|TSLA|SPCX/);
  assert.doesNotMatch(source, /SPCX|SpaceX|Terraform|Starmind/);
});
