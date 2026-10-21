import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  companyBookmarks,
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

test("section ids stay safe for hashes", () => {
  assert.equal(sectionSlug("edgar:revenue_gaap"), "edgar-revenue_gaap");
  assert.equal(sectionSlug(""), "");
});

test("overview tiles stay name-only jump targets", () => {
  const bookmarks = companyBookmarks("overview", {
    history: [{ close: 10 }, { close: 12 }],
    technical: { rows: [{ enoughData: true, rating: "Buy", label: "Medium term" }] },
    operating: [{ id: "deliveries", name: "Deliveries", latest: "12", latestPeriod: "2026Q2", qoqValue: 0.1, series: [{ reported: true, value: 12 }] }],
    fundamentals: [],
    guidance: [{ latestPeriod: "2026Q3", text: "Raised the outlook." }],
  });
  assert.deepEqual(bookmarks.map((item) => item.id), ["technical"]);
  assert.deepEqual(bookmarks.map((item) => item.title), ["Technical Summary"]);
  assert.equal(bookmarks.some((item) => item.id === "metrics"), false);
  assert.equal(bookmarks.every((item) => item.headline == null), true);
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
  assert.equal(admin[0].headline, undefined);
  const view = renderCompanyBookmarks(admin, fakeDocument(), { ticker: "exmpl", tab: "admin" });
  const tile = byClass(view, "deep-dive-section-tile")[0];
  const chip = byClass(view, "section-strip-chip")[0];
  assert.equal(tile.href, "#stock/EXMPL/admin/metrics");
  assert.equal(chip.href, "#stock/EXMPL/admin/metrics");
  assert.equal(byClass(view, "section-strip")[0].getAttribute("aria-label"), "Sections");
  assert.equal(tile.getAttribute("aria-label"), "Company metrics");
  assert.equal(byClass(tile, "deep-dive-section-tile-value").length, 0);
  assert.equal(byClass(tile, "deep-dive-spark").length, 0);
  assert.equal(view.className, "company-bookmarks");
  assert.equal(byClass(view, "section-strip")[0].getAttribute("aria-hidden"), "true");
  assert.equal(String(byClass(view, "section-strip")[0].className).includes("section-strip-docked"), false);
  assert.equal(chip.getAttribute("tabindex"), "-1");
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

test("admin metric review actions sit in a compact column left of the name", () => {
  const metrics = app.slice(app.indexOf("function renderCompanyMetrics"), app.indexOf("function appendMetricGroups"));
  const line = metrics.slice(metrics.indexOf("const line ="));
  const actions = line.indexOf('node("div", "metric-review-actions")');
  const actionsAppend = line.indexOf("row.append(actions)");
  const name = line.indexOf('node("span", "stock-metric-row-name"');
  const nameAppend = line.indexOf("main.append(name)");
  const mainAppend = line.indexOf("row.append(main)");
  assert.ok(actions > 0 && actionsAppend > actions, "actions cell is built before it is appended");
  assert.ok(nameAppend > name && mainAppend > nameAppend, "the name stays inside the main cell");
  assert.ok(actionsAppend < mainAppend, "the action column is appended before the metric name");
  assert.match(line, /actions\.append\(button\)/);
  assert.doesNotMatch(line, /row\.append\(button\)/);
  assert.match(line, /submitMetricReview\(card\.id, next\)/);
  assert.match(line, /dataset\.metricAction = card\.pending \? "approve" : "undo"/);
  assert.match(line, /`Approve \$\{card\.name\}`/);
  assert.match(line, /`Undo approval of \$\{card\.name\}`/);
  const rowRule = styles.match(/\.stock-metric-row\s*\{[^}]*\}/);
  assert.ok(rowRule);
  assert.match(rowRule[0], /display:\s*grid/);
  assert.match(rowRule[0], /grid-template-columns:\s*max-content minmax\(0, 1fr\)/);
  assert.doesNotMatch(rowRule[0], /flex-wrap|margin-left:\s*auto/);
  const action = styles.match(/\.metric-review-action\s*\{[^}]*\}/);
  assert.ok(action);
  assert.match(action[0], /margin-left:\s*0/);
  assert.match(action[0], /margin-block:\s*0/);
  assert.match(action[0], /min-width:\s*4\.5rem/);
  assert.match(action[0], /height:\s*24px/);
  assert.match(action[0], /min-height:\s*24px/);
  assert.doesNotMatch(action[0], /margin-left:\s*auto/);
  assert.match(styles, /\.metric-review-actions\s*\{[^}]*display:\s*flex/);
  assert.match(styles, /\.metric-review-actions\s*\{[^}]*gap:\s*4px/);
  assert.match(styles, /\.stock-metric-row-main\s*\{[^}]*grid-column:\s*2/);
  assert.match(styles, /\.stock-metric-row-main\s*\{[^}]*min-width:\s*0/);
  assert.match(styles, /\.metric-review-action::after\s*\{[^}]*content:\s*none/);
  const phone = styles.slice(styles.indexOf(".stock-page [data-stock-panel]"), styles.indexOf("/* Data refresh"));
  assert.match(phone, /@media \(max-width: 640px\)[\s\S]*\.stock-metric-row\s*\{[^}]*grid-template-columns:\s*max-content minmax\(0, 1fr\)/);
  assert.match(phone, /@media \(max-width: 640px\)[\s\S]*\.metric-review-action\s*\{[^}]*min-height:\s*28px/);
});

test("company section chips scroll to the section and write the tab hash", () => {
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
  assert.equal(byClass(view, "deep-dive-section-tile").length, 0);
  assert.equal(byClass(view, "deep-dive-sections").length, 0);
  const strip = byClass(view, "section-strip")[0];
  assert.equal(strip, view);
  assert.equal(strip.getAttribute("aria-label"), "Sections");
  assert.equal(strip.getAttribute("aria-hidden"), undefined);
  assert.equal(String(strip.className).includes("is-visible"), true);
  assert.equal(String(strip.className).includes("section-strip-docked"), true);
  const chips = byClass(view, "section-strip-chip");
  assert.equal(chips.length, 2);
  assert.equal(chips[0].getAttribute("tabindex"), undefined);
  assert.equal(chips[0].href, "#stock/EXMPL/kpis/deliveries");
  assert.equal(textOf(chips[0]), "Deliveries");
  assert.equal(textOf(chips[1]), "Factory notes");
  let prevented = false;
  chips[0].listeners.click[0]({ button: 0, preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(booked, "deliveries|#stock/EXMPL/kpis/deliveries");
  assert.equal(chips[0].getAttribute("aria-current"), "location");
  assert.equal(target.scrolled.behavior, "smooth");
  assert.equal(target.focused.preventScroll, true);
  chips[1].listeners.click[0]({ button: 0, preventDefault() {} });
  assert.equal(notes.getAttribute("aria-current") == null && chips[1].getAttribute("aria-current"), "location");
  assert.equal(chips[0].getAttribute("aria-current"), undefined);
});

test("the section strip is visible on load and tracks the section in view", () => {
  const bookmarks = companyBookmarks("overview", {
    history: [{ close: 10 }, { close: 12 }],
  });
  const page = element("div");
  for (const id of ["price", "technical", "metrics"]) {
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
  assert.equal(byClass(view, "deep-dive-sections").length, 0);
  assert.equal(observers.length, 1);
  assert.equal(String(strip.className).includes("is-visible"), true);
  assert.equal(String(strip.className).includes("section-strip-docked"), true);
  assert.equal(strip.getAttribute("aria-hidden"), undefined);
  assert.equal(byClass(view, "section-strip-chip").some((chip) => chip.dataset.sectionId === "quarterly-notes"), false);
  const technicalChip = byClass(view, "section-strip-chip").find((chip) => chip.dataset.sectionId === "technical");
  assert.equal(technicalChip.getAttribute("tabindex"), undefined);
  assert.equal(technicalChip.href, "#stock/EXMPL/analysis/technical");
  const technical = page.children.find((child) => child.dataset.companySection === "technical");
  observers[0].callback([{ target: technical, isIntersecting: true, intersectionRatio: 0.8 }]);
  assert.equal(technicalChip.getAttribute("aria-current"), "location");
  assert.equal(String(strip.className).includes("is-visible"), true);
  assert.equal(strip.getAttribute("aria-hidden"), undefined);
  assert.equal(technicalChip.getAttribute("tabindex"), undefined);
  const technicalBookmark = bookmarks.find((item) => item.id === "technical");
  assert.equal(technicalBookmark.title, "Technical Summary");
  assert.equal(technicalBookmark.headline, undefined);
});

test("the company page renders the sticky section strip for every tab except Research", () => {
  const page = app.slice(app.indexOf("function renderStockPage"), app.indexOf("function renderResearchPage"));
  assert.match(page, /renderCompanyBookmarks\(companyBookmarks/);
  assert.match(page, /session\.stockTab !== "research"/);
  assert.match(page, /parseStockTab\(location\.hash\)/);
  assert.match(page, /parseStockPanel\(location\.hash\)/);
  assert.match(app, /revealCompanySection\(root, panel, session\.stockTab\)/);
  assert.match(app, /stockTabHash\(session\.selected, id\)/);
  assert.match(styles, /\.company-section-target,\s*#stock-panel-price,\s*\.trends-layout \[data-company-section\]\s*\{[^}]*scroll-margin-top:\s*104px/);
  assert.match(styles, /\.section-strip\s*\{[^}]*position:\s*sticky/);
  assert.match(styles, /\.section-strip\.section-strip-docked\s*\{[^}]*height:\s*auto/);
  assert.match(styles, /\.stock-page-tabs \.stock-tab\s*\{[^}]*cursor:\s*pointer/);
});
