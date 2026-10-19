// Deep dive panels for /#stock/<ticker>. The layout comes from payload.deep_dive.
// Nothing here names a ticker. Missing numbers stay empty; they are never drawn as zero
// and example figures are never substituted.
import { GEO } from "./geo.js";
import { stockHash } from "./routes.js";
import { formatChange, formatMetricValue } from "./stock-page.js";

const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)"];

export function hasDeepDive(payload) {
  return Array.isArray(payload?.deep_dive?.sections) && payload.deep_dive.sections.length > 0;
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
export function trendTone(change, inverse = false) {
  const number = Number(change);
  if (change == null || Number.isNaN(number) || number === 0) return "flat";
  const up = number > 0;
  return (inverse ? !up : up) ? "up" : "down";
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
  root.dataset.stockPanel = "deep-dive";
  root.id = "stock-panel-deep-dive";
  if (!hasDeepDive(payload)) {
    root.append(el(doc, "p", "data-state", "No deep dive is configured for this company."));
    return root;
  }
  const banner = el(doc, "p", "deep-dive-banner");
  banner.textContent = dive.disclaimer
    || "Figures appear when a source has been collected. Pending review values stay labeled. Empty series are not zero.";
  root.append(banner);

  const ticker = options.ticker || payload?.ticker || "";
  const jump = el(doc, "nav", "deep-dive-jump");
  const label = el(doc, "span", "deep-dive-jump-label", "On this page");
  label.id = "deep-dive-jump-label";
  jump.append(label);
  jump.setAttribute("aria-labelledby", "deep-dive-jump-label");
  root.append(jump);

  const secondary = [];
  for (const section of dive.sections) {
    const block = renderSection(section, doc);
    if (section.placement === "secondary") secondary.push(block);
    else root.append(block);
    jump.append(renderBookmark(section, ticker, root, jump, doc, options));
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
  return root;
}

function renderBookmark(section, ticker, root, jump, doc, options) {
  const sectionId = String(section?.id || "");
  const chip = el(doc, "a", "deep-dive-jump-chip");
  const mark = el(doc, "span", "deep-dive-jump-mark", "#");
  mark.setAttribute("aria-hidden", "true");
  chip.append(mark, el(doc, "span", "deep-dive-jump-text", section.title || sectionId));
  const href = stockHash(ticker, sectionId) || stockHash(ticker) || "";
  if (href) chip.href = href;
  chip.dataset.sectionId = sectionId;
  if (options.currentSection && options.currentSection === sectionId) {
    chip.setAttribute("aria-current", "location");
  }
  chip.addEventListener("click", (event) => {
    if (event && event.button != null && event.button !== 0) return;
    if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) return;
    event?.preventDefault();
    markCurrentBookmark(jump, chip);
    revealDeepDiveSection(root, sectionId);
    // Pass the hash, not the element's resolved href. In a browser, a.href is absolute
    // and would replace the `#stock/<T>` route if it were written into the history URL.
    if (typeof options.onBookmark === "function") options.onBookmark(sectionId, href);
  });
  return chip;
}

function markCurrentBookmark(jump, chip) {
  for (const link of jump.children || []) {
    if (!String(link.className || "").split(/\s+/).includes("deep-dive-jump-chip")) continue;
    link.removeAttribute?.("aria-current");
  }
  chip.setAttribute("aria-current", "location");
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

function renderSection(section, doc) {
  const block = el(doc, "section", "deep-dive-section");
  block.id = `deep-dive-${section.id}`;
  block.dataset.placement = section.placement || "primary";
  const title = el(doc, "h3", "stock-section-title", section.title || "");
  const grid = el(doc, "div", "deep-dive-grid");
  for (const panel of section.panels || []) grid.append(renderPanel(panel, doc));
  block.append(title, grid);
  return block;
}

function renderPanel(panel, doc) {
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
  if (panel.kind === "forecast-band") body.append(renderForecast(panel, doc));
  else {
    if (panel.kpis?.length && panel.kind === "scoreboard") body.append(renderScoreboard(panel.kpis, doc));
    else if (panel.kpis?.length) body.append(renderKpis(panel.kpis, doc));
    if (panel.modes?.length) body.append(renderModes(panel, doc));
    else if (panel.series?.length) body.append(renderChart(panel.series, doc));
    if (panel.map) body.append(renderMap(panel, doc));
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
  return article;
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
  if (panel.map) return true;
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
  if (tile.value != null && tile.approval_state === "proposed") meta.append(pendingBadge(doc));
  const confidence = confidenceLabel(tile.confidence);
  if (confidence) meta.append(el(doc, "span", "deep-dive-confidence", confidence));
  if (tile.qoq != null) meta.append(directionMark(doc, "QoQ", tile.qoq, tile.inverse));
  if (tile.yoy != null) meta.append(directionMark(doc, "YoY", tile.yoy, tile.inverse));
  if (meta.childNodes.length) card.append(meta);
  if (withSpark) {
    const spark = sparkline(doc, tile.sparkline || []);
    if (spark) card.append(spark);
  }
  return card;
}

function pendingBadge(doc) {
  const badge = el(doc, "span", "review-badge", "Pending review");
  return badge;
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

function sparkline(doc, values) {
  const nums = values.map((value) => (value == null || value === "" ? null : Number(value)));
  if (!nums.some((value) => value != null && !Number.isNaN(value))) return null;
  const finite = nums.filter((value) => value != null && !Number.isNaN(value));
  const min = Math.min(...finite);
  const max = Math.max(...finite);
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

function renderChart(seriesList, doc) {
  const drawn = (seriesList || []).filter((series) => (series.points || []).length);
  if (!drawn.length) return el(doc, "p", "deep-dive-empty", "Not yet disclosed");
  const periods = periodsOf(drawn);
  if (!periods.length) return el(doc, "p", "deep-dive-empty", "Not yet disclosed");
  const width = 640;
  const height = 200;
  const pad = { l: 36, r: 36, t: 12, b: 28 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const bars = drawn.filter((series) => series.role !== "line" && series.role !== "line-dashed");
  const lines = drawn.filter((series) => series.role === "line" || series.role === "line-dashed");
  const leftMax = maxOf(bars.length ? bars : drawn, periods);
  const rightMax = lines.some((series) => series.axis === "right") ? maxOf(lines.filter((series) => series.axis === "right"), periods) : null;
  const svg = svgEl(doc, "svg", { class: "deep-dive-chart", viewBox: `0 0 ${width} ${height}`, role: "img" });
  svg.append(svgEl(doc, "title", {}, "Trend chart"));
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
  const first = periods[0];
  const last = periods[periods.length - 1];
  svg.append(svgEl(doc, "text", { x: pad.l, y: height - 8, class: "deep-dive-axis" }, first));
  svg.append(svgEl(doc, "text", { x: width - pad.r, y: height - 8, class: "deep-dive-axis", "text-anchor": "end" }, last));
  const wrap = el(doc, "div", "deep-dive-chart-wrap");
  wrap.append(svg, legend(doc, drawn));
  return wrap;
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

function renderMap(panel, doc) {
  const kind = panel.map === "world" ? "world" : "us";
  const spec = kind === "world" ? GEO.world : GEO.us;
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
  const markers = panel.markers || [];
  let placed = 0;
  for (const marker of markers) {
    const point = anchor(kind, marker.place);
    if (!point) continue;
    placed += 1;
    const radius = marker.reported ? Math.max(4, Math.min(18, Math.sqrt(Math.abs(Number(marker.value) || 0)) )) : 4;
    const dot = svgEl(doc, "circle", {
      cx: point[0],
      cy: point[1],
      r: radius.toFixed(1),
      class: marker.reported ? "deep-dive-bubble" : "deep-dive-bubble-empty",
    });
    const label = marker.reported
      ? `${marker.place}: ${formatDeepValue(marker.value, "")}`
      : `${marker.place}: not yet disclosed`;
    dot.append(svgEl(doc, "title", {}, label));
    svg.append(dot);
  }
  const wrap = el(doc, "div", "deep-dive-map-wrap");
  wrap.append(svg);
  if (!markers.length) {
    const note = panel.map === "sites"
      ? "Site locations are not collected. No map service or API key is configured."
      : "No places have been disclosed yet.";
    wrap.append(el(doc, "p", "deep-dive-empty", note));
  } else if (!placed) {
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
      row.append(
        el(doc, "td", "", marker.place),
        el(doc, "td", "mono", marker.reported ? formatDeepValue(marker.value, "") : (marker.reported === false ? "Not yet disclosed" : "")),
        el(doc, "td", "", marker.status || ""),
      );
      table.append(row);
    }
    wrap.append(table);
  }
  return wrap;
}

function renderMultiples(tiles, doc) {
  const grid = el(doc, "div", "deep-dive-multiples");
  for (const tile of tiles) grid.append(renderTile(tile, doc, true));
  return grid;
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
    if (item.reported && item.approval_state === "proposed") row.append(pendingBadge(doc));
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
    (record.cells || []).forEach((value) => {
      row.append(el(doc, "td", "", value == null || value === "" ? "—" : String(value)));
    });
    node.append(row);
  }
  return node;
}

function renderForecast(panel, doc) {
  const forecast = panel.forecast || {};
  const wrap = el(doc, "div", "deep-dive-forecast");
  const flag = el(doc, "p", "deep-dive-model-flag", "Not a real model");
  wrap.append(flag);
  wrap.append(el(doc, "p", "deep-dive-note", forecast.disclaimer || panel.subtitle || ""));
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
  return wrap;
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
