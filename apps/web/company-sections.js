// Section tiles and the sticky section strip for every company tab.
// No ticker names. The strip replaces the Sections dropdown.
import { trendTone } from "./deep-dive.js";
import { stockPanelHash } from "./routes.js";
import { formatChange, formatMetricValue, quarterlyNoteEntries } from "./stock-page.js";

export const TREND_SECTIONS = [
  { id: "price", title: "Price", icon: "price" },
  { id: "technical", title: "Technical Summary", icon: "technical" },
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
  calls: ["M3 4h10v6H6l-3 2.5z"],
  default: ["M8 2.5l4.5 4.5L8 11.5 3.5 7z"],
};

export function sectionSlug(id) {
  const slug = String(id || "").trim().replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80);
  return /^[A-Za-z0-9]/.test(slug) ? slug : "";
}

function closesOf(history) {
  return (history || []).map((bar) => {
    const number = Number(bar?.adj_close ?? bar?.close ?? bar?.price);
    return Number.isFinite(number) ? number : null;
  }).filter((value) => value != null);
}

function finite(value) {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

/** Headline for one company metric card. Null when the card has no reported figure. */
export function metricHeadline(metric) {
  if (!metric || metric.unavailable || metric.panelType === "text") return null;
  const display = metric.latest && metric.latest !== "not reported" ? String(metric.latest) : "";
  if (!display) return null;
  const reported = (metric.series || []).filter((point) => point?.reported && finite(point.value) != null);
  const change = finite(metric.qoqValue);
  const prior = reported.length > 1 ? finite(reported.at(-2).value) : null;
  const latest = reported.length ? finite(reported.at(-1).value) : null;
  const derived = change == null && latest != null && prior != null && prior !== 0
    ? (latest - prior) / Math.abs(prior)
    : change;
  return {
    display,
    label: metric.latestPeriod || metric.unit || "",
    change: derived,
    sparkline: reported.map((point) => finite(point.value)),
    pending: Boolean(metric.pending),
    estimate: metric.estimate === true,
  };
}

function firstHeadline(lists) {
  for (const metric of lists.flat().filter(Boolean)) {
    const headline = metricHeadline(metric);
    if (!headline) continue;
    return { ...headline, label: metric.name || headline.label };
  }
  return null;
}

function priceHeadline(history) {
  const closes = closesOf(history);
  if (!closes.length) return null;
  const latest = closes.at(-1);
  const prior = closes.length > 1 ? closes.at(-2) : null;
  const change = prior != null && prior !== 0 ? (latest - prior) / Math.abs(prior) : null;
  return {
    value: latest,
    unit: "USD",
    label: "Last close",
    change,
    sparkline: closes.slice(-32),
  };
}

function technicalHeadline(technical) {
  const row = (technical?.rows || []).find((item) => item?.enoughData && item.rating);
  if (!row) return null;
  return { display: String(row.rating), label: row.label || "Consensus" };
}

function metricBookmarks(metrics, icon, fallbackId, fallbackTitle) {
  const cards = (metrics || []).filter(Boolean);
  if (!cards.length) {
    return [{
      id: fallbackId,
      title: fallbackTitle,
      anchorId: `stock-panel-${fallbackId}`,
      icon,
      headline: null,
    }];
  }
  return cards.map((metric) => {
    const id = sectionSlug(metric.id) || fallbackId;
    return {
      id,
      title: metric.name || id,
      anchorId: `stock-metric-${id}`,
      icon,
      headline: metricHeadline(metric),
    };
  });
}

function textHeadline(display, label) {
  const value = String(display || "").trim();
  if (!value) return null;
  return { display: value, label: label || "" };
}

/** Tiles for the active company tab. Deep dive keeps its own section list. */
export function companyBookmarks(tab, context = {}) {
  if (tab === "trends") {
    const present = new Set(context.present?.length ? context.present : TREND_SECTIONS.map((item) => item.id));
    const supplied = context.headlines || {};
    return TREND_SECTIONS.filter((item) => present.has(item.id)).map((item) => ({
      id: item.id,
      title: item.title,
      anchorId: `trends-panel-${item.id}`,
      icon: item.icon,
      headline: supplied[item.id]
        || (item.id === "price" ? priceHeadline(context.history) : null)
        || (item.id === "technical" ? technicalHeadline(context.technical) : null),
    }));
  }
  if (tab === "kpis") return metricBookmarks(context.operating, "kpi", "kpis", "Operating KPIs");
  if (tab === "fundamentals") return metricBookmarks(context.fundamentals, "fundamentals", "fundamentals", "Fundamentals");
  if (tab !== "overview") return [];
  const notes = quarterlyNoteEntries(context.guidance);
  const latestQuarter = notes[0]?.quarter || "";
  return [
    {
      id: "price",
      title: "Price",
      anchorId: "stock-panel-price",
      icon: "price",
      headline: priceHeadline(context.history),
    },
    {
      id: "technical",
      title: "Technical Summary",
      anchorId: "stock-panel-technical",
      icon: "technical",
      headline: technicalHeadline(context.technical),
    },
    {
      id: "metrics",
      title: "Company metrics",
      anchorId: "stock-panel-metrics",
      icon: "metrics",
      headline: firstHeadline([context.operating, context.fundamentals, context.guidance]),
    },
    {
      id: "quarterly-notes",
      title: "Quarterly notes",
      anchorId: "stock-panel-quarterly-notes",
      icon: "calls",
      headline: textHeadline(latestQuarter, "Latest quarter"),
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

function sparkline(doc, values) {
  const nums = (values || []).map((value) => finite(value));
  if (!nums.some((value) => value != null)) return null;
  const finiteValues = nums.filter((value) => value != null);
  const min = Math.min(...finiteValues);
  const max = Math.max(...finiteValues);
  const span = max - min || 1;
  const width = 96;
  const height = 28;
  const step = nums.length > 1 ? width / (nums.length - 1) : width;
  const coords = [];
  nums.forEach((value, index) => {
    if (value == null) return;
    coords.push(`${(index * step).toFixed(1)},${(height - ((value - min) / span) * (height - 4) - 2).toFixed(1)}`);
  });
  const svg = svgEl(doc, "svg", { class: "deep-dive-spark", viewBox: `0 0 ${width} ${height}` });
  svg.setAttribute("aria-hidden", "true");
  svg.append(svgEl(doc, "polyline", {
    points: coords.join(" "),
    fill: "none",
    stroke: "var(--series-1)",
    "stroke-width": "1.5",
  }));
  return svg;
}

function changeLine(doc, change) {
  const number = finite(change);
  if (number == null) return null;
  const tone = trendTone(number, false);
  const arrow = number > 0 ? "▲" : number < 0 ? "▼" : "▬";
  const word = tone === "up" ? "Up" : tone === "down" ? "Down" : "Flat";
  const formatted = formatChange(number);
  const line = el(doc, "span", `deep-dive-section-change direction-mark-${tone}`);
  line.append(el(doc, "span", "deep-dive-section-arrow", arrow), el(doc, "span", "mono", formatted || ""));
  line.setAttribute("aria-label", `${word} ${formatted} versus the prior period`);
  return line;
}

function headlineText(headline) {
  if (!headline) return "";
  if (headline.display) return headline.display;
  if (headline.value == null) return headline.empty_label || "Not yet disclosed";
  return formatMetricValue(headline.value, headline.unit);
}

function tileLabel(bookmark) {
  const parts = [bookmark.title || bookmark.id];
  if (bookmark.headline) {
    parts.push(headlineText(bookmark.headline));
    if (bookmark.headline.label) parts.push(bookmark.headline.label);
    if (bookmark.headline.estimate) parts.push("Estimate");
    if (bookmark.headline.pending) parts.push("Pending review");
  }
  return parts.filter(Boolean).join(", ");
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
  tile.setAttribute("aria-label", tileLabel(bookmark));
  const head = el(doc, "span", "deep-dive-section-tile-head");
  head.append(sectionIcon(doc, bookmark.icon), el(doc, "span", "deep-dive-section-tile-name", bookmark.title || bookmark.id));
  tile.append(head);
  const headline = bookmark.headline;
  if (headline) {
    const value = el(doc, "span", `deep-dive-section-tile-value mono${headline.display || headline.value != null ? "" : " is-empty"}`);
    value.textContent = headlineText(headline);
    tile.append(value);
    if (headline.label) tile.append(el(doc, "span", "deep-dive-section-tile-metric", headline.label));
    const meta = el(doc, "span", "deep-dive-section-tile-meta");
    if (headline.estimate) meta.append(el(doc, "span", "deep-dive-estimate", "Estimate"));
    if (headline.pending && (headline.display || headline.value != null)) meta.append(el(doc, "span", "review-badge", "Pending review"));
    const change = changeLine(doc, headline.change);
    if (change) meta.append(change);
    if (meta.childNodes.length) tile.append(meta);
    const spark = sparkline(doc, headline.sparkline || []);
    if (spark) tile.append(spark);
  }
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

function renderStrip(bookmarks, root, doc, options) {
  const strip = el(doc, "nav", "section-strip");
  strip.setAttribute("aria-label", "Sections");
  strip.setAttribute("aria-hidden", "true");
  const track = el(doc, "div", "section-strip-track");
  for (const bookmark of bookmarks) {
    const chip = el(doc, "a", "section-strip-chip", bookmark.title || bookmark.id);
    chip.setAttribute("tabindex", "-1");
    bindBookmark(chip, bookmark, root, options);
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
  if (!grid || !strip) return;
  const gridObserver = new Observer((entries) => {
    const visible = entries.some((entry) => entry.isIntersecting);
    setClass(strip, "is-visible", !visible);
    setChipTabbable(strip, !visible);
    if (visible) strip.setAttribute("aria-hidden", "true");
    else strip.removeAttribute("aria-hidden");
  });
  gridObserver.observe(grid);
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
  const root = el(doc, "div", "company-bookmarks");
  const grid = el(doc, "nav", "deep-dive-sections");
  grid.setAttribute("aria-label", "Company sections");
  root.append(renderStrip(items, root, doc, options), grid);
  for (const bookmark of items) grid.append(renderTile(bookmark, root, doc, options));
  watchSectionNav(root, doc, options);
  return root;
}

function tabMatches(node, tab) {
  if (!tab) return true;
  const owner = owningTab(node);
  return !owner || owner === tab;
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
