import {
  PERIODS,
  chartValue,
  isNumericValue,
  periodQuote,
  resolveChartPeriod,
  sessionReturn,
  validBars,
} from "./chart-period.js";
import { resolveTrend, servingPriceTrend, trendLabel, trendSentence, trendTitle } from "./trend-state.js";
import { applyRefresh, dashboardBanner, hasDashboardData, networkError, readApiResponse } from "./api-response.js";
import { createAccountSettings } from "./account-settings.js";
import { applyTheme, overlayColor } from "./theme.js";
import {
  correlationDrift, driverChange, driverLabel, driverRows, driverTrend, driverValue,
  number as signalNumber, pressureSummary, RELEASE_WINDOWS, signed,
} from "./signals.js";
import { emailFromIdToken, fallbackSelection, parseSettingsHash, stripOrder } from "./settings-model.js";
import {
  CHART_LANES,
  chartSettingsFor,
  isMarketOverlay,
  overlayDefinition,
  overlayGroups,
  valuesForOverlay,
} from "./chart-overlays.js";

const root = document.querySelector("#app");
const svgNS = "http://www.w3.org/2000/svg";
const authKeys = { state: "oauth_state", verifier: "oauth_verifier", hash: "oauth_return_hash" };
const session = {
  config: null,
  accessToken: null,
  email: null,
  prefs: null,
  dashboard: null,
  chartData: {},
  chartDataState: {},
  chartDataError: {},
  // "loading" until the first /dashboard answer; "error" when it could not be read.
  dashboardState: "loading",
  // Last refresh failure. Ordinary redraws keep showing it until a refresh succeeds.
  dashboardError: "",
  status: null,
  // A settings save that failed after the dialog closed: { message, reopen }.
  settingsError: null,
  selected: null,
  mobileView: "chart",
  periodSave: false,
  chartSaveError: "",
};
const apiLabels = { dashboard: "Dashboard data", status: "Refresh status", prefs: "Your preferences" };
let prefsSaveQueue = Promise.resolve();

function node(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined && value !== null) element.textContent = String(value);
  return element;
}

function action(label, className, onClick) {
  const button = node("button", className, label);
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

function apiUrl(path) {
  return `${session.config.apiBaseUrl.replace(/\/+$/, "")}/${path}`;
}

function showGate(title, message, error = "", showSignIn = false) {
  root.replaceChildren();
  const gate = node("main", "gate");
  gate.append(node("p", "eyebrow", "INVESTOR DASHBOARD"));
  gate.append(node("h1", "", title));
  gate.append(node("p", "muted", message));
  if (error) gate.append(node("p", "error-copy", error));
  if (showSignIn) {
    const controls = node("div", "gate-actions");
    controls.append(action("Sign in", "button-primary", beginSignIn));
    gate.append(controls);
  }
  root.append(gate);
}

function randomBase64Url(size = 48) {
  const bytes = crypto.getRandomValues(new Uint8Array(size));
  let binary = "";
  bytes.forEach((byte) => (binary += String.fromCharCode(byte)));
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function challengeFor(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  let binary = "";
  new Uint8Array(digest).forEach((byte) => (binary += String.fromCharCode(byte)));
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function callbackUrl() {
  return session.config.callbackUrl || `${location.origin}/auth/callback`;
}

async function beginSignIn() {
  try {
    const verifier = randomBase64Url();
    const state = randomBase64Url(32);
    sessionStorage.setItem(authKeys.verifier, verifier);
    sessionStorage.setItem(authKeys.state, state);
    if (parseSettingsHash(location.hash)) sessionStorage.setItem(authKeys.hash, location.hash);
    const authorize = new URL("/oauth2/authorize", session.config.cognitoDomain);
    authorize.search = new URLSearchParams({
      client_id: session.config.clientId,
      response_type: "code",
      scope: "openid email profile",
      redirect_uri: callbackUrl(),
      state,
      code_challenge: await challengeFor(verifier),
      code_challenge_method: "S256",
    }).toString();
    location.assign(authorize);
  } catch (error) {
    showGate("Sign-in could not start", "This browser could not prepare a secure Cognito sign-in request.", error.message);
  }
}

async function completeSignIn() {
  const query = new URLSearchParams(location.search);
  const code = query.get("code");
  if (!code) return false;
  const expectedState = sessionStorage.getItem(authKeys.state);
  const verifier = sessionStorage.getItem(authKeys.verifier);
  sessionStorage.removeItem(authKeys.state);
  sessionStorage.removeItem(authKeys.verifier);
  if (!expectedState || query.get("state") !== expectedState || !verifier) {
    throw new Error("The sign-in state did not match. Start sign-in again.");
  }

  const response = await fetch(new URL("/oauth2/token", session.config.cognitoDomain), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: session.config.clientId,
      code,
      redirect_uri: callbackUrl(),
      code_verifier: verifier,
    }),
  });
  const tokens = await response.json();
  if (!response.ok || !tokens.access_token) {
    throw new Error(tokens.error_description || tokens.error || "Cognito did not return an access token.");
  }
  session.accessToken = tokens.access_token;
  session.email = emailFromIdToken(tokens.id_token);
  // Keep a #settings/... deep link that was saved before the Cognito round trip.
  const pendingHash = sessionStorage.getItem(authKeys.hash) || "";
  sessionStorage.removeItem(authKeys.hash);
  history.replaceState({}, "", location.pathname + pendingHash);
  return true;
}

function signOut() {
  session.accessToken = null;
  session.prefs = null;
  session.dashboard = null;
  const logout = new URL("/logout", session.config.cognitoDomain);
  logout.search = new URLSearchParams({
    client_id: session.config.clientId,
    logout_uri: session.config.logoutUrl || `${location.origin}/`,
  }).toString();
  location.assign(logout);
}

async function apiGet(path) {
  const what = apiLabels[path] || "The request";
  let response;
  try {
    response = await fetch(apiUrl(path), {
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        Accept: "application/json",
      },
      cache: "no-store",
    });
  } catch (cause) {
    throw networkError(what, cause);
  }
  return readApiResponse(response, what);
}

async function putPrefs(candidate) {
  let response;
  try {
    response = await fetch(apiUrl("prefs"), {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(candidate),
    });
  } catch (cause) {
    throw networkError("Saving preferences", cause);
  }
  return readApiResponse(response, "Saving preferences");
}

async function savePrefs(update) {
  session.prefs = { ...session.prefs, ...update };
  const save = prefsSaveQueue.catch(() => {}).then(async () => {
    const saved = await putPrefs({ ...session.prefs, version: session.prefs.version });
    session.prefs = { ...session.prefs, version: saved.version };
    session.chartSaveError = "";
    return saved;
  });
  prefsSaveQueue = save;
  return save;
}

async function loadChartData(ticker, force = false) {
  if (!ticker || (!force && ["ready", "loading"].includes(session.chartDataState[ticker]))) return;
  session.chartDataState[ticker] = "loading";
  if (session.selected === ticker) renderDashboard();
  try {
    session.chartData[ticker] = await apiGet(`chart/${encodeURIComponent(ticker)}`);
    session.chartDataState[ticker] = "ready";
    session.chartDataError[ticker] = "";
    session.chartSaveError = "";
  } catch (error) {
    session.chartDataState[ticker] = "error";
    session.chartData[ticker] = null;
    session.chartDataError[ticker] = error.message;
    if (error.status === 401) {
      settings.close();
      session.accessToken = null;
      showGate("Your session expired", "Sign in again to continue to your private dashboard.", "", true);
      return;
    }
  }
  if (session.selected === ticker) renderDashboard();
}

function updateChartSettings(ticker, settings) {
  const chartSettings = { ...(session.prefs.chart_settings || {}), [ticker]: settings };
  session.prefs = { ...session.prefs, chart_settings: chartSettings };
  session.chartSaveError = "";
  renderDashboard();
  savePrefs({ chart_settings: chartSettings }).catch((error) => {
    session.chartSaveError = error.message;
    if (session.selected === ticker) renderDashboard();
  });
}

// Displayed prices use close_raw (the actual traded close) and fall back to close.
// Chart paths and chip returns use chartValue (adj_close, then close) from chart-period.js.
function displayPrice(row) {
  if (!row) return null;
  return isNumericValue(row.close_raw) ? Number(row.close_raw) : isNumericValue(row.close) ? Number(row.close) : null;
}

function formatPrice(value) {
  if (!isNumericValue(value)) return "—";
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(
    Number(value),
  );
}

function formatPercent(value, digits = 2) {
  if (!isNumericValue(value)) return "—";
  const number = Number(value) * 100;
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}%`;
}

function formatTime(value, timeZone) {
  if (!value) return "not yet collected";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "timestamp unavailable";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  }).format(date);
}

function selectedData() {
  return session.dashboard?.tickers?.[session.selected] || null;
}

function returns(history, offset) {
  return sessionReturn(validBars(history), offset);
}

function polarity(value) {
  if (!isNumericValue(value) || Number(value) === 0) return "neutral";
  return Number(value) > 0 ? "positive" : "negative";
}

function seriesPath(values, xAt, yAt) {
  let path = "";
  let drawing = false;
  values.forEach((value, index) => {
    if (!isNumericValue(value)) {
      drawing = false;
      return;
    }
    path += `${drawing ? "L" : "M"}${xAt(index).toFixed(2)} ${yAt(Number(value), index).toFixed(2)} `;
    drawing = true;
  });
  return path.trim();
}

function scalePercent(values) {
  const first = values.find(isNumericValue);
  if (!isNumericValue(first) || Number(first) === 0) return values.map(() => null);
  return values.map((value) => (isNumericValue(value) ? (Number(value) / Number(first) - 1) * 100 : null));
}

function scaleIndicator(values, inverse) {
  const numeric = values.filter(isNumericValue).map(Number);
  if (!numeric.length) return values.map(() => null);
  const min = Math.min(...numeric);
  const max = Math.max(...numeric);
  const spread = max - min || 1;
  return values.map((value) => {
    if (!isNumericValue(value)) return null;
    const normalized = ((Number(value) - min) / spread) * 100;
    return inverse ? 100 - normalized : normalized;
  });
}

function drawChart(history, ticker, periodId = "1M", overlayIds = [], chartContext = {}) {
  if (!history?.length) return null;
  const bars = history;
  const sourcePrices = bars.map(chartValue);
  if (sourcePrices.filter(isNumericValue).length < 2) return null;
  const marketSelected = overlayIds.some(isMarketOverlay);
  const firstPrice = sourcePrices.find(isNumericValue);
  const compareMarkets = marketSelected && isNumericValue(firstPrice) && Number(firstPrice) !== 0;
  const prices = compareMarkets ? scalePercent(sourcePrices) : sourcePrices;
  const configured = overlayIds.map((id) => {
    const definition = overlayDefinition(id);
    const values = valuesForOverlay(id, { ...chartContext, bars });
    const scaled = compareMarkets && (definition?.kind === "market" || definition?.kind === "average")
      ? scalePercent(values)
      : definition?.kind === "market" || definition?.kind === "average"
        ? values
        : scaleIndicator(values, definition?.inverse);
    return { id, definition, values, scaled };
  });
  const priceNumeric = prices.filter(isNumericValue).map(Number);
  const axisValues = [...priceNumeric];
  if (compareMarkets) {
    configured.filter((item) => item.definition?.kind === "market").forEach((item) => {
      axisValues.push(...item.scaled.filter(isNumericValue).map(Number));
    });
  } else {
    configured.filter((item) => item.definition?.kind === "average").forEach((item) => {
      axisValues.push(...item.scaled.filter(isNumericValue).map(Number));
    });
  }
  const min = Math.min(...axisValues);
  const max = Math.max(...axisValues);
  const spread = max - min || 1;
  const left = 70;
  const right = 890;
  const top = 18;
  const bottom = 178;
  const xAt = (index) => left + (index / (bars.length - 1)) * (right - left);
  const yValue = (value) => bottom - ((value - min) / spread) * (bottom - top);
  const yNormalized = (value) => bottom - (value / 100) * (bottom - top);
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("class", "price-chart");
  svg.setAttribute("viewBox", "0 0 940 210");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${ticker} ${periodId} chart, ${bars.length} observations`);
  svg.dataset.period = periodId;
  svg.dataset.sessions = String(bars.length);
  svg.dataset.scaleMode = compareMarkets ? "percent" : "price";
  const title = document.createElementNS(svgNS, "title");
  title.textContent = `${ticker} ${periodId} daily close and selected overlays`;
  const lastPrice = sourcePrices.filter(isNumericValue).at(-1);
  const desc = document.createElementNS(svgNS, "desc");
  desc.textContent = compareMarkets
    ? `Price and market overlays shown as percent change from the start of the selected period. Latest price ${formatPrice(displayPrice(bars.at(-1)))}.`
    : `Adjusted price range ${formatPrice(min)} to ${formatPrice(max)}. Latest price ${formatPrice(displayPrice(bars.at(-1)))}. Indicator overlays use normalized scales.`;
  svg.append(title, desc);

  for (let index = 0; index < 4; index += 1) {
    const y = top + ((bottom - top) / 3) * index;
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("class", "chart-grid-line");
    line.setAttribute("x1", String(left));
    line.setAttribute("x2", String(right));
    line.setAttribute("y1", String(y));
    line.setAttribute("y2", String(y));
    svg.append(line);
    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", "4");
    label.setAttribute("y", String(y + 4));
    label.setAttribute("fill", "var(--muted)");
    label.setAttribute("font-size", "11");
    label.setAttribute("font-family", "IBM Plex Mono, monospace");
    label.textContent = compareMarkets ? `${(max - ((max - min) / 3) * index).toFixed(1)}%` : formatPrice(max - ((max - min) / 3) * index);
    svg.append(label);
  }

  const pricePath = seriesPath(prices, xAt, (value) => yValue(value));
  const area = document.createElementNS(svgNS, "path");
  area.setAttribute("class", "chart-area");
  area.setAttribute("d", `${pricePath} L${right} ${bottom} L${left} ${bottom} Z`);
  const priceLine = document.createElementNS(svgNS, "path");
  priceLine.setAttribute("class", "chart-path");
  priceLine.setAttribute("d", pricePath);
  svg.append(area, priceLine);

  configured.forEach(({ id, definition, scaled }) => {
    if (!definition || !scaled.some(isNumericValue)) return;
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("class", "chart-overlay-path");
    path.setAttribute("d", seriesPath(scaled, xAt, (value) => definition.kind === "macro" || definition.kind === "fundamental" ? yNormalized(value) : yValue(value)));
    path.setAttribute("stroke", overlayColor(definition.color, session.prefs.display.theme));
    path.setAttribute("stroke-dasharray", definition.kind === "average" ? "none" : "5 4");
    path.dataset.overlay = id;
    svg.append(path);
  });

  if (Number.isFinite(lastPrice) && firstPrice !== 0) {
    const latestIndex = sourcePrices.length - 1;
    const latestValue = prices[latestIndex];
    if (isNumericValue(latestValue)) {
      const marker = document.createElementNS(svgNS, "circle");
      marker.setAttribute("cx", String(xAt(latestIndex)));
      marker.setAttribute("cy", String(yValue(latestValue)));
      marker.setAttribute("r", "4");
      marker.setAttribute("fill", "var(--surface)");
      marker.setAttribute("stroke", "var(--price)");
      marker.setAttribute("stroke-width", "2");
      svg.append(marker);
    }
  }
  [
    { bar: bars[0], x: left, anchor: "start" },
    { bar: bars.at(-1), x: right, anchor: "end" },
  ].forEach(({ bar, x, anchor }) => {
    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", String(x));
    label.setAttribute("y", "202");
    label.setAttribute("fill", "var(--muted)");
    label.setAttribute("font-size", "10");
    label.setAttribute("font-family", "IBM Plex Mono, monospace");
    label.setAttribute("text-anchor", anchor);
    const stamp = String(bar?.ts || bar?.date || "");
    label.textContent = periodId === "1D" && stamp.includes("T") ? stamp.slice(11, 16) : stamp.slice(0, 10);
    svg.append(label);
  });
  return svg;
}

function sectionHeader(title, subtitle = "") {
  const header = node("div", "panel-header");
  const copy = node("div");
  copy.append(node("h2", "", title));
  if (subtitle) copy.append(node("p", "panel-subtitle", subtitle));
  header.append(copy);
  return header;
}

function renderWatchlist() {
  const list = node("nav", "watchlist");
  list.setAttribute("aria-label", "Watchlist");
  const { pinned, others } = stripOrder(session.prefs || {});
  [...pinned, ...others].forEach((ticker, index) => {
    if (index === pinned.length && pinned.length && others.length) {
      const divider = node("span", "watchlist-divider");
      divider.setAttribute("aria-hidden", "true");
      list.append(divider);
    }
    const record = session.dashboard?.tickers?.[ticker];
    const history = record?.price_history || [];
    const latest = history.at(-1);
    const button = node("button", "ticker-button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(ticker === session.selected));
    button.setAttribute("aria-label", `${ticker}, ${latest ? formatPrice(displayPrice(latest)) : "no price data"}`);
    button.addEventListener("click", () => {
      session.selected = ticker;
      renderDashboard();
      loadChartData(ticker);
    });
    const symbol = node("span", "ticker-symbol", ticker);
    if (pinned.includes(ticker)) {
      const star = node("span", "ticker-pin", "★");
      star.setAttribute("aria-hidden", "true");
      symbol.prepend(star);
    }
    button.append(symbol);
    button.append(
      node(
        "span",
        `ticker-meta ${polarity(returns(history, 1))}`,
        latest ? `${formatPrice(displayPrice(latest))} · ${formatPercent(returns(history, 1))}` : "Waiting for history",
      ),
    );
    list.append(button);
  });
  const manage = node("button", "ticker-button ticker-manage");
  manage.type = "button";
  manage.dataset.opener = "strip";
  manage.setAttribute("aria-label", "My tickers: add, pin or reorder");
  manage.append(node("span", "ticker-symbol", "+ Add / pin"), node("span", "ticker-meta", "My tickers"));
  manage.addEventListener("click", () => settings.open("tickers", "strip"));
  list.append(manage);
  return list;
}

function periodDirection(value) {
  const tone = polarity(value);
  if (tone === "positive") return "up";
  if (tone === "negative") return "down";
  return "flat";
}

function disabledPeriodTip(period, quote) {
  const start = quote.historyStarts ? `, history starts ${quote.historyStarts}` : "";
  const have = `${quote.sessionsAvailable} session${quote.sessionsAvailable === 1 ? "" : "s"} available${start}`;
  if (period.id === "5Y") return `Not enough history yet · 5Y needs about five years of daily closes (${have})`;
  return `Not enough history yet · ${period.id} needs ${quote.needed} sessions (${have})`;
}

function renderPeriodChips(history, activeId, onSelect, intraday) {
  const group = node("div", "period-chips");
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", "Period: return over each period; select one to chart it");
  PERIODS.forEach((period) => {
    const quote = periodQuote(history, period.id);
    const selected = quote.available && period.id === activeId;
    const button = node("button", "period-chip");
    button.type = "button";
    button.dataset.period = period.id;
    button.setAttribute("aria-pressed", selected ? "true" : "false");
    const liveReturn = period.id === "1D" && isNumericValue(intraday?.change_pct) ? Number(intraday.change_pct) : null;
    const shownReturn = liveReturn == null ? quote.returnValue : liveReturn;
    const valueClass = quote.available || liveReturn != null ? polarity(shownReturn) : "neutral";
    const valueText = quote.available || liveReturn != null ? formatPercent(shownReturn, 1) : "—";
    button.append(node("span", "period-label", period.id), node("span", `period-return mono ${valueClass}`, valueText));
    if (!quote.available) {
      const reason = disabledPeriodTip(period, quote);
      button.setAttribute("aria-disabled", "true");
      button.title = reason;
      button.setAttribute("aria-label", `${period.id}, ${reason}`);
      button.addEventListener("click", (event) => event.preventDefault());
    } else {
      const shown = selected ? "shown on the chart" : "click to show this period on the chart";
      button.title = `${period.id} return ${valueText} · ${shown}`;
      button.setAttribute("aria-label", `${period.id} return ${valueText}, ${periodDirection(shownReturn)}, ${shown}`);
      button.addEventListener("click", () => onSelect(period.id));
    }
    group.append(button);
  });
  return group;
}

function renderTrend(history, tickerData, ticker) {
  const trend = resolveTrend(history, servingPriceTrend(tickerData, ticker));
  const tone =
    trend.state === "uptrend" ? "up" : trend.state === "downtrend" ? "down" : trend.state === "range" ? "range" : "insufficient";
  const row = node("div", "trend-row");
  const pill = node("span", `trend-pill ${tone}`, trendLabel(trend));
  pill.dataset.ticker = ticker || "";
  pill.dataset.state = trend.state || "insufficient";
  if (trend.since) pill.dataset.since = trend.since;
  if (trend.vsMa20 != null) pill.dataset.vsMa20 = String(trend.vsMa20);
  pill.tabIndex = 0;
  pill.title = trendTitle(trend, formatPrice, formatPercent);
  pill.setAttribute("aria-label", trendSentence(trend, formatPercent));
  row.append(pill);
  if (trend.ma20 != null && trend.vsMa20 != null) {
    const vs = node("span", "trend-vs");
    vs.setAttribute("aria-hidden", "true");
    vs.append(document.createTextNode("vs 20-day avg "));
    vs.append(node("span", `mono ${polarity(trend.vsMa20)}`, formatPercent(trend.vsMa20, 1)));
    row.append(vs);
  }
  return row;
}

function renderOverlayControls(tickerData, chartData, chartDataState, onSettingsChange) {
  const panel = node("section", "chart-controls");
  const settings = chartSettingsFor(session.prefs, session.selected);
  const groups = overlayGroups(tickerData, chartData, session.dashboard);
  const activeGroup = groups.find((group) => group.id === session.overlayGroup) || groups[0];
  const tabs = node("div", "overlay-tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Chart overlay groups");
  groups.forEach((group) => {
    const button = node("button", "overlay-tab");
    button.type = "button";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(group.id === activeGroup?.id));
    button.setAttribute("aria-controls", "overlay-options");
    button.textContent = group.label;
    button.addEventListener("click", () => {
      session.overlayGroup = group.id;
      renderDashboard();
      root.querySelector(`[data-overlay-tab="${group.id}"]`)?.focus();
    });
    button.dataset.overlayTab = group.id;
    tabs.append(button);
  });
  const group = node("div", "overlay-options");
  group.id = "overlay-options";
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", `${activeGroup?.label || "Available"} chart overlays`);
  const overlays = activeGroup?.overlays || [];
  if (overlays.length) {
    const allSelected = overlays.every((overlay) => settings.overlays.includes(overlay.id));
    const allButton = node("button", "overlay-chip overlay-all", allSelected ? "Clear group" : "Add group");
    allButton.type = "button";
    allButton.addEventListener("click", () => {
      const selected = new Set(settings.overlays);
      if (allSelected) overlays.forEach((overlay) => selected.delete(overlay.id));
      else overlays.forEach((overlay) => selected.add(overlay.id));
      onSettingsChange({ ...settings, overlays: [...selected].slice(-5) });
    });
    group.append(allButton);
  }
  overlays.forEach((overlay) => {
    const selected = settings.overlays.includes(overlay.id);
    const button = node("button", `overlay-chip${selected ? " selected" : ""}`);
    button.type = "button";
    button.setAttribute("aria-pressed", String(selected));
    button.title = selected ? `Remove ${overlay.label} from the chart` : `Add ${overlay.label} to the chart`;
    button.dataset.overlay = overlay.id;
    button.append(node("span", "overlay-swatch", ""));
    button.querySelector(".overlay-swatch").style.setProperty("--overlay-color", overlayColor(overlay.color, session.prefs.display.theme));
    button.append(document.createTextNode(overlay.label));
    button.addEventListener("click", () => {
      const next = selected ? settings.overlays.filter((id) => id !== overlay.id) : [...settings.overlays.slice(-4), overlay.id];
      onSettingsChange({ ...settings, overlays: next });
      root.querySelector(`[data-overlay="${overlay.id}"]`)?.focus();
    });
    group.append(button);
  });
  if (!overlays.length) group.append(node("span", "overlay-empty", chartDataState === "loading" ? "Loading available series…" : "No series available in this group."));
  panel.append(tabs, group);
  if (settings.overlays.length) {
    const selection = node("p", "chart-control-note", `${settings.overlays.length} of 5 overlays · market groups compare percentage change; indicators are normalized`);
    panel.append(selection);
  }

  const laneGroup = node("div", "lane-controls");
  laneGroup.setAttribute("role", "group");
  laneGroup.setAttribute("aria-label", "Under-chart lanes");
  CHART_LANES.forEach((lane) => {
    const selected = settings.lanes.includes(lane.id);
    const button = node("button", `lane-toggle${selected ? " selected" : ""}`, lane.label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(selected));
    button.addEventListener("click", () => {
      const lanes = selected ? settings.lanes.filter((id) => id !== lane.id) : [...settings.lanes, lane.id];
      onSettingsChange({ ...settings, lanes });
    });
    laneGroup.append(button);
  });
  panel.append(laneGroup);
  if (session.chartSaveError) {
    const error = node("p", "chart-save-error", `Chart settings could not be saved: ${session.chartSaveError}`);
    error.setAttribute("role", "alert");
    panel.append(error);
  }
  return panel;
}

function alignedLaneValues(points, bars, key) {
  const ordered = [...(points || [])]
    .filter((point) => point.date && isNumericValue(point[key]))
    .sort((left, right) => String(left.date).localeCompare(String(right.date)));
  let previous = null;
  let pointIndex = 0;
  return bars.map((bar) => {
    const day = String(bar.date || bar.ts || "").slice(0, 10);
    while (pointIndex < ordered.length && String(ordered[pointIndex].date).slice(0, 10) <= day) {
      previous = Number(ordered[pointIndex][key]);
      pointIndex += 1;
    }
    return previous;
  });
}

function renderLane(lane, bars, tickerData, chartData, chartDataState) {
  const row = node("section", "chart-lane");
  row.setAttribute("aria-label", `${lane.label} chart lane`);
  const info = node("div", "lane-info");
  info.append(node("h3", "lane-title", lane.label));
  let values = [];
  let suffix = "";
  if (lane.id === "VOL") {
    values = bars.map((bar) => isNumericValue(bar.volume) ? Number(bar.volume) : null);
    suffix = "shares · IEX volume where reported";
  } else if (lane.id === "SI") {
    const percentagePoints = (chartData?.short_interest || []).filter((point) => isNumericValue(point.short_pct_denominator));
    const latestDenominator = percentagePoints.at(-1)?.denominator_type;
    values = alignedLaneValues(
      chartData?.short_interest || [],
      bars,
      percentagePoints.length ? "short_pct_denominator" : "shares_short",
    );
    suffix = latestDenominator === "estimated_public_float"
      ? "Short % of estimated public float · SEC market value ÷ measurement-date close"
      : percentagePoints.length
        ? "Short % of shares outstanding proxy · SEC public float unavailable"
        : "Shares sold short · denominator data unavailable";
  } else if (lane.id === "OPT") {
    values = alignedLaneValues(chartData?.options || [], bars, "put_call_volume_ratio");
    suffix = "Put/call volume ratio · IV30 shown when available";
  } else {
    values = alignedLaneValues(chartData?.macro_pressure || [], bars, "value");
    suffix = `Net macro tailwind (+) or headwind (−) for ${session.selected} · −1 to +1`;
  }
  const valid = values.filter(isNumericValue);
  if (!valid.length) {
    const message = chartDataState === "loading"
      ? "Loading lane data…"
      : chartDataState === "error"
        ? "Lane data could not be loaded. Refresh data to retry."
        : lane.id === "VOL"
          ? "Volume is unavailable for this period."
          : lane.id === "SI"
            ? "FINRA short-interest or SEC float/share data is not available yet."
            : lane.id === "OPT"
              ? "Options history is unavailable for this period."
              : "Macro pressure is unavailable for this period.";
    info.append(node("p", "lane-subtitle", suffix));
    row.append(info, node("p", "data-state lane-empty", message));
    return row;
  }
  info.append(node("p", "lane-subtitle", suffix));
  const latest = valid.at(-1);
  let stat = lane.id === "VOL"
    ? `${Math.round(latest).toLocaleString()} sh`
    : lane.id === "PRESS"
      ? `${latest > 0 ? "+" : ""}${latest.toFixed(2)}`
      : lane.id === "SI"
        ? (chartData?.short_interest || []).some((point) => isNumericValue(point.short_pct_denominator))
          ? `${latest.toFixed(2)}%`
          : `${Math.round(latest).toLocaleString()} sh`
        : `${latest.toFixed(2)} P/C`;
  if (lane.id === "OPT") {
    const iv = alignedLaneValues(chartData?.options || [], bars, "iv30").filter(isNumericValue).at(-1);
    if (isNumericValue(iv)) stat += ` · IV30 ${(Number(iv) * 100).toFixed(0)}%`;
  }
  if (lane.id === "SI") {
    const daysToCover = alignedLaneValues(chartData?.short_interest || [], bars, "days_to_cover").filter(isNumericValue).at(-1);
    if (isNumericValue(daysToCover)) stat += ` · ${Number(daysToCover).toFixed(1)} DTC`;
  }
  info.append(node("strong", `lane-stat ${lane.id === "PRESS" ? polarity(latest) : ""}`.trim(), stat));

  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("class", `lane-chart lane-${lane.id.toLowerCase()}`);
  svg.setAttribute("viewBox", "0 0 940 68");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${lane.label} over ${bars.length} chart observations`);
  const title = document.createElementNS(svgNS, "title");
  title.textContent = `${lane.label}: ${stat}`;
  svg.append(title);
  const left = 70;
  const right = 890;
  const bottom = 64;
  const xAt = (index) => left + (index / Math.max(1, values.length - 1)) * (right - left);
  if (lane.id === "VOL") {
    const max = Math.max(...valid) || 1;
    values.forEach((value, index) => {
      if (!isNumericValue(value)) return;
      const height = (Number(value) / max) * 54;
      const bar = document.createElementNS(svgNS, "rect");
      bar.setAttribute("x", String(xAt(index) - Math.max(1, 7 - values.length / 100) / 2));
      bar.setAttribute("y", String(bottom - height));
      bar.setAttribute("width", String(Math.max(1, 7 - values.length / 100)));
      bar.setAttribute("height", String(height));
      bar.setAttribute("class", "lane-volume-bar");
      svg.append(bar);
    });
  } else {
    const min = lane.id === "PRESS" ? -1 : Math.min(...valid);
    const max = lane.id === "PRESS" ? 1 : Math.max(...valid);
    const spread = max - min || 1;
    const yAt = (value) => bottom - 6 - ((value - min) / spread) * 52;
    if (lane.id === "PRESS") {
      const zero = document.createElementNS(svgNS, "line");
      zero.setAttribute("x1", String(left));
      zero.setAttribute("x2", String(right));
      zero.setAttribute("y1", String(yAt(0)));
      zero.setAttribute("y2", String(yAt(0)));
      zero.setAttribute("class", "lane-zero-line");
      svg.append(zero);
    }
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("d", seriesPath(values, xAt, (value) => yAt(value)));
    path.setAttribute("class", `lane-line ${lane.id === "PRESS" ? "lane-pressure-line" : ""}`.trim());
    svg.append(path);
  }
  row.append(info, svg);
  return row;
}

function renderChartLanes(settings, bars, tickerData, chartData, chartDataState) {
  const lanes = node("div", "chart-lanes");
  settings.lanes.forEach((id) => {
    const lane = CHART_LANES.find((item) => item.id === id);
    if (lane) lanes.append(renderLane(lane, bars, tickerData, chartData, chartDataState));
  });
  return lanes;
}

function renderPricePanel(tickerData, chartData, chartDataState, onSelectPeriod, onSettingsChange) {
  const history = tickerData?.price_history || [];
  const bars = validBars(history);
  const chartSettings = chartSettingsFor(session.prefs, session.selected);
  const activeId = resolveChartPeriod(session.prefs.display?.chart_period, history);
  const quote = periodQuote(history, activeId);
  const intradayBars = (tickerData?.intraday?.bars || []).map((bar) => ({
    ts: bar.ts,
    date: bar.ts,
    close: bar.close,
    adj_close: bar.close,
    volume: bar.volume,
  }));
  const chartHistory = activeId === "1D" && intradayBars.length >= 2 ? intradayBars : quote.available ? quote.window : bars;
  const panel = node("section", "panel price-panel");
  const overview = node("div", "overview");
  const quoteBlock = node("div", "overview-quote");
  const live = tickerData?.intraday;
  const liveLast = isNumericValue(live?.last) ? Number(live.last) : null;
  quoteBlock.append(node("h2", "overview-kicker", `${session.selected} · ${liveLast == null ? "daily closes" : "live"}`));
  const latest = bars.at(-1);
  quoteBlock.append(node("p", "overview-price", liveLast == null ? (latest ? formatPrice(displayPrice(latest)) : "—") : formatPrice(liveLast)));
  quoteBlock.append(renderTrend(history, tickerData, session.selected));
  const asOf = latest?.date || tickerData?.price_as_of;
  const meta = node("p", "overview-meta");
  if (!latest) {
    meta.textContent = "No daily closes yet";
  } else if (quote.available) {
    meta.append(document.createTextNode(`As of ${asOf} · `));
    meta.append(node("span", polarity(quote.returnValue), `${formatPercent(quote.returnValue, 1)} ${activeId}`));
    meta.append(document.createTextNode(" on the chart"));
  } else {
    meta.textContent = `As of ${asOf}`;
  }
  quoteBlock.append(meta);
  overview.append(quoteBlock, renderPeriodChips(history, quote.available ? activeId : "", onSelectPeriod, tickerData?.intraday));
  panel.append(overview);
  panel.append(renderOverlayControls(tickerData, chartData, chartDataState, onSettingsChange));
  if (chartDataState === "error") {
    const error = node("p", "data-state error chart-data-error", "Historical overlay data could not be loaded. Use Refresh data to retry.");
    error.setAttribute("role", "alert");
    panel.append(error);
  }

  if (chartHistory.length >= 2) {
    const wrap = node("div", "chart-wrap");
    const chart = drawChart(chartHistory, session.selected, activeId, chartSettings.overlays, {
      tickerData,
      chartData,
      dashboard: session.dashboard,
    });
    if (chart) {
      wrap.append(chart);
      panel.append(wrap);
      const legend = node("div", "chart-legend");
      const values = chartHistory.map(chartValue);
      legend.append(
        node(
          "span",
          "",
          `${activeId} · ${formatPercent(activeId === "1D" && isNumericValue(live?.change_pct) ? live.change_pct : quote.returnValue, 1)} · ${chartHistory.length.toLocaleString()} ${activeId === "1D" && intradayBars.length >= 2 ? "hourly bars" : "sessions · daily closes"}`,
        ),
      );
      legend.append(
        node(
          "span",
          "",
          chart.dataset.scaleMode === "percent"
            ? "Price and market overlays · percent change from period start"
            : `Adjusted ${formatPrice(Math.min(...values))} – ${formatPrice(Math.max(...values))}`,
        ),
      );
      chartSettings.overlays.forEach((id) => {
        const definition = overlayDefinition(id);
        const series = valuesForOverlay(id, { bars: chartHistory, tickerData, chartData, dashboard: session.dashboard });
        const latestValue = series.filter(isNumericValue).at(-1);
        if (!definition || !isNumericValue(latestValue)) return;
        const label = node("span", "chart-legend-item");
        const swatch = node("span", "overlay-swatch");
        swatch.style.setProperty("--overlay-color", overlayColor(definition.color, session.prefs.display.theme));
        label.append(swatch);
        const shown = definition.kind === "market"
          ? formatPercent(Number(latestValue) / Number(series.find(isNumericValue)) - 1, 1)
          : definition.kind === "average"
            ? formatPrice(latestValue)
            : Number(latestValue).toFixed(2);
        label.append(document.createTextNode(`${definition.label} · ${shown}`));
        legend.append(label);
      });
      panel.append(legend);
    } else {
      panel.append(node("p", "data-state", "Price history is incomplete; waiting for more valid observations."));
    }
  } else {
    panel.append(
      node(
        "p",
        "data-state",
        session.dashboardState === "loading"
          ? "Loading daily closes…"
          : session.dashboardState === "error" && !tickerData
            ? "Daily closes could not be loaded. Use Try again above."
            : bars.length === 1
              ? "Only one daily close is available; a line chart needs at least two points."
              : "No historical closes are published yet. Start the initial backfill and this chart will fill as batches complete.",
      ),
    );
  }
  panel.append(renderChartLanes(chartSettings, chartHistory, tickerData, chartData, chartDataState));
  return panel;
}

function renderContracts(tickerData) {
  const contracts = tickerData?.contracts;
  if (!contracts) return null;
  const panel = node("section", "panel");
  panel.append(sectionHeader("Government contracts", "Trailing 12 months · SpaceX and Tesla awards"));
  const total = isNumericValue(contracts.ttm_obligated) ? formatPrice(contracts.ttm_obligated) : "—";
  panel.append(node("p", "news-score", `TTM obligated ${total}`));
  const list = node("ul", "news-list");
  (contracts.recent || []).slice(0, 5).forEach((row) => {
    const item = node("li", "news-item");
    const link = node("a", "news-title", `${row.agency || "Agency"} · ${row.award_id || ""}`);
    if (row.url) {
      link.href = row.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    item.append(link);
    const amount = isNumericValue(row.amount) ? formatPrice(row.amount) : "—";
    item.append(node("p", "news-meta", `${row.date || ""} · ${amount}`));
    list.append(item);
  });
  if (!list.childElementCount) {
    panel.append(node("p", "data-state", "No awards in the last 30 days."));
  } else {
    panel.append(list);
  }
  return panel;
}

function renderDrivers(tickerData) {
  const panel = node("section", "panel signals-panel");
  panel.dataset.mobilePanel = "signals";
  panel.id = "mobile-panel-signals";
  panel.append(sectionHeader("Macro signals", `${session.selected} · ${tickerData?.trend?.date || "latest published observations"}`));
  const drivers = driverRows(tickerData);
  if (!drivers.length && session.dashboardState === "loading") {
    panel.append(node("p", "data-state", "Loading driver trends…"));
    return panel;
  }
  if (!drivers.length && session.dashboardState === "error") {
    panel.append(node("p", "data-state", "Driver trends could not be loaded. Use Try again above."));
    return panel;
  }
  if (!drivers.length) {
    panel.append(node("p", "data-state", "Driver trends are unavailable until daily prices and macro observations have been processed."));
  } else {
    const body = node("div", "panel-body signals-body");
    const pressure = pressureSummary(tickerData);
    const headline = node("div", "signal-heading");
    headline.append(node("h3", "", "Net macro pressure"),
      node("span", `mono ${pressure.value == null ? "" : polarity(pressure.value)}`,
        `${pressure.label}${pressure.value == null ? "" : ` ${signed(pressure.value)}`}`));
    const gauge = node("div", "pressure-gauge");
    gauge.setAttribute("role", "img");
    gauge.setAttribute("aria-label", `Net macro pressure: ${pressure.label}, ${signed(pressure.value)} on a -1 to +1 scale`);
    const fill = node("span", `pressure-fill ${pressure.value != null && pressure.value < 0 ? "down" : "up"}`);
    fill.style.width = `${pressure.width}%`;
    fill.style.left = `${pressure.value != null && pressure.value < 0 ? 50 - pressure.width : 50}%`;
    gauge.append(fill);
    body.append(headline, gauge, node("p", "signal-note",
      `${pressure.linked} of ${pressure.total} observed drivers linked. Monthly releases use separate event windows.`));
    const missing = tickerData?.trend?.coverage?.missing || [];
    if (missing.length) body.append(node("p", "signal-note", `${missing.length} inputs unavailable: ${missing.map(driverLabel).join(", ")}.`));
    body.append(node("h3", "signal-label", `Top drivers for ${session.selected}`), renderDriverList(drivers.slice(0, 5)));
    const all = node("details", "signal-drawer");
    all.append(node("summary", "", `All driver trends · ${drivers.length} observations`), renderDriverList(drivers));
    body.append(all);
    panel.append(body);
  }
  return panel;
}

function renderDriverList(rows) {
  const list = node("ul", "signal-driver-list");
  rows.forEach(row => {
    const item = node("li", "signal-driver-row");
    const name = node("div", "signal-driver-copy");
    name.append(node("span", "driver-name", driverLabel(row.series_id)),
      node("span", "signal-note", `${driverValue(row)} · ${driverTrend(row)}`));
    if (row.obs_date) name.title = `Observed ${row.obs_date}; available ${row.available_date || row.obs_date}. Driver trend uses the 1M z-score, not price moving averages.`;
    const change = node("span", "signal-change mono", driverChange(row));
    change.title = `1-month change; z-score ${signed(row.z_1m)}; 1-year range ${signed(row.range_pct_1y, 0)}`;
    const effect = signalNumber(row.effect);
    const direction = effect == null ? "Link unavailable" : effect > 0 ? "Tailwind" : effect < 0 ? "Headwind" : "Neutral";
    const strength = node("span", `signal-strength ${effect == null ? "" : polarity(effect)}`, direction);
    const bars = node("span", "strength-bars");
    bars.setAttribute("aria-hidden", "true");
    const count = effect == null ? 0 : Math.abs(effect) >= 1 ? 3 : Math.abs(effect) >= 0.5 ? 2 : 1;
    for (let i = 0; i < 3; i++) bars.append(node("span", i < count ? "active" : ""));
    strength.prepend(bars);
    strength.title = effect == null ? "Needs finite 90-day correlation and 1-month z-score."
      : `${row.strength || "Measured"} link; effect ${signed(effect)} = correlation(90D) × z(1M). Association, not a causal forecast.`;
    strength.setAttribute("aria-label", `${direction}; ${count} of 3 strength bars`);
    item.append(name, change, strength);
    list.append(item);
  });
  return list;
}

function signalSparkline(points, color = "var(--price)", label = "Historical series") {
  const values = points.map(point => signalNumber(point.value));
  const finite = values.filter(value => value != null);
  if (finite.length < 2) return node("span", "signal-note", "History unavailable");
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 240 48");
  svg.setAttribute("class", "signal-sparkline");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  const lo = Math.min(...finite), hi = Math.max(...finite);
  let connected = false;
  const segments = values.map((value, i) => {
    if (value == null) { connected = false; return ""; }
    const command = connected ? "L" : "M";
    connected = true;
    return `${command}${4 + i / (values.length - 1) * 232},${44 - (hi === lo ? 0.5 : (value - lo) / (hi - lo)) * 40}`;
  });
  const path = document.createElementNS(svgNS, "path");
  path.setAttribute("d", segments.join(" "));
  path.setAttribute("stroke", color);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke-width", "2");
  svg.append(path);
  return svg;
}

function renderMacroPanels(tickerData, chartData, chartDataState) {
  const grid = node("div", "macro-panel-grid");
  const rows = driverRows(tickerData);
  const rates = rows.filter(row => ["DGS2", "DGS10", "DGS30", "T10Y2Y", "DFII10", "SOFR", "T10YIE"].includes(row.series_id));
  const ratesPanel = node("section", "panel");
  ratesPanel.dataset.mobilePanel = "signals";
  ratesPanel.append(sectionHeader("Rates & yields", "Latest available observations · 1M change in basis points"));
  const rateBody = node("div", "panel-body");
  if (!rates.length) rateBody.append(node("p", "data-state", "Rates are unavailable until D1 observations reach the trend build."));
  rates.forEach(row => {
    const entry = node("div", "rate-entry");
    const copy = node("div", "signal-heading");
    copy.append(node("span", "", driverLabel(row.series_id)),
      node("span", "mono", `${driverValue(row)} · ${driverChange(row)}`));
    entry.append(copy, signalSparkline((chartData?.macro_series?.[row.series_id] || []).slice(-63),
      "var(--price)", `${driverLabel(row.series_id)} over the last 63 stored sessions`));
    rateBody.append(entry);
  });
  ratesPanel.append(rateBody);
  const inflation = node("section", "panel");
  inflation.dataset.mobilePanel = "calendar";
  inflation.id = "mobile-panel-calendar";
  inflation.append(sectionHeader("Inflation & release links", `YoY trend · historical links to ${session.selected}`));
  const releaseBody = node("div", "panel-body");
  const links = tickerData?.release_links;
  const latest = (links?.latest || []).filter(row => /^(CORE_)?(CPI|PCE)_YOY$/.test(row.series_id));
  if (!latest.length) releaseBody.append(node("p", "data-state", "Release history is unavailable until M1 has published release-dated data."));
  latest.forEach(row => {
    const block = node("div", "release-block");
    block.append(node("h3", "signal-label", driverLabel(row.series_id)),
      node("p", "", `${signalNumber(row.yoy) == null ? "--" : Number(row.yoy).toFixed(2)}% YoY · ${row.trend_direction || "Trend unavailable"} · ${row.consecutive_releases || 0} releases`),
      node("p", "signal-note", `Released ${row.release_date || "--"} · ${row.surprise_basis === "consensus" ? "Consensus surprise" : "YoY change"} ${signed(row.surprise)}pp`));
    const windows = (links?.summaries || []).filter(summary => summary.series_id === row.series_id);
    windows.forEach(summary => {
      const numeric = signalNumber(summary.correlation_surprise);
      block.append(node("p", "release-window", `${RELEASE_WINDOWS[summary.window] || summary.window}: ${
        numeric == null ? (summary.n_releases >= 12
          ? "Link unavailable; insufficient usable history or variation"
          : "Link unavailable; at least 12 paired releases required")
          : `correlation ${signed(numeric)}`
      } · n=${summary.n_releases || 0}`));
    });
    releaseBody.append(block);
  });
  const calendar = session.dashboard?.releases?.next || [];
  if (calendar.length) {
    const drawer = node("details", "signal-drawer");
    drawer.append(node("summary", "", `Release calendar · ${calendar.length} upcoming`));
    calendar.forEach(row => drawer.append(node("p", "signal-note", `${row.series} · ${formatTime(row.release_ts, session.prefs.display.time_zone)}`)));
    releaseBody.append(drawer);
  }
  releaseBody.append(node("p", "signal-note", "Monthly releases are excluded from daily rolling correlations. Missing consensus uses YoY change, not a consensus surprise. Links describe historical association, not causation."));
  inflation.append(releaseBody);
  const drift = node("section", "panel");
  drift.dataset.mobilePanel = "signals";
  drift.append(sectionHeader("Correlation drift", `${session.selected} · 30D / 90D rolling correlations`));
  const drawer = node("details", "signal-drawer panel-body");
  drawer.append(node("summary", "", "Show correlation history and month-over-month drift"));
  if (chartDataState === "error") drawer.append(node("p", "data-state error", "Correlation history could not be loaded. Use Try again above."));
  else if (chartDataState === "loading") drawer.append(node("p", "data-state", "Loading correlation history…"));
  const daily = rows.filter(row => !row.series_id.endsWith("_YOY"));
  daily.forEach(row => {
    const history = chartData?.correlation_history?.[row.series_id] || [];
    const change = correlationDrift(row, history);
    const entry = node("div", "rate-entry");
    entry.append(node("h3", "signal-label", driverLabel(row.series_id)),
      node("p", "mono", `30D ${signed(row.corr_30d)} · 90D ${signed(row.corr_90d)}`),
      signalSparkline(history.map(point => ({value: point.corr_90d})), "var(--blue)", `${driverLabel(row.series_id)} 90-day correlation history`),
      node("p", "signal-note", change ? `${change.signFlip ? "Sign flip · " : ""}${change.stronger ? "Strengthening" : "Fading"} · 1M drift ${signed(change.delta)}` : "Drift unavailable; needs 22 linked sessions."));
    drawer.append(entry);
  });
  if (!daily.length) drawer.append(node("p", "data-state", "No daily driver correlations available yet."));
  drift.append(drawer);
  grid.append(ratesPanel, inflation, drift);
  return grid;
}

function renderNews(tickerData) {
  const panel = node("section", "panel");
  panel.append(sectionHeader("News & sentiment", "Headlines from the last 48 hours"));
  const news = tickerData?.news;
  if (!news && session.dashboardState === "loading") {
    panel.append(node("p", "data-state", "Loading headlines…"));
    return panel;
  }
  if (!news && session.dashboardState === "error") {
    panel.append(node("p", "data-state", "Headlines could not be loaded. Use Try again above."));
    return panel;
  }
  if (!news) {
    panel.append(node("p", "data-state", "News has not been collected yet."));
    return panel;
  }
  const body = node("div", "panel-body");
  const score = isNumericValue(news.sentiment_7d) ? Number(news.sentiment_7d) : null;
  const summary = node("p", "news-score");
  summary.append(document.createTextNode("7-day sentiment "));
  summary.append(node("span", `mono ${polarity(score)}`, score == null ? "—" : `${score > 0 ? "+" : ""}${score.toFixed(2)}`));
  body.append(summary);
  const headlines = Array.isArray(news.headlines) ? news.headlines.slice(0, 10) : [];
  if (!headlines.length) {
    body.append(node("p", "data-state", "No headlines in the last 48 hours."));
    panel.append(body);
    return panel;
  }
  const list = node("ul", "news-list");
  headlines.forEach((item) => {
    const row = node("li", "news-item");
    const link = node("a", "news-title", item.title || "Untitled");
    link.href = item.url || "#";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    row.append(link);
    const meta = node("p", "news-meta");
    meta.append(document.createTextNode(`${item.publisher || "Source"} · ${item.published_at || ""}`));
    if (item.label) {
      const tone = item.label === "bullish" ? "positive" : item.label === "bearish" ? "negative" : "neutral";
      meta.append(node("span", `news-label ${tone}`, item.label));
    }
    row.append(meta);
    list.append(row);
  });
  body.append(list);
  panel.append(body);
  return panel;
}

function renderFundamentals() {
  const panel = node("section", "panel");
  panel.append(sectionHeader("Company fundamentals", "Quarterly release data"));
  const rows = (session.dashboard?.fundamentals || [])
    .filter((row) => row.ticker === session.selected)
    .sort((a, b) => String(b.release_date).localeCompare(String(a.release_date)))
    .slice(0, 8);
  if (!rows.length && session.dashboardState === "loading") {
    panel.append(node("p", "data-state", "Loading quarterly fundamentals…"));
    return panel;
  }
  if (!rows.length && session.dashboardState === "error") {
    panel.append(node("p", "data-state", "Quarterly fundamentals could not be loaded. Use Try again above."));
    return panel;
  }
  if (!rows.length) {
    panel.append(node("p", "data-state", "No quarterly fundamentals have been published for this ticker yet."));
    return panel;
  }
  const scroll = node("div", "table-scroll");
  const table = node("table", "fundamentals-table");
  const caption = node("caption", "visually-hidden", `${session.selected} quarterly fundamentals`);
  const head = node("thead");
  const headerRow = node("tr");
  ["Metric", "Quarter", "Value", "Released"].forEach((label) => headerRow.append(node("th", "", label)));
  head.append(headerRow);
  const body = node("tbody");
  rows.forEach((row) => {
    const tr = node("tr");
    tr.append(node("td", "", String(row.metric).replaceAll("_", " ")));
    tr.append(node("td", "mono", row.fiscal_quarter || "—"));
    const value = isNumericValue(row.value)
      ? row.unit === "ratio"
        ? `${(Number(row.value) * 100).toFixed(1)}%`
        : Number(row.value).toLocaleString()
      : "—";
    tr.append(node("td", "mono", value));
    tr.append(node("td", "mono", row.release_date || "—"));
    body.append(tr);
  });
  table.append(caption, head, body);
  scroll.append(table);
  panel.append(scroll);
  return panel;
}

function renderFilings(tickerData) {
  const panel = node("section", "panel");
  panel.append(sectionHeader("Filings & events", "Latest SEC filings and the last 14 days of events"));
  const filings = tickerData?.filings;
  const events = session.dashboard?.events;
  if (!filings && !events && session.dashboardState === "loading") {
    panel.append(node("p", "data-state", "Loading filings…"));
    return panel;
  }
  if (!filings && !events && session.dashboardState === "error") {
    panel.append(node("p", "data-state", "Filings could not be loaded. Use Try again above."));
    return panel;
  }
  if (!filings && !events) {
    panel.append(node("p", "data-state", "Filings have not been collected yet."));
    return panel;
  }
  const list = node("ul", "news-list");
  (filings || []).forEach((row) => {
    const item = node("li", "news-item");
    const link = node("a", "news-title", `${row.form || "Filing"} · ${row.title || ""}`);
    if (row.url) {
      link.href = row.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    item.append(link);
    item.append(node("p", "news-meta", `${row.filed_at || ""} · ${row.class || "filing"}`));
    list.append(item);
  });
  (events || []).slice(0, 8).forEach((event) => {
    const item = node("li", "news-item");
    const link = node("a", "news-title", event.title || event.type || "Event");
    if (event.source_url) {
      link.href = event.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    item.append(link);
    item.append(node("p", "news-meta", `${event.event_ts || ""} · ${event.type || "event"}`));
    list.append(item);
  });
  if (!list.childElementCount) {
    panel.append(node("p", "data-state", "No filings or events in the current window."));
    return panel;
  }
  panel.append(list);
  return panel;
}

function renderDashboard() {
  const errorMessage = dashboardBanner(session);
  if (!session.prefs) return;
  session.selected = fallbackSelection(session.prefs, session.selected);
  root.replaceChildren();
  applyTheme(session.prefs.display);

  const header = node("header", "app-header");
  const brand = node("div", "brand-line");
  brand.append(node("h1", "", "Investor Dashboard"));
  const market = node("span", "market-badge");
  market.append(node("span", "dot ok"), document.createTextNode(" Private workspace"));
  brand.append(market);
  header.append(brand);
  const actions = node("div", "header-actions");
  const lastRefresh = session.dashboard?.generated_at
    ? `Data ${formatTime(session.dashboard.generated_at, session.prefs.display.time_zone)}`
    : "Refresh data";
  actions.append(action(lastRefresh, "", () => refreshData(true)));
  actions.append(settings.renderAccountButton());
  header.append(actions);
  root.append(header);
  root.append(renderWatchlist());

  if (session.settingsError) {
    const { message, reopen } = session.settingsError;
    const alert = node("p", "data-state error", `${message} `);
    alert.setAttribute("role", "alert");
    // Opening the dialog clears settingsError and redraws the page without this alert.
    alert.append(action("Open settings", "button-link", reopen));
    root.append(alert);
  }

  if (errorMessage) {
    const alert = node("p", "data-state error");
    alert.setAttribute("role", "alert");
    alert.textContent = `${errorMessage} `;
    alert.append(action("Try again", "button-link", () => refreshData(true)));
    root.append(alert);
  }

  const tickerData = selectedData();
  const chartData = session.chartData[session.selected] || null;
  const chartDataState = session.chartDataState[session.selected] || "loading";
  let notice;
  const onMessage = (message, isError = false) => {
    notice = node("p", `data-state${isError ? " error" : ""}`, message);
    notice.setAttribute("role", isError ? "alert" : "status");
    root.prepend(notice);
    window.setTimeout(() => notice?.remove(), 6000);
  };
  const onSelectPeriod = async (periodId) => {
    if (session.periodSave || session.prefs.display?.chart_period === periodId) return;
    session.periodSave = true;
    try {
      await savePrefs({ display: { ...session.prefs.display, chart_period: periodId } });
      // An older prefs response can omit chart_period; keep the click for this session.
      if (session.prefs.display?.chart_period !== periodId) {
        session.prefs = {
          ...session.prefs,
          display: { ...session.prefs.display, chart_period: periodId },
        };
      }
      renderDashboard();
      root.querySelector(`[data-period="${periodId}"]`)?.focus();
    } catch (error) {
      onMessage(error.message, true);
    } finally {
      session.periodSave = false;
    }
  };
  const mainGrid = node("main", "dashboard-grid");
  mainGrid.setAttribute("aria-label", `${session.selected} investor dashboard`);
  const primary = node("div", "primary-column");
  const price = renderPricePanel(tickerData, chartData, chartDataState, onSelectPeriod, (next) => updateChartSettings(session.selected, next));
  price.dataset.mobilePanel = "chart";
  price.id = "mobile-panel-chart";
  primary.append(price);
  primary.append(renderMacroPanels(tickerData, chartData, chartDataState));
  const contracts = renderContracts(tickerData);
  if (contracts) {
    contracts.dataset.mobilePanel = "more";
    primary.append(contracts);
  }
  const fundamentals = renderFundamentals();
  fundamentals.dataset.mobilePanel = "more";
  fundamentals.id = "mobile-panel-more";
  primary.append(fundamentals);
  const side = node("aside", "side-column");
  side.setAttribute("aria-label", "Macro drivers, news and filings");
  const news = renderNews(tickerData), filings = renderFilings(tickerData);
  news.dataset.mobilePanel = "more";
  filings.dataset.mobilePanel = "more";
  side.append(renderDrivers(tickerData), news, filings);
  mainGrid.append(primary, side);
  const mobileTabs = node("div", "mobile-view-tabs");
  mobileTabs.setAttribute("role", "tablist");
  mobileTabs.setAttribute("aria-label", "Dashboard sections");
  mainGrid.dataset.mobileView = session.mobileView;
  ["chart", "signals", "calendar", "more"].forEach(view => {
    const tab = action(view[0].toUpperCase() + view.slice(1), "", () => {
      session.mobileView = view;
      renderDashboard();
      root.querySelector(`#mobile-tab-${view}`)?.focus();
    });
    tab.id = `mobile-tab-${view}`;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", `mobile-panel-${view}`);
    tab.setAttribute("aria-selected", String(view === session.mobileView));
    tab.tabIndex = view === session.mobileView ? 0 : -1;
    tab.addEventListener("keydown", event => {
      const views = ["chart", "signals", "calendar", "more"];
      const index = views.indexOf(view);
      const next = event.key === "ArrowRight" ? (index + 1) % 4
        : event.key === "ArrowLeft" ? (index + 3) % 4 : event.key === "Home" ? 0 : event.key === "End" ? 3 : null;
      if (next == null) return;
      event.preventDefault();
      root.querySelector(`#mobile-tab-${views[next]}`)?.click();
    });
    mobileTabs.append(tab);
    if (window.matchMedia("(max-width: 640px)").matches) {
      const panel = mainGrid.querySelector(`#mobile-panel-${view}`);
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", tab.id);
    }
  });
  root.append(mobileTabs);
  root.append(mainGrid);
  const footer = node("footer", "dashboard-footer");
  footer.append(node("span", "", "Information for research; not investment advice."));
  footer.append(node("span", "", session.dashboard?.generated_at ? `Snapshot ${session.dashboard.generated_at}` : "Serving snapshot unavailable"));
  root.append(footer);

  if (!session.dashboard && !errorMessage) {
    root.querySelector(".price-panel-state")?.remove();
  }
  settings.refresh();
}

async function refreshData(showLoading) {
  if (showLoading) {
    const refreshButton = [...root.querySelectorAll("button")].find((button) => button.textContent.startsWith("Data "));
    if (refreshButton) {
      refreshButton.disabled = true;
      refreshButton.textContent = "Refreshing…";
    }
  }
  const [dashboardResult, statusResult] = await Promise.allSettled([apiGet("dashboard"), apiGet("status")]);
  if (dashboardResult.status === "fulfilled" && !hasDashboardData(dashboardResult.value)) {
    const error = new Error("Dashboard data arrived without any tickers. Try again.");
    error.status = 200;
    Object.assign(dashboardResult, { status: "rejected", reason: error });
  }
  const outcome = applyRefresh(session, dashboardResult, statusResult);
  session.dashboard = outcome.dashboard;
  session.dashboardState = outcome.dashboardState;
  session.status = outcome.status;
  [dashboardResult, statusResult]
    .filter((result) => result.status === "rejected")
    .forEach((result) => console.error("Dashboard refresh failed:", result.reason));
  if (outcome.unauthorized) {
    settings.close();
    session.accessToken = null;
    showGate("Your session expired", "Sign in again to continue to your private dashboard.", "", true);
    return;
  }
  session.dashboardError = outcome.dashboardError;
  renderDashboard();
  await loadChartData(session.selected, true);
}

async function start() {
  let loadedConfig;
  try {
    const response = await fetch("/config.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Runtime configuration is missing (${response.status}).`);
    loadedConfig = await response.json();
  } catch (error) {
    showGate("Dashboard is not configured", "Create config.json from config.example.json and fill in the deployed API and Cognito client values.", error.message);
    return;
  }

  const required = ["apiBaseUrl", "cognitoDomain", "clientId"];
  const missing = required.filter((key) => !loadedConfig[key] || loadedConfig[key].includes("your-"));
  if (missing.length) {
    showGate("Dashboard is not configured", "The application is deployed, but its public runtime configuration is incomplete.", `Missing: ${missing.join(", ")}`);
    return;
  }
  session.config = loadedConfig;

  try {
    await completeSignIn();
  } catch (error) {
    history.replaceState({}, "", location.pathname);
    showGate("Sign-in failed", "Your Cognito session could not be established.", error.message, true);
    return;
  }

  if (!session.accessToken) {
    showGate("Your market data, at a glance", "Sign in with your invited Cognito account to view the dashboard and manage your watchlist.", "", true);
    return;
  }

  try {
    session.prefs = await apiGet("prefs");
    session.selected = fallbackSelection(session.prefs, null);
    renderDashboard();
    window.addEventListener("hashchange", () => settings.openFromHash());
    settings.openFromHash();
    await refreshData(false);
  } catch (error) {
    if (error.status === 401) {
      showGate("Your session expired", "Sign in again to continue to your private dashboard.", "", true);
      return;
    }
    showGate("Dashboard could not load", "The preferences service did not return your account settings.", error.message, true);
  }
}

const settings = createAccountSettings({
  session,
  putPrefs,
  getPrefs: () => apiGet("prefs"),
  onPrefsChange: () => renderDashboard(),
  onTickerAdded: (ticker) => {
    session.selected = ticker;
    refreshData(false);
  },
  onUnauthorized: () => {
    settings.close();
    session.accessToken = null;
    showGate("Your session expired", "Sign in again to continue to your private dashboard.", "", true);
  },
  onSaveError: (message, reopen) => {
    session.settingsError = { message, reopen };
    renderDashboard();
  },
  signOut,
  formatPrice,
  formatPercent,
  formatTime,
  displayPrice,
  browserTimeZone: () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
});

applyTheme();
window.matchMedia("(max-width: 640px)").addEventListener("change", () => renderDashboard());
start();
