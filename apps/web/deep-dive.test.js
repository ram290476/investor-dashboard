import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  axisLabel,
  axisLabels,
  axisTicks,
  confidenceLabel,
  formatDeepValue,
  hasDeepDive,
  orderedDeepDiveSections,
  renderDeepDive,
  revealDeepDiveSection,
  sectionHeadline,
  trendTone,
} from "./deep-dive.js";
import { nextStockTab, stockTabs, STOCK_TABS } from "./stock-page.js";

const source = await readFile(new URL("./deep-dive.js", import.meta.url), "utf8");
const geo = await readFile(new URL("./geo.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const ci = await readFile(new URL("../../.github/workflows/ci.yml", import.meta.url), "utf8");
const deepStyles = styles.slice(styles.indexOf("Deep dive is a company-page tab"));

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

function classNames(item) {
  const fromProp = String(item?.className || "");
  const fromAttr = typeof item?.getAttribute === "function" ? String(item.getAttribute("class") || "") : "";
  return `${fromProp} ${fromAttr}`.split(/\s+/).filter(Boolean);
}

function byClass(node, className) {
  const found = [];
  walk(node, (item) => {
    if (classNames(item).includes(className)) found.push(item);
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
  const chips = byClass(view, "deep-dive-section-tile");
  assert.equal(chips.length, sample.deep_dive.sections.length);
  assert.equal(byClass(view, "deep-dive-jump-label").length, 0);
  assert.doesNotMatch(textOf(view), /On this page/);
  assert.equal(byClass(view, "deep-dive-sections")[0].getAttribute("aria-label"), "Deep dive sections");
  const strip = byClass(view, "section-strip")[0];
  assert.equal(strip.getAttribute("aria-label"), "Sections");
  assert.equal(strip.getAttribute("aria-hidden"), "true");
  assert.equal(String(strip.className).includes("is-visible"), false);
  assert.equal(byClass(view, "section-strip-chip").length, sample.deep_dive.sections.length);
  assert.deepEqual(chips.map((chip) => chip.dataset.sectionId), ["scoreboard", "forecast", "places", "later"]);
  for (const chip of chips) {
    assert.equal(chip.tagName, "A");
    assert.equal(String(chip.className).includes("overlay-chip"), false);
    assert.equal(chip.getAttribute("role"), undefined);
    assert.equal(byClass(chip, "deep-dive-section-icon").length, 1);
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
  const phoneLater = byClass(phone, "deep-dive-section-tile").find((chip) => chip.dataset.sectionId === "later");
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

  const laterChip = byClass(view, "section-strip-chip").find((chip) => chip.dataset.sectionId === "later");
  assert.equal(laterChip.getAttribute("aria-current"), "location");
  assert.equal(laterChip.getAttribute("tabindex"), "-1");
  assert.equal(laterChip.href, "#stock/ABCD/deep-dive/later");
});

test("section tiles use a catalog headline, then the first series, and omit a metric when none exists", () => {
  const fleet = {
    id: "fleet",
    title: "Fleet",
    headline: "sites",
    panels: [{
      kind: "trend-bars",
      kpis: [
        { metric_id: "fleet", label: "Fleet", value: 10, unit: "count", qoq: 0.1, sparkline: [1, 10], approval_state: "approved" },
        {
          metric_id: "sites",
          label: "Sites",
          value: 4,
          unit: "count",
          qoq: -0.25,
          sparkline: [8, 4],
          approval_state: "proposed",
          estimate_only: true,
          source_kind: "news_estimate",
        },
      ],
    }],
  };
  const picked = sectionHeadline(fleet);
  assert.equal(picked.metric_id, "sites");
  assert.equal(picked.value, 4);
  assert.equal(picked.estimate, true);
  assert.equal(picked.approval_state, "proposed");
  assert.equal(picked.change, -0.25);
  assert.equal(sectionHeadline({ ...fleet, headline: "" }).metric_id, "fleet");
  assert.equal(sectionHeadline({ ...fleet, headline: "not_there" }).metric_id, "fleet");
  const fromSeries = sectionHeadline({
    id: "output",
    title: "Output",
    panels: [{
      kind: "trend-bars",
      series: [{
        metric_id: "built",
        label: "Built",
        unit: "count",
        approval_state: "proposed",
        points: [
          { period: "2025Q1", value: 2, reported: true },
          { period: "2025Q2", value: 4, reported: true },
        ],
      }],
    }],
  });
  assert.equal(fromSeries.metric_id, "built");
  assert.equal(fromSeries.value, 4);
  assert.equal(fromSeries.change, 1);
  assert.equal(fromSeries.approval_state, "proposed");
  assert.equal(sectionHeadline({
    id: "notes",
    title: "Notes",
    panels: [{ kind: "table", title: "Notes", table: { columns: [{ id: "a", label: "A" }], rows: [] } }],
  }), null);

  let booked = "";
  const view = renderDeepDive({
    deep_dive: {
      sections: [
        fleet,
        {
          id: "notes",
          title: "Notes",
          panels: [{ id: "notes", kind: "milestone-timeline", title: "Notes", milestones: [{ id: "pilot", label: "Pilot", reported: false }] }],
        },
      ],
    },
  }, fakeDocument(false), {
    ticker: "exmpl",
    onBookmark: (id, href) => {
      booked = `${id}|${href}`;
    },
  });
  const sites = byClass(view, "deep-dive-section-tile").find((tile) => tile.dataset.sectionId === "fleet");
  assert.match(textOf(sites), /Sites/);
  assert.match(textOf(sites), /Estimate/);
  assert.match(textOf(sites), /Pending review/);
  assert.equal(byClass(sites, "deep-dive-spark").length, 1);
  assert.equal(byClass(sites, "deep-dive-section-icon").length, 1);
  const notes = byClass(view, "deep-dive-section-tile").find((tile) => tile.dataset.sectionId === "notes");
  assert.equal(byClass(notes, "deep-dive-section-tile-value").length, 0);
  assert.equal(byClass(notes, "deep-dive-spark").length, 0);
  assert.match(textOf(notes), /Notes/);
  const event = {
    button: 0,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    preventDefault() { this.defaultPrevented = true; },
  };
  notes.listeners.click[0](event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(booked, "notes|#stock/EXMPL/deep-dive/notes");
  assert.equal(notes.getAttribute("aria-current"), "location");
  const section = findId(view, "deep-dive-notes");
  assert.equal(section.scrolled.behavior, "smooth");
  assert.equal(section.scrolled.block, "start");
});

test("section tiles follow primary then secondary, and regulator records sit beside the estimate", () => {
  const payload = {
    deep_dive: {
      title: "Deep dive",
      sections: [
        {
          id: "later",
          title: "Later",
          placement: "secondary",
          panels: [{ id: "later", kind: "trend-bars", title: "Later", kpis: [{ label: "Plants", value: 2, unit: "count" }] }],
        },
        {
          id: "fleet",
          title: "Fleet",
          placement: "primary",
          panels: [{
            id: "fleet",
            kind: "trend-bars",
            title: "Fleet",
            kpis: [{
              label: "Fleet",
              unit: "count",
              value: 40,
              estimate_only: true,
              source_kind: "news_estimate",
              approval_state: "proposed",
              regulator_value: 12,
              regulator_source_url: "https://regulator.example/roster",
            }, {
              label: "Permits held",
              unit: "permits",
              value: 1,
              source_kind: "av_regulator",
              approval_state: "proposed",
            }],
          }],
        },
      ],
    },
  };
  assert.deepEqual(orderedDeepDiveSections(payload.deep_dive.sections).map((section) => section.id), ["fleet", "later"]);
  const view = renderDeepDive(payload, fakeDocument(false), { ticker: "abcd" });
  const chips = byClass(view, "deep-dive-section-tile");
  assert.deepEqual(chips.map((chip) => chip.dataset.sectionId), ["fleet", "later"]);
  assert.equal(chips[0].getAttribute("role"), undefined);
  const fleet = byClass(view, "deep-dive-tile").find((tile) => textOf(tile).includes("Fleet"));
  assert.match(textOf(fleet), /Estimate/);
  assert.match(textOf(fleet), /Regulator records/);
  assert.match(textOf(fleet), /12/);
  const permits = byClass(view, "deep-dive-tile").find((tile) => textOf(tile).includes("Permits held"));
  assert.equal(byClass(permits, "deep-dive-regulator").length, 1);
  assert.equal(byClass(permits, "deep-dive-estimate").length, 0);
  const sections = byClass(view, "deep-dive-section").map((section) => section.id);
  assert.deepEqual(sections, ["deep-dive-fleet", "deep-dive-later"]);
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
  assert.deepEqual(stockTabs(null).map((tab) => tab.id), ["trends", "overview", "fundamentals", "kpis"]);
  assert.deepEqual(stockTabs(sample).map((tab) => tab.id), ["trends", "overview", "fundamentals", "deep-dive", "kpis"]);
  assert.equal(stockTabs(sample).find((tab) => tab.id === "deep-dive").label, "Deep dive");
  assert.equal(nextStockTab("fundamentals", "ArrowRight", stockTabs(sample)), "deep-dive");
  assert.equal(nextStockTab("deep-dive", "ArrowRight", stockTabs(sample)), "kpis");
  assert.equal(nextStockTab("kpis", "ArrowRight", stockTabs(sample)), "trends");
  assert.equal(nextStockTab("overview", "End", stockTabs(sample)), "kpis");
  assert.equal(nextStockTab("kpis", "Home", stockTabs(sample)), "trends");
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
  assert.doesNotMatch(source, /On this page/);
  assert.match(source, /sectionHeadline/);
  assert.match(source, /deep-dive-section-tile/);
  assert.match(styles, /\.deep-dive-sections\s*\{[^}]*grid-template-columns:\s*repeat\(6/);
  assert.match(styles, /\.deep-dive-section-tile:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--amber\)/);
  assert.match(styles, /\.stock-page-tabs\s*\{[^}]*border-radius:\s*12px/);
  assert.match(styles, /\.stock-tab-deep\[aria-selected="true"\][\s\S]*var\(--amber\)/);
  const phoneGrid = styles.slice(styles.lastIndexOf("@media (max-width: 640px)"));
  assert.match(phoneGrid, /\.deep-dive-sections\s*\{[^}]*repeat\(2/);
  assert.match(app, /hasDeepDive\(payload\)/);
  assert.match(app, /stock-tab-mark", "\+"/);
  assert.match(deepStyles, /grid-template-columns:\s*repeat\(6/);
  assert.match(styles, /\.stock-page-tabs\s*\{[^}]*display:\s*flex/);
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

function nodesWithClass(node, className, { excluded = "" } = {}) {
  const found = [];
  walk(node, (item) => {
    const classes = `${item.className || ""} ${item.attributes?.class || ""}`.split(/\s+/);
    if (classes.includes(className) && !(excluded && classes.includes(excluded))) found.push(item);
  });
  return found;
}

test("axis labels put the quarter above the year and month names on monthly charts", () => {
  assert.deepEqual(axisLabel("2026Q2"), { period: "2026Q2", primary: "Q2", secondary: "2026", kind: "quarter" });
  assert.deepEqual(axisLabel("2026-01"), { period: "2026-01", primary: "Jan", secondary: "2026", kind: "month" });
  const months = axisLabels(["2025-12", "2026-01", "2026-02"]);
  assert.deepEqual(months.map((item) => [item.primary, item.secondary]), [
    ["Dec", "2025"],
    ["Jan", "2026"],
    ["Feb", ""],
  ]);
  const crowded = ["2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"];
  const ticks = axisTicks(crowded, 10);
  assert.equal(ticks[0].primary, "Q1");
  assert.equal(ticks[0].secondary, "2024");
  assert.equal(ticks.at(-1).primary, "Q2");
  assert.equal(ticks.at(-1).secondary, "2026");
  assert.ok(ticks.length < crowded.length);
  assert.ok(ticks.every((item) => item.secondary));
  const monthTicks = axisTicks(["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04"], 8);
  assert.ok(monthTicks.some((item) => item.primary === "Jan" && item.secondary === "2026"));
  assert.equal(monthTicks[0].primary, "Nov");

  const quarterly = renderDeepDive({
    deep_dive: {
      title: "Deep dive",
      sections: [{
        id: "bars",
        title: "Bars",
        panels: [{
          id: "bars",
          kind: "trend-bars",
          title: "Bars",
          series: [{
            label: "Reported",
            role: "bar",
            points: [
              { period: "2026Q1", value: 2, reported: true },
              { period: "2026Q2", value: 4, reported: true },
            ],
          }],
        }],
      }],
    },
  }, fakeDocument(false));
  const quarterText = nodesWithClass(quarterly, "deep-dive-axis", { excluded: "deep-dive-axis-year" }).map((node) => node.textContent);
  const quarterYears = nodesWithClass(quarterly, "deep-dive-axis-year").map((node) => node.textContent);
  assert.deepEqual(quarterText, ["Q1", "Q2"]);
  assert.deepEqual(quarterYears, ["2026", "2026"]);
  assert.equal(quarterText.some((label) => label === "2026Q2"), false);

  const monthly = renderDeepDive({
    deep_dive: {
      title: "Deep dive",
      sections: [{
        id: "months",
        title: "Months",
        panels: [{
          id: "months",
          kind: "trend-bars",
          title: "Months",
          cadence: "monthly",
          series: [{
            label: "Reports",
            role: "bar",
            points: [
              { period: "2025-12", value: 1, reported: true },
              { period: "2026-01", value: 2, reported: true },
              { period: "2026-02", value: 3, reported: true },
            ],
          }],
        }],
      }],
    },
  }, fakeDocument(false));
  const monthText = nodesWithClass(monthly, "deep-dive-axis", { excluded: "deep-dive-axis-year" }).map((node) => node.textContent);
  const monthYears = nodesWithClass(monthly, "deep-dive-axis-year").map((node) => node.textContent);
  assert.deepEqual(monthText, ["Dec", "Jan", "Feb"]);
  assert.deepEqual(monthYears, ["2025", "2026"]);
  assert.equal(monthText.includes("2026-01"), false);
});

test("an estimate stays beside the reported figure and a stored headline waits for the actual", () => {
  const view = renderDeepDive({
    deep_dive: {
      title: "Deep dive",
      sections: [{
        id: "score",
        title: "Score",
        panels: [{
          id: "tiles",
          kind: "scoreboard",
          title: "Score",
          kpis: [{
            label: "Fleet",
            unit: "count",
            value: 12,
            approval_state: "approved",
            approved: true,
            estimate: true,
            estimate_value: 40,
            estimate_source_url: "https://news.example/fleet",
          }, {
            label: "Output",
            unit: "count",
            value: 9,
            estimate_only: true,
            source_kind: "news_estimate",
            approval_state: "proposed",
            confidence: "low",
            source_url: "https://news.example/output",
            estimate_source_url: "https://news.example/output",
          }],
        }, {
          id: "forecast",
          kind: "forecast-band",
          title: "Forecast",
          forecast: {
            disclaimer: "Seasonal copy of the same quarter a year earlier. Not a forecast model.",
            prior_projection_note: "Stored headline versus the reported actual.",
            prior_projection: [{
              segment_id: "total",
              segment_label: "Total",
              field: "revenue",
              period: "2026Q2",
              projected: 20,
              actual: 25,
              unit: "USD",
            }],
            stored_headlines: [
              { segment_id: "total", field: "revenue", period: "2026Q2", value: 20, unit: "USD" },
              { segment_id: "total", segment_label: "Total", field: "revenue", period: "2026Q3", value: 28, unit: "USD" },
            ],
            segments: [{ id: "total", label: "Total", metrics: [{ field: "revenue", unit: "USD", actuals: [], forecast: [] }] }],
          },
        }],
      }],
    },
  }, fakeDocument(false));
  const fleet = byClass(view, "deep-dive-tile").find((tile) => textOf(tile).includes("Fleet"));
  assert.equal(byClass(fleet, "deep-dive-tile-value")[0].textContent, "12");
  assert.equal(byClass(fleet, "deep-dive-estimate").length, 1);
  assert.match(textOf(byClass(fleet, "deep-dive-estimate-line")[0]), /40/);
  assert.equal(byClass(fleet, "deep-dive-source-link")[0].getAttribute("href"), "https://news.example/fleet");
  const output = byClass(view, "deep-dive-tile").find((tile) => textOf(tile).includes("Output"));
  assert.match(textOf(output), /Estimate/);
  assert.match(textOf(output), /Pending review/);
  assert.match(textOf(output), /Low confidence/);
  const prior = textOf(view);
  assert.match(prior, /Stored headline/);
  assert.match(prior, /Waiting for the report/);
  assert.match(prior, /2026Q3/);
  assert.match(prior, /2026Q2/);
});
