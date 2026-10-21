// Sticky Sections strip for company tabs. Admin keeps its section tiles.
// No ticker names. The strip is the section jump list.
import { ANALYSIS_TAB_ID, OVERVIEW_TAB_ID, canonicalStockTab, stockPanelHash } from "./routes.js";

export const TREND_SECTIONS = [
  { id: "price", title: "Price", icon: "price" },
  { id: "rates", title: "Rates & yields", icon: "default" },
  { id: "inflation", title: "Inflation surprise", icon: "default" },
  { id: "market-comparison", title: "Market comparison", icon: "default" },
  { id: "moving-averages", title: "Moving averages", icon: "default" },
  { id: "volatility", title: "Volatility", icon: "default" },
  { id: "dollar-oil", title: "Dollar & oil", icon: "default" },
  { id: "tariffs", title: "Tariffs & geopolitics", icon: "default" },
  { id: "correlation", title: "Correlation drift", icon: "default" },
  { id: "catalyst-calendar", title: "Catalyst calendar", icon: "default" },
  { id: "drivers", title: "Macro signals", icon: "technical" },
  { id: "news", title: "News & sentiment", icon: "default" },
  { id: "filings", title: "Filings & events", icon: "fundamentals" },
  { id: "contracts", title: "Government contracts", icon: "default" },
  { id: "about-data", title: "About this data", icon: "default" },
];

const ICON_PATHS = {
  price: ["M2 12l3.2-3.2 2.4 2.4L12.2 4", "M9.5 4H13v3.5"],
  technical: ["M3 13V8", "M8 13V3", "M13 13V6"],
  metrics: ["M3 4h10", "M3 8h10", "M3 12h6"],
  kpi: ["M3 13V9", "M7 13V5", "M11 13V7", "M3 13h10"],
  fundamentals: ["M4 2.5h6l3 3V13.5H4z", "M10 2.5V6h3"],
  default: ["M8 2.5l4.5 4.5L8 11.5 3.5 7z"],
};

export function sectionSlug(id) {
  const slug = String(id || "").trim().replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80);
  return /^[A-Za-z0-9]/.test(slug) ? slug : "";
}

function metricBookmarks(metrics, icon, fallbackId, fallbackTitle) {
  const cards = (metrics || []).filter(Boolean);
  if (!cards.length) {
    return [{
      id: fallbackId,
      title: fallbackTitle,
      anchorId: `stock-panel-${fallbackId}`,
      icon,
    }];
  }
  return cards.map((metric) => {
    const id = sectionSlug(metric.id) || fallbackId;
    return {
      id,
      title: metric.name || id,
      anchorId: `stock-metric-${id}`,
      icon,
    };
  });
}

/** Jump targets for the active company tab. Research keeps its own section list. */
export function companyBookmarks(tab, context = {}) {
  const id = canonicalStockTab(tab);
  if (id === OVERVIEW_TAB_ID) {
    const present = new Set(context.present?.length ? context.present : TREND_SECTIONS.map((item) => item.id));
    return TREND_SECTIONS.filter((item) => present.has(item.id)).map((item) => ({
      id: item.id,
      title: item.title,
      anchorId: `trends-panel-${item.id}`,
      icon: item.icon,
    }));
  }
  if (tab === "kpis") return metricBookmarks(context.operating, "kpi", "kpis", "Operating KPIs");
  if (tab === "fundamentals") return metricBookmarks(context.fundamentals, "fundamentals", "fundamentals", "Fundamentals");
  if (tab === "admin") {
    return [{
      id: "metrics",
      title: "Company metrics",
      anchorId: "stock-panel-metrics",
      icon: "metrics",
    }];
  }
  if (id !== ANALYSIS_TAB_ID) return [];
  return [
    {
      id: "technical",
      title: "Technical Summary",
      anchorId: "stock-panel-technical",
      icon: "technical",
    },
  ];
}

function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text != null && text !== "") node.textContent = text;
  return node;
}

function svgEl(doc, tag, attrs) {
  const node = doc.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  return node;
}

function sectionIcon(doc, kind) {
  const svg = svgEl(doc, "svg", {
    class: "deep-dive-section-icon",
    viewBox: "0 0 16 16",
    "aria-hidden": "true",
    focusable: "false",
  });
  for (const d of ICON_PATHS[kind] || ICON_PATHS.default) {
    svg.append(svgEl(doc, "path", {
      d,
      fill: "none",
      stroke: "currentColor",
      "stroke-width": "1.6",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    }));
  }
  return svg;
}

function setClass(node, name, on) {
  const parts = String(node?.className || "").split(/\s+/).filter(Boolean);
  const has = parts.includes(name);
  if (on && !has) node.className = [...parts, name].join(" ");
  else if (!on && has) node.className = parts.filter((part) => part !== name).join(" ");
}

function findByClass(node, className) {
  if (String(node?.className || "").split(/\s+/).includes(className)) return node;
  for (const child of node?.children || []) {
    const found = findByClass(child, className);
    if (found) return found;
  }
  return null;
}

function markCurrent(root, sectionId) {
  const wanted = new Set(["deep-dive-section-tile", "section-strip-chip"]);
  const stack = [root];
  while (stack.length) {
    const node = stack.pop();
    const names = String(node?.className || "").split(/\s+/);
    if (names.some((name) => wanted.has(name))) {
      if (node.dataset?.sectionId === sectionId) {
        node.setAttribute("aria-current", "location");
        if (names.includes("section-strip-chip")) {
          node.scrollIntoView?.({ inline: "nearest", block: "nearest" });
        }
      } else node.removeAttribute?.("aria-current");
    }
    if (node?.children) stack.push(...node.children);
  }
}

function bindBookmark(node, bookmark, root, options) {
  const sectionId = String(bookmark?.id || "");
  const href = stockPanelHash(options.ticker, options.tab, sectionId) || stockPanelHash(options.ticker, options.tab) || "";
  if (href && String(node.tagName || "").toUpperCase() === "A") node.href = href;
  node.dataset.sectionId = sectionId;
  if (options.currentSection && options.currentSection === sectionId) node.setAttribute("aria-current", "location");
  node.addEventListener("click", (event) => {
    if (event && event.button != null && event.button !== 0) return;
    if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) return;
    event?.preventDefault();
    markCurrent(root, sectionId);
    revealCompanySection(options.scrollRoot || root, sectionId, options.tab);
    if (typeof options.onBookmark === "function") options.onBookmark(sectionId, href);
  });
}

function renderTile(bookmark, root, doc, options) {
  const tile = el(doc, "a", "deep-dive-section-tile");
  const label = bookmark.title || bookmark.id;
  tile.setAttribute("aria-label", label);
  const head = el(doc, "span", "deep-dive-section-tile-head");
  head.append(sectionIcon(doc, bookmark.icon), el(doc, "span", "deep-dive-section-tile-name", label));
  tile.append(head);
  bindBookmark(tile, bookmark, root, options);
  return tile;
}

function setChipTabbable(strip, on) {
  const stack = [strip];
  while (stack.length) {
    const node = stack.pop();
    const names = String(node?.className || "").split(/\s+/);
    if (names.includes("section-strip-chip")) {
      if (on) node.removeAttribute?.("tabindex");
      else node.setAttribute?.("tabindex", "-1");
    }
    if (node?.children) stack.push(...node.children);
  }
}

function renderStrip(bookmarks, root, doc, options, docked) {
  const strip = el(doc, "nav", docked ? "section-strip section-strip-docked is-visible" : "section-strip");
  strip.setAttribute("aria-label", "Sections");
  if (!docked) strip.setAttribute("aria-hidden", "true");
  const markRoot = root || strip;
  const track = el(doc, "div", "section-strip-track");
  for (const bookmark of bookmarks) {
    const chip = el(doc, "a", "section-strip-chip", bookmark.title || bookmark.id);
    if (!docked) chip.setAttribute("tabindex", "-1");
    bindBookmark(chip, bookmark, markRoot, options);
    track.append(chip);
  }
  strip.append(track);
  return strip;
}

function owningTab(node) {
  let current = node;
  while (current) {
    const tab = current.dataset?.stockPanel || current.getAttribute?.("data-stock-panel") || "";
    if (tab) return tab;
    current = current.parentElement;
  }
  return "";
}

function watchSectionNav(root, doc, options = {}) {
  const Observer = doc.defaultView?.IntersectionObserver;
  if (typeof Observer !== "function") return;
  const grid = findByClass(root, "deep-dive-sections");
  const strip = findByClass(root, "section-strip");
  const docked = String(strip?.className || "").split(/\s+/).includes("section-strip-docked");
  if (grid && strip && !docked) {
    const gridObserver = new Observer((entries) => {
      const visible = entries.some((entry) => entry.isIntersecting);
      setClass(strip, "is-visible", !visible);
      setChipTabbable(strip, !visible);
      if (visible) strip.setAttribute("aria-hidden", "true");
      else strip.removeAttribute("aria-hidden");
    });
    gridObserver.observe(grid);
  }
  if (!strip) return;
  const host = options.scrollRoot || root;
  const wanted = options.tab || "";
  const sections = [];
  const stack = [host];
  while (stack.length) {
    const node = stack.pop();
    const mark = node?.dataset?.companySection || node?.getAttribute?.("data-company-section") || "";
    if (mark && tabMatches(node, wanted)) sections.push(node);
    if (node?.children) stack.push(...node.children);
  }
  if (!sections.length) return;
  const sectionObserver = new Observer((entries) => {
    const best = entries
      .filter((entry) => entry.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!best) return;
    const id = best.target?.dataset?.companySection || best.target?.getAttribute?.("data-company-section") || "";
    if (id) markCurrent(root, id);
  }, { rootMargin: "-20% 0px -55% 0px", threshold: [0.15, 0.4, 0.7] });
  for (const section of sections) sectionObserver.observe(section);
}

export function renderCompanyBookmarks(bookmarks, doc = document, options = {}) {
  const items = Array.isArray(bookmarks) ? bookmarks : [];
  if (!items.length) return null;
  if (options.tab !== "admin") {
    const strip = renderStrip(items, null, doc, options, true);
    watchSectionNav(strip, doc, options);
    return strip;
  }
  const root = el(doc, "div", "company-bookmarks");
  const grid = el(doc, "nav", "deep-dive-sections");
  grid.setAttribute("aria-label", "Company sections");
  root.append(renderStrip(items, root, doc, options, false), grid);
  for (const bookmark of items) grid.append(renderTile(bookmark, root, doc, options));
  watchSectionNav(root, doc, options);
  return root;
}

function tabMatches(node, tab) {
  if (!tab) return true;
  const owner = owningTab(node);
  return !owner || canonicalStockTab(owner) === canonicalStockTab(tab);
}

function findCompanySection(node, sectionId, tab = "") {
  if (!node || typeof node !== "object") return null;
  const mark = node.dataset?.companySection || node.getAttribute?.("data-company-section") || "";
  if (mark && mark === sectionId && tabMatches(node, tab)) return node;
  for (const child of node.children || []) {
    const found = findCompanySection(child, sectionId, tab);
    if (found) return found;
  }
  return null;
}

/** Scroll a company-page section into view. The hash update stays with the caller. */
export function revealCompanySection(root, sectionId, tab = "") {
  const target = findCompanySection(root, sectionId, tab);
  if (!target) return null;
  if (typeof target.hasAttribute !== "function" || !target.hasAttribute("tabindex")) {
    target.tabIndex = -1;
    target.setAttribute?.("tabindex", "-1");
  }
  target.scrollIntoView?.({ behavior: "smooth", block: "start" });
  target.focus?.({ preventScroll: true });
  return target;
}
