// Rates & yields blocks (#89). Pure view models: missing prints stay unavailable, never zero.

export const CURVE_TENOR_ORDER = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"];
const REGIME_MIN_BP = 1;

export function finiteNumber(value) {
  if (typeof value === "boolean" || value == null) return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

export function formatBp(value) {
  const number = finiteNumber(value);
  if (number == null) return "--";
  const rounded = Math.round(number);
  if (rounded === 0) return "0";
  return `${rounded > 0 ? "+" : ""}${rounded}`;
}

export function formatYield(value) {
  const number = finiteNumber(value);
  return number == null ? "--" : `${number.toFixed(2)}%`;
}

export function curveRegime(tenors) {
  const row = (label) => (tenors || []).find((item) => item.tenor === label);
  const two = row("2Y");
  const ten = row("10Y");
  const twoNow = finiteNumber(two?.yield);
  const twoAgo = finiteNumber(two?.yield_1m);
  const tenNow = finiteNumber(ten?.yield);
  const tenAgo = finiteNumber(ten?.yield_1m);
  if (twoNow == null || twoAgo == null || tenNow == null || tenAgo == null) return null;
  const spread = ((tenNow - twoNow) - (tenAgo - twoAgo)) * 100;
  const level = (tenNow - tenAgo) * 100;
  if (Math.abs(spread) < REGIME_MIN_BP || Math.abs(level) < REGIME_MIN_BP) return null;
  if (spread > 0 && level < 0) return "bull steepening";
  if (spread > 0 && level > 0) return "bear steepening";
  if (spread < 0 && level < 0) return "bull flattening";
  return "bear flattening";
}

function statusOf(rates, key, fallbackSource) {
  const status = { ...(rates?.status?.[key] || {}) };
  if (status.source === undefined) status.source = fallbackSource;
  return status;
}

function whenText(lastAttempt) {
  return lastAttempt ? `last attempt ${lastAttempt}` : "no attempt recorded";
}

function unavailableMessage(block, status, fallback) {
  const source = status.source || "source not selected";
  const detail = status.detail || fallback;
  return `Unavailable · ${block} · ${source} · ${whenText(status.last_attempt)}. ${detail}`;
}

function errorMessage(block, status) {
  const source = status.source || "source not selected";
  const detail = status.detail || "Refresh data to retry.";
  return `${block} could not be loaded · ${source} · ${whenText(status.last_attempt)}. ${detail}`;
}

function linePath(values, width, height) {
  const finite = values.filter((value) => value != null);
  if (finite.length < 2) return "";
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  let connected = false;
  return values.map((value, index) => {
    if (value == null) {
      connected = false;
      return "";
    }
    const command = connected ? "L" : "M";
    connected = true;
    const x = 8 + (values.length === 1 ? 0 : index / (values.length - 1) * (width - 16));
    const y = 8 + (hi === lo ? 0.5 : (hi - value) / (hi - lo)) * (height - 16);
    return `${command}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

export function curvePaths(tenors) {
  const ordered = CURVE_TENOR_ORDER.map((label) => (tenors || []).find((item) => item.tenor === label) || { tenor: label });
  const today = ordered.map((item) => finiteNumber(item.yield));
  const ago = ordered.map((item) => finiteNumber(item.yield_1m));
  const both = [...today, ...ago].filter((value) => value != null);
  if (both.length < 2) return { today: "", ago: "" };
  const lo = Math.min(...both);
  const hi = Math.max(...both);
  const yOf = (value) => 8 + (hi === lo ? 0.5 : (hi - value) / (hi - lo)) * 84;
  const path = (series) => {
    let connected = false;
    return series.map((value, index) => {
      if (value == null) {
        connected = false;
        return "";
      }
      const command = connected ? "L" : "M";
      connected = true;
      const x = 8 + index / (series.length - 1) * 284;
      return `${command}${x.toFixed(1)},${yOf(value).toFixed(1)}`;
    }).join(" ");
  };
  return { today: path(today), ago: path(ago) };
}

function tenorViews(curve) {
  return CURVE_TENOR_ORDER.map((label) => {
    const row = (curve?.tenors || []).find((item) => item.tenor === label) || {};
    const change = finiteNumber(row.chg_1m_bp);
    return {
      tenor: label,
      yieldText: formatYield(row.yield),
      bpText: formatBp(row.chg_1m_bp),
      bpClass: change == null || Math.round(change) === 0 ? "bp-flat" : change > 0 ? "bp-up" : "bp-down",
    };
  });
}

export function curveView(rates, dashboardState) {
  const status = statusOf(rates, "curve", "FRED");
  const curve = rates?.curve;
  const ready = Boolean(curve?.tenors?.some((row) => finiteNumber(row.yield) != null));
  if (!ready && dashboardState === "loading") {
    return { kind: "loading", message: "Loading Treasury yield curve…", todayPath: "", agoPath: "", tenors: [] };
  }
  if (!ready && (dashboardState === "error" || status.state === "error")) {
    return { kind: "error", message: errorMessage("Treasury yield curve", status), todayPath: "", agoPath: "", tenors: [] };
  }
  if (!ready) {
    return {
      kind: "unavailable",
      message: unavailableMessage("Treasury yield curve", status, "Constant-maturity Treasury yields are not in the curve block yet."),
      todayPath: "",
      agoPath: "",
      tenors: [],
    };
  }
  const regime = curve.regime || curveRegime(curve.tenors);
  const paths = curvePaths(curve.tenors);
  return {
    kind: "ready",
    regime,
    dates: `${curve.date} vs ${curve.ref_date_1m}`,
    ariaLabel: `Treasury yield curve on ${curve.date} versus ${curve.ref_date_1m}. ${regime || "Regime unavailable"}.`,
    todayPath: paths.today,
    agoPath: paths.ago,
    tenors: tenorViews(curve),
  };
}

const FOMC_CLASS = { Cut: "fomc-cut", Hold: "fomc-hold", Hike: "fomc-hike" };

export function fomcSegments(outcomes) {
  return (outcomes || []).flatMap((row) => {
    const prob = finiteNumber(row?.prob);
    const label = String(row?.label || "").trim();
    if (!label || prob == null || prob < 0 || prob > 1) return [];
    return [{
      label,
      prob,
      className: FOMC_CLASS[label] || "fomc-other",
      width: `${Math.round(prob * 1000) / 10}%`,
      title: `${label} ${Math.round(prob * 100)}%`,
    }];
  });
}

export function fomcView(rates, dashboardState) {
  const status = statusOf(rates, "fomc", "Kalshi");
  const fomc = rates?.fomc;
  const segments = fomcSegments(fomc?.outcomes);
  const ready = Boolean(fomc?.meeting_date && segments.length);
  if (!ready && dashboardState === "loading") {
    return { kind: "loading", message: "Loading FOMC odds…", segments: [], history: [] };
  }
  if (!ready && (dashboardState === "error" || status.state === "error")) {
    return { kind: "error", message: errorMessage("FOMC odds", status), segments: [], history: [] };
  }
  if (!ready) {
    return {
      kind: "unavailable",
      message: unavailableMessage("FOMC odds", status, "Live collection waits until the Kalshi terms are accepted."),
      segments: [],
      history: [],
    };
  }
  const history = (fomc.history_14d || []).filter((row) => finiteNumber(row.cut) != null);
  const legend = segments.map((segment) => segment.title).join(" · ");
  return {
    kind: "ready",
    meeting: `${fomc.meeting_date} · Kalshi`,
    segments,
    legend,
    ariaLabel: `FOMC outcome probabilities. ${legend}`,
    history,
    sparkLabel: "Cut probability over the stored history",
  };
}

export function policyBars(path, currentRate) {
  const rows = (path || []).flatMap((row) => {
    const rate = finiteNumber(row?.implied_rate);
    const meeting = String(row?.meeting || "").trim();
    if (!meeting || rate == null) return [];
    return [{ meeting, rate, change: finiteNumber(row.chg_1m_bp) }];
  });
  if (rows.length < 4) return [];
  const reference = finiteNumber(currentRate);
  const levels = rows.map((row) => row.rate).concat(reference == null ? [] : [reference]);
  const lo = Math.min(...levels);
  const hi = Math.max(...levels);
  return rows.map((row) => ({
    meeting: row.meeting,
    rateText: formatYield(row.rate),
    changeText: formatBp(row.change),
    height: `${Math.round((hi === lo ? 0.5 : (row.rate - lo) / (hi - lo)) * 100)}%`,
    reference: reference == null ? null : `${Math.round((hi === lo ? 50 : (reference - lo) / (hi - lo)) * 100)}%`,
  }));
}

export function policyPathView(rates, dashboardState) {
  const status = statusOf(rates, "policy_path", null);
  const bars = policyBars(rates?.policy_path, rates?.policy_current_rate);
  if (!bars.length && dashboardState === "loading") {
    return { kind: "loading", message: "Loading implied policy path…", bars: [] };
  }
  if (!bars.length && (dashboardState === "error" || status.state === "error")) {
    return { kind: "error", message: errorMessage("Implied policy path", status), bars: [] };
  }
  if (!bars.length) {
    return {
      kind: "unavailable",
      message: unavailableMessage("Implied policy path", status, "Implied policy path source has not been selected."),
      bars: [],
    };
  }
  return {
    kind: "ready",
    note: status.detail || "Implied rates for the next meetings",
    bars,
    ariaLabel: `Implied policy path. ${bars.map((bar) => `${bar.meeting} ${bar.rateText}`).join(", ")}`,
  };
}

export function ratesPanelBlocks(rates, dashboardState) {
  return {
    curve: curveView(rates, dashboardState),
    fomc: fomcView(rates, dashboardState),
    policy: policyPathView(rates, dashboardState),
  };
}

export function sparklinePath(values) {
  return linePath(values.map((value) => finiteNumber(value)), 100, 28);
}
