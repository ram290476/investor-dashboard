// Research panels for /#stock/<ticker>. The layout comes from payload.deep_dive.
// Nothing here names a ticker. Missing numbers stay empty; they are never drawn as zero
// and example figures are never substituted.
import { GEO } from "./geo.js";
import { stockHash } from "./routes.js";
import { formatChange, formatMetricValue } from "./stock-page.js";

const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)"];

export function hasDeepDive(payload) {
  return Array.isArray(payload?.deep_dive?.sections) && payload.deep_dive.sections.length > 0;
}

/** The wording stays the serving disclaimer, above the Research forecast panel. */
export function forecastDisclaimerText(dive) {
  return String(dive?.disclaimer || "").trim()
    || "Figures appear when a source has been collected. Pending review values stay labeled. Empty series are not zero.";
}

/** Primary sections first, then secondary, so the section strip matches the page. */
export function orderedDeepDiveSections(sections) {
  const list = Array.isArray(sections) ? sections : [];
  const primary = [];
  const secondary = [];
  for (const section of list) {
    if (section?.placement === "secondary") secondary.push(section);
    else primary.push(section);
  }
  return [...primary, ...secondary];
}

export function formatDeepValue(value, unit) {
  if (value == null || Number.isNaN(Number(value))) return "";
  if (unit === "per_share") {
    return Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return formatMetricValue(value, unit);
}

export function confidenceLabel(level) {
  if (level === "low") return "Low confidence";
  if (level === "medium") return "Medium confidence";
  if (level === "placeholder") return "Placeholder";
  return "";
}

/** Favorable direction. Inverse metrics treat a decline as favorable. */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Quarter or month label. Quarters keep the year on every tick. Months keep the year only when it changes. */
export function axisLabel(period) {
  const text = String(period || "");
  const quarter = /^(?:FY)?(\d{4})Q([1-4])$/.exec(text);
  if (quarter) {
    const secondary = text.startsWith("FY") ? `FY${quarter[1]}` : quarter[1];
    return { period: text, primary: `Q${quarter[2]}`, secondary, kind: "quarter" };
  }
  const month = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(text);
  if (month) {
    return { period: text, primary: MONTHS[Number(month[2]) - 1], secondary: month[1], kind: "month" };
  }
  return { period: text, primary: text, secondary: "", kind: "other" };
}

export function axisLabels(periods) {
  const items = (periods || []).map((period) => axisLabel(period));
  const monthly = items.length > 0 && items.every((item) => item.kind === "month");
  if (!monthly) return items;
  let previous = "";
  return items.map((item) => {
    const show = Boolean(item.secondary) && item.secondary !== previous;
    if (item.secondary) previous = item.secondary;
    return { ...item, secondary: show ? item.secondary : "" };
  });
}

const NHTSA_RANGE_START = "2024-01";
const NHTSA_AXIS_FONT = 8;

/** `Jan '24`. Used by the recalls, complaints, and investigations chart. */
export function nhtsaMonthLabel(period) {
  const month = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(String(period || ""));
  if (!month) return "";
  return `${MONTHS[Number(month[2]) - 1]} '${month[1].slice(2)}`;
}

/**
 * Months to draw for the NHTSA chart. The range starts at Jan 2024, or at the
 * earliest reported month when that month is later. Earlier points are dropped.
 */
export function nhtsaPeriods(seriesList) {
  const periods = periodsOf(seriesList);
  const reported = periods.filter((period) => (seriesList || []).some((series) => {
    const value = valueAt(series, period);
    return value != null && value !== "" && Number.isFinite(Number(value));
  }));
  if (!reported.length) return [];
  const earliest = reported.slice().sort()[0];
  const start = earliest > NHTSA_RANGE_START ? earliest : NHTSA_RANGE_START;
  return periods.filter((period) => period >= start);
}

function nhtsaLabelWidth(text, fontSize) {
  return String(text || "").length * fontSize * 0.62;
}

/**
 * Month ticks for a rendered width. Thin until the labels fit. If that would
 * skip more than every third month, rotate the labels and thin only as much
 * as the rotated width needs.
 */
export function layoutNhtsaAxis(periods, slotPx, fontSize = NHTSA_AXIS_FONT) {
  const labels = (periods || []).map((period) => ({
    period,
    primary: nhtsaMonthLabel(period) || String(period),
    secondary: "",
    kind: "month",
  }));
  if (!labels.length) return { ticks: [], rotate: false, fontSize, step: 1 };
  const widest = labels.reduce((max, item) => Math.max(max, nhtsaLabelWidth(item.primary, fontSize)), 1);
  const gap = 6;
  const slot = Math.max(Number(slotPx) || 0, 1);
  const thin = (size) => {
    const chosen = labels.filter((item, index) => (
      index === 0 || index === labels.length - 1 || index % size === 0
    ));
    if (chosen.length >= 3) {
      const previous = labels.indexOf(chosen[chosen.length - 2]);
      if (labels.length - 1 - previous < size) chosen.splice(chosen.length - 2, 1);
    }
    return chosen;
  };
  const uprightStep = Math.max(1, Math.ceil((widest + gap) / slot));
  if (uprightStep <= 3) return { ticks: thin(uprightStep), rotate: false, fontSize, step: uprightStep };
  const rotated = widest * Math.SQRT1_2 + fontSize;
  const rotateStep = Math.max(1, Math.ceil((rotated + gap) / slot));
  return { ticks: thin(rotateStep), rotate: true, fontSize, step: rotateStep };
}

function isNhtsaChart(panel) {
  const series = panel?.series || [];
  if (series.some((item) => item?.source_kind === "nhtsa_odi")) return true;
  const title = String(panel?.title || "");
  return /recalls/i.test(title) && /complaints/i.test(title) && /investigations/i.test(title);
}

/** Drop ticks that would collide. Shown quarter ticks still carry the year underneath. */
export function axisTicks(periods, slotWidth = 48) {
  const labels = axisLabels(periods);
  if (!labels.length) return [];
  const monthly = labels.every((item) => item.kind === "month");
  const min = monthly ? 22 : 34;
  const step = slotWidth < min ? Math.ceil(min / Math.max(slotWidth, 1)) : 1;
  if (step <= 1) return labels;
  return labels.filter((item, index) => {
    if (index === 0 || index === labels.length - 1) return true;
    if (monthly && item.secondary) return true;
    return index % step === 0;
  });
}

export function trendTone(change, inverse = false) {
  const number = Number(change);
  if (change == null || Number.isNaN(number) || number === 0) return "flat";
  const up = number > 0;
  return (inverse ? !up : up) ? "up" : "down";
}

// Visible text for the sticky section bar. Keys are catalog titles; the chip
// tooltip keeps the original wording. Unknown titles pass through with the
// same word abbreviations, so a new company does not need its own list.
const SECTION_SHORT_LABELS = {
  "trend scoreboard": "Scoreboard",
  "revenue & earnings forecast": "Rev & EPS",
  "revenue and earnings forecast": "Rev & EPS",
  "robotaxi & autonomy": "Robotaxi",
  "commercial energy": "Comm. energy",
  "fsd by region": "FSD regions",
  "production & deliveries": "Prod. & deliveries",
  "manufacturing footprint": "Mfg. footprint",
  "energy projects & news": "Energy news",
  "public vehicle records": "Vehicle records",
  "segment gross margin": "Seg. margin",
  "government obligations": "Gov. obligations",
  "projected launches": "Proj. launches",
  "new factories": "Factories",
  "revenue by segment": "Rev by segment",
  "revenue mix": "Rev mix",
  "segment operating income": "Seg. op. income",
  "net sales by product": "Sales by product",
  "operating income": "Op. income",
  "sales by segment": "Sales by seg.",
  "sales by product": "Sales by product",
  "revenue by channel": "Rev by channel",
  "gross profit and operating income": "GP & op. income",
  "operating income by segment": "Op. income by seg.",
  "revenue by end market": "Rev by market",
  "reportable segments": "Segments",
  "revenue by market": "Rev by market",
  "revenue by customer": "Rev by customer",
};

/** Short chip label. The caller keeps the catalog title for the tooltip. */
export function shortSectionLabel(title) {
  const original = String(title || "").replace(/\s+/g, " ").trim();
  if (!original) return "";
  const known = SECTION_SHORT_LABELS[original.toLowerCase()];
  if (known) return known;
  // A leading maker name before Semi is not part of the chip.
  if (/^[A-Z][a-z]+\s+Semi$/.test(original)) return "Semi";
  return original
    .replace(/\bRevenue\b/g, "Rev")
    .replace(/\bearnings\b/g, "EPS")
    .replace(/\bOperating\b/g, "Op.")
    .replace(/\boperating\b/g, "op.")
    .replace(/\bSegment\b/g, "Seg.")
    .replace(/\bsegment\b/g, "seg.")
    .replace(/\bProjected\b/g, "Proj.")
    .replace(/\bProduction\b/g, "Prod.")
    .replace(/\bManufacturing\b/g, "Mfg.")
    .replace(/\bGovernment\b/g, "Gov.")
    .replace(/\bGross profit\b/g, "GP");
}

function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text != null && text !== "") node.textContent = text;
  return node;
}

function svgEl(doc, tag, attrs, text) {
  const node = doc.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  if (text != null && text !== "") node.textContent = String(text);
  return node;
}

function reportedPoints(series) {
  return (series?.points || []).filter((point) => point && point.reported && point.value != null && point.value !== "");
}

function periodsOf(seriesList) {
  const seen = [];
  for (const series of seriesList || []) {
    for (const point of series.points || []) {
      if (point?.period && !seen.includes(point.period)) seen.push(point.period);
    }
  }
  return seen.sort();
}

function valueAt(series, period) {
  const point = (series?.points || []).find((item) => item.period === period);
  return point && point.reported ? point.value : null;
}

export function renderDeepDive(payload, doc = document, options = {}) {
  const dive = payload?.deep_dive;
  const root = el(doc, "div", "deep-dive");
  root.dataset.stockPanel = "research";
  root.id = "stock-panel-research";
  if (!hasDeepDive(payload)) {
    root.append(el(doc, "p", "data-state", "No research is configured for this company."));
    return root;
  }

  const ticker = options.ticker || payload?.ticker || "";
  const forecastLabel = forecastDisclaimerText(dive);
  let forecastLabelPlaced = false;
  const claimForecastLabel = () => {
    if (forecastLabelPlaced) return "";
    forecastLabelPlaced = true;
    return forecastLabel;
  };
  const visibleSections = orderedDeepDiveSections(dive.sections).filter((section) => (
    (section?.panels || []).some(panelShown)
  ));
  if (!visibleSections.length) {
    const fallback = showsResearchFallback(payload);
    root.append(el(
      doc,
      "p",
      fallback ? "deep-dive-fallback" : "data-state",
      fallback ? RESEARCH_FALLBACK : "Not yet disclosed",
    ));
    return root;
  }
  const nav = renderSectionNav(visibleSections, ticker, root, doc, options);
  root.append(nav);

  const secondary = [];
  for (const section of visibleSections) {
    const block = renderSection(section, doc, claimForecastLabel);
    if (!block) continue;
    if (section.placement === "secondary") secondary.push(block);
    else root.append(block);
  }
  if (secondary.length) {
    const wrap = el(doc, "section", "deep-dive-secondary");
    wrap.dataset.placement = "secondary";
    const toggle = el(doc, "button", "deep-dive-secondary-toggle", "Secondary");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "true");
    const note = el(doc, "span", "deep-dive-secondary-note", "Lower-priority trends");
    const head = el(doc, "div", "deep-dive-secondary-head");
    head.append(toggle, note);
    const body = el(doc, "div", "deep-dive-secondary-body");
    secondary.forEach((block) => body.append(block));
    const phone = typeof doc.defaultView?.matchMedia === "function"
      && doc.defaultView.matchMedia("(max-width: 640px)").matches;
    if (phone) {
      body.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
    }
    toggle.addEventListener("click", () => {
      body.hidden = !body.hidden;
      toggle.setAttribute("aria-expanded", String(!body.hidden));
    });
    wrap.append(head, body);
    root.append(wrap);
  }
  watchSectionNav(root, doc);
  watchNhtsaAxes(root, doc);
  return root;
}

/** Reported points required before a world map, or a sparse panel, is shown. */
export const MIN_DATA_POINTS = 4;

/** Reported chart points required before the Regulator records panel is shown. */
export const REGULATOR_MIN_DATA_POINTS = 2;

export const RESEARCH_FALLBACK = "No deployment data available";

/** A marker counts when a place has a reported number. Empty and undisclosed maps do not. */
export function mapDataPoints(panel) {
  return (panel?.markers || []).filter((marker) => {
    if (!marker || typeof marker !== "object") return false;
    if (!String(marker.place || "").trim()) return false;
    if (!marker.reported) return false;
    const value = Number(marker.value);
    return marker.value != null && marker.value !== "" && Number.isFinite(value);
  });
}

/** Reported markers whose place resolves on this panel's map. */
export function plottableMapPoints(panel) {
  const kind = panel?.map === "world" ? "world" : (panel?.map === "us" || panel?.map === "sites" ? "us" : "");
  const points = mapDataPoints(panel);
  if (!kind) return points;
  return points.filter((marker) => anchor(kind, marker.place));
}

function panelLabel(panel) {
  return `${panel?.id || ""} ${panel?.title || ""}`.toLowerCase().replace(/[_-]+/g, " ");
}

/** Semi production and Optimus are hidden as whole panels below MIN_DATA_POINTS. */
function isSparsePanel(panel) {
  const label = panelLabel(panel);
  return label.includes("semi production") || /(^|[^a-z])optimus([^a-z]|$)/.test(label);
}

/** Regulator records is hidden as a whole panel below REGULATOR_MIN_DATA_POINTS. */
function isRegulatorRecordsPanel(panel) {
  const label = panelLabel(panel);
  return label.includes("regulator record");
}

function isDeploymentMap(panel) {
  if (panel?.map !== "world") return false;
  const label = panelLabel(panel);
  return label.includes("storage") || label.includes("deploy");
}

/** Distinct reported periods, plus milestones that actually have a date or state. */
function panelDataPoints(panel) {
  const periods = new Set();
  const seriesList = [...(panel?.series || [])];
  for (const mode of panel?.modes || []) seriesList.push(...(mode?.series || []));
  for (const series of seriesList) {
    reportedPoints(series).forEach((point, index) => {
      const period = String(point?.period || "").trim();
      periods.add(period || `${series?.metric_id || series?.label || "series"}:${index}`);
    });
  }
  let milestones = 0;
  for (const milestone of panel?.milestones || []) {
    if (milestone?.reported) milestones += 1;
  }
  return periods.size + milestones;
}

/**
 * Series values the chart can draw. A point counts when it is reported, has a
 * period, and has a finite number. Unreported, blank, and non-finite points do not.
 */
export function plottableSeriesPoints(panel) {
  const seriesList = [...(panel?.series || [])];
  for (const mode of panel?.modes || []) seriesList.push(...(mode?.series || []));
  const points = [];
  for (const series of seriesList) {
    for (const point of reportedPoints(series)) {
      if (!String(point?.period || "").trim()) continue;
      if (!Number.isFinite(Number(point.value))) continue;
      points.push(point);
    }
  }
  return points;
}

function worldMapShown(panel) {
  return panel?.map === "world" && plottableMapPoints(panel).length >= MIN_DATA_POINTS;
}

function nonMapContent(panel) {
  return Boolean(
    (panel?.kpis || []).length
    || (panel?.series || []).length
    || (panel?.modes || []).length
    || (panel?.multiples || []).length
    || (panel?.milestones || []).length
    || panel?.table
    || (panel?.notes || []).length
  );
}

function panelShown(panel) {
  if (!panel || typeof panel !== "object") return false;
  if (panel.hide_when_empty && !hasVisibleData(panel)) return false;
  if (isSparsePanel(panel) && panelDataPoints(panel) < MIN_DATA_POINTS) return false;
  if (isRegulatorRecordsPanel(panel) && plottableSeriesPoints(panel).length < REGULATOR_MIN_DATA_POINTS) return false;
  if (panel.kind === "forecast-band" || panel.kind === "stacked-area" || panel.kind === "headline-card") return true;
  if (panel.map && !mapDataPoints(panel).length && !nonMapContent(panel)) return false;
  return true;
}

/** Research would render nothing because every gated panel is under its threshold. */
export function showsResearchFallback(payload) {
  if (!hasDeepDive(payload)) return false;
  const sections = orderedDeepDiveSections(payload.deep_dive.sections);
  const visible = sections.some((section) => (section?.panels || []).some(panelShown));
  if (visible) return false;
  return sections.some((section) => (section?.panels || []).some((panel) => (
    isSparsePanel(panel) || isDeploymentMap(panel) || isRegulatorRecordsPanel(panel)
  )));
}

function sectionStripFade(doc, side) {
  const name = side === "end" ? "section-strip-fade section-strip-fade-end" : "section-strip-fade section-strip-fade-start";
  const node = el(doc, "span", name);
  node.setAttribute("aria-hidden", "true");
  return node;
}

function renderSectionNav(sections, ticker, root, doc, options) {
  const strip = el(doc, "nav", "section-strip section-strip-docked is-visible");
  strip.setAttribute("aria-label", "Research sections");
  const track = el(doc, "div", "section-strip-track");
  track.append(sectionStripFade(doc, "start"));
  for (const section of sections || []) {
    const full = String(section.title || section.id || "").replace(/\s+/g, " ").trim();
    const chip = el(doc, "a", "section-strip-chip");
    chip.append(el(doc, "span", "section-strip-label", shortSectionLabel(full)));
    if (full) {
      chip.title = full;
      chip.setAttribute("title", full);
      chip.setAttribute("aria-label", full);
    }
    bindSectionLink(chip, section, ticker, root, doc, options);
    track.append(chip);
  }
  track.append(sectionStripFade(doc, "end"));
  strip.append(track);
  return strip;
}

function bindSectionLink(node, section, ticker, root, doc, options) {
  const sectionId = String(section?.id || "");
  const href = stockHash(ticker, sectionId) || stockHash(ticker) || "";
  if (href && String(node.tagName || "").toUpperCase() === "A") node.href = href;
  node.dataset.sectionId = sectionId;
  if (options.currentSection && options.currentSection === sectionId) {
    node.setAttribute("aria-current", "location");
  }
  node.addEventListener("click", (event) => {
    if (event && event.button != null && event.button !== 0) return;
    if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) return;
    event?.preventDefault();
    markCurrentSection(root, sectionId);
    closeSectionMenu(root);
    revealDeepDiveSection(root, sectionId);
    // Pass the hash, not the element's resolved href. In a browser, a.href is absolute
    // and would replace the `#stock/<T>` route if it were written into the history URL.
    if (typeof options.onBookmark === "function") options.onBookmark(sectionId, href);
  });
  return node;
}

function markCurrentSection(root, sectionId) {
  const wanted = new Set(["section-strip-chip"]);
  const stack = [root];
  while (stack.length) {
    const node = stack.pop();
    const names = String(node?.className || "").split(/\s+/);
    if (names.some((name) => wanted.has(name))) {
      if (node.dataset?.sectionId === sectionId) {
        node.setAttribute("aria-current", "location");
        if (names.includes("section-strip-chip")) node.scrollIntoView?.({ inline: "nearest", block: "nearest" });
      } else node.removeAttribute?.("aria-current");
    }
    if (node?.children) stack.push(...node.children);
  }
}

function closeSectionMenu(root) {
  const menu = findByClass(root, "deep-dive-sections-menu");
  const toggle = findByClass(root, "deep-dive-sections-toggle");
  if (menu) menu.hidden = true;
  if (toggle) toggle.setAttribute("aria-expanded", "false");
}

function findByClass(node, className) {
  if (String(node?.className || "").split(/\s+/).includes(className)) return node;
  for (const child of node?.children || []) {
    const found = findByClass(child, className);
    if (found) return found;
  }
  return null;
}

function watchSectionNav(root, doc) {
  const Observer = doc.defaultView?.IntersectionObserver;
  if (typeof Observer !== "function") return;
  const strip = findByClass(root, "section-strip");
  if (!strip) return;
  const sections = [];
  const stack = [root];
  while (stack.length) {
    const node = stack.pop();
    if (String(node?.className || "").split(/\s+/).includes("deep-dive-section")) sections.push(node);
    if (node?.children) stack.push(...node.children);
  }
  if (!sections.length) return;
  const sectionObserver = new Observer((entries) => {
    const best = entries
      .filter((entry) => entry.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!best) return;
    const id = String(best.target?.id || "").replace(/^deep-dive-/, "");
    if (id) markCurrentSection(root, id);
  }, { rootMargin: "-15% 0px -50% 0px", threshold: [0.15, 0.4, 0.7] });
  for (const section of sections) sectionObserver.observe(section);
}

function findById(node, id) {
  if (!node || typeof node !== "object") return null;
  if (node.id === id) return node;
  for (const child of node.children || []) {
    const found = findById(child, id);
    if (found) return found;
  }
  return null;
}

function expandSecondary(target) {
  let node = target;
  while (node) {
    const className = typeof node.className === "string" ? node.className : "";
    if (className.split(/\s+/).includes("deep-dive-secondary-body")) {
      node.hidden = false;
      const wrap = node.parentElement;
      const stack = [...(wrap?.children || [])];
      while (stack.length) {
        const child = stack.pop();
        const name = typeof child.className === "string" ? child.className : "";
        if (name.split(/\s+/).includes("deep-dive-secondary-toggle")) {
          child.setAttribute("aria-expanded", "true");
        }
        if (child.children) stack.push(...child.children);
      }
      return;
    }
    node = node.parentElement;
  }
}

/** Scroll a deep-dive section into view without changing the `#stock/<T>` route. */
export function revealDeepDiveSection(root, sectionId) {
  const target = findById(root, `deep-dive-${sectionId}`);
  if (!target) return null;
  expandSecondary(target);
  if (typeof target.hasAttribute !== "function" || !target.hasAttribute("tabindex")) {
    target.tabIndex = -1;
    target.setAttribute?.("tabindex", "-1");
  }
  target.scrollIntoView?.({ behavior: "smooth", block: "start" });
  target.focus?.({ preventScroll: true });
  return target;
}

function renderSection(section, doc, claimForecastLabel) {
  const panels = (section.panels || []).filter(panelShown);
  if (!panels.length) return null;
  const block = el(doc, "section", "deep-dive-section");
  block.id = `deep-dive-${section.id}`;
  block.dataset.placement = section.placement || "primary";
  const title = el(doc, "h3", "stock-section-title", section.title || "");
  const grid = el(doc, "div", "deep-dive-grid");
  for (const panel of panels) grid.append(renderPanel(panel, doc, claimForecastLabel));
  block.append(title, grid);
  return block;
}

function renderPanel(panel, doc, claimForecastLabel) {
  const article = el(doc, "article", "panel deep-dive-panel");
  article.dataset.width = panel.width || "wide";
  article.dataset.kind = panel.kind || "";
  const header = el(doc, "header", "panel-header");
  const copy = el(doc, "div", "");
  copy.append(el(doc, "h2", "", panel.title || ""));
  if (panel.subtitle) copy.append(el(doc, "p", "panel-subtitle", panel.subtitle));
  const tools = el(doc, "div", "deep-dive-tools");
  if (panel.cadence) tools.append(el(doc, "span", "overlay-chip", panel.cadence === "monthly" ? "Monthly" : "Quarterly"));
  header.append(copy, tools);
  const body = el(doc, "div", "panel-body");
  const forecastLabel = panel.kind === "forecast-band" && claimForecastLabel ? claimForecastLabel() : "";
  if (panel.kind === "forecast-band") body.append(renderForecast(panel, doc, forecastLabel));
  else if (panel.kind === "stacked-area" || panel.kind === "headline-card") {
    body.append(renderArchetypePanel(panel, doc));
  } else {
    if (panel.kpis?.length && panel.kind === "scoreboard") body.append(renderScoreboard(panel.kpis, doc));
    else if (panel.kpis?.length) body.append(renderKpis(panel.kpis, doc));
    if (panel.modes?.length) body.append(renderModes(panel, doc));
    else if (panel.series?.length) body.append(renderChart(panel.series, doc, { axis: isNhtsaChart(panel) ? "nhtsa" : "" }));
    if (panel.map === "world") {
      const listed = (panel.markers || []).some((marker) => String(marker?.place || "").trim());
      if (worldMapShown(panel) || listed) body.append(renderMap(panel, doc, { drawMap: worldMapShown(panel) }));
    } else if (panel.map && mapDataPoints(panel).length) body.append(renderMap(panel, doc));
    if (panel.multiples?.length) body.append(renderMultiples(panel.multiples, doc));
    if (panel.milestones?.length) body.append(renderMilestones(panel.milestones, doc));
    if (panel.table) body.append(renderTable(panel.table, doc, panel.empty_label));
    if (!hasVisibleData(panel)) {
      body.append(el(doc, "p", "deep-dive-empty", panel.empty_label || "Not yet disclosed"));
    }
  }
  article.append(header, body);
  if (panel.source_note) {
    const foot = el(doc, "footer", "deep-dive-source");
    foot.textContent = `Source: ${panel.source_note}`;
    article.append(foot);
  }
  for (const note of panel.notes || []) body.append(el(doc, "p", "deep-dive-note", note));
  if (!forecastLabel) return article;
  const stack = el(doc, "div", "deep-dive-forecast-block");
  stack.dataset.width = panel.width || "wide";
  stack.setAttribute("data-width", panel.width || "wide");
  stack.append(el(doc, "p", "deep-dive-banner", forecastLabel), article);
  return stack;
}

function hasVisibleData(panel) {
  if (panel.kind === "scoreboard" && (panel.kpis || []).length) return true;
  if ((panel.kpis || []).some((tile) => tile.value != null)) return true;
  if ((panel.series || []).some((series) => reportedPoints(series).length)) return true;
  if ((panel.modes || []).some((mode) => (mode.series || []).some((series) => reportedPoints(series).length))) return true;
  if ((panel.markers || []).some((marker) => marker.reported)) return true;
  if ((panel.multiples || []).length) return true;
  if ((panel.milestones || []).length) return true;
  if (panel.table) return true;
  if (mapDataPoints(panel).length) return true;
  return false;
}

function renderScoreboard(tiles, doc) {
  const grid = el(doc, "div", "deep-dive-scoreboard");
  for (const tile of tiles) grid.append(renderTile(tile, doc, true));
  return grid;
}

function renderKpis(tiles, doc) {
  const row = el(doc, "div", "deep-dive-kpis");
  for (const tile of tiles) row.append(renderTile(tile, doc, false));
  return row;
}

function renderTile(tile, doc, withSpark) {
  const card = el(doc, "div", "deep-dive-tile");
  card.append(el(doc, "span", "deep-dive-tile-label", tile.label || tile.metric_id || ""));
  const value = el(doc, "span", "deep-dive-tile-value mono");
  if (tile.value == null) {
    value.textContent = tile.empty_label || "Not yet disclosed";
    value.classList.add("is-empty");
  } else {
    value.textContent = formatDeepValue(tile.value, tile.unit);
  }
  card.append(value);
  const meta = el(doc, "div", "deep-dive-tile-meta");
  if (tile.estimate_only || tile.source_kind === "news_estimate") meta.append(estimateBadge(doc));
  if (tile.source_kind === "av_regulator" && !tile.estimate_only) meta.append(regulatorBadge(doc));
  if (tile.value != null && tile.approval_state === "proposed") meta.append(pendingBadge(doc));
  const confidence = confidenceLabel(tile.confidence);
  if (confidence) meta.append(el(doc, "span", "deep-dive-confidence", confidence));
  if (!tile.estimate_only && tile.estimate_value != null) {
    const line = el(doc, "span", "deep-dive-estimate-line");
    line.append(estimateBadge(doc), el(doc, "span", "mono", formatDeepValue(tile.estimate_value, tile.unit)));
    const link = sourceLink(doc, tile.estimate_source_url, "Source");
    if (link) line.append(link);
    meta.append(line);
  }
  if (tile.estimate_only) {
    const link = sourceLink(doc, tile.estimate_source_url || tile.source_url, "Source");
    if (link) meta.append(link);
  }
  if (tile.regulator_value != null && tile.regulator_value !== "") {
    const line = el(doc, "span", "deep-dive-regulator-line");
    line.append(regulatorBadge(doc), el(doc, "span", "mono", formatDeepValue(tile.regulator_value, tile.unit)));
    const link = sourceLink(doc, tile.regulator_source_url, "Source");
    if (link) line.append(link);
    meta.append(line);
  }
  if (tile.qoq != null) meta.append(directionMark(doc, "QoQ", tile.qoq, tile.inverse));
  if (tile.yoy != null) meta.append(directionMark(doc, "YoY", tile.yoy, tile.inverse));
  if (tile.share != null && !Number.isNaN(Number(tile.share))) {
    meta.append(el(doc, "span", "deep-dive-share", `${(Number(tile.share) * 100).toFixed(1)}% of mix`));
  }
  if (meta.childNodes.length) card.append(meta);
  if (withSpark) {
    const spark = sparkline(doc, tile.sparkline || [], tile.sparkScale);
    if (spark) card.append(spark);
  }
  const detected = renderPatterns(doc, tile.patterns);
  if (detected) card.append(detected);
  return card;
}

function renderPatterns(doc, items) {
  const flags = Array.isArray(items) ? items.filter((item) => item && item.label) : [];
  if (!flags.length) return null;
  const box = el(doc, "div", "deep-dive-patterns");
  box.append(el(doc, "span", "deep-dive-pattern-label", "Pattern detection"));
  box.append(el(doc, "span", "deep-dive-pattern-note", "Not a forecast."));
  for (const item of flags) {
    const row = el(doc, "p", "deep-dive-pattern");
    row.append(el(doc, "span", "deep-dive-pattern-name", item.label));
    if (item.detail) row.append(el(doc, "span", "deep-dive-pattern-detail", item.detail));
    box.append(row);
  }
  return box;
}

function renderArchetypePanel(panel, doc) {
  const wrap = el(doc, "div", "deep-dive-archetype");
  const lead = (panel.kpis || [])[0];
  if (lead) wrap.append(renderTile(lead, doc, true));
  if ((panel.multiples || []).length) wrap.append(renderMultiples(panel.multiples, doc, true));
  const detail = el(doc, "div", "deep-dive-detail-body");
  let hasDetail = false;
  if (panel.kind === "stacked-area" && (panel.series || []).some((series) => reportedPoints(series).length)) {
    detail.append(renderStackedArea(panel.series, doc));
    hasDetail = true;
  } else if (panel.kind === "headline-card" && (panel.series || []).some((series) => reportedPoints(series).length)) {
    detail.append(renderChart(panel.series, doc));
    hasDetail = true;
  }
  if (panel.table && (panel.table.rows || []).length) {
    detail.append(renderTable(panel.table, doc, panel.empty_label));
    hasDetail = true;
  }
  if (hasDetail) {
    const details = el(doc, "details", "deep-dive-details");
    details.append(el(doc, "summary", "", "Quarterly detail"));
    details.append(detail);
    wrap.append(details);
  }
  if (!hasVisibleData(panel) && !(panel.kpis || []).length && !(panel.multiples || []).length) {
    wrap.append(el(doc, "p", "deep-dive-empty", panel.empty_label || "Not yet disclosed"));
  }
  return wrap;
}

function pendingBadge(doc) {
  const badge = el(doc, "span", "review-badge", "Pending review");
  return badge;
}

function estimateBadge(doc) {
  return el(doc, "span", "deep-dive-estimate", "Estimate");
}

function regulatorBadge(doc) {
  return el(doc, "span", "deep-dive-regulator", "Regulator records");
}

function sourceLink(doc, url, title) {
  if (!url || !String(url).startsWith("https://")) return null;
  const link = el(doc, "a", "deep-dive-source-link", title || "Source");
  link.setAttribute("href", url);
  link.setAttribute("rel", "noreferrer");
  link.setAttribute("target", "_blank");
  return link;
}

function directionMark(doc, kind, change, inverse) {
  const tone = trendTone(change, inverse);
  const arrow = Number(change) > 0 ? "▲" : Number(change) < 0 ? "▼" : "▬";
  const word = tone === "up" ? (inverse ? "Improved" : "Uptrend")
    : tone === "down" ? (inverse ? "Worsened" : "Downtrend")
    : "Flat";
  const mark = el(doc, "span", `direction-mark direction-mark-${tone}`);
  mark.append(
    el(doc, "span", "direction-word", word),
    el(doc, "span", "mono", `${kind} ${arrow} ${formatChange(change)}`),
  );
  mark.setAttribute("aria-label", `${kind} ${word}, ${formatChange(change)}`);
  return mark;
}

function sparkline(doc, values, scale) {
  const nums = values.map((value) => (value == null || value === "" ? null : Number(value)));
  const finite = nums.filter((value) => value != null && !Number.isNaN(value));
  if (finite.length < 2) return null;
  const min = scale && scale.min != null ? Number(scale.min) : Math.min(...finite);
  const max = scale && scale.max != null ? Number(scale.max) : Math.max(...finite);
  const span = max - min || 1;
  const width = 96;
  const height = 28;
  const step = nums.length > 1 ? width / (nums.length - 1) : width;
  const coords = [];
  nums.forEach((value, index) => {
    if (value == null || Number.isNaN(value)) return;
    coords.push(`${(index * step).toFixed(1)},${(height - ((value - min) / span) * (height - 4) - 2).toFixed(1)}`);
  });
  const svg = svgEl(doc, "svg", { class: "deep-dive-spark", viewBox: `0 0 ${width} ${height}`, role: "img" });
  svg.append(svgEl(doc, "polyline", { points: coords.join(" "), fill: "none", stroke: "var(--series-1)", "stroke-width": "1.5" }));
  return svg;
}

function renderChart(seriesList, doc, options = {}) {
  const nhtsa = options.axis === "nhtsa";
  const drawn = (seriesList || []).filter((series) => (series.points || []).length);
  if (!drawn.length) return el(doc, "p", "deep-dive-empty", "Not yet disclosed");
  const periods = nhtsa ? nhtsaPeriods(drawn) : periodsOf(drawn);
  if (!periods.length) return el(doc, "p", "deep-dive-empty", "Not yet disclosed");
  const width = 640;
  const height = nhtsa ? 250 : 220;
  const pad = { l: nhtsa ? 44 : 36, r: 36, t: 12, b: nhtsa ? 64 : 36 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const bars = drawn.filter((series) => series.role !== "line" && series.role !== "line-dashed");
  const lines = drawn.filter((series) => series.role === "line" || series.role === "line-dashed");
  const leftMax = maxOf(bars.length ? bars : drawn, periods);
  const rightMax = lines.some((series) => series.axis === "right") ? maxOf(lines.filter((series) => series.axis === "right"), periods) : null;
  const svg = svgEl(doc, "svg", { class: "deep-dive-chart", viewBox: `0 0 ${width} ${height}`, role: "img" });
  svg.append(svgEl(doc, "title", {}, "Trend chart"));
  if (nhtsa) {
    svg.setAttribute("data-nhtsa-axis", "1");
    svg.setAttribute("data-periods", periods.join(","));
    svg.setAttribute("data-view-width", String(width));
    svg.setAttribute("data-inner-width", String(innerW));
    svg.setAttribute("data-view-height", String(height));
    svg.setAttribute("data-pad-left", String(pad.l));
  }
  const slot = innerW / periods.length;
  periods.forEach((period, index) => {
    let stacked = 0;
    bars.forEach((series) => {
      const value = Number(valueAt(series, period));
      if (!Number.isFinite(value)) return;
      const h = (Math.abs(value) / (leftMax || 1)) * innerH;
      const y = pad.t + innerH - stacked - h;
      const rect = svgEl(doc, "rect", {
        x: (pad.l + index * slot + slot * 0.18).toFixed(1),
        y: y.toFixed(1),
        width: (slot * 0.64).toFixed(1),
        height: Math.max(h, 0).toFixed(1),
        fill: SERIES[(series.color_index || 1) - 1] || SERIES[0],
      });
      if (String(series.role).includes("hatch")) rect.setAttribute("fill-opacity", "0.45");
      svg.append(rect);
      stacked += h;
    });
  });
  for (const series of lines) {
    const scale = series.axis === "right" && rightMax ? rightMax : leftMax || 1;
    const coords = [];
    periods.forEach((period, index) => {
      const value = valueAt(series, period);
      if (value == null) return;
      const x = pad.l + index * slot + slot / 2;
      const y = pad.t + innerH - (Number(value) / scale) * innerH;
      coords.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    });
    if (coords.length) {
      svg.append(svgEl(doc, "polyline", {
        points: coords.join(" "),
        fill: "none",
        stroke: SERIES[(series.color_index || 1) - 1] || "var(--amber)",
        "stroke-width": "1.75",
        "stroke-dasharray": series.role === "line-dashed" ? "4 3" : "0",
      }));
    }
  }
  const slotWidth = periods.length ? innerW / periods.length : innerW;
  const widthPx = Number(options.widthPx) || width;
  const slotPx = nhtsa ? slotWidth * (widthPx / width) : slotWidth;
  const ticks = nhtsa ? layoutNhtsaAxis(periods, slotPx).ticks : axisTicks(periods, slotWidth);
  const rotate = nhtsa ? layoutNhtsaAxis(periods, slotPx).rotate : false;
  paintAxisLabels(svg, doc, periods, ticks, { slot, padLeft: pad.l, height, rotate, nhtsa });
  const wrap = el(doc, "div", "deep-dive-chart-wrap");
  wrap.append(svg, legend(doc, drawn));
  return wrap;
}

function paintAxisLabels(svg, doc, periods, ticks, geometry) {
  const { slot, padLeft, height, rotate, nhtsa } = geometry;
  if (svg.querySelectorAll) {
    for (const node of svg.querySelectorAll(".deep-dive-axis")) node.remove();
  }
  for (const label of ticks) {
    const index = periods.indexOf(label.period);
    if (index < 0) continue;
    const x = (padLeft + index * slot + slot / 2).toFixed(1);
    const y = rotate ? height - 4 : height - (nhtsa ? 22 : 18);
    const text = svgEl(doc, "text", {
      x,
      y,
      class: nhtsa ? "deep-dive-axis deep-dive-axis-month" : "deep-dive-axis",
      "text-anchor": rotate ? "end" : "middle",
    }, label.primary);
    if (rotate) text.setAttribute("transform", `rotate(-45 ${x} ${y})`);
    svg.append(text);
    if (label.secondary) {
      svg.append(svgEl(doc, "text", {
        x,
        y: height - 6,
        class: "deep-dive-axis deep-dive-axis-year",
        "text-anchor": "middle",
      }, label.secondary));
    }
  }
}

/** Re-fit NHTSA month labels to the chart's rendered width. Hidden charts are left alone. */
export function fitNhtsaAxes(root) {
  if (!root?.querySelectorAll) return;
  for (const svg of root.querySelectorAll("[data-nhtsa-axis='1']")) {
    const width = svg.getBoundingClientRect?.().width || 0;
    if (width < 40) continue;
    const fitted = Number(svg.getAttribute("data-axis-fitted") || 0);
    if (Math.abs(fitted - width) < 4) continue;
    const periods = String(svg.getAttribute("data-periods") || "").split(",").filter(Boolean);
    if (!periods.length) continue;
    const viewW = Number(svg.getAttribute("data-view-width") || 640);
    const inner = Number(svg.getAttribute("data-inner-width") || viewW - 80);
    const height = Number(svg.getAttribute("data-view-height") || 250);
    const padLeft = Number(svg.getAttribute("data-pad-left") || 44);
    const slot = inner / periods.length;
    const slotPx = slot * (width / viewW);
    const plan = layoutNhtsaAxis(periods, slotPx);
    paintAxisLabels(svg, svg.ownerDocument || root.ownerDocument, periods, plan.ticks, {
      slot, padLeft, height, rotate: plan.rotate, nhtsa: true,
    });
    svg.setAttribute("data-axis-fitted", String(Math.round(width)));
  }
}

function watchNhtsaAxes(root, doc) {
  const fit = () => fitNhtsaAxes(root);
  const view = doc?.defaultView;
  if (typeof view?.requestAnimationFrame === "function") view.requestAnimationFrame(fit);
  if (typeof view?.ResizeObserver === "function") {
    const observer = new view.ResizeObserver(fit);
    if (root.querySelectorAll) {
      for (const node of root.querySelectorAll("[data-nhtsa-axis='1']")) observer.observe(node);
    }
  }
}

function maxOf(seriesList, periods) {
  let max = 0;
  for (const period of periods) {
    let stack = 0;
    for (const series of seriesList) {
      const value = Number(valueAt(series, period));
      if (!Number.isFinite(value)) continue;
      if (series.role === "line" || series.role === "line-dashed") max = Math.max(max, Math.abs(value));
      else stack += Math.abs(value);
    }
    max = Math.max(max, stack);
  }
  return max || 1;
}

function legend(doc, seriesList) {
  const row = el(doc, "div", "deep-dive-legend");
  seriesList.forEach((series) => {
    const item = el(doc, "span", "deep-dive-legend-item");
    const swatch = el(doc, "i", "deep-dive-swatch");
    swatch.style.background = SERIES[(series.color_index || 1) - 1] || SERIES[0];
    item.append(swatch, doc.createTextNode(series.label || ""));
    row.append(item);
  });
  return row;
}

function renderModes(panel, doc) {
  const wrap = el(doc, "div", "deep-dive-modes");
  const toggles = el(doc, "div", "deep-dive-mode-toggles");
  const slot = el(doc, "div", "deep-dive-mode-slot");
  const modes = panel.modes || [];
  let current = 0;
  const paint = () => {
    slot.replaceChildren();
    const mode = modes[current];
    if (!mode) {
      slot.append(el(doc, "p", "deep-dive-empty", "Not yet collected"));
      return;
    }
    if (mode.kpis?.length) slot.append(renderKpis(mode.kpis, doc));
    if ((mode.series || []).some((series) => reportedPoints(series).length)) slot.append(renderChart(mode.series, doc));
    else slot.append(el(doc, "p", "deep-dive-empty", "Not yet collected"));
    for (const note of mode.notes || []) slot.append(el(doc, "p", "deep-dive-note", note));
    for (const item of mode.series_breaks || []) {
      if (item?.note) slot.append(el(doc, "p", "deep-dive-note", item.note));
    }
    [...toggles.children].forEach((button, index) => {
      button.setAttribute("aria-pressed", String(index === current));
    });
  };
  modes.forEach((mode, index) => {
    const button = el(doc, "button", "overlay-chip", mode.label || mode.id);
    button.type = "button";
    button.addEventListener("click", () => {
      current = index;
      paint();
    });
    toggles.append(button);
  });
  wrap.append(toggles, slot);
  paint();
  return wrap;
}

function anchor(mapName, place) {
  const book = mapName === "world" ? GEO.world.anchors : GEO.us.anchors;
  if (!book) return null;
  if (book[place]) return book[place];
  const key = Object.keys(book).find((name) => name.toLowerCase() === String(place).toLowerCase());
  return key ? book[key] : null;
}

function renderMap(panel, doc, options = {}) {
  const drawMap = options.drawMap !== false;
  const kind = panel.map === "world" ? "world" : "us";
  const spec = kind === "world" ? GEO.world : GEO.us;
  const wrap = el(doc, "div", "deep-dive-map-wrap");
  const markers = panel.markers || [];
  let placed = 0;
  if (drawMap) {
    const svg = svgEl(doc, "svg", {
      class: "deep-dive-map",
      viewBox: `0 0 ${spec.w} ${spec.h}`,
      role: "img",
    });
    svg.append(svgEl(doc, "title", {}, kind === "world" ? "World map" : "United States map"));
    if (kind === "world") {
      for (const path of spec.countries) {
        svg.append(svgEl(doc, "path", { d: path, class: "deep-dive-land" }));
      }
    } else {
      svg.append(svgEl(doc, "path", { d: spec.states, class: "deep-dive-land" }));
      if (spec.mesh) svg.append(svgEl(doc, "path", { d: spec.mesh, class: "deep-dive-mesh" }));
    }
    for (const marker of markers) {
      const point = anchor(kind, marker.place);
      if (!point) continue;
      placed += 1;
      const radius = marker.reported ? Math.max(4, Math.min(18, Math.sqrt(Math.abs(Number(marker.value) || 0)) )) : 4;
      const dot = svgEl(doc, "circle", {
        cx: point[0],
        cy: point[1],
        r: radius.toFixed(1),
        class: marker.estimate ? "deep-dive-bubble deep-dive-bubble-estimate" : (marker.reported ? "deep-dive-bubble" : "deep-dive-bubble-empty"),
      });
      const label = marker.reported
        ? `${marker.place}: ${formatDeepValue(marker.value, "")}`
        : `${marker.place}: not yet disclosed`;
      dot.append(svgEl(doc, "title", {}, label));
      svg.append(dot);
    }
    wrap.append(svg);
  }
  if (drawMap && !markers.length) {
    const note = panel.map === "sites"
      ? "Site locations are not collected. No map service or API key is configured."
      : "No places have been disclosed yet.";
    wrap.append(el(doc, "p", "deep-dive-empty", note));
  } else if (drawMap && markers.length && !placed) {
    wrap.append(el(doc, "p", "deep-dive-note", "Places are listed in the table. They are outside this map's anchors."));
  }
  const listed = markers.filter((marker) => marker.place);
  if (listed.length) {
    const table = el(doc, "table", "deep-dive-table");
    const head = el(doc, "tr");
    ["Place", "Value", "Status"].forEach((label) => head.append(el(doc, "th", "", label)));
    table.append(head);
    for (const marker of listed) {
      const row = el(doc, "tr");
      const place = el(doc, "td", "", marker.place);
      if (marker.estimate) place.append(estimateBadge(doc));
      row.append(
        place,
        el(doc, "td", "mono", marker.reported ? formatDeepValue(marker.value, "") : (marker.reported === false ? "Not yet disclosed" : "")),
        el(doc, "td", "", marker.status || ""),
      );
      table.append(row);
    }
    wrap.append(table);
  }
  return wrap;
}

function sharedSparkScale(tiles) {
  const finite = [];
  for (const tile of tiles || []) {
    for (const value of tile?.sparkline || []) {
      if (value == null || value === "" || Number.isNaN(Number(value))) continue;
      finite.push(Number(value));
    }
  }
  if (!finite.length) return null;
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  return { min: min >= 0 ? 0 : min, max };
}

function renderMultiples(tiles, doc, shareScale = false) {
  const grid = el(doc, "div", "deep-dive-multiples");
  const scale = shareScale ? sharedSparkScale(tiles) : null;
  for (const tile of tiles) {
    grid.append(renderTile({ ...tile, sparkScale: scale }, doc, true));
  }
  return grid;
}

function renderStackedArea(seriesList, doc) {
  const drawn = (seriesList || []).filter((series) => reportedPoints(series).length);
  if (!drawn.length) return el(doc, "p", "deep-dive-empty", "Not yet disclosed");
  const periods = periodsOf(drawn);
  const complete = periods.filter((period) => drawn.every((series) => {
    const value = valueAt(series, period);
    return value != null && Number.isFinite(Number(value));
  }));
  if (!complete.length) return renderChart(seriesList, doc);
  const width = 640;
  const height = 220;
  const pad = { l: 44, r: 12, t: 12, b: 36 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  let max = 0;
  for (const period of complete) {
    let stack = 0;
    for (const series of drawn) stack += Math.abs(Number(valueAt(series, period)));
    max = Math.max(max, stack);
  }
  max = max || 1;
  const svg = svgEl(doc, "svg", { class: "deep-dive-chart", viewBox: `0 0 ${width} ${height}`, role: "img" });
  svg.append(svgEl(doc, "title", {}, "Segment mix"));
  for (const fraction of [0.5, 1]) {
    const y = pad.t + innerH - fraction * innerH;
    svg.append(svgEl(doc, "line", {
      x1: pad.l, x2: width - pad.r, y1: y.toFixed(1), y2: y.toFixed(1), class: "deep-dive-gridline",
    }));
  }
  const slot = complete.length > 1 ? innerW / (complete.length - 1) : innerW;
  const xAt = (index) => pad.l + (complete.length > 1 ? index * slot : innerW / 2);
  const baselines = complete.map(() => pad.t + innerH);
  drawn.forEach((series, seriesIndex) => {
    const top = [];
    const bottom = [];
    complete.forEach((period, index) => {
      const value = Math.abs(Number(valueAt(series, period)));
      const y = baselines[index] - (value / max) * innerH;
      top.push(`${xAt(index).toFixed(1)},${y.toFixed(1)}`);
      bottom.push(`${xAt(index).toFixed(1)},${baselines[index].toFixed(1)}`);
      baselines[index] = y;
    });
    const points = [...top, ...bottom.reverse()].join(" ");
    svg.append(svgEl(doc, "polygon", {
      points,
      fill: SERIES[seriesIndex % SERIES.length],
      "fill-opacity": "0.85",
    }));
  });
  const slotWidth = complete.length > 1 ? slot : innerW;
  for (const label of axisTicks(complete, slotWidth)) {
    const index = complete.indexOf(label.period);
    if (index < 0) continue;
    const x = xAt(index).toFixed(1);
    svg.append(svgEl(doc, "text", {
      x, y: height - 18, class: "deep-dive-axis", "text-anchor": "middle",
    }, label.primary));
    if (label.secondary) {
      svg.append(svgEl(doc, "text", {
        x, y: height - 6, class: "deep-dive-axis deep-dive-axis-year", "text-anchor": "middle",
      }, label.secondary));
    }
  }
  const wrap = el(doc, "div", "deep-dive-chart-wrap");
  wrap.append(svg, legend(doc, drawn));
  return wrap;
}

function renderMilestones(items, doc) {
  const list = el(doc, "ol", "deep-dive-milestones");
  for (const item of items) {
    const row = el(doc, "li", item.reported ? "is-reported" : "is-empty");
    row.append(el(doc, "span", "deep-dive-milestone-label", item.label || item.id || ""));
    const detail = item.reported
      ? [item.date, item.state].filter(Boolean).join(" · ")
      : (item.empty_label || "Not yet disclosed");
    row.append(el(doc, "span", "deep-dive-milestone-detail", detail));
    if (item.estimate) row.append(estimateBadge(doc));
    if (item.reported && item.approval_state === "proposed") row.append(pendingBadge(doc));
    const link = sourceLink(doc, item.source_url, "Source");
    if (item.estimate && link) row.append(link);
    list.append(row);
  }
  return list;
}

function renderTable(table, doc, emptyLabel) {
  const columns = table?.columns || [];
  if (!columns.length) return el(doc, "p", "deep-dive-empty", emptyLabel || "Not yet disclosed");
  const node = el(doc, "table", "deep-dive-table");
  const head = el(doc, "tr");
  columns.forEach((column) => head.append(el(doc, "th", "", column.label || column.id || "")));
  node.append(head);
  if (!(table.rows || []).length) {
    const row = el(doc, "tr");
    const cell = el(doc, "td", "deep-dive-empty", emptyLabel || "Not yet disclosed");
    cell.colSpan = columns.length;
    row.append(cell);
    node.append(row);
    return node;
  }
  for (const record of table.rows) {
    const row = el(doc, "tr");
    (record.cells || []).forEach((value, index) => {
      const cell = el(doc, "td", "", value == null || value === "" ? "—" : String(value));
      if (index === 0 && record.estimate) {
        cell.append(estimateBadge(doc));
        const link = sourceLink(doc, record.source_url, "Source");
        if (link) cell.append(link);
      }
      row.append(cell);
    });
    node.append(row);
  }
  return node;
}

function renderForecast(panel, doc, shownLabel = "") {
  const forecast = panel.forecast || {};
  const wrap = el(doc, "div", "deep-dive-forecast");
  const flag = el(doc, "p", "deep-dive-model-flag", "Not a real model");
  wrap.append(flag);
  const note = String(forecast.disclaimer || panel.subtitle || "").trim();
  if (note && note !== String(shownLabel || "").trim()) wrap.append(el(doc, "p", "deep-dive-note", note));
  const headlines = el(doc, "div", "deep-dive-kpis");
  const total = (forecast.segments || []).find((segment) => segment.id === "total") || (forecast.segments || [])[0];
  if (total) {
    for (const metric of total.metrics || []) headlines.append(forecastHeadline(metric, doc));
  }
  wrap.append(headlines);
  const chartSeries = [];
  let color = 1;
  for (const segment of forecast.segments || []) {
    for (const metric of segment.metrics || []) {
      if (metric.field !== "revenue") continue;
      const actuals = {
        label: `${segment.label} actual`,
        role: "bar",
        color_index: color,
        points: (metric.actuals || []).map((point) => ({
          period: point.period,
          value: point.value,
          reported: point.reported !== false && point.value != null,
        })),
      };
      const future = {
        label: `${segment.label} baseline`,
        role: "bar-hatched",
        color_index: color,
        points: (metric.forecast || []).filter((point) => point.reported).map((point) => ({
          period: point.period,
          value: point.value,
          reported: true,
        })),
      };
      if (actuals.points.length) chartSeries.push(actuals);
      if (future.points.length) chartSeries.push(future);
      color = color % 6 + 1;
    }
  }
  if (chartSeries.length) wrap.append(renderChart(chartSeries, doc));
  else wrap.append(el(doc, "p", "deep-dive-empty", "Not estimated. Actuals appear here when they are already on this company page."));
  wrap.append(forecastTable(forecast, doc));
  if (forecast.consensus_note) wrap.append(el(doc, "p", "deep-dive-note", forecast.consensus_note));
  if (forecast.prior_projection_note) wrap.append(el(doc, "p", "deep-dive-note", forecast.prior_projection_note));
  const compared = forecast.prior_projection || [];
  const waiting = (forecast.stored_headlines || []).filter((row) => !compared.some((item) => (
    item.period === row.period && item.field === row.field && item.segment_id === row.segment_id
  )));
  if (compared.length || waiting.length) wrap.append(renderPrior(compared, waiting, doc));
  return wrap;
}

function renderPrior(compared, waiting, doc) {
  const table = el(doc, "table", "deep-dive-table");
  const head = el(doc, "tr");
  ["Segment", "Quarter", "Stored headline", "Actual"].forEach((label) => head.append(el(doc, "th", "", label)));
  table.append(head);
  for (const row of compared) {
    const line = el(doc, "tr");
    line.append(
      el(doc, "td", "", `${row.segment_label || row.segment_id || ""} ${row.field || ""}`.trim()),
      el(doc, "td", "", row.period || ""),
      el(doc, "td", "mono", formatDeepValue(row.projected, row.unit)),
      el(doc, "td", "mono", formatDeepValue(row.actual, row.unit)),
    );
    table.append(line);
  }
  for (const row of waiting) {
    const line = el(doc, "tr");
    line.append(
      el(doc, "td", "", `${row.segment_label || row.segment_id || ""} ${row.field || ""}`.trim()),
      el(doc, "td", "", row.period || ""),
      el(doc, "td", "mono", formatDeepValue(row.value, row.unit)),
      el(doc, "td", "", "Waiting for the report"),
    );
    table.append(line);
  }
  return table;
}

function forecastHeadline(metric, doc) {
  const next = (metric.forecast || []).find((point) => point.reported && point.value != null) || (metric.forecast || [])[0];
  const tile = {
    label: metric.field === "earnings" ? "Next-quarter EPS baseline" : "Next-quarter revenue baseline",
    unit: metric.unit,
    value: next && next.reported ? next.value : null,
    empty_label: next && !next.reported ? "Not estimated" : (metric.empty_label || "Not yet disclosed"),
    approval_state: "proposed",
    confidence: next?.value != null ? "placeholder" : null,
    period: next?.period,
  };
  return renderTile(tile, doc, false);
}

function forecastTable(forecast, doc) {
  const table = el(doc, "table", "deep-dive-table");
  const head = el(doc, "tr");
  ["Segment", "Latest actual", "Next baseline", "80% band", "Vs consensus"].forEach((label) => {
    head.append(el(doc, "th", "", label));
  });
  table.append(head);
  for (const segment of forecast.segments || []) {
    const revenue = (segment.metrics || []).find((metric) => metric.field === "revenue") || {};
    const actual = (revenue.actuals || []).filter((point) => point.value != null).at?.(-1)
      || [...(revenue.actuals || [])].filter((point) => point.value != null).pop();
    const next = (revenue.forecast || []).find((point) => point.reported && point.value != null);
    const row = el(doc, "tr");
    row.append(
      el(doc, "td", "", segment.label || ""),
      el(doc, "td", "mono", actual ? formatDeepValue(actual.value, revenue.unit) : "Not yet disclosed"),
      el(doc, "td", "mono", next ? formatDeepValue(next.value, revenue.unit) : "Not estimated"),
      el(doc, "td", "", "—"),
      el(doc, "td", "", "—"),
    );
    table.append(row);
  }
  return table;
}
