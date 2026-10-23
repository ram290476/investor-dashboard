/**
 * Revenue Flow for the Analysis tab.
 *
 * Every income-statement line with a value for the selected period becomes a
 * node. Lines with no value are left out. A split that does not add up to its
 * parent gets an Other remainder so the flows balance. No missing number is
 * invented. Right-side links are cubic Beziers with long horizontal controls.
 */

import {
  alignedValue,
  changeRatio,
  changeTone,
  collectStatement,
  compactQuarters,
  flowMetrics,
  formatChangePct,
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
const NODE_W = 168;
const LINK_GAP = 210;
const COLUMN_PITCH = NODE_W + LINK_GAP;
const LANE_Y = { high: 18, upper: 132, mid: 246, lower: 360, low: 486 };
const STACK_GAP = 76;
const BALANCE_RATIO = 0.005;
const FAN = 196;
const TOP_Y = 18;
const ATTACH = 28;
const ANCHOR = TOP_Y + ATTACH + FAN;

const OPEX_DETAIL = new Set(["rd", "sga", "restructuring", "other_opex"]);
const BRIDGE_ROLES = new Set(["interest_income", "interest_expense", "interest_other", "other_income"]);
const MARGIN_ROLES = new Set(["gross_profit", "operating_profit", "pretax", "net_profit"]);

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

function finite(value) {
  return Number.isFinite(value) ? value : null;
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

function priorSlice(metric, year, quarter, current) {
  if (!metric || !current) return null;
  if (quarter != null) return periodSlice(metric, year - 1, quarter);
  return alignedValue(metric, year - 1, current.quarters);
}

function decorate(metric, slice, year, quarter, revenueNow, revenuePrior) {
  const role = metric?.role || "";
  const prior = priorSlice(metric, year, quarter, slice);
  const ratio = changeRatio(slice, prior);
  let marginText = "";
  let marginTone = "flat";
  if (MARGIN_ROLES.has(role) && revenueNow && revenueNow.value) {
    const margin = slice.value / revenueNow.value;
    marginText = formatMargin(margin);
    if (prior && revenuePrior && revenuePrior.value) {
      const pts = margin - prior.value / revenuePrior.value;
      marginText = `${marginText} ${formatPts(pts)}`.trim();
      marginTone = changeTone(pts);
    }
  }
  return {
    changeText: formatChangePct(ratio),
    changeTone: changeTone(ratio),
    marginText,
    marginTone,
  };
}

function makeNode(metric, slice, year, quarter, revenueNow, revenuePrior, extra = {}) {
  const role = extra.role || metric?.role || "other";
  const visual = decorate(metric || { role, points: [] }, slice, year, quarter, revenueNow, revenuePrior);
  return {
    id: extra.id || metric.id,
    role,
    label: extra.label || metric?.name || "Other",
    value: slice.value,
    valueText: moneyText(slice.value),
    estimate: Boolean(slice.estimate || extra.estimate),
    pending: Boolean(slice.pending || extra.pending),
    derived: Boolean(extra.derived),
    column: extra.column,
    lane: extra.lane,
    stack: extra.stack || 0,
    tone: extra.tone || toneForRole(role, slice.value),
    ...visual,
  };
}

function basisText(year, quarter, quarters, estimated, prior) {
  if (quarter != null) {
    const estimate = estimated.includes(quarter);
    const head = estimate ? `Q${quarter} FY${year} placeholder — pending review` : `Q${quarter} FY${year} actual`;
    return prior ? `${head} · changes vs. Q${quarter} FY${year - 1}` : head;
  }
  const actual = quarters.filter((item) => !estimated.includes(item));
  const parts = [];
  if (actual.length) parts.push(`${compactQuarters(actual)} FY${year} actual`);
  if (estimated.length) parts.push(`${compactQuarters(estimated)} placeholder — pending review`);
  if (!parts.length) parts.push(`FY${year}`);
  if (!prior) return parts.join(" + ");
  return `${parts.join(" + ")} · changes vs. ${compactQuarters(quarters)} FY${year - 1}`;
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

function addRemainderNode(nodes, links, parent, parts, column, lane, stack, linkFrom = parent.id) {
  const gap = remainder(parent.value, parts);
  if (gap == null) return null;
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
    lane,
    stack,
    tone: "muted",
    derived: true,
  });
  node.year = parent.year;
  node.quarter = parent.quarter;
  nodes.push(node);
  pushLink(links, linkFrom, node.id, gap, "muted", true);
  return node;
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

  const revenueSlice = revenueItem?.slice || (segments.length ? {
    value: segments.reduce((sum, item) => sum + item.slice.value, 0),
    estimate: segments.some((item) => item.slice.estimate),
    pending: segments.some((item) => item.slice.pending),
    quarters: unionQuarters(segments).quarters,
    estimatedQuarters: unionQuarters(segments).estimated,
    derived: true,
  } : null);

  const revenuePrior = revenueItem
    ? priorSlice(revenueItem.metric, year, quarter, revenueItem.slice)
    : null;
  const covered = unionQuarters(present);
  const hasPrior = Boolean(revenuePrior || present.some((item) => priorSlice(item.metric, year, quarter, item.slice)));

  const nodes = [];
  const links = [];
  const balances = [];

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
    nodes.push(makeNode(revenueItem?.metric || { id: "revenue", role: "revenue", name: "Revenue", points: [] }, revenueSlice, year, quarter, revenueSlice, revenuePrior, {
      id: "revenue",
      role: "revenue",
      label: "Revenue",
      column: "revenue",
      lane: "mid",
      tone: "grey",
      derived: Boolean(revenueSlice.derived) || !revenueItem,
    }));
  }

  segments.forEach((item, index) => {
    const node = makeNode(item.metric, item.slice, year, quarter, revenueSlice, revenuePrior, {
      column: "segments",
      lane: "stack",
      stack: index,
    });
    nodes.push(node);
    if (revenueSlice) pushLink(links, node.id, "revenue", item.slice.value, "grey", false);
  });

  const totalCost = costItems.find((item) => isTotalCost(item.metric));
  const costDetails = totalCost ? costItems.filter((item) => item !== totalCost) : costItems.length > 1 ? costItems : [];
  const costTotal = totalCost || (costItems.length === 1 ? costItems[0] : null);

  const splitParts = [];
  if (grossItem) {
    const node = makeNode(grossItem.metric, grossItem.slice, year, quarter, revenueSlice, revenuePrior, {
      column: "split",
      lane: "high",
    });
    nodes.push(node);
    splitParts.push(grossItem.slice.value);
    if (revenueSlice) pushLink(links, "revenue", node.id, grossItem.slice.value, "green", true);
  }
  if (costTotal && !costDetails.length) {
    const node = makeNode(costTotal.metric, costTotal.slice, year, quarter, revenueSlice, revenuePrior, {
      column: "split",
      lane: "low",
    });
    nodes.push(node);
    splitParts.push(costTotal.slice.value);
    if (revenueSlice) pushLink(links, "revenue", node.id, costTotal.slice.value, "red", true);
  } else if (costTotal && costDetails.length) {
    const node = makeNode(costTotal.metric, costTotal.slice, year, quarter, revenueSlice, revenuePrior, {
      column: "split",
      lane: "low",
    });
    nodes.push(node);
    splitParts.push(costTotal.slice.value);
    if (revenueSlice) pushLink(links, "revenue", node.id, costTotal.slice.value, "red", true);
    const detailValues = [];
    costDetails.forEach((item, index) => {
      const detail = makeNode(item.metric, item.slice, year, quarter, revenueSlice, revenuePrior, {
        column: "operate",
        lane: "low",
        stack: index,
      });
      nodes.push(detail);
      detailValues.push(item.slice.value);
      pushLink(links, node.id, detail.id, item.slice.value, "red", true);
    });
    track(node.id, costTotal.slice.value, detailValues);
    addRemainderNode(nodes, links, { ...node, year, quarter, quarters: costTotal.slice.quarters }, detailValues, "operate", "low", costDetails.length + 1);
  } else {
    costDetails.forEach((item, index) => {
      const node = makeNode(item.metric, item.slice, year, quarter, revenueSlice, revenuePrior, {
        column: "split",
        lane: "low",
        stack: index,
      });
      nodes.push(node);
      splitParts.push(item.slice.value);
      if (revenueSlice) pushLink(links, "revenue", node.id, item.slice.value, "red", true);
    });
  }

  if (revenueSlice && splitParts.length) {
    track("revenue", revenueSlice.value, splitParts);
    const revenueNode = nodes.find((node) => node.id === "revenue");
    addRemainderNode(nodes, links, { ...revenueNode, year, quarter, quarters: revenueSlice.quarters }, splitParts, "split", "mid", 1);
  }

  const operateParts = [];
  if (operatingItem && grossItem) {
    const node = makeNode(operatingItem.metric, operatingItem.slice, year, quarter, revenueSlice, revenuePrior, {
      column: "operate",
      lane: "upper",
    });
    nodes.push(node);
    operateParts.push(operatingItem.slice.value);
    pushLink(links, grossItem.metric.id, node.id, operatingItem.slice.value, "green", true);
  } else if (operatingItem) {
    nodes.push(makeNode(operatingItem.metric, operatingItem.slice, year, quarter, revenueSlice, revenuePrior, {
      column: "operate",
      lane: "upper",
    }));
  }

  const detailColumn = opexItem && detailItems.length ? "opexDetail" : "operate";
  if (opexItem) {
    const node = makeNode(opexItem.metric, opexItem.slice, year, quarter, revenueSlice, revenuePrior, {
      id: opexItem.metric.id,
      column: "operate",
      lane: "lower",
      label: opexItem.metric.name || "Operating expenses",
    });
    nodes.push(node);
    if (grossItem) {
      operateParts.push(opexItem.slice.value);
      pushLink(links, grossItem.metric.id, node.id, opexItem.slice.value, "red", true);
    }
    if (detailItems.length) {
      const detailValues = [];
    detailItems.forEach((item, index) => {
      const lane = item.metric.role === "rd" ? "upper" : item.metric.role === "sga" ? "low" : "lower";
      const detail = makeNode(item.metric, item.slice, year, quarter, revenueSlice, revenuePrior, {
        column: detailColumn,
        lane,
        stack: item.metric.role === "rd" && detailColumn === "operate" ? 1 : item.metric.role === "sga" ? 0 : index,
      });
        nodes.push(detail);
        detailValues.push(item.slice.value);
        pushLink(links, node.id, detail.id, item.slice.value, "red", true);
      });
      track(node.id, opexItem.slice.value, detailValues);
      addRemainderNode(nodes, links, { ...node, year, quarter, quarters: opexItem.slice.quarters }, detailValues, detailColumn, "mid", 1);
    }
  } else if (detailItems.length && grossItem) {
    detailItems.forEach((item, index) => {
      const lane = item.metric.role === "rd" ? "upper" : item.metric.role === "sga" ? "low" : "lower";
      const detail = makeNode(item.metric, item.slice, year, quarter, revenueSlice, revenuePrior, {
        column: "operate",
        lane,
        stack: item.metric.role === "rd" ? 1 : item.metric.role === "sga" ? 0 : index,
      });
      nodes.push(detail);
      operateParts.push(item.slice.value);
      pushLink(links, grossItem.metric.id, detail.id, item.slice.value, "red", true);
    });
  } else {
    detailItems.forEach((item, index) => {
      nodes.push(makeNode(item.metric, item.slice, year, quarter, revenueSlice, revenuePrior, {
        column: "operate",
        lane: "lower",
        stack: index,
      }));
    });
  }

  if (grossItem && operateParts.length) {
    track(grossItem.metric.id, grossItem.slice.value, operateParts);
    const grossNode = nodes.find((node) => node.id === grossItem.metric.id);
    addRemainderNode(nodes, links, { ...grossNode, year, quarter, quarters: grossItem.slice.quarters }, operateParts, "operate", "mid", 0);
  }

  const bridgeSum = [];
  if (operatingItem && pretaxItem) bridgeSum.push(operatingItem.slice.value);
  const inflowColumn = detailItems.length ? detailColumn : "operate";
  bridgeItems.forEach((item, index) => {
    const signed = signedBridge(item.metric.role, item.slice.value);
    const node = makeNode(item.metric, item.slice, year, quarter, revenueSlice, revenuePrior, {
      column: inflowColumn,
      lane: "lower",
      stack: index,
    });
    nodes.push(node);
    if (pretaxItem && signed != null) {
      bridgeSum.push(signed);
      pushLink(links, node.id, pretaxItem.metric.id, signed, node.tone, true);
    }
  });

  if (pretaxItem) {
    const node = makeNode(pretaxItem.metric, pretaxItem.slice, year, quarter, revenueSlice, revenuePrior, {
      column: "bridge",
      lane: "mid",
    });
    nodes.push(node);
    if (operatingItem) pushLink(links, operatingItem.metric.id, node.id, operatingItem.slice.value, "green", true);
    if (bridgeSum.length) {
      track(node.id, pretaxItem.slice.value, bridgeSum);
      addRemainderNode(nodes, links, { ...node, year, quarter, quarters: pretaxItem.slice.quarters }, bridgeSum, "bridge", "low", 0);
    }
    const exit = [];
    if (taxItem) {
      const tax = makeNode(taxItem.metric, taxItem.slice, year, quarter, revenueSlice, revenuePrior, {
        column: "result",
        lane: "high",
      });
      nodes.push(tax);
      exit.push(taxItem.slice.value);
      pushLink(links, node.id, tax.id, taxItem.slice.value, "red", true);
    }
    if (netItem) {
      const net = makeNode(netItem.metric, netItem.slice, year, quarter, revenueSlice, revenuePrior, {
        column: "result",
        lane: "lower",
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
        "mid",
        0,
        node.id,
      );
    }
  } else {
    if (taxItem) {
      nodes.push(makeNode(taxItem.metric, taxItem.slice, year, quarter, revenueSlice, revenuePrior, {
        column: "result",
        lane: "high",
      }));
    }
    if (netItem) {
      nodes.push(makeNode(netItem.metric, netItem.slice, year, quarter, revenueSlice, revenuePrior, {
        column: "result",
        lane: "lower",
      }));
    }
  }

  const columns = [];
  for (const name of ["segments", "revenue", "split", "costDetail", "operate", "opexDetail", "bridge", "result"]) {
    if (nodes.some((node) => node.column === name)) columns.push(name);
  }

  const cards = statement.metrics
    .filter((metric) => metric.role === "deliveries" || metric.role === "production")
    .map((metric) => {
      const slice = periodSlice(metric, year, quarter);
      if (!slice) return null;
      const prior = priorSlice(metric, year, quarter, slice);
      const ratio = changeRatio(slice, prior);
      const usd = String(metric.unit || "").toLowerCase().startsWith("usd");
      return {
        id: metric.id,
        role: metric.role,
        label: metric.name,
        valueText: usd ? moneyText(slice.value) : Math.round(slice.value).toLocaleString("en-US"),
        changeText: formatChangePct(ratio),
        changeTone: changeTone(ratio),
        pending: slice.pending,
      };
    })
    .filter(Boolean);

  const estimate = covered.estimated.length > 0 || Boolean(revenueSlice?.estimate);
  const header = `${String(statement.ticker || "").trim()} FY${year}${estimate ? "E" : ""}${quarter ? ` Q${quarter}` : ""}`.trim();
  return {
    year,
    quarter,
    estimate,
    header,
    subtitle: basisText(
      year,
      quarter,
      revenueSlice?.quarters?.length ? revenueSlice.quarters : covered.quarters,
      revenueSlice?.estimatedQuarters || covered.estimated,
      hasPrior,
    ),
    nodes,
    links,
    balances,
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

function thickness(value, scale) {
  const share = scale ? Math.abs(value) / Math.abs(scale) : 0.15;
  return Math.max(4, Math.min(42, share * 54));
}

function placeInflows(nodes) {
  const inflows = nodes
    .filter((node) => ["interest_income", "interest_expense", "interest_other", "other_income"].includes(node.role))
    .sort((a, b) => (a.stack || 0) - (b.stack || 0));
  if (!inflows.length) return;
  const column = inflows[0].column;
  const pinned = nodes.filter((node) => node.column === column && node.pinned).sort((a, b) => a.y - b.y);
  const need = inflows.length * 82 - 12;
  let y = null;
  for (let index = 0; index < pinned.length - 1; index += 1) {
    const top = pinned[index].y + pinned[index].h + 20;
    const bottom = pinned[index + 1].y - 12;
    if (bottom - top >= need) {
      y = top;
      break;
    }
  }
  if (y == null) {
    const floor = pinned.length ? Math.max(...pinned.map((node) => node.y + node.h)) + 28 : ANCHOR + FAN + 88;
    y = Math.max(floor, ANCHOR + FAN + 88);
  }
  for (const node of inflows) {
    node.y = y;
    node.pinned = true;
    y += 82;
  }
}

function setAttach(node, y) {
  if (!node) return;
  node.y = y - ATTACH;
  node.pinned = true;
}

function placeCurves(nodes) {
  const byRole = (role) => nodes.find((node) => node.role === role) || null;
  const gross = byRole("gross_profit");
  const operating = byRole("operating_profit");
  const opex = byRole("opex");
  const pretax = byRole("pretax");
  const tax = byRole("tax");
  const net = byRole("net_profit");
  const rd = byRole("rd");
  const sga = byRole("sga");
  setAttach(gross, ANCHOR);
  setAttach(operating, ANCHOR - FAN);
  setAttach(opex, ANCHOR + FAN);
  setAttach(pretax, ANCHOR);
  setAttach(tax, ANCHOR - FAN);
  setAttach(net, ANCHOR + FAN);
  const opexAttach = opex ? ANCHOR + FAN : ANCHOR;
  if (rd && operating && rd.column === operating.column) setAttach(rd, ANCHOR);
  else setAttach(rd, opexAttach - FAN);
  if (sga && opex && sga.column === opex.column) setAttach(sga, ANCHOR + FAN * 2);
  else setAttach(sga, opexAttach + FAN);
  placeInflows(nodes);

  const byColumn = new Map();
  for (const node of nodes) {
    if (node.column === "segments" || node.column === "revenue") continue;
    const list = byColumn.get(node.column) || [];
    list.push(node);
    byColumn.set(node.column, list);
  }
  for (const list of byColumn.values()) {
    const pinned = list.filter((node) => node.pinned).sort((a, b) => a.y - b.y);
    const loose = list.filter((node) => !node.pinned).sort((a, b) => (a.stack || 0) - (b.stack || 0) || a.y - b.y);
    if (!loose.length) continue;
    const floor = pinned.length ? Math.max(...pinned.map((node) => node.y + node.h)) + 28 : ANCHOR + FAN + 80;
    let y = Math.max(floor, ANCHOR + FAN + 88);
    for (const node of loose) {
      node.y = y;
      y += 82;
    }
  }
}

export function layoutRevenueFlow(flow) {
  const nodes = (flow.nodes || []).map((node) => ({ ...node }));
  const links = (flow.links || []).map((link) => ({ ...link }));
  const columns = flow.columns || [];
  const segments = nodes.filter((node) => node.column === "segments").sort((a, b) => a.stack - b.stack);
  segments.forEach((node, index) => {
    node.x = 8;
    node.y = 12 + index * 78;
    node.w = NODE_W;
    node.h = 70;
  });
  const columnX = (column) => 8 + Math.max(0, columns.indexOf(column)) * COLUMN_PITCH;
  for (const node of nodes) {
    if (node.column === "segments") continue;
    node.x = columnX(node.column);
    node.y = (LANE_Y[node.lane] ?? 24) + (node.stack || 0) * STACK_GAP;
    node.w = node.role === "revenue" ? 18 : NODE_W;
    node.h = node.role === "revenue" ? Math.max(250, segments.length * 78) : 70;
    if (node.role === "revenue") node.y = 48;
  }
  placeCurves(nodes);
  const revenue = nodes.find((node) => node.id === "revenue");
  const scale = revenue?.value || Math.max(...nodes.map((node) => Math.abs(node.value)), 1);
  const incoming = revenue ? links.filter((link) => link.to === "revenue") : [];
  incoming.sort((a, b) => {
    const ay = nodes.find((node) => node.id === a.from)?.y || 0;
    const by = nodes.find((node) => node.id === b.from)?.y || 0;
    return ay - by;
  });
  if (revenue && incoming.length) {
    const total = incoming.reduce((sum, link) => sum + link.value, 0) || 1;
    let cursor = revenue.y;
    for (const link of incoming) {
      const band = Math.max(4, (revenue.h * link.value) / total);
      link.y2 = cursor + band / 2;
      link.t2 = band;
      cursor += band;
    }
  }
  const outgoing = revenue ? links.filter((link) => link.from === "revenue") : [];
  if (revenue && outgoing.length) {
    const total = outgoing.reduce((sum, link) => sum + link.value, 0) || 1;
    let cursor = revenue.y;
    const ordered = outgoing.slice().sort((a, b) => {
      const ay = nodes.find((node) => node.id === a.to)?.y || 0;
      const by = nodes.find((node) => node.id === b.to)?.y || 0;
      return ay - by;
    });
    for (const link of ordered) {
      const band = Math.max(4, (revenue.h * link.value) / total);
      link.y1 = cursor + band / 2;
      link.t1 = band;
      cursor += band;
    }
  }
  for (const link of links) {
    const source = nodes.find((node) => node.id === link.from);
    const target = nodes.find((node) => node.id === link.to);
    if (!source || !target) continue;
    const x1 = source.role === "revenue" ? source.x + 156 : source.x + source.w;
    const y1 = link.y1 || source.y + 28;
    const x2 = target.x;
    const y2 = link.y2 || target.y + 28;
    const band = link.t1 || thickness(link.value, scale);
    link.x1 = x1;
    link.y1 = y1;
    link.x2 = x2;
    link.y2 = y2;
    link.d = ribbonPath(x1, y1, x2, y2, link.t1 || band, link.t2 || band, link.strong);
  }
  const width = Math.max(640, 32 + columns.length * COLUMN_PITCH);
  const height = Math.max(560, ...nodes.map((node) => node.y + node.h + 28), 560);
  return { width, height, nodes, links };
}

function footnote(flow) {
  const notes = [];
  if (flow.pending) notes.push("Company metrics are pending review.");
  if (flow.placeholder) notes.push("Estimated quarters are the placeholder forecast, not a reviewed projection.");
  if (flow.derivedRevenue) notes.push("Revenue is the sum of reported segments because a consolidated total is not on file.");
  if ((flow.balances || []).some((row) => row.gap)) notes.push("Other is the remainder that makes a split add up to its parent.");
  if (!notes.length) notes.push("Reported figures only. Lines without a value for this period are left out.");
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
    header: statement.ticker,
    subtitle: "No income-statement period is on file",
    nodes: [],
    links: [],
    balances: [],
    columns: [],
    cards: [],
    derivedRevenue: false,
    pending: false,
    placeholder: false,
    empty: true,
  } : buildFlow(statement, resolved);
  flow.header = flow.empty && resolved.year == null
    ? statement.ticker
    : `${statement.ticker} ${flow.header.replace(statement.ticker, "").trim()}`.replace(/\s+/g, " ").trim();
  if (!flow.empty) {
    const label = `FY${resolved.year}${flow.estimate ? "E" : ""}${resolved.quarter ? ` Q${resolved.quarter}` : ""}`;
    flow.header = `${statement.ticker} ${label}`;
  }
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

function svg(doc, tag, attrs) {
  const node = doc.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value != null) node.setAttribute(key, String(value));
  }
  return node;
}

function drawLabel(doc, parent, node) {
  const x = node.role === "revenue" ? node.x + node.w + 10 : node.x;
  const y = node.role === "revenue" ? 16 : node.y;
  const name = svg(doc, "text", { class: "flow-name", x, y: y + 14 });
  name.textContent = node.label;
  const value = svg(doc, "text", { class: "flow-value-line", x, y: y + 36 });
  const amount = svg(doc, "tspan", { class: `flow-amount flow-amount-${node.tone}` });
  amount.textContent = node.valueText;
  value.append(amount);
  if (node.changeText) {
    const change = svg(doc, "tspan", { class: `flow-change is-${node.changeTone}`, dx: 8 });
    change.textContent = node.changeText;
    value.append(change);
  }
  parent.append(name, value);
  if (node.marginText) {
    const margin = svg(doc, "text", { class: `flow-margin is-${node.marginTone}`, x, y: y + 54 });
    margin.textContent = node.marginText;
    parent.append(margin);
  }
}

export function renderRevenueFlow(model, doc = document, options = {}) {
  const panel = el(doc, "section", "panel revenue-flow");
  panel.setAttribute("aria-label", `${model.ticker || "Company"} revenue flow`);
  const header = el(doc, "div", "flow-heading");
  header.append(el(doc, "h2", "flow-title", model.header || model.ticker || "Revenue flow"));
  header.append(el(doc, "p", "flow-subtitle", model.subtitle || ""));
  if (model.pending) header.append(el(doc, "span", "review-badge", "Pending review"));
  panel.append(header);

  if (model.periods.length) {
    const selector = el(doc, "div", "flow-selector");
    selector.setAttribute("role", "group");
    selector.setAttribute("aria-label", "Fiscal period");
    const years = el(doc, "div", "flow-years");
    for (const period of model.periods) {
      const button = el(doc, "button", "flow-period", `FY${period.year}${period.estimate ? "E" : ""}`);
      button.type = "button";
      button.dataset.year = String(period.year);
      button.dataset.quarter = "year";
      const selected = model.selection.year === period.year;
      button.setAttribute("aria-pressed", String(selected));
      button.addEventListener("click", () => options.onSelect?.(period.year, null));
      years.append(button);
    }
    const quarters = el(doc, "div", "flow-quarters");
    const current = model.periods.find((period) => period.year === model.selection.year);
    const full = el(doc, "button", "flow-period", "Full year");
    full.type = "button";
    full.dataset.year = String(model.selection.year ?? "");
    full.dataset.quarter = "year";
    full.setAttribute("aria-pressed", String(model.selection.quarter == null));
    full.addEventListener("click", () => options.onSelect?.(model.selection.year, null));
    quarters.append(full);
    for (const quarter of current?.quarters || []) {
      const button = el(doc, "button", "flow-period", `Q${quarter}`);
      button.type = "button";
      button.dataset.year = String(model.selection.year);
      button.dataset.quarter = String(quarter);
      button.setAttribute("aria-pressed", String(model.selection.quarter === quarter));
      button.addEventListener("click", () => options.onSelect?.(model.selection.year, quarter));
      quarters.append(button);
    }
    selector.append(years, quarters);
    panel.append(selector);
  }

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
    role: "img",
    "aria-label": model.header,
  });
  const revenue = layout.nodes.find((node) => node.role === "revenue");
  if (revenue) {
    figure.append(svg(doc, "rect", {
      class: "flow-revenue-bar",
      x: revenue.x,
      y: revenue.y,
      width: revenue.w,
      height: revenue.h,
      rx: 4,
    }));
  }
  for (const link of layout.links) {
    if (!link.d) continue;
    const path = svg(doc, "path", {
      class: `flow-link flow-link-${link.tone}`,
      d: link.d,
      "data-curve": link.strong ? "strong" : "gentle",
      "data-from": link.from,
      "data-to": link.to,
    });
    figure.append(path);
  }
  for (const node of layout.nodes) drawLabel(doc, figure, node);
  scroll.append(figure);
  panel.append(scroll);

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
    panel.append(cards);
  }
  panel.append(el(doc, "p", "flow-footnote", model.footnote || ""));
  return panel;
}
