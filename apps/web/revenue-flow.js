/**
 * Revenue Flow for the Analysis tab.
 *
 * Reported income-statement lines become a left-to-right flow. A segment that
 * is the total of other reported segments is an intermediate node. Lines with
 * no value for the selected period are left out. A positive gap becomes an
 * Other remainder. A parent that is smaller than its parts is flagged, not
 * rewritten. Nothing here invents a missing figure.
 */

import {
  alignedValue,
  changeRatio,
  changeTone,
  collectStatement,
  compactQuarters,
  flowMetrics,
  formatMargin,
  formatPts,
  formatScaled,
  isExpenseRole,
  periodSlice,
  quartersWithData,
  signedBridge,
  yearsWithData,
} from "./statement-model.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const BAR_W = 8;
const COLUMN_PITCH = 228;
const BALANCE_RATIO = 0.005;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const COLUMNS = ["segments", "rollup", "revenue", "split", "costDetail", "operate", "opexDetail", "bridge", "result"];
const DETAIL_TAILS = ["regulatory_credits", "credits", "leasing", "lease", "sales"];
const GENERIC_WORDS = new Set(["revenue", "revenues", "sales", "total", "other", "income", "generation", "storage", "automotive"]);
const OPEX_DETAIL = new Set(["rd", "sga", "restructuring", "other_opex"]);
const BRIDGE_ROLES = new Set(["interest_income", "interest_expense", "interest_other", "other_income"]);
const MARGIN_ROLES = new Set(["gross_profit", "operating_profit", "net_profit"]);
const TOTAL_COST_IDS = new Set(["cost_of_revenue", "cost_of_sales", "cogs", "cost_of_goods_sold"]);
const TOTAL_COST_NAMES = new Set(["cost of revenue", "cost of sales", "cost of goods sold"]);

export function resolveSelection(periods, selection = {}) {
  if (!periods?.length) return { year: null, quarter: null };
  const wanted = Number(selection.year);
  const period = periods.find((item) => item.year === wanted) || periods.at(-1);
  const quarter = Number(selection.quarter);
  if (!Number.isInteger(quarter) || quarter < 1 || !period.quarters.includes(quarter)) {
    return { year: period.year, quarter: null };
  }
  return { year: period.year, quarter };
}

function remainder(parent, parts) {
  if (!Number.isFinite(parent)) return null;
  const sum = parts.reduce((total, value) => total + (Number.isFinite(value) ? value : 0), 0);
  const gap = parent - sum;
  const tolerance = Math.max(1, Math.abs(parent) * BALANCE_RATIO);
  if (Math.abs(gap) <= tolerance) return null;
  return gap;
}

function isTotalCost(metric) {
  const id = String(metric.id || "").toLowerCase();
  const name = String(metric.name || "").toLowerCase();
  return TOTAL_COST_IDS.has(id) || TOTAL_COST_NAMES.has(name);
}

function moneyText(value) {
  return formatScaled(value, { money: true });
}

function toneForRole(role, value) {
  if (role === "segment_revenue" || role === "revenue") return "grey";
  if (role === "other") return "muted";
  if (role === "interest_other" || role === "other_income") return value >= 0 ? "green" : "red";
  if (isExpenseRole(role)) return "red";
  return "green";
}

function formatFlowChange(ratio) {
  if (!Number.isFinite(ratio)) return "—";
  const pct = ratio * 100;
  const abs = Math.abs(pct);
  if (abs < 0.05) return "0%";
  const digits = abs >= 10 && Math.abs(abs - Math.round(abs)) < 0.05 ? 0 : 1;
  return `${pct > 0 ? "▲" : "▼"}${abs.toFixed(digits)}%`;
}

function flowTone(ratio) {
  if (!Number.isFinite(ratio) || Math.abs(ratio) < 0.0005) return Number.isFinite(ratio) ? "flat" : "none";
  return changeTone(ratio);
}

function flowPts(delta) {
  if (!Number.isFinite(delta)) return "";
  const pts = Math.abs(delta * 100);
  if (pts < 0.05) return "0.0 pts";
  return formatPts(delta);
}

function nameLines(label) {
  const text = String(label || "");
  const words = text.split(" ");
  if (text.length <= 16 || words.length < 2) return [text];
  let splitAt = 1;
  let best = Infinity;
  for (let index = 1; index < words.length; index += 1) {
    const score = Math.abs(words.slice(0, index).join(" ").length - words.slice(index).join(" ").length);
    if (score < best) {
      best = score;
      splitAt = index;
    }
  }
  return [words.slice(0, splitAt).join(" "), words.slice(splitAt).join(" ")];
}

function actualQuarters(slice, quarter) {
  if (!slice) return [];
  if (quarter != null) return slice.estimate ? [] : [quarter];
  const estimates = new Set(slice.estimatedQuarters || []);
  return (slice.quarters || []).filter((item) => !estimates.has(item));
}

function changeBasis(metric, slice, year, quarter) {
  const quarters = actualQuarters(slice, quarter);
  if (!metric || !slice || !quarters.length) return { current: null, prior: null, quarters: [] };
  if (quarter != null) return { current: slice, prior: periodSlice(metric, year - 1, quarter), quarters };
  const current = quarters.length === (slice.quarters || []).length ? slice : alignedValue(metric, year, quarters);
  return { current, prior: alignedValue(metric, year - 1, quarters), quarters };
}

function decorate(metric, slice, year, quarter, revenueNow, revenuePrior) {
  const role = metric?.role || "";
  const basis = changeBasis(metric, slice, year, quarter);
  const ratio = changeRatio(basis.current, basis.prior);
  let marginText = "";
  let marginTone = "flat";
  if (MARGIN_ROLES.has(role) && revenueNow && revenueNow.value) {
    const margin = slice.value / revenueNow.value;
    marginText = formatMargin(margin);
    const revenueBasisNow = revenueNow.basisCurrent;
    const revenueBasisPrior = revenueNow.basisPrior;
    if (basis.prior && revenueBasisNow && revenueBasisPrior && revenueBasisNow.value && revenueBasisPrior.value) {
      const currentMargin = basis.current.value / revenueBasisNow.value;
      const priorMargin = basis.prior.value / revenueBasisPrior.value;
      const pts = currentMargin - priorMargin;
      marginText = `${formatMargin(currentMargin)} ${flowPts(pts)}`.trim();
      marginTone = flowTone(pts);
    }
  }
  return {
    changeText: formatFlowChange(ratio),
    changeTone: flowTone(ratio),
    marginText,
    marginTone,
    comparedQuarters: basis.quarters,
    hasPrior: Boolean(basis.prior),
  };
}

function flowLabel(name, totalFamily = "") {
  if (totalFamily) {
    if (totalFamily === "automotive") return "Total auto";
    const pretty = totalFamily.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
    return `Total ${pretty}`;
  }
  const raw = String(name || "").replace(/\s+/g, " ").trim();
  const rules = [
    [/automotive sales/i, "Car sales"],
    [/regulatory credits/i, "Regulatory credits"],
    [/automotive leasing/i, "Leasing"],
    [/energy generation and storage/i, "Energy"],
    [/services and other/i, "Services & other"],
    [/research and development|\br&d\b/i, "Research & development"],
    [/selling, general|\bsg&a\b|sales and administrative/i, "Sales & admin"],
    [/cost of (revenue|goods(?: and services)? sold|sales)/i, "Cost of sales"],
    [/operating income|income from operations|operating profit/i, "Operating profit"],
    [/operating expenses/i, "Operating expenses"],
    [/^gross profit$/i, "Gross profit"],
    [/income before tax|before income taxes|pre-tax|pretax/i, "Pre-tax income"],
    [/income tax|tax expense|provision for/i, "Taxes & other"],
    [/gaap net income|net income|net profit|net loss/i, "Net profit"],
    [/interest income/i, "Interest income"],
    [/interest expense/i, "Interest expense"],
    [/other income/i, "Other income"],
  ];
  for (const [pattern, label] of rules) {
    if (pattern.test(raw)) return label;
  }
  return raw.replace(/\s+revenue$/i, "") || raw;
}

function cardLabel(name) {
  const stripped = String(name || "").replace(/^total\s+/i, "").trim();
  return stripped.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function makeNode(metric, slice, year, quarter, revenueNow, revenuePrior, extra = {}) {
  const role = extra.role || metric?.role || "other";
  const visual = decorate(metric || { role, points: [] }, slice, year, quarter, revenueNow, revenuePrior);
  return {
    id: extra.id || metric.id,
    role,
    label: extra.label || flowLabel(metric?.name || "Other", extra.totalFamily || ""),
    value: slice.value,
    valueText: moneyText(slice.value),
    estimate: Boolean(slice.estimate || extra.estimate),
    pending: Boolean(slice.pending || extra.pending),
    derived: Boolean(extra.derived),
    column: extra.column,
    lane: extra.lane,
    stack: extra.stack || 0,
    tone: extra.tone || toneForRole(role, slice.value),
    cap: Boolean(extra.cap),
    note: extra.note || "",
    ...visual,
  };
}

function monthSpan(year, quarter, periodEnd) {
  let endMonth = quarter * 3;
  let endYear = year;
  const match = /^(\d{4})-(\d{2})/.exec(String(periodEnd || ""));
  if (match) {
    endYear = Number(match[1]);
    endMonth = Number(match[2]);
  }
  const startMonth = ((endMonth - 3 + 12) % 12) + 1;
  const startYear = startMonth > endMonth ? endYear - 1 : endYear;
  const start = MONTHS[startMonth - 1];
  const end = MONTHS[endMonth - 1];
  if (startYear === endYear) return `${start}–${end} ${endYear}`;
  return `${start} ${startYear}–${end} ${endYear}`;
}

function flowSubtitle(year, quarter, slice, compared, hasPrior, periodEnd) {
  const head = "Where the money went";
  let period;
  if (quarter != null) {
    period = monthSpan(year, quarter, periodEnd);
    if (slice?.estimate) period += " placeholder";
  } else if (slice?.estimatedQuarters?.length) {
    const estimates = new Set(slice.estimatedQuarters);
    const actual = (slice.quarters || []).filter((item) => !estimates.has(item));
    const parts = [];
    if (actual.length) parts.push(`${compactQuarters(actual)} FY${year}`);
    parts.push(`${compactQuarters(slice.estimatedQuarters)} placeholder`);
    period = parts.join(" + ");
  } else if ((slice?.quarters || []).length && slice.quarters.length < 4) {
    period = `${compactQuarters(slice.quarters)} FY${year}`;
  } else {
    period = `FY${year}`;
  }
  if (!hasPrior || !compared?.length) return `${head} · ${period}`;
  const priorLabel = quarter != null
    ? `Q${quarter} ${year - 1}`
    : compared.length >= 4
      ? `FY${year - 1}`
      : `${compactQuarters(compared)} FY${year - 1}`;
  return `${head} · ${period} · ▲▼ changes vs. ${priorLabel}`;
}

function unionQuarters(items) {
  const quarters = new Set();
  const estimated = new Set();
  for (const item of items) {
    for (const quarter of item.slice?.quarters || []) quarters.add(quarter);
    for (const quarter of item.slice?.estimatedQuarters || []) estimated.add(quarter);
  }
  return {
    quarters: [...quarters].sort((a, b) => a - b),
    estimated: [...estimated].sort((a, b) => a - b),
  };
}

function pushLink(links, from, to, value, tone, strong) {
  if (!Number.isFinite(value) || value === 0) return;
  links.push({
    from,
    to,
    value: Math.abs(value),
    tone,
    strong: Boolean(strong),
  });
}

function addRemainderNode(nodes, links, parent, parts, column, stack, linkFrom = parent.id) {
  const gap = remainder(parent.value, parts);
  if (gap == null || gap < 0) return null;
  const node = makeNode(null, {
    value: gap,
    estimate: parent.estimate,
    pending: parent.pending,
    quarters: parent.quarters || [],
    estimatedQuarters: [],
  }, parent.year, parent.quarter, null, null, {
    id: `other:${parent.id}`,
    role: "other",
    label: "Other",
    column,
    stack,
    tone: "muted",
    derived: true,
  });
  nodes.push(node);
  pushLink(links, linkFrom, node.id, gap, "muted", true);
  return node;
}

function familyKey(metric) {
  let id = String(metric.id || "").toLowerCase().replace(/_(revenue|revenues)$/, "");
  for (const tail of DETAIL_TAILS) {
    if (id.endsWith(`_${tail}`)) return id.slice(0, -(tail.length + 1)) || id;
  }
  return id || String(metric.id || "");
}

function isFamilyTotal(metric, family) {
  const id = String(metric.id || "").toLowerCase();
  const name = String(metric.name || "").toLowerCase();
  return id === family || id === `${family}_revenue` || id === `${family}_revenues` || name.startsWith("total ");
}

function sourceRank(metric) {
  const blob = `${metric.id} ${metric.name}`.toLowerCase();
  if (/regulatory.?credit|\bcredits\b/.test(blob)) return 20;
  if (/leas/.test(blob)) return 30;
  if (/energy/.test(blob)) return 40;
  if (/service/.test(blob)) return 50;
  if (/sales/.test(blob)) return 10;
  return 90;
}

function sameValue(left, right) {
  const scale = Math.max(Math.abs(left), Math.abs(right), 1);
  return Math.abs(left - right) <= Math.max(1, scale * BALANCE_RATIO);
}

function sharesWord(left, right) {
  const words = (metric) => `${metric.id} ${metric.name}`.toLowerCase().split(/[^a-z0-9]+/)
    .filter((word) => word.length > 4 && !GENERIC_WORDS.has(word));
  const seen = new Set(words(right));
  return words(left).some((word) => seen.has(word));
}

function segmentHierarchy(segments) {
  const families = new Map();
  for (const item of segments) {
    const key = familyKey(item.metric);
    const group = families.get(key) || [];
    group.push(item);
    families.set(key, group);
  }
  const childSet = new Set();
  const groups = [];
  for (const [key, group] of families) {
    if (group.length < 2) continue;
    const parent = group.find((item) => isFamilyTotal(item.metric, key));
    if (!parent) continue;
    const children = group.filter((item) => item !== parent);
    groups.push({ key, parent, children });
    for (const child of children) childSet.add(child);
  }
  const suppressed = new Set();
  for (const item of segments) {
    if (childSet.has(item) || groups.some((group) => group.parent === item)) continue;
    const duplicate = segments.find((other) => other !== item
      && (childSet.has(other) || groups.some((group) => group.parent === other))
      && sameValue(item.slice.value, other.slice.value)
      && sharesWord(item.metric, other.metric));
    if (duplicate) suppressed.add(item);
  }
  return { groups, childSet, suppressed };
}

function sliceFlags(slice) {
  return {
    estimate: Boolean(slice?.estimate),
    pending: Boolean(slice?.pending),
    quarters: slice?.quarters || [],
    estimatedQuarters: slice?.estimatedQuarters || [],
    periodEnd: slice?.periodEnd || "",
  };
}

function buildFlow(statement, selection) {
  const { year, quarter } = selection;
  const flows = flowMetrics(statement);
  const present = flows.map((metric) => ({ metric, slice: periodSlice(metric, year, quarter) })).filter((item) => item.slice);
  const byRole = (role) => present.filter((item) => item.metric.role === role);
  const one = (role) => byRole(role)[0] || null;

  const revenueItem = one("revenue");
  const segments = byRole("segment_revenue");
  const costItems = byRole("cost_of_sales");
  const grossItem = one("gross_profit");
  const opexItem = one("opex");
  const detailItems = present.filter((item) => OPEX_DETAIL.has(item.metric.role));
  const operatingItem = one("operating_profit");
  const bridgeItems = present.filter((item) => BRIDGE_ROLES.has(item.metric.role));
  const pretaxItem = one("pretax");
  const taxItem = one("tax");
  const netItem = one("net_profit");
  const hierarchy = segmentHierarchy(segments);

  const revenueSlice = revenueItem?.slice || (segments.length ? {
    value: segments.reduce((sum, item) => sum + item.slice.value, 0),
    estimate: segments.some((item) => item.slice.estimate),
    pending: segments.some((item) => item.slice.pending),
    quarters: unionQuarters(segments).quarters,
    estimatedQuarters: unionQuarters(segments).estimated,
    periodEnd: segments.find((item) => item.slice.periodEnd)?.slice.periodEnd || "",
    derived: true,
  } : null);

  const revenueBasis = revenueItem
    ? changeBasis(revenueItem.metric, revenueItem.slice, year, quarter)
    : { current: null, prior: null, quarters: [] };
  const revenueContext = revenueSlice ? {
    ...revenueSlice,
    basisCurrent: revenueBasis.current,
    basisPrior: revenueBasis.prior,
  } : null;
  const covered = unionQuarters(present);

  const nodes = [];
  const links = [];
  const balances = [];
  const mismatches = [];

  const track = (parentId, parentValue, partValues) => {
    const gap = remainder(parentValue, partValues);
    balances.push({
      parent: parentId,
      parentValue,
      partSum: partValues.reduce((sum, value) => sum + value, 0),
      gap: gap || 0,
    });
    return gap;
  };

  if (revenueSlice) {
    nodes.push(makeNode(
      revenueItem?.metric || { id: "revenue", role: "revenue", name: "Revenue", points: [] },
      revenueSlice,
      year,
      quarter,
      revenueContext,
      null,
      {
        id: "revenue",
        role: "revenue",
        label: "Revenue",
        column: "revenue",
        tone: "grey",
        derived: Boolean(revenueSlice.derived) || !revenueItem,
      },
    ));
  }

  const orderedSources = [];
  for (const group of hierarchy.groups) {
    group.children.sort((a, b) => sourceRank(a.metric) - sourceRank(b.metric) || a.metric.name.localeCompare(b.metric.name));
    orderedSources.push(...group.children);
  }
  const directs = segments
    .filter((item) => !hierarchy.childSet.has(item) && !hierarchy.suppressed.has(item) && !hierarchy.groups.some((group) => group.parent === item))
    .sort((a, b) => sourceRank(a.metric) - sourceRank(b.metric) || a.metric.name.localeCompare(b.metric.name));
  orderedSources.push(...directs);

  orderedSources.forEach((item, index) => {
    const parent = hierarchy.groups.find((group) => group.children.includes(item));
    const node = makeNode(item.metric, item.slice, year, quarter, revenueContext, null, {
      column: "segments",
      stack: index,
    });
    nodes.push(node);
    if (parent) pushLink(links, node.id, parent.parent.metric.id, item.slice.value, "grey", false);
    else if (revenueSlice) pushLink(links, node.id, "revenue", item.slice.value, "grey", false);
  });

  const inflowParts = [];
  for (const group of hierarchy.groups) {
    const item = group.parent;
    const node = makeNode(item.metric, item.slice, year, quarter, revenueContext, null, {
      column: "rollup",
      stack: hierarchy.groups.indexOf(group),
      totalFamily: group.key,
    });
    nodes.push(node);
    if (revenueSlice) {
      inflowParts.push(item.slice.value);
      pushLink(links, node.id, "revenue", item.slice.value, "grey", false);
    }
    const childValues = group.children.map((child) => child.slice.value);
    const gap = track(node.id, item.slice.value, childValues);
    if (gap != null && gap < 0) {
      mismatches.push({ parent: node.id, parentValue: item.slice.value, partSum: item.slice.value - gap, gap });
    }
    addRemainderNode(nodes, links, { ...node, year, quarter, quarters: item.slice.quarters }, childValues, "segments", orderedSources.length + 1, node.id);
  }
  for (const item of directs) inflowParts.push(item.slice.value);

  let revenueMismatch = null;
  if (revenueSlice && inflowParts.length) {
    const gap = track("revenue-in", revenueSlice.value, inflowParts);
    if (gap != null && gap > 0) {
      const node = makeNode(null, {
        value: gap,
        ...sliceFlags(revenueSlice),
      }, year, quarter, null, null, {
        id: "other:revenue-in",
        role: "other",
        label: "Other",
        column: "segments",
        stack: orderedSources.length + 2,
        tone: "muted",
        derived: true,
      });
      nodes.push(node);
      pushLink(links, node.id, "revenue", gap, "muted", false);
    } else if (gap != null && gap < 0) {
      revenueMismatch = {
        reported: revenueSlice.value,
        segments: inflowParts.reduce((sum, value) => sum + value, 0),
        gap,
      };
      const revenueNode = nodes.find((node) => node.id === "revenue");
      if (revenueNode) revenueNode.note = "Reported total differs from segments";
    }
  }

  const totalCost = costItems.find((item) => isTotalCost(item.metric));
  const costDetails = totalCost ? costItems.filter((item) => item !== totalCost) : costItems.length > 1 ? costItems : [];
  const costTotal = totalCost || (costItems.length === 1 ? costItems[0] : null);
  const splitParts = [];

  if (grossItem) {
    const node = makeNode(grossItem.metric, grossItem.slice, year, quarter, revenueContext, null, {
      column: "split",
      stack: 0,
    });
    nodes.push(node);
    splitParts.push(grossItem.slice.value);
    if (revenueSlice) pushLink(links, "revenue", node.id, grossItem.slice.value, "green", true);
  }
  if (costTotal) {
    const node = makeNode(costTotal.metric, costTotal.slice, year, quarter, revenueContext, null, {
      column: "split",
      stack: 1,
      cap: costDetails.length === 0,
    });
    nodes.push(node);
    splitParts.push(costTotal.slice.value);
    if (revenueSlice) pushLink(links, "revenue", node.id, costTotal.slice.value, "red", true);
    if (costDetails.length) {
      const detailValues = [];
      costDetails.forEach((item, index) => {
        const detail = makeNode(item.metric, item.slice, year, quarter, revenueContext, null, {
          column: "costDetail",
          stack: index,
          cap: true,
        });
        nodes.push(detail);
        detailValues.push(item.slice.value);
        pushLink(links, node.id, detail.id, item.slice.value, "red", true);
      });
      track(node.id, costTotal.slice.value, detailValues);
      addRemainderNode(nodes, links, { ...node, year, quarter, quarters: costTotal.slice.quarters }, detailValues, "costDetail", costDetails.length + 1);
    }
  } else {
    costDetails.forEach((item, index) => {
      const node = makeNode(item.metric, item.slice, year, quarter, revenueContext, null, {
        column: "split",
        stack: index + 1,
        cap: true,
      });
      nodes.push(node);
      splitParts.push(item.slice.value);
      if (revenueSlice) pushLink(links, "revenue", node.id, item.slice.value, "red", true);
    });
  }

  if (revenueSlice && splitParts.length) {
    track("revenue", revenueSlice.value, splitParts);
    const revenueNode = nodes.find((node) => node.id === "revenue");
    addRemainderNode(nodes, links, { ...revenueNode, year, quarter, quarters: revenueSlice.quarters }, splitParts, "split", 2);
  }

  const operateParts = [];
  if (operatingItem) {
    const node = makeNode(operatingItem.metric, operatingItem.slice, year, quarter, revenueContext, null, {
      column: "operate",
      stack: 0,
    });
    nodes.push(node);
    if (grossItem) {
      operateParts.push(operatingItem.slice.value);
      pushLink(links, grossItem.metric.id, node.id, operatingItem.slice.value, "green", true);
    }
  }

  const detailColumn = opexItem && detailItems.length ? "opexDetail" : "operate";
  if (opexItem) {
    const node = makeNode(opexItem.metric, opexItem.slice, year, quarter, revenueContext, null, {
      column: "operate",
      stack: 1,
      cap: detailItems.length === 0,
    });
    nodes.push(node);
    if (grossItem) {
      operateParts.push(opexItem.slice.value);
      pushLink(links, grossItem.metric.id, node.id, opexItem.slice.value, "red", true);
    }
    if (detailItems.length) {
      const detailValues = [];
      detailItems.forEach((item, index) => {
        const detail = makeNode(item.metric, item.slice, year, quarter, revenueContext, null, {
          column: detailColumn,
          stack: index,
          cap: true,
        });
        nodes.push(detail);
        detailValues.push(item.slice.value);
        pushLink(links, node.id, detail.id, item.slice.value, "red", true);
      });
      track(node.id, opexItem.slice.value, detailValues);
      addRemainderNode(nodes, links, { ...node, year, quarter, quarters: opexItem.slice.quarters }, detailValues, detailColumn, detailItems.length + 1);
    }
  } else if (detailItems.length && grossItem) {
    detailItems.forEach((item, index) => {
      const detail = makeNode(item.metric, item.slice, year, quarter, revenueContext, null, {
        column: "operate",
        stack: index + 1,
        cap: true,
      });
      nodes.push(detail);
      operateParts.push(item.slice.value);
      pushLink(links, grossItem.metric.id, detail.id, item.slice.value, "red", true);
    });
  } else {
    detailItems.forEach((item, index) => {
      nodes.push(makeNode(item.metric, item.slice, year, quarter, revenueContext, null, {
        column: "operate",
        stack: index + 1,
        cap: true,
      }));
    });
  }

  if (grossItem && operateParts.length) {
    track(grossItem.metric.id, grossItem.slice.value, operateParts);
    const grossNode = nodes.find((node) => node.id === grossItem.metric.id);
    addRemainderNode(nodes, links, { ...grossNode, year, quarter, quarters: grossItem.slice.quarters }, operateParts, "operate", 2);
  }

  let interestNode = null;
  if (bridgeItems.length > 1) {
    const displayed = bridgeItems.reduce((sum, item) => sum + (signedBridge(item.metric.role, item.slice.value) || 0), 0);
    let currentActual = 0;
    let priorActual = 0;
    let comparable = true;
    for (const item of bridgeItems) {
      const basis = changeBasis(item.metric, item.slice, year, quarter);
      if (!basis.current || !basis.prior) {
        comparable = false;
        break;
      }
      currentActual += signedBridge(item.metric.role, basis.current.value) || 0;
      priorActual += signedBridge(item.metric.role, basis.prior.value) || 0;
    }
    const ratio = comparable && priorActual !== 0 ? (currentActual - priorActual) / Math.abs(priorActual) : null;
    const includesOther = bridgeItems.some((item) => item.metric.role === "other_income" || item.metric.role === "interest_other");
    interestNode = makeNode(null, {
      value: displayed,
      estimate: bridgeItems.some((item) => item.slice.estimate),
      pending: bridgeItems.some((item) => item.slice.pending),
      quarters: unionQuarters(bridgeItems).quarters,
      estimatedQuarters: unionQuarters(bridgeItems).estimated,
    }, year, quarter, null, null, {
      id: "interest_net",
      role: "interest_other",
      label: includesOther ? "Interest & other, net" : "Interest, net",
      column: "opexDetail",
      stack: 20,
    });
    interestNode.changeText = formatFlowChange(ratio);
    interestNode.changeTone = flowTone(ratio);
    interestNode.parts = bridgeItems.map((item) => item.metric.id);
    nodes.push(interestNode);
  } else if (bridgeItems.length === 1) {
    const item = bridgeItems[0];
    interestNode = makeNode(item.metric, item.slice, year, quarter, revenueContext, null, {
      column: "opexDetail",
      stack: 20,
    });
    nodes.push(interestNode);
  }

  const bridgeSum = [];
  if (operatingItem && pretaxItem) bridgeSum.push(operatingItem.slice.value);
  if (interestNode && pretaxItem) {
    const signed = interestNode.value;
    if (signed) {
      bridgeSum.push(signed);
      pushLink(links, interestNode.id, pretaxItem.metric.id, signed, interestNode.tone, true);
    }
  }

  if (pretaxItem) {
    const node = makeNode(pretaxItem.metric, pretaxItem.slice, year, quarter, revenueContext, null, {
      column: "bridge",
      stack: 0,
    });
    nodes.push(node);
    if (operatingItem) pushLink(links, operatingItem.metric.id, node.id, operatingItem.slice.value, "green", true);
    if (bridgeSum.length) {
      track(node.id, pretaxItem.slice.value, bridgeSum);
      addRemainderNode(nodes, links, { ...node, year, quarter, quarters: pretaxItem.slice.quarters }, bridgeSum, "bridge", 2);
    }
    const exit = [];
    if (taxItem) {
      const tax = makeNode(taxItem.metric, taxItem.slice, year, quarter, revenueContext, null, {
        column: "result",
        stack: 0,
        cap: true,
      });
      nodes.push(tax);
      exit.push(taxItem.slice.value);
      pushLink(links, node.id, tax.id, taxItem.slice.value, "red", true);
    }
    if (netItem) {
      const net = makeNode(netItem.metric, netItem.slice, year, quarter, revenueContext, null, {
        column: "result",
        stack: 1,
      });
      nodes.push(net);
      exit.push(netItem.slice.value);
      pushLink(links, node.id, net.id, netItem.slice.value, "green", true);
    }
    if (exit.length) {
      track(`${node.id}:split`, pretaxItem.slice.value, exit);
      addRemainderNode(
        nodes,
        links,
        { ...node, id: `${node.id}:split`, year, quarter, quarters: pretaxItem.slice.quarters },
        exit,
        "result",
        2,
        node.id,
      );
    }
  } else {
    if (taxItem) {
      nodes.push(makeNode(taxItem.metric, taxItem.slice, year, quarter, revenueContext, null, {
        column: "result",
        stack: 0,
        cap: true,
      }));
    }
    if (netItem) {
      nodes.push(makeNode(netItem.metric, netItem.slice, year, quarter, revenueContext, null, {
        column: "result",
        stack: 1,
      }));
    }
  }

  const columns = COLUMNS.filter((name) => nodes.some((node) => node.column === name));
  const cards = statement.metrics
    .filter((metric) => metric.role === "deliveries" || metric.role === "production")
    .map((metric) => {
      const slice = periodSlice(metric, year, quarter);
      if (!slice) return null;
      const basis = changeBasis(metric, slice, year, quarter);
      const ratio = changeRatio(basis.current, basis.prior);
      const usd = String(metric.unit || "").toLowerCase().startsWith("usd");
      return {
        id: metric.id,
        role: metric.role,
        label: cardLabel(metric.name),
        valueText: usd ? moneyText(slice.value) : Math.round(slice.value).toLocaleString("en-US"),
        changeText: formatFlowChange(ratio),
        changeTone: flowTone(ratio),
        pending: slice.pending,
      };
    })
    .filter(Boolean);

  const revenueNode = nodes.find((node) => node.id === "revenue");
  const compared = revenueNode?.comparedQuarters?.length
    ? revenueNode.comparedQuarters
    : revenueBasis.quarters;
  const hasPrior = Boolean(revenueNode?.hasPrior || present.some((item) => changeBasis(item.metric, item.slice, year, quarter).prior));
  return {
    year,
    quarter,
    estimate: covered.estimated.length > 0 || Boolean(revenueSlice?.estimate),
    subtitle: flowSubtitle(year, quarter, revenueSlice, compared, hasPrior, revenueSlice?.periodEnd),
    nodes,
    links,
    balances,
    mismatches,
    revenueMismatch,
    columns,
    cards,
    derivedRevenue: Boolean(revenueSlice?.derived),
    pending: nodes.some((node) => node.pending) || cards.some((card) => card.pending),
    placeholder: present.some((item) => item.slice.estimate),
    empty: nodes.length === 0,
  };
}

export function ribbonPath(x1, y1, x2, y2, thickness1, thickness2, strong) {
  const width = Math.abs(x2 - x1);
  const pull = strong ? 0.9 : 0.38;
  const dx = Math.max(strong ? 84 : 28, width * pull);
  const direction = x2 >= x1 ? 1 : -1;
  const c1 = x1 + direction * dx;
  const c2 = x2 - direction * dx;
  const a = Math.max(1, thickness1) / 2;
  const b = Math.max(1, thickness2) / 2;
  const n = (value) => Math.round(value * 10) / 10;
  return [
    `M ${n(x1)} ${n(y1 - a)}`,
    `C ${n(c1)} ${n(y1 - a)}, ${n(c2)} ${n(y2 - b)}, ${n(x2)} ${n(y2 - b)}`,
    `L ${n(x2)} ${n(y2 + b)}`,
    `C ${n(c2)} ${n(y2 + b)}, ${n(c1)} ${n(y1 + a)}, ${n(x1)} ${n(y1 + a)}`,
    "Z",
  ].join(" ");
}

export function curvePull(strong) {
  return strong ? 0.9 : 0.38;
}

function barHeight(value, scale) {
  const height = (Math.abs(value) / Math.abs(scale || value || 1)) * 188;
  return Math.max(16, Math.min(200, height));
}

function stackBands(links, nodes, node, side) {
  const list = links.filter((link) => (side === "in" ? link.to === node.id : link.from === node.id));
  if (!list.length) return;
  const total = list.reduce((sum, link) => sum + link.value, 0) || 1;
  list.sort((a, b) => {
    const aId = side === "in" ? a.from : a.to;
    const bId = side === "in" ? b.from : b.to;
    const ay = nodes.find((item) => item.id === aId)?.y || 0;
    const by = nodes.find((item) => item.id === bId)?.y || 0;
    return ay - by;
  });
  let cursor = node.y;
  for (const link of list) {
    const band = Math.max(5, (node.h * link.value) / total);
    if (side === "in") {
      link.y2 = cursor + band / 2;
      link.t2 = Math.min(band, node.h);
    } else {
      link.y1 = cursor + band / 2;
      link.t1 = Math.min(band, node.h);
    }
    cursor += band;
  }
}

export function layoutRevenueFlow(flow) {
  const nodes = (flow.nodes || []).map((node) => ({ ...node, placed: false }));
  const links = (flow.links || []).map((link) => ({ ...link }));
  const byId = (id) => nodes.find((node) => node.id === id) || null;
  const byRole = (role) => nodes.find((node) => node.role === role) || null;
  const revenue = byId("revenue") || byRole("revenue");
  const scale = Math.abs(revenue?.value) || Math.max(...nodes.map((node) => Math.abs(node.value)), 1);
  for (const node of nodes) {
    node.w = BAR_W;
    node.h = barHeight(node.value, scale);
  }
  const columns = (flow.columns || []).filter((name) => nodes.some((node) => node.column === name));
  const columnX = new Map(columns.map((name, index) => [name, 132 + index * COLUMN_PITCH]));

  const sources = nodes.filter((node) => node.column === "segments").sort((a, b) => a.stack - b.stack);
  let cursor = 62;
  for (const node of sources) {
    node.x = columnX.get("segments") || 132;
    node.y = cursor;
    node.placed = true;
    cursor += node.h + 68;
  }

  const rollups = nodes.filter((node) => node.column === "rollup").sort((a, b) => a.stack - b.stack);
  for (const node of rollups) {
    const children = links
      .filter((link) => link.to === node.id)
      .map((link) => byId(link.from))
      .filter(Boolean);
    node.x = columnX.get("rollup") || node.x;
    if (children.length) {
      const top = Math.min(...children.map((child) => child.y));
      const bottom = Math.max(...children.map((child) => child.y + child.h));
      node.y = (top + bottom) / 2 - node.h / 2;
    } else node.y = 62;
    node.placed = true;
  }

  if (revenue) {
    const inflows = links
      .filter((link) => link.to === revenue.id)
      .map((link) => byId(link.from))
      .filter(Boolean);
    revenue.x = columnX.get("revenue") || revenue.x;
    if (inflows.length) {
      const top = Math.min(...inflows.map((node) => node.y));
      const bottom = Math.max(...inflows.map((node) => node.y + node.h));
      const inflowHeight = inflows.reduce((sum, node) => sum + node.h, 0);
      revenue.h = Math.max(revenue.h, Math.min(320, inflowHeight));
      revenue.y = (top + bottom) / 2 - revenue.h / 2;
    } else revenue.y = 62;
    revenue.placed = true;
  }

  const gross = byRole("gross_profit");
  const cost = nodes.find((node) => node.cap && node.column === "split" && node.role === "cost_of_sales") || nodes.find((node) => node.role === "cost_of_sales" && node.column === "split");
  const operating = byRole("operating_profit");
  const opex = byRole("opex");
  const pretax = byRole("pretax");
  const tax = byRole("tax");
  const net = byRole("net_profit");
  const interest = byId("interest_net") || nodes.find((node) => BRIDGE_ROLES.has(node.role));

  const place = (node, xKey, y) => {
    if (!node) return;
    node.x = columnX.get(xKey) ?? columnX.get(node.column) ?? 132;
    node.y = y;
    node.placed = true;
  };
  if (revenue) {
    place(gross, "split", revenue.y);
    if (cost) place(cost, "split", revenue.y + revenue.h + 64);
    if (gross) {
      const operatingHeight = operating?.h || 24;
      place(operating, "operate", gross.y - operatingHeight - 28);
      place(opex, "operate", gross.y + gross.h + 108);
    }
    if (operating) place(interest, "opexDetail", operating.y + operating.h + 36);
    if (opex) {
      const details = nodes.filter((node) => node.column === "opexDetail" && node !== interest).sort((a, b) => a.stack - b.stack);
      details.forEach((node, index) => place(node, "opexDetail", opex.y + index * (node.h + 78)));
    }
    if (operating) {
      const meeting = interest ? operating.y + (interest.y + interest.h - operating.y) * 0.62 : operating.y + 8;
      place(pretax, "bridge", meeting);
    }
    if (pretax) {
      place(tax, "result", pretax.y - (tax?.h || 22) - 52);
      place(net, "result", pretax.y + pretax.h + 58);
    } else if (operating) {
      place(tax, "result", operating.y - (tax?.h || 22) - 20);
      place(net, "result", (interest?.y || operating.y) + (interest?.h || operating.h) + 48);
    }
  }

  for (const name of columns) {
    const loose = nodes.filter((node) => node.column === name && !node.placed).sort((a, b) => a.stack - b.stack);
    const placed = nodes.filter((node) => node.column === name && node.placed);
    let y = placed.length ? Math.max(...placed.map((node) => node.y + node.h)) + 76 : 62;
    for (const node of loose) {
      node.x = columnX.get(name) || 132;
      node.y = y;
      node.placed = true;
      y += node.h + 76;
    }
    const columnNodes = nodes.filter((node) => node.column === name).sort((a, b) => a.y - b.y);
    for (let index = 1; index < columnNodes.length; index += 1) {
      const previous = columnNodes[index - 1];
      const needed = previous.y + previous.h + 72;
      if (columnNodes[index].y < needed) columnNodes[index].y = needed;
    }
  }

  const labelTop = (node) => node.y - (node.marginText ? 58 : 42) - (nameLines(node.label).length - 1) * 15;
  const minTop = Math.min(...nodes.map(labelTop), 16);
  if (minTop < 14) {
    const shift = 14 - minTop;
    for (const node of nodes) node.y += shift;
  }

  for (const node of nodes) {
    stackBands(links, nodes, node, "in");
    stackBands(links, nodes, node, "out");
  }
  for (const link of links) {
    const source = byId(link.from);
    const target = byId(link.to);
    if (!source || !target) continue;
    const x1 = source.x + source.w;
    const x2 = target.x;
    const y1 = link.y1 || source.y + source.h / 2;
    const y2 = link.y2 || target.y + target.h / 2;
    const thickness = Math.max(6, Math.min(source.h, target.h, (Math.abs(link.value) / scale) * 188));
    link.x1 = x1;
    link.y1 = y1;
    link.x2 = x2;
    link.y2 = y2;
    link.d = ribbonPath(x1, y1, x2, y2, link.t1 || thickness, link.t2 || thickness, link.strong);
  }
  const width = Math.max(720, 180 + (columns.length ? (columns.length - 1) * COLUMN_PITCH + 280 : 720));
  const height = Math.max(420, ...nodes.map((node) => node.y + node.h + 36));
  return { width, height, nodes, links };
}

function footnote(flow) {
  const notes = ["Source: company filings and SEC XBRL."];
  if (flow.pending) notes.push("Company metrics are pending review.");
  if (flow.placeholder) notes.push("Estimated quarters are a placeholder, not a reviewed projection.");
  if (flow.derivedRevenue) notes.push("Revenue is the sum of reported segments because a consolidated total is not on file.");
  if (flow.revenueMismatch) {
    const row = flow.revenueMismatch;
    notes.push(`Revenue is ${moneyText(row.reported)} and the segments flowing into it sum to ${moneyText(row.segments)}. The reported total is shown; the difference is not filled in.`);
  }
  if ((flow.balances || []).some((row) => row.gap > 0)) notes.push("Other is the remainder that makes a split add up to its parent.");
  if ((flow.mismatches || []).length) notes.push("A reported parent is smaller than its parts. Both figures are shown.");
  if (notes.length === 1) notes.push("Reported figures only. Lines without a value for this period are left out.");
  return notes.join(" ");
}

export function revenueFlowModel(ticker, payload, chartData, selection = {}) {
  const statement = collectStatement(payload, chartData);
  statement.ticker = String(ticker || payload?.ticker || "").toUpperCase();
  const flows = flowMetrics(statement);
  const periods = yearsWithData(flows, 40).map((year) => ({
    year,
    quarters: quartersWithData(flows, year),
    estimate: flows.some((metric) => metric.points.some((point) => point.year === year && point.estimate)),
  }));
  const resolved = resolveSelection(periods, selection);
  const flow = resolved.year == null ? {
    year: null,
    quarter: null,
    estimate: false,
    subtitle: "No income-statement period is on file",
    nodes: [],
    links: [],
    balances: [],
    mismatches: [],
    revenueMismatch: null,
    columns: [],
    cards: [],
    derivedRevenue: false,
    pending: false,
    placeholder: false,
    empty: true,
  } : buildFlow(statement, resolved);
  const periodLabel = flow.empty || resolved.year == null
    ? ""
    : `FY${resolved.year}${flow.estimate ? "E" : ""}${resolved.quarter ? ` Q${resolved.quarter}` : ""}`;
  flow.header = periodLabel ? `${statement.ticker} ${periodLabel}` : statement.ticker;
  flow.when = flow.empty || resolved.year == null
    ? ""
    : resolved.quarter
      ? `Q${resolved.quarter} ${resolved.year}`
      : `FY${resolved.year}${flow.estimate ? "E" : ""}`;
  return {
    ticker: statement.ticker,
    periods,
    selection: resolved,
    ...flow,
    footnote: flow.empty ? "No income-statement lines are on file for this ticker." : footnote(flow),
  };
}

function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text != null && text !== "") node.textContent = text;
  return node;
}

function addOption(doc, select, value, label, selected) {
  const option = el(doc, "option", "", label);
  option.value = value;
  option.selected = selected;
  option.setAttribute("value", value);
  if (selected) option.setAttribute("selected", "");
  select.append(option);
  if (selected) select.value = value;
  return option;
}

function periodDropdowns(doc, model, options) {
  const selector = el(doc, "div", "flow-selector");
  selector.setAttribute("role", "group");
  selector.setAttribute("aria-label", "Fiscal period");

  const yearField = el(doc, "label", "flow-field");
  yearField.append(el(doc, "span", "flow-field-label", "Year"));
  const yearSelect = el(doc, "select", "flow-select");
  yearSelect.dataset.control = "year";
  yearSelect.setAttribute("data-control", "year");
  for (const period of model.periods) {
    const label = `FY${period.year}${period.estimate ? "E" : ""}`;
    addOption(doc, yearSelect, String(period.year), label, model.selection.year === period.year);
  }
  yearSelect.addEventListener("change", () => {
    const year = Number(yearSelect.value);
    const period = model.periods.find((item) => item.year === year);
    const quarter = period?.quarters.includes(model.selection.quarter) ? model.selection.quarter : null;
    options.onSelect?.(year, quarter);
  });
  yearField.append(yearSelect);

  const quarterField = el(doc, "label", "flow-field");
  quarterField.append(el(doc, "span", "flow-field-label", "Quarter"));
  const quarterSelect = el(doc, "select", "flow-select");
  quarterSelect.dataset.control = "quarter";
  quarterSelect.setAttribute("data-control", "quarter");
  const current = model.periods.find((period) => period.year === model.selection.year);
  addOption(doc, quarterSelect, "year", "Full Year", model.selection.quarter == null);
  for (const quarter of current?.quarters || []) {
    addOption(doc, quarterSelect, String(quarter), `Q${quarter}`, model.selection.quarter === quarter);
  }
  quarterSelect.addEventListener("change", () => {
    const raw = quarterSelect.value;
    const quarter = raw === "year" ? null : Number(raw);
    options.onSelect?.(model.selection.year, Number.isInteger(quarter) ? quarter : null);
  });
  quarterField.append(quarterSelect);
  selector.append(yearField, quarterField);
  return selector;
}

function svg(doc, tag, attrs) {
  const node = doc.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value != null) node.setAttribute(key, String(value));
  }
  return node;
}

function drawLabel(doc, parent, node) {
  const x = node.x + node.w / 2;
  const lines = nameLines(node.label);
  const nameBase = node.y - (node.marginText ? 42 : 26);
  lines.forEach((line, index) => {
    const name = svg(doc, "text", {
      class: "flow-name",
      x,
      y: nameBase - (lines.length - 1 - index) * 15,
      "text-anchor": "middle",
    });
    name.textContent = line;
    parent.append(name);
  });
  const value = svg(doc, "text", {
    class: "flow-value-line",
    x,
    y: node.y - (node.marginText ? 24 : 8),
    "text-anchor": "middle",
  });
  const amount = svg(doc, "tspan", { class: `flow-amount flow-amount-${node.tone}` });
  amount.textContent = node.valueText;
  value.append(amount);
  if (node.changeText) {
    const change = svg(doc, "tspan", { class: `flow-change is-${node.changeTone}`, dx: 6 });
    change.textContent = node.changeText;
    value.append(change);
  }
  parent.append(value);
  if (node.marginText) {
    const margin = svg(doc, "text", {
      class: `flow-margin is-${node.marginTone}`,
      x,
      y: node.y - 8,
      "text-anchor": "middle",
    });
    margin.textContent = node.marginText;
    parent.append(margin);
  }
  if (node.note) {
    const note = svg(doc, "text", {
      class: "flow-note",
      x,
      y: node.y + node.h + 16,
      "text-anchor": "middle",
    });
    note.textContent = node.note;
    parent.append(note);
  }
}

export function renderRevenueFlow(model, doc = document, options = {}) {
  const panel = el(doc, "section", "panel revenue-flow");
  panel.setAttribute("aria-label", `${model.ticker || "Company"} revenue flow`);
  const header = el(doc, "div", "flow-heading");
  const copy = el(doc, "div", "flow-copy");
  const title = el(doc, "h2", "flow-title");
  title.append(el(doc, "span", "flow-ticker", model.ticker || "Revenue flow"));
  if (model.when) title.append(el(doc, "span", "flow-when", model.when));
  copy.append(title);
  copy.append(el(doc, "p", "flow-subtitle", model.subtitle || ""));
  if (model.pending) copy.append(el(doc, "span", "review-badge", "Pending review"));
  header.append(copy);
  if (model.periods.length) header.append(periodDropdowns(doc, model, options));
  panel.append(header);

  if (model.empty) {
    panel.append(el(doc, "p", "flow-empty", "No income-statement lines are on file for this period."));
    panel.append(el(doc, "p", "flow-footnote", model.footnote || ""));
    return panel;
  }

  const layout = layoutRevenueFlow(model);
  const scroll = el(doc, "div", "flow-scroll");
  const figure = svg(doc, "svg", {
    class: "flow-chart",
    viewBox: `0 0 ${layout.width} ${layout.height}`,
    width: layout.width,
    height: layout.height,
    preserveAspectRatio: "xMinYMin meet",
    role: "img",
    "aria-label": model.header,
  });
  for (const link of layout.links) {
    if (!link.d) continue;
    figure.append(svg(doc, "path", {
      class: `flow-link flow-link-${link.tone}`,
      d: link.d,
      "data-curve": link.strong ? "strong" : "gentle",
      "data-from": link.from,
      "data-to": link.to,
    }));
  }
  for (const node of layout.nodes) {
    figure.append(svg(doc, "rect", {
      class: `flow-bar flow-bar-${node.tone}${node.cap ? " is-cap" : ""}`,
      x: node.x,
      y: node.y,
      width: node.w,
      height: node.h,
      rx: 1,
      "data-node": node.id,
    }));
    drawLabel(doc, figure, node);
  }
  scroll.append(figure);
  panel.append(scroll);

  const footer = el(doc, "div", "flow-footer");
  if (model.cards.length) {
    const cards = el(doc, "div", "flow-cards");
    for (const card of model.cards) {
      const item = el(doc, "article", "flow-card");
      item.dataset.card = card.role;
      item.append(el(doc, "h3", "flow-card-label", card.label));
      const value = el(doc, "p", "flow-card-value");
      value.append(doc.createTextNode(`${card.valueText} `));
      if (card.changeText) value.append(el(doc, "span", `flow-change is-${card.changeTone}`, card.changeText));
      item.append(value);
      cards.append(item);
    }
    footer.append(cards);
  }
  footer.append(el(doc, "p", "flow-footnote", model.footnote || ""));
  panel.append(footer);
  return panel;
}
