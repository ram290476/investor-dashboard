import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";
import {
  axisLabel,
  axisLabels,
  axisTicks,
  confidenceLabel,
  formatDeepValue,
  hasDeepDive,
  layoutNhtsaAxis,
  mapDataPoints,
  MIN_DATA_POINTS,
  plottableMapPoints,
  plottableSeriesPoints,
  REGULATOR_MIN_DATA_POINTS,
  RESEARCH_FALLBACK,
  showsResearchFallback,
  nhtsaMonthLabel,
  nhtsaPeriods,
  orderedDeepDiveSections,
  renderDeepDive,
  revealDeepDiveSection,
  shortSectionLabel,
  trendTone,
} from "./deep-dive.js";
import { nextStockTab, stockTabs, STOCK_TABS } from "./stock-page.js";

const source = await readFile(new URL("./deep-dive.js", import.meta.url), "utf8");
const geo = await readFile(new URL("./geo.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const ci = await readFile(new URL("../../.github/workflows/ci.yml", import.meta.url), "utf8");
const deepStyles = styles.slice(styles.indexOf("Research is a company-page tab"));

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
  assert.equal(byClass(view, "deep-dive-section-tile").length, 0);
  assert.equal(byClass(view, "deep-dive-sections").length, 0);
  assert.equal(byClass(view, "deep-dive-jump-label").length, 0);
  assert.doesNotMatch(textOf(view), /On this page/);
  const strip = byClass(view, "section-strip")[0];
  assert.equal(strip.getAttribute("aria-label"), "Research sections");
  assert.equal(strip.getAttribute("aria-hidden"), undefined);
  assert.equal(String(strip.className).includes("is-visible"), true);
  assert.equal(String(strip.className).includes("section-strip-docked"), true);
  const chips = byClass(view, "section-strip-chip");
  assert.equal(chips.length, sample.deep_dive.sections.length);
  assert.deepEqual(chips.map((chip) => chip.dataset.sectionId), ["scoreboard", "forecast", "places", "later"]);
  for (const chip of chips) {
    assert.equal(chip.tagName, "A");
    assert.equal(String(chip.className).includes("overlay-chip"), false);
    assert.equal(chip.getAttribute("role"), undefined);
    assert.equal(chip.getAttribute("tabindex"), undefined);
    assert.doesNotMatch(chip.href, /^#deep-dive-/);
    assert.match(chip.href, /^#stock\/ABCD\/research\//);
  }
  const forecast = chips.find((chip) => chip.dataset.sectionId === "forecast");
  assert.equal(forecast.href, "#stock/ABCD/research/forecast");
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
  assert.equal(booked, "later|#stock/ABCD/research/later");
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
  const phoneLater = byClass(phone, "section-strip-chip").find((chip) => chip.dataset.sectionId === "later");
  phoneLater.listeners.click[0]({
    button: 0,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    preventDefault() {},
  });
  assert.equal(byClass(phone, "deep-dive-secondary-body")[0].hidden, false);
  assert.equal(phoneLater.href, "#stock/ZZZZ/research/later");

  const laterChip = byClass(view, "section-strip-chip").find((chip) => chip.dataset.sectionId === "later");
  assert.equal(laterChip.getAttribute("aria-current"), "location");
  assert.equal(laterChip.getAttribute("tabindex"), undefined);
  assert.equal(laterChip.href, "#stock/ABCD/research/later");
});

test("section chips jump by name and leave the metric on the section", () => {
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
  const sites = byClass(view, "section-strip-chip").find((chip) => chip.dataset.sectionId === "fleet");
  assert.equal(textOf(sites), "Fleet");
  assert.equal(sites.getAttribute("title"), "Fleet");
  assert.equal(sites.getAttribute("aria-label"), "Fleet");
  assert.equal(byClass(view, "deep-dive-section-tile").length, 0);
  const sitesMetric = byClass(view, "deep-dive-tile").find((tile) => textOf(tile).includes("Sites"));
  assert.match(textOf(sitesMetric), /Estimate/);
  assert.match(textOf(sitesMetric), /Pending review/);
  const notes = byClass(view, "section-strip-chip").find((chip) => chip.dataset.sectionId === "notes");
  assert.equal(textOf(notes), "Notes");
  assert.equal(notes.getAttribute("title"), "Notes");
  assert.equal(notes.getAttribute("aria-label"), "Notes");
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
  assert.equal(booked, "notes|#stock/EXMPL/research/notes");
  assert.equal(notes.getAttribute("aria-current"), "location");
  const section = findId(view, "deep-dive-notes");
  assert.equal(section.scrolled.behavior, "smooth");
  assert.equal(section.scrolled.block, "start");
});

test("section chips follow primary then secondary, and regulator records sit beside the estimate", () => {
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
  const chips = byClass(view, "section-strip-chip");
  assert.deepEqual(chips.map((chip) => chip.dataset.sectionId), ["fleet", "later"]);
  assert.equal(chips[0].getAttribute("role"), undefined);
  assert.equal(byClass(view, "deep-dive-section-tile").length, 0);
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
  assert.deepEqual(stockTabs(null).map((tab) => tab.id), ["summary", "analysis", "fundamentals", "kpis"]);
  assert.deepEqual(stockTabs(sample).map((tab) => tab.id), ["summary", "analysis", "research", "fundamentals", "kpis"]);
  assert.equal(stockTabs(sample).find((tab) => tab.id === "research").label, "Research");
  assert.equal(stockTabs(sample).find((tab) => tab.id === "analysis").label, "Analysis");
  assert.equal(stockTabs({ deep_dive: { title: "Deep dive", sections: sample.deep_dive.sections } }).find((tab) => tab.id === "research").label, "Research");
  assert.equal(stockTabs(sample).some((tab) => tab.id === "admin"), false);
  assert.equal(nextStockTab("summary", "ArrowRight", stockTabs(sample)), "analysis");
  assert.equal(nextStockTab("trends", "ArrowRight", stockTabs(sample)), "analysis");
  assert.equal(nextStockTab("analysis", "ArrowRight", stockTabs(sample)), "research");
  assert.equal(nextStockTab("overview", "ArrowRight", stockTabs(sample)), "research");
  assert.equal(nextStockTab("technical", "ArrowRight", stockTabs(sample)), "research");
  assert.equal(nextStockTab("research", "ArrowRight", stockTabs(sample)), "fundamentals");
  assert.equal(nextStockTab("deep-dive", "ArrowRight", stockTabs(sample)), "fundamentals");
  assert.equal(nextStockTab("fundamentals", "ArrowLeft", stockTabs(sample)), "research");
  assert.equal(nextStockTab("kpis", "ArrowRight", stockTabs(sample)), "summary");
  assert.equal(nextStockTab("analysis", "End", stockTabs(sample)), "kpis");
  assert.equal(nextStockTab("kpis", "Home", stockTabs(sample)), "summary");
});

test("the forecast label sits once above the Research forecast panel", () => {
  const label = "Placeholder only — not a real forecast model.";
  const plain = {
    deep_dive: {
      disclaimer: label,
      sections: [{
        id: "forecast",
        title: "Revenue and earnings forecast",
        panels: [{
          id: "forecast",
          kind: "forecast-band",
          title: "Revenue and earnings forecast",
          forecast: { disclaimer: label, segments: [] },
        }],
      }],
    },
  };
  const research = renderDeepDive(plain, fakeDocument(false));
  const banners = byClass(research, "deep-dive-banner");
  assert.equal(banners.length, 1);
  assert.equal(banners[0].textContent, label);
  const block = byClass(research, "deep-dive-forecast-block")[0];
  assert.equal(block.children[0], banners[0]);
  assert.equal(classNames(block.children[1]).includes("deep-dive-panel"), true);
  assert.equal(byClass(research, "deep-dive-note").some((node) => node.textContent === label), false);
  assert.equal(byClass(renderDeepDive(sample, fakeDocument(false)), "trend-scoreboard-notice").length, 0);
  assert.doesNotMatch(source, /renderTrendDisclaimer|trend-scoreboard-notice/);
  assert.doesNotMatch(source, /TSLA|Tesla|SPCX/);
});

test("rendered deep dive shows pending review, empty copy, and a placeholder forecast", () => {
  const view = renderDeepDive(sample, fakeDocument(false));
  const text = textOf(view);
  assert.equal(byClass(view, "deep-dive-banner").length, 1);
  assert.equal(byClass(view, "deep-dive-banner")[0].textContent, sample.deep_dive.disclaimer);
  assert.match(text, /Not yet disclosed/);
  assert.match(text, /Pending review/);
  assert.match(text, /Low confidence/);
  assert.match(text, /Improved/);
  assert.match(text, /Not a real model/);
  assert.match(text, /Not a forecast model/);
  assert.match(text, /No licensed consensus source is configured/);
  assert.equal(byClass(view, "deep-dive-map").length, 0);
  assert.doesNotMatch(text, /Site locations are not collected/);
  assert.doesNotMatch(text, /Chargers/);
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
  assert.match(textOf(empty), /No research is configured/);
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
  assert.match(app, /stockTabs\(payload, \{ admin: metricAdmin, fund: fundTicker \}\)/);
  assert.match(app, /renderDeepDive\(payload/);
  assert.match(app, /openDeepDiveBookmark/);
  assert.match(app, /revealDeepDiveSection\(/);
  assert.match(source, /scrollIntoView/);
  assert.match(source, /preventDefault/);
  assert.match(source, /stockHash\(/);
  assert.doesNotMatch(source, /#deep-dive-\$\{/);
  assert.doesNotMatch(source, /overlay-chip deep-dive-jump-chip/);
  assert.doesNotMatch(source, /On this page/);
  assert.doesNotMatch(source, /sectionHeadline/);
  assert.doesNotMatch(source, /deep-dive-section-tile/);
  assert.match(source, /section-strip-docked/);
  assert.match(styles, /\.deep-dive-sections\s*\{[^}]*grid-template-columns:\s*repeat\(6/);
  assert.match(styles, /\.deep-dive-section-tile:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--amber\)/);
  assert.match(styles, /\.stock-page-tabs\s*\{[^}]*border-radius:\s*12px/);
  assert.match(styles, /\.stock-tab-deep\[aria-selected="true"\][\s\S]*var\(--amber\)/);
  const phoneGrid = styles.slice(styles.lastIndexOf("@media (max-width: 640px)"));
  assert.match(phoneGrid, /\.deep-dive-sections\s*\{[^}]*repeat\(2/);
  assert.match(app, /hasDeepDive\(payload\)/);
  assert.match(app, /stock-tab-mark", "\+"/);
  assert.match(deepStyles, /grid-template-columns:\s*repeat\(6/);
  assert.match(deepStyles, /\.deep-dive-panel,\s*\n\.deep-dive-chart-wrap,\s*\n\.deep-dive-map-wrap,\s*\n\.deep-dive-forecast\s*\{[^}]*overflow-x:\s*clip/);
  assert.doesNotMatch(deepStyles, /\.section-strip,\s*\n\.section-strip-track\s*\{[^}]*overflow-x:\s*clip/);
  assert.match(deepStyles, /\.deep-dive-chart,\s*\n\.deep-dive-map,\s*\n\.deep-dive-spark\s*\{[^}]*max-width:\s*100%/);
  assert.match(deepStyles, /\.deep-dive-table\s*\{[^}]*width:\s*100%/);
  assert.match(deepStyles, /\.deep-dive-table\s*\{[^}]*table-layout:\s*fixed/);
  const stripTrack = deepStyles.match(/\.section-strip-track\s*\{[^}]*\}/)[0];
  assert.match(stripTrack, /flex-wrap:\s*nowrap/);
  assert.match(stripTrack, /overflow-x:\s*auto/);
  assert.match(stripTrack, /scrollbar-width:\s*none/);
  assert.match(stripTrack, /min-width:\s*0/);
  assert.doesNotMatch(stripTrack, /flex-wrap:\s*wrap/);
  assert.match(deepStyles, /\.section-strip\s*\{[^}]*overflow:\s*visible/);
  assert.match(deepStyles, /\.section-strip\s*\{[^}]*width:\s*100%/);
  assert.match(deepStyles, /\.section-strip-chip\s*\{[^}]*min-width:\s*0/);
  assert.match(deepStyles, /\.section-strip-chip\s*\{[^}]*flex:\s*0 0 auto/);
  assert.match(deepStyles, /\.section-strip-chip\s*\{[^}]*white-space:\s*nowrap/);
  assert.match(deepStyles, /\.deep-dive \.section-strip-chip\s*\{[^}]*min-width:\s*0/);
  assert.match(deepStyles, /\.deep-dive \.section-strip-label\s*\{[^}]*white-space:\s*nowrap/);
  assert.match(deepStyles, /@container section-strip scroll-state\(scrollable: right\)/);
  assert.match(source, /section-strip-fade-start/);
  assert.match(source, /section-strip-fade-end/);
  assert.match(source, /section-strip-label/);
  assert.match(deepStyles, /max-width:\s*480px\)[\s\S]*?top:\s*98px/);
  assert.equal(deepStyles.match(/@media[^{]+\{[^@]*\.section-strip-track[^{]*overflow-x:\s*auto/s), null);
  assert.match(styles, /\.stock-page-tabs\s*\{[^}]*display:\s*flex/);
  assert.match(ci, /node --check apps\/web\/deep-dive\.js/);
  assert.match(ci, /node --check apps\/web\/geo\.js/);
});

test("deep dive section chips abbreviate catalog titles and keep the full label as the tooltip", async () => {
  const expected = {
    "AI deals": "AI deals",
    "Chargers": "Chargers",
    "Commercial energy": "Comm. energy",
    "Energy projects & news": "Energy news",
    "FSD by region": "FSD regions",
    "Government obligations": "Gov. obligations",
    "Gross profit and operating income": "GP & op. income",
    "Launches": "Launches",
    "Manufacturing footprint": "Mfg. footprint",
    "Net sales by product": "Sales by product",
    "New factories": "Factories",
    "Operating income": "Op. income",
    "Operating income by segment": "Op. income by seg.",
    "Optimus": "Optimus",
    "Production & deliveries": "Prod. & deliveries",
    "Projected launches": "Proj. launches",
    "Public vehicle records": "Vehicle records",
    "Reportable segments": "Segments",
    "Revenue & earnings forecast": "Rev & EPS",
    "Revenue and earnings forecast": "Rev & EPS",
    "Revenue by channel": "Rev by channel",
    "Revenue by customer": "Rev by customer",
    "Revenue by end market": "Rev by market",
    "Revenue by market": "Rev by market",
    "Revenue by segment": "Rev by segment",
    "Revenue mix": "Rev mix",
    "Robotaxi & autonomy": "Robotaxi",
    "Sales by product": "Sales by product",
    "Sales by segment": "Sales by seg.",
    "Segment gross margin": "Seg. margin",
    "Segment operating income": "Seg. op. income",
    "Starlink": "Starlink",
    "Starmind": "Starmind",
    "Starship": "Starship",
    "Terraform": "Terraform",
    "Tesla Semi": "Semi",
    "Trend scoreboard": "Scoreboard",
  };
  const dir = new URL("../../services/data-jobs/src/functions/company_ir/catalog/", import.meta.url);
  const titles = new Set();
  for (const name of await readdir(dir)) {
    const catalog = JSON.parse(await readFile(new URL(name, dir), "utf8"));
    for (const section of catalog.deep_dive?.sections || []) {
      if (section?.title) titles.add(section.title);
    }
  }
  assert.deepEqual([...titles].sort(), Object.keys(expected).sort());
  for (const [title, label] of Object.entries(expected)) {
    assert.equal(shortSectionLabel(title), label);
    assert.ok(label.length <= title.length, `${title} grew`);
  }
  assert.equal(shortSectionLabel("Revenue outlook"), "Rev outlook");
  const view = renderDeepDive({
    deep_dive: {
      sections: [...titles].map((title, index) => ({
        id: `section-${index}`,
        title,
        panels: [{ id: `panel-${index}`, kind: "note", title, series: reportedSeries(MIN_DATA_POINTS) }],
      })),
    },
  }, fakeDocument(false), { ticker: "exmpl" });
  const chips = byClass(view, "section-strip-chip");
  assert.equal(chips.length, titles.size);
  for (const chip of chips) {
    const full = chip.getAttribute("title");
    assert.equal(chip.getAttribute("aria-label"), full);
    assert.equal(textOf(chip), expected[full]);
  }
  const headings = byClass(view, "stock-section-title").map((node) => node.textContent);
  assert.deepEqual(headings.sort(), [...titles].sort());
  assert.doesNotMatch(source, /TSLA|Tesla|NVDA|SPCX|SpaceX/);
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

test("research maps render only when a place has a reported value", () => {
  assert.equal(mapDataPoints({ markers: [{ place: "Austin", reported: false, value: 4 }] }).length, 0);
  assert.equal(mapDataPoints({ markers: [] }).length, 0);
  assert.equal(mapDataPoints({ markers: [{ place: "Austin", reported: true, value: 12 }] }).length, 1);
  const view = renderDeepDive({
    deep_dive: {
      title: "Deep dive",
      sections: [{
        id: "maps",
        title: "Maps",
        panels: [
          { id: "fsd", title: "FSD by region", map: "world", markers: [] },
          { id: "chargers", title: "Chargers", kind: "map-sites", map: "sites", markers: [{ place: "Texas", reported: false, value: null }] },
          { id: "markets", title: "Markets", map: "world", markers: [{ place: "Norway", value: null }] },
          {
            id: "storage",
            title: "Storage deployments",
            map: "world",
            kpis: [{ label: "Deployed", value: 3, unit: "GWh" }],
            markers: [],
          },
          {
            id: "fleet",
            title: "Robotaxi fleet",
            map: "us",
            markers: [{ place: "Austin", reported: true, value: 12 }],
          },
        ],
      }],
    },
  }, fakeDocument(false));
  const text = textOf(view);
  assert.equal(byClass(view, "deep-dive-map").length, 1);
  assert.doesNotMatch(text, /FSD by region|Site locations are not collected|No places have been disclosed/);
  assert.doesNotMatch(text, /Chargers/);
  assert.match(text, /Storage deployments/);
  assert.match(text, /Deployed/);
  assert.match(text, /Robotaxi fleet/);
  assert.equal(byClass(view, "deep-dive-map-wrap").length, 1);
  assert.doesNotMatch(source, /TSLA|Tesla|NVDA|SPCX|ARKK/);
});

function reportedSeries(count) {
  return [{
    label: "Reported",
    role: "bar",
    points: Array.from({ length: count }, (_item, index) => ({
      period: `${2020 + index}Q1`,
      value: index + 1,
      reported: true,
    })),
  }];
}

function countryMarkers(names) {
  return names.map((place, index) => ({ place, reported: true, value: index + 1 }));
}

test("world maps and sparse panels share the four-point threshold", () => {
  assert.equal(MIN_DATA_POINTS, 4);
  assert.equal(RESEARCH_FALLBACK, "No deployment data available");
  assert.equal(plottableMapPoints({ map: "world", markers: [{ place: "Austin", reported: true, value: 2 }] }).length, 0);
  assert.equal(plottableMapPoints({ map: "world", markers: countryMarkers(["China", "Germany", "Japan"]) }).length, 3);
  assert.equal(plottableMapPoints({ map: "us", markers: [{ place: "Austin", reported: true, value: 12 }] }).length, 1);
  assert.match(source, /export const MIN_DATA_POINTS = 4/);
  assert.match(source, /plottableMapPoints\(panel\)\.length >= MIN_DATA_POINTS/);
  assert.match(source, /panelDataPoints\(panel\) < MIN_DATA_POINTS/);
  assert.doesNotMatch(source, /TSLA|Tesla|NVDA|SPCX|AAPL/);
  const fallbackRule = styles.match(/\.deep-dive-fallback\s*\{[^}]*\}/)[0];
  assert.match(fallbackRule, /background:\s*var\(--surface\)/);
  assert.match(fallbackRule, /color:\s*var\(--secondary\)/);
  assert.match(fallbackRule, /border:\s*1px solid var\(--line\)/);
  assert.doesNotMatch(fallbackRule, /#[0-9a-fA-F]{3,8}/);

  const sparse = renderDeepDive({
    deep_dive: {
      title: "Deep dive",
      sections: [
        {
          id: "energy",
          title: "Commercial energy",
          panels: [{
            id: "energy-deployments",
            title: "Storage deployments",
            map: "world",
            kpis: [{ label: "Deployed", value: 3, unit: "GWh" }],
            series: reportedSeries(6),
            markers: countryMarkers(["China", "Germany", "Japan"]),
          }],
        },
        {
          id: "semi",
          title: "Vehicle program",
          panels: [
            { id: "semi-orders", title: "Orders", series: reportedSeries(2) },
            { id: "semi-production", title: "Semi production", series: reportedSeries(3) },
          ],
        },
        {
          id: "optimus",
          title: "Optimus",
          panels: [{
            id: "optimus",
            title: "Optimus",
            kpis: [{ label: "Optimus", value: 1 }],
            milestones: [
              { id: "gen-2", label: "Gen 2", reported: true },
              { id: "gen-3", label: "Gen 3", reported: false },
            ],
          }],
        },
        {
          id: "starlink",
          title: "Constellation",
          panels: [{
            id: "starlink",
            title: "Production and deployments",
            map: "world",
            series: reportedSeries(1),
            markers: countryMarkers(["Australia"]),
          }],
        },
      ],
    },
  }, fakeDocument(false));
  const sparseText = textOf(sparse);
  assert.equal(byClass(sparse, "deep-dive-map").length, 0);
  assert.match(sparseText, /Storage deployments/);
  assert.match(sparseText, /Deployed/);
  assert.match(sparseText, /China/);
  assert.match(sparseText, /Orders/);
  assert.doesNotMatch(sparseText, /Semi production/);
  assert.doesNotMatch(sparseText, /Optimus/);
  assert.match(sparseText, /Production and deployments/);
  const chips = byClass(sparse, "section-strip-label").map((node) => node.textContent);
  assert.deepEqual(chips, ["Comm. energy", "Vehicle program", "Constellation"]);
  assert.equal(byClass(sparse, "deep-dive-fallback").length, 0);

  const shown = renderDeepDive({
    deep_dive: {
      title: "Deep dive",
      sections: [
        {
          id: "energy",
          title: "Commercial energy",
          panels: [{
            id: "energy-deployments",
            title: "Storage deployments",
            map: "world",
            markers: countryMarkers(["China", "Germany", "Japan", "Australia"]),
          }],
        },
        {
          id: "semi",
          title: "Vehicle program",
          panels: [{ id: "semi-production", title: "Semi production", series: reportedSeries(4) }],
        },
        {
          id: "optimus",
          title: "Optimus",
          panels: [{
            id: "optimus",
            title: "Optimus",
            milestones: [
              { id: "a", label: "A", reported: true },
              { id: "b", label: "B", reported: true },
              { id: "c", label: "C", reported: true },
              { id: "d", label: "D", reported: true },
            ],
          }],
        },
      ],
    },
  }, fakeDocument(false));
  assert.equal(byClass(shown, "deep-dive-map").length, 1);
  assert.match(textOf(shown), /Semi production/);
  assert.match(textOf(shown), /Optimus/);
  assert.deepEqual(byClass(shown, "section-strip-label").map((node) => node.textContent), [
    "Comm. energy",
    "Vehicle program",
    "Optimus",
  ]);

  const emptyPayload = {
    deep_dive: {
      title: "Deep dive",
      sections: [
        {
          id: "energy",
          title: "Commercial energy",
          panels: [{ id: "energy-deployments", title: "Storage deployments", map: "world", markers: [] }],
        },
        {
          id: "semi",
          title: "Vehicle program",
          panels: [{ id: "semi-production", title: "Semi production", series: reportedSeries(1) }],
        },
        {
          id: "optimus",
          title: "Optimus",
          panels: [{ id: "optimus", title: "Optimus", series: [] }],
        },
      ],
    },
  };
  assert.equal(showsResearchFallback(emptyPayload), true);
  const empty = renderDeepDive(emptyPayload, fakeDocument(false));
  assert.equal(byClass(empty, "deep-dive-fallback").length, 1);
  assert.equal(byClass(empty, "deep-dive-fallback")[0].textContent, RESEARCH_FALLBACK);
  assert.equal(byClass(empty, "section-strip-chip").length, 0);
  assert.equal(byClass(empty, "deep-dive-map").length, 0);
  const kept = {
    deep_dive: {
      ...emptyPayload.deep_dive,
      sections: [
        ...emptyPayload.deep_dive.sections,
        { id: "bars", title: "Bars", panels: [{ id: "bars", title: "Bars", series: reportedSeries(1) }] },
      ],
    },
  };
  assert.equal(showsResearchFallback(kept), false);
  assert.equal(byClass(renderDeepDive(kept, fakeDocument(false)), "deep-dive-fallback").length, 0);
});

function chartPoint(period, value, reported = true) {
  return { period, value, reported };
}

function regulatorPanel(points) {
  return {
    id: "public-regulator",
    kind: "trend-bars",
    title: "Regulator records",
    width: "wide",
    cadence: "monthly",
    kpis: [{ label: "Permits held", value: null, empty_label: "Not yet disclosed" }],
    series: [{ label: "Permits held", role: "bar", points }],
  };
}

test("regulator records stay hidden under two plottable points", () => {
  assert.equal(REGULATOR_MIN_DATA_POINTS, 2);
  assert.match(source, /export const REGULATOR_MIN_DATA_POINTS = 2/);
  assert.match(source, /plottableSeriesPoints\(panel\)\.length < REGULATOR_MIN_DATA_POINTS/);
  assert.doesNotMatch(source, /TSLA|Tesla|NVDA|SPCX|AAPL/);

  const blank = regulatorPanel([
    { period: "2026-08", value: null, reported: false },
    { period: "2026-09", value: "", reported: true },
  ]);
  const one = regulatorPanel([chartPoint("2026-10", 4)]);
  const two = regulatorPanel([chartPoint("2026-09", 3), chartPoint("2026-10", 4)]);
  const unplotted = regulatorPanel([
    chartPoint("2026-10", 4),
    { period: "", value: 9, reported: true },
    { period: "2026-11", value: "pending", reported: true },
    { period: "2026-12", value: 8, reported: false },
  ]);
  assert.equal(plottableSeriesPoints(blank).length, 0);
  assert.equal(plottableSeriesPoints(one).length, 1);
  assert.equal(plottableSeriesPoints(two).length, 2);
  assert.equal(plottableSeriesPoints(unplotted).length, 1);
  assert.equal(plottableSeriesPoints({
    series: [{ points: [chartPoint("2026-10", 2)] }],
    modes: [{ series: [{ points: [chartPoint("2026-11", 5)] }] }],
  }).length, 2);

  const hidden = renderDeepDive({
    deep_dive: {
      title: "Deep dive",
      sections: [{
        id: "robotaxi",
        title: "Robotaxi & autonomy",
        panels: [
          { id: "fleet", title: "Fleet", kpis: [{ label: "Fleet", value: 40, unit: "count" }] },
          one,
        ],
      }],
    },
  }, fakeDocument(false));
  assert.match(textOf(hidden), /Fleet/);
  assert.doesNotMatch(textOf(hidden), /Regulator records/);
  assert.equal(byClass(hidden, "deep-dive-chart").length, 0);
  assert.deepEqual(byClass(hidden, "section-strip-label").map((node) => node.textContent), ["Robotaxi"]);
  assert.equal(byClass(hidden, "deep-dive-fallback").length, 0);

  const dropped = renderDeepDive({
    deep_dive: {
      title: "Deep dive",
      sections: [
        {
          id: "records",
          title: "Public vehicle records",
          panels: [blank],
        },
        {
          id: "fleet",
          title: "Fleet",
          panels: [{ id: "fleet", title: "Fleet", series: reportedSeries(1) }],
        },
      ],
    },
  }, fakeDocument(false));
  assert.doesNotMatch(textOf(dropped), /Regulator records/);
  assert.deepEqual(byClass(dropped, "section-strip-label").map((node) => node.textContent), ["Fleet"]);

  const shown = renderDeepDive({
    deep_dive: {
      title: "Deep dive",
      sections: [{
        id: "records",
        title: "Public vehicle records",
        panels: [two],
      }],
    },
  }, fakeDocument(false));
  assert.match(textOf(shown), /Regulator records/);
  assert.equal(byClass(shown, "deep-dive-chart").length, 1);
  assert.deepEqual(byClass(shown, "section-strip-label").map((node) => node.textContent), ["Vehicle records"]);
  assert.equal(byClass(shown, "deep-dive-fallback").length, 0);

  const onlyEmpty = {
    deep_dive: {
      title: "Deep dive",
      sections: [{ id: "records", title: "Public vehicle records", panels: [one] }],
    },
  };
  assert.equal(showsResearchFallback(onlyEmpty), true);
  const fallback = renderDeepDive(onlyEmpty, fakeDocument(false));
  assert.equal(byClass(fallback, "deep-dive-fallback").length, 1);
  assert.equal(byClass(fallback, "deep-dive-fallback")[0].textContent, RESEARCH_FALLBACK);
  assert.equal(byClass(fallback, "section-strip-chip").length, 0);
  assert.doesNotMatch(textOf(fallback), /Regulator records/);

  const withSparse = {
    deep_dive: {
      title: "Deep dive",
      sections: [
        {
          id: "energy",
          title: "Commercial energy",
          panels: [{ id: "energy-deployments", title: "Storage deployments", map: "world", markers: [] }],
        },
        {
          id: "semi",
          title: "Vehicle program",
          panels: [{ id: "semi-production", title: "Semi production", series: reportedSeries(1) }],
        },
        { id: "records", title: "Public vehicle records", panels: [unplotted] },
      ],
    },
  };
  assert.equal(showsResearchFallback(withSparse), true);
  const sparseFallback = renderDeepDive(withSparse, fakeDocument(false));
  assert.equal(byClass(sparseFallback, "deep-dive-fallback")[0].textContent, RESEARCH_FALLBACK);
  assert.equal(byClass(sparseFallback, "section-strip-chip").length, 0);

  const credits = renderDeepDive({
    deep_dive: {
      title: "Deep dive",
      sections: [{
        id: "credits",
        title: "Credits",
        panels: [{
          id: "credits",
          title: "Regulatory credits",
          series: reportedSeries(1),
        }],
      }],
    },
  }, fakeDocument(false));
  assert.match(textOf(credits), /Regulatory credits/);
  assert.equal(byClass(credits, "deep-dive-fallback").length, 0);
});

test("the NHTSA chart starts at Jan 2024 and uses month labels that fit the width", () => {
  assert.equal(nhtsaMonthLabel("2024-01"), "Jan '24");
  assert.equal(nhtsaMonthLabel("2026-12"), "Dec '26");
  const series = [
    {
      source_kind: "nhtsa_odi",
      label: "Recalls",
      role: "bar",
      points: [
        { period: "2022-06", value: 1, reported: true },
        { period: "2024-01", value: 2, reported: true },
        { period: "2024-02", value: 3, reported: true },
      ],
    },
    {
      label: "Complaints",
      role: "bar",
      points: [{ period: "2023-12", value: 4, reported: true }, { period: "2024-06", value: 5, reported: true }],
    },
  ];
  assert.deepEqual(nhtsaPeriods(series), ["2024-01", "2024-02", "2024-06"]);
  assert.deepEqual(nhtsaPeriods([{ points: [{ period: "2025-03", value: 1, reported: true }] }]), ["2025-03"]);
  const months = [];
  for (let index = 0; index < 30; index += 1) {
    const year = 2024 + Math.floor(index / 12);
    const month = String((index % 12) + 1).padStart(2, "0");
    months.push(`${year}-${month}`);
  }
  const wide = layoutNhtsaAxis(months, 568 / months.length);
  const narrow = layoutNhtsaAxis(months, 280 / months.length);
  assert.equal(wide.rotate, false);
  assert.equal(wide.ticks[0].primary, "Jan '24");
  assert.ok(narrow.rotate);
  assert.ok(narrow.ticks.length < months.length);
  assert.ok(narrow.ticks.length <= wide.ticks.length);
  const gap = (plan) => {
    const indexes = plan.ticks.map((tick) => months.indexOf(tick.period));
    return indexes.slice(1).map((index, position) => index - indexes[position]);
  };
  assert.ok(gap(narrow).every((item) => item >= narrow.step));
  assert.ok(gap(wide).every((item) => item >= wide.step));
  const view = renderDeepDive({
    deep_dive: {
      title: "Deep dive",
      sections: [{
        id: "records",
        title: "Public vehicle records",
        panels: [{
          id: "nhtsa-odi",
          title: "NHTSA recalls, complaints, and investigations",
          cadence: "monthly",
          series,
        }],
      }],
    },
  }, fakeDocument(false));
  const labels = nodesWithClass(view, "deep-dive-axis", { excluded: "deep-dive-axis-year" }).map((node) => node.textContent);
  assert.ok(labels.includes("Jan '24"));
  assert.equal(labels.some((label) => /2022|2023|2022-06|2023-12/.test(label)), false);
  assert.ok(byClass(view, "deep-dive-axis-month").length > 0);
  assert.match(styles, /\.deep-dive-axis-month\s*\{[^}]*font-size:\s*8px/);
  assert.doesNotMatch(source, /TSLA|Tesla|NVDA|SPCX/);
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

test("pattern detection stays inside the panel and the quarterly detail starts closed", () => {
  assert.doesNotMatch(source, /top-track|Top tracked|top tracked/i);
  const view = renderDeepDive({
    ticker: "abcd",
    deep_dive: {
      title: "Deep dive",
      sections: [{
        id: "mix",
        title: "Revenue mix",
        archetype: "segment_revenue_mix",
        panels: [{
          id: "mix-panel",
          kind: "stacked-area",
          title: "Revenue mix",
          archetype: "segment_revenue_mix",
          kpis: [{
            metric_id: "revenue",
            label: "Revenue",
            unit: "USD",
            value: 100,
            period: "2026Q2",
            qoq: 0.1,
            yoy: 0.2,
            sparkline: [80, 90, 100],
            approval_state: "proposed",
            patterns: [{
              kind: "growth_streak",
              label: "Growth streak, 3 periods",
              detail: "The last 3 reported period changes were increases.",
              detection: "pattern",
              forecast: false,
            }],
          }],
          multiples: [{
            metric_id: "part",
            label: "Part",
            unit: "USD",
            value: 40,
            period: "2026Q2",
            share: 0.4,
            sparkline: [20, 30, 40],
            approval_state: "proposed",
            patterns: [{
              kind: "inflection",
              label: "Inflection to growth",
              detail: "The change turned to growth.",
              detection: "pattern",
              forecast: false,
            }],
          }],
          series: [{
            metric_id: "part",
            label: "Part",
            role: "area",
            points: [
              { period: "2026Q1", value: 30, reported: true },
              { period: "2026Q2", value: 40, reported: true },
            ],
          }],
          table: {
            columns: [{ id: "period", label: "Period" }, { id: "part", label: "Part" }],
            rows: [{ cells: ["2026Q2", 40] }],
          },
        }],
      }],
    },
  }, fakeDocument(false));
  const page = textOf(view);
  assert.match(page, /Pattern detection/);
  assert.match(page, /Not a forecast/);
  assert.match(page, /Growth streak, 3 periods/);
  assert.match(page, /40\.0% of mix/);
  assert.doesNotMatch(page, /Top tracked|top-track|Most notable/);
  const details = byClass(view, "deep-dive-details")[0];
  assert.equal(details.tagName, "DETAILS");
  assert.equal(details.getAttribute("open"), undefined);
  assert.match(textOf(details), /Quarterly detail/);
  const patterns = byClass(view, "deep-dive-patterns");
  assert.equal(patterns.length, 2);
  assert.ok(patterns.every((node) => node.parentElement.className.includes("deep-dive-tile")));
});

test("operating income card shows the headline, QoQ, and pattern label, and hides when empty", () => {
  const filled = renderDeepDive({
    ticker: "abcd",
    deep_dive: {
      title: "Deep dive",
      sections: [{
        id: "operating-income",
        title: "Operating income",
        archetype: "growth_margin",
        panels: [{
          id: "operating-income-operating_income",
          kind: "headline-card",
          title: "Operating income",
          hide_when_empty: true,
          kpis: [{
            metric_id: "operating_income",
            label: "Operating income (loss)",
            unit: "USD",
            value: 718000000,
            period: "2026Q3",
            qoq: 0.804,
            yoy: -0.558,
            sparkline: [1624, 1409, 941, 398, 718],
            approval_state: "proposed",
            patterns: [{
              kind: "inflection",
              label: "Inflection to growth",
              detail: "The change turned to growth after 3 periods of decline.",
              detection: "pattern",
              forecast: false,
            }],
          }],
          series: [{
            metric_id: "operating_income",
            points: [
              { period: "2026Q2", value: 398, reported: true },
              { period: "2026Q3", value: 718, reported: true },
            ],
          }],
          table: {
            columns: [{ id: "period", label: "Period" }, { id: "operating_income", label: "Operating income" }],
            rows: [{ cells: ["2026Q3", 718] }],
          },
        }],
      }],
    },
  }, fakeDocument(false));
  const page = textOf(filled);
  assert.match(page, /718/);
  assert.match(page, /QoQ/);
  assert.match(page, /YoY/);
  assert.match(page, /Pattern detection/);
  assert.match(page, /Not a forecast/);
  assert.match(page, /Inflection to growth/);
  assert.match(page, /Pending review/);
  const details = byClass(filled, "deep-dive-details")[0];
  assert.equal(details.tagName, "DETAILS");
  assert.equal(details.getAttribute("open"), undefined);
  assert.equal(byClass(filled, "deep-dive-spark").length, 1);

  const onePoint = renderDeepDive({
    deep_dive: {
      sections: [{
        id: "operating-income",
        title: "Operating income",
        panels: [{
          id: "one",
          kind: "headline-card",
          title: "Operating income",
          hide_when_empty: true,
          kpis: [{
            label: "Operating income (loss)",
            unit: "USD",
            value: 100,
            qoq: null,
            yoy: null,
            sparkline: [100],
            approval_state: "proposed",
          }],
        }],
      }],
    },
  }, fakeDocument(false));
  assert.equal(byClass(onePoint, "deep-dive-spark").length, 0);
  assert.doesNotMatch(textOf(onePoint), /QoQ|YoY/);

  const hidden = renderDeepDive({
    deep_dive: {
      sections: [{
        id: "operating-income",
        title: "Operating income",
        panels: [{
          id: "empty",
          kind: "headline-card",
          title: "Operating income",
          hide_when_empty: true,
          kpis: [{ label: "Operating income (loss)", value: null, empty_label: "Not yet disclosed" }],
        }],
      }],
    },
  }, fakeDocument(false));
  assert.equal(byClass(hidden, "deep-dive-panel").length, 0);
  assert.equal(byClass(hidden, "deep-dive-section").length, 0);
  assert.doesNotMatch(textOf(hidden), /Operating income/);
});
