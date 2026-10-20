import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  companyBookmarks,
  metricHeadline,
  renderCompanyBookmarks,
  sectionSlug,
} from "./company-sections.js";

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

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
      node.children.push(kid);
    }
  };
  node.setAttribute = (key, value) => {
    node.attributes[key] = String(value);
    if (key === "class") node.className = String(value);
  };
  node.getAttribute = (key) => node.attributes[key];
  node.hasAttribute = (key) => Object.prototype.hasOwnProperty.call(node.attributes, key);
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
  return node;
}

function fakeDocument() {
  return {
    createElement: (tag) => element(tag),
    createElementNS: (_ns, tag) => element(tag),
    defaultView: {},
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

test("a metric headline uses the reported value, then nothing when the card has no figure", () => {
  assert.equal(sectionSlug("edgar:revenue_gaap"), "edgar-revenue_gaap");
  const pending = metricHeadline({
    id: "deliveries",
    name: "Deliveries",
    latest: "12",
    latestPeriod: "2026Q2",
    qoqValue: 0.25,
    pending: true,
    estimate: true,
    series: [
      { reported: true, value: 8 },
      { reported: true, value: 12 },
    ],
  });
  assert.equal(pending.display, "12");
  assert.equal(pending.label, "2026Q2");
  assert.equal(pending.change, 0.25);
  assert.equal(pending.pending, true);
  assert.equal(pending.estimate, true);
  assert.deepEqual(pending.sparkline, [8, 12]);
  assert.equal(metricHeadline({ latest: "not reported", series: [] }), null);
  assert.equal(metricHeadline({ panelType: "text", latest: "A note", text: "A note" }), null);
  assert.equal(metricHeadline({ unavailable: true, latest: "4" }), null);
});

test("overview tiles fall back to a name and icon when a section has no metric", () => {
  const bookmarks = companyBookmarks("overview", {
    history: [],
    technical: { rows: [{ enoughData: false, label: "Short term" }] },
    operating: [],
    fundamentals: [],
    guidance: [],
  });
  assert.deepEqual(bookmarks.map((item) => item.id), ["price", "technical", "quarterly-notes"]);
  assert.equal(bookmarks.some((item) => item.id === "metrics"), false);
  assert.equal(bookmarks.every((item) => item.headline == null), true);
  const withPrice = companyBookmarks("overview", {
    history: [{ close: 10 }, { close: 12 }],
    technical: { rows: [{ enoughData: true, rating: "Buy", label: "Medium term" }] },
    operating: [{ id: "deliveries", name: "Deliveries", latest: "12", latestPeriod: "2026Q2", qoqValue: 0.1, series: [{ reported: true, value: 12 }] }],
  });
  assert.equal(withPrice[0].headline.value, 12);
  assert.equal(withPrice[0].headline.unit, "USD");
  assert.equal(withPrice[1].headline.display, "Buy");
  assert.equal(withPrice.some((item) => item.id === "metrics"), false);
});

test("company metrics move to the Admin tab and stay pending review", () => {
  const admin = companyBookmarks("admin", {
    operating: [{
      id: "deliveries",
      name: "Deliveries",
      latest: "12",
      latestPeriod: "2026Q2",
      qoqValue: 0.1,
      pending: true,
      series: [{ reported: true, value: 12 }],
    }],
    fundamentals: [],
    guidance: [],
  });
  assert.deepEqual(admin.map((item) => item.id), ["metrics"]);
  assert.equal(admin[0].title, "Company metrics");
  assert.equal(admin[0].anchorId, "stock-panel-metrics");
  assert.equal(admin[0].headline.display, "12");
  assert.equal(admin[0].headline.label, "Deliveries");
  assert.equal(admin[0].headline.pending, true);
  const view = renderCompanyBookmarks(admin, fakeDocument(), { ticker: "exmpl", tab: "admin" });
  const tile = byClass(view, "deep-dive-section-tile")[0];
  const chip = byClass(view, "section-strip-chip")[0];
  assert.equal(tile.href, "#stock/EXMPL/admin/metrics");
  assert.equal(chip.href, "#stock/EXMPL/admin/metrics");
  assert.equal(byClass(view, "section-strip")[0].getAttribute("aria-label"), "Sections");
  assert.match(tile.getAttribute("aria-label"), /Pending review/);
  const metrics = app.slice(app.indexOf("function renderCompanyMetrics"), app.indexOf("function appendMetricGroups"));
  assert.match(metrics, /dataset\.stockPanel = "admin"/);
  assert.doesNotMatch(metrics, /dataset\.stockPanel = "overview"/);
  assert.match(metrics, /Pending review/);
  assert.match(metrics, /"Approve"/);
  assert.doesNotMatch(metrics, /approval_state\s*=\s*"approved"/);
  assert.doesNotMatch(metrics, /status:\s*"approved"/);
  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  assert.match(page, /renderCompanyMetrics\(charts, pageState\)/);
  assert.match(page, /item\.id === "admin" \? "stock-panel-metrics"/);
  assert.match(styles, /\.stock-page\[data-stock-tab="admin"\] \[data-stock-panel="admin"\]/);
  assert.match(styles, /\.stock-page\[data-stock-tab="admin"\] \.company-metrics\s*\{[^}]*display:\s*grid/);
});

test("company section tiles scroll to the section and write the tab hash", () => {
  const bookmarks = companyBookmarks("kpis", {
    operating: [
      {
        id: "deliveries",
        name: "Deliveries",
        latest: "12",
        latestPeriod: "2026Q2",
        qoqValue: 0.25,
        pending: true,
        estimate: true,
        series: [{ reported: true, value: 9 }, { reported: true, value: 12 }],
      },
      { id: "notes", name: "Factory notes", panelType: "text", latest: "A note" },
    ],
  });
  const page = element("div");
  const target = element("section");
  target.dataset.companySection = "deliveries";
  page.append(target);
  const notes = element("section");
  notes.dataset.companySection = "notes";
  page.append(notes);
  let booked = "";
  const view = renderCompanyBookmarks(bookmarks, fakeDocument(), {
    ticker: "exmpl",
    tab: "kpis",
    scrollRoot: page,
    onBookmark: (id, href) => {
      booked = `${id}|${href}`;
    },
  });
  page.append(view);
  const tiles = byClass(view, "deep-dive-section-tile");
  assert.equal(tiles.length, 2);
  assert.equal(view.getAttribute("class") || view.className, "company-bookmarks");
  assert.equal(byClass(view, "deep-dive-sections")[0].getAttribute("aria-label"), "Company sections");
  const strip = byClass(view, "section-strip")[0];
  assert.equal(strip.getAttribute("aria-label"), "Sections");
  assert.equal(strip.getAttribute("aria-hidden"), "true");
  assert.equal(String(strip.className).includes("is-visible"), false);
  const chips = byClass(view, "section-strip-chip");
  assert.equal(chips.length, tiles.length);
  assert.equal(chips[0].getAttribute("tabindex"), "-1");
  assert.equal(chips[0].href, "#stock/EXMPL/kpis/deliveries");
  const deliveries = tiles[0];
  assert.equal(byClass(deliveries, "deep-dive-section-icon").length, 1);
  assert.equal(deliveries.href, "#stock/EXMPL/kpis/deliveries");
  assert.match(deliveries.getAttribute("aria-label"), /Deliveries/);
  assert.match(deliveries.getAttribute("aria-label"), /Pending review/);
  assert.match(textOf(deliveries), /Estimate/);
  assert.equal(byClass(deliveries, "deep-dive-spark").length, 1);
  const notesTile = tiles[1];
  assert.equal(byClass(notesTile, "deep-dive-section-tile-value").length, 0);
  assert.equal(byClass(notesTile, "deep-dive-spark").length, 0);
  let prevented = false;
  deliveries.listeners.click[0]({ button: 0, preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(booked, "deliveries|#stock/EXMPL/kpis/deliveries");
  assert.equal(deliveries.getAttribute("aria-current"), "location");
  assert.equal(target.scrolled.behavior, "smooth");
  assert.equal(target.focused.preventScroll, true);
  notesTile.listeners.click[0]({ button: 0, preventDefault() {} });
  assert.equal(notes.getAttribute("aria-current") == null && notesTile.getAttribute("aria-current"), "location");
  assert.equal(chips[1].getAttribute("aria-current"), "location");
  assert.equal(chips[0].getAttribute("aria-current"), undefined);
});

test("the section strip appears when the grid leaves view and tracks the section in view", () => {
  const bookmarks = companyBookmarks("overview", {
    history: [{ close: 10 }, { close: 12 }],
    guidance: [{ id: "outlook", latestPeriod: "2026Q3", text: "Raised the outlook.", sourceUrl: "https://example.com/letter" }],
  });
  const page = element("div");
  for (const id of ["price", "technical", "metrics", "quarterly-notes"]) {
    const section = element("section");
    section.dataset.companySection = id;
    section.dataset.stockPanel = "overview";
    page.append(section);
  }
  const observers = [];
  const doc = fakeDocument();
  doc.defaultView.IntersectionObserver = function Observer(callback) {
    const record = { callback, targets: [] };
    observers.push(record);
    this.observe = (target) => record.targets.push(target);
    this.disconnect = () => {};
  };
  const view = renderCompanyBookmarks(bookmarks, doc, { ticker: "exmpl", tab: "overview", scrollRoot: page });
  const strip = byClass(view, "section-strip")[0];
  const grid = byClass(view, "deep-dive-sections")[0];
  assert.equal(observers.length, 2);
  observers[0].callback([{ target: grid, isIntersecting: false, intersectionRatio: 0 }]);
  assert.equal(String(strip.className).includes("is-visible"), true);
  assert.equal(strip.getAttribute("aria-hidden"), undefined);
  const notesChip = byClass(view, "section-strip-chip").find((chip) => chip.dataset.sectionId === "quarterly-notes");
  assert.equal(notesChip.getAttribute("tabindex"), undefined);
  assert.equal(notesChip.href, "#stock/EXMPL/overview/quarterly-notes");
  const notes = page.children.find((child) => child.dataset.companySection === "quarterly-notes");
  observers[1].callback([{ target: notes, isIntersecting: true, intersectionRatio: 0.8 }]);
  assert.equal(notesChip.getAttribute("aria-current"), "location");
  const priceChip = byClass(view, "section-strip-chip").find((chip) => chip.dataset.sectionId === "price");
  assert.equal(priceChip.getAttribute("aria-current"), undefined);
  observers[0].callback([{ target: grid, isIntersecting: true, intersectionRatio: 1 }]);
  assert.equal(String(strip.className).includes("is-visible"), false);
  assert.equal(strip.getAttribute("aria-hidden"), "true");
  assert.equal(notesChip.getAttribute("tabindex"), "-1");
  assert.equal(bookmarks.find((item) => item.id === "quarterly-notes").headline.display, "Q3 2026");
});

test("the company page renders section tiles for every tab except deep dive", () => {
  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  assert.match(page, /renderCompanyBookmarks\(companyBookmarks/);
  assert.match(page, /session\.stockTab !== "deep-dive"/);
  assert.match(page, /parseStockTab\(location\.hash\)/);
  assert.match(page, /parseStockPanel\(location\.hash\)/);
  assert.match(app, /revealCompanySection\(root, panel, session\.stockTab\)/);
  assert.match(app, /stockTabHash\(session\.selected, id\)/);
  assert.match(styles, /\.company-section-target,\s*#stock-panel-price,\s*\.trends-layout \[data-company-section\]\s*\{[^}]*scroll-margin-top:\s*104px/);
  assert.match(styles, /\.deep-dive-section-tile\s*\{[^}]*cursor:\s*pointer/);
  assert.match(styles, /\.stock-page-tabs \.stock-tab\s*\{[^}]*cursor:\s*pointer/);
});
