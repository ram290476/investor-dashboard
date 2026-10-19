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
  assert.deepEqual(bookmarks.map((item) => item.id), ["price", "technical", "metrics"]);
  assert.equal(bookmarks.every((item) => item.headline == null), true);
  const withPrice = companyBookmarks("overview", {
    history: [{ close: 10 }, { close: 12 }],
    technical: { rows: [{ enoughData: true, rating: "Buy", label: "Medium term" }] },
    operating: [{ id: "deliveries", name: "Deliveries", latest: "12", latestPeriod: "2026Q2", qoqValue: 0.1, series: [{ reported: true, value: 12 }] }],
  });
  assert.equal(withPrice[0].headline.value, 12);
  assert.equal(withPrice[0].headline.unit, "USD");
  assert.equal(withPrice[1].headline.display, "Buy");
  assert.equal(withPrice[2].headline.display, "12");
  assert.equal(withPrice[2].headline.label, "Deliveries");
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
  assert.equal(byClass(view, "deep-dive-sections-toggle")[0].getAttribute("aria-label"), "Sections");
  assert.equal(String(byClass(view, "deep-dive-section-nav")[0].className).includes("is-visible"), false);
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
  const back = byClass(view, "deep-dive-sections-back")[0];
  const grid = byClass(view, "deep-dive-sections")[0];
  back.listeners.click[0]();
  assert.equal(grid.scrolled.behavior, "smooth");
  const toggle = byClass(view, "deep-dive-sections-toggle")[0];
  toggle.listeners.click[0]();
  assert.equal(byClass(view, "deep-dive-sections-menu")[0].hidden, false);
  view.listeners.keydown[0]({ key: "Escape" });
  assert.equal(byClass(view, "deep-dive-sections-menu")[0].hidden, true);
});

test("the company page renders section tiles for every tab except deep dive", () => {
  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  assert.match(page, /renderCompanyBookmarks\(companyBookmarks/);
  assert.match(page, /session\.stockTab !== "deep-dive"/);
  assert.match(page, /parseStockTab\(location\.hash\)/);
  assert.match(page, /parseStockPanel\(location\.hash\)/);
  assert.match(app, /revealCompanySection\(root, panel\)/);
  assert.match(app, /stockTabHash\(session\.selected, id\)/);
  assert.match(styles, /\.company-section-target,\s*#stock-panel-price\s*\{[^}]*scroll-margin-top:\s*56px/);
  assert.match(styles, /\.deep-dive-section-tile\s*\{[^}]*cursor:\s*pointer/);
  assert.match(styles, /\.stock-page-tabs \.stock-tab\s*\{[^}]*cursor:\s*pointer/);
});
