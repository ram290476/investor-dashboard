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
import { CHART_PLOT, barStamp, chartTicks, indexAtPlotX, plotX } from "./chart-scale.js";
import { applyTheme, overlayColor } from "./theme.js";
import { parseStockHash, routeFromLocation, routeFromPath, routeStateForLocation, stockHash, tickerResearchPath } from "./routes.js";
import { CATALYST_CATEGORIES, catalystCategoriesInWindow, catalystDateLabel, catalystMove, catalystRows, markerIndex, movingAverageRows, sensitivityRows, sortedDrivers } from "./roadmap.js";
import { legendItems, overlayCorrelation, seriesLineStyle } from "./chart-legend.js";
import { sparkline, sparklineModel, sparklineSummary } from "./sparkline.js";
import {
  correlationDrift, driverChange, driverLabel, driverRows, driverTrend, driverValue,
  number as signalNumber, pressureSummary, RELEASE_WINDOWS, signed,
} from "./signals.js";
import { drawerControls, emailFromIdToken, fallbackSelection, isPanelOpen, parseSettingsHash, stripOrder, withPanelState } from "./settings-model.js";
import {
  callsCopy,
  companyName,
  emptyKpiCopy,
  formatMetricValue,
  freshnessBadge,
  LONG_PRESS_MS,
  nextStockTab,
  stockPanels,
  STOCK_TABS,
} from "./stock-page.js";
import { curveView, fomcView, policyPathView } from "./rates-panel.js";
import { clockDelay, headerDate, marketLabel } from "./market-status.js";
import {
  CHART_LANES,
  OVERLAYS,
  addOverlay,
  alignedLaneValues,
  availableLanes,
  chartSettingsFor,
  fundamentalSummary,
  fundamentalObservation,
  isMarketOverlay,
  overlayDefinition,
  overlayGroups,
  laneValues,
  valuesForOverlay,
} from "./chart-overlays.js";

const root = document.querySelector("#app");
const chartAvailability = new Map();
const svgNS = "http://www.w3.org/2000/svg";
const authKeys = {
  state: "oauth_state",
  verifier: "oauth_verifier",
  hash: "oauth_return_hash",
  path: "oauth_return_path",
  dashboardTicker: "oauth_return_dashboard_ticker",
};
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
  dashboardSelection: null,
  route: { page: "dashboard" },
  mobileView: "chart",
  periodSave: false,
  chartSaveError: "",
  watchlistScroll: 0,
  catalystCategories: CATALYST_CATEGORIES.map(category => category.id),
  focusCatalyst: null,
  driverSort: "effect",
  // Inner disclosures that are not bottom drawers (all drivers, release calendar).
  innerDrawers: new Set(),
  panelSaveError: "",
  stockPage: {},
  stockPageState: {},
  stockTab: "overview",
};
const apiLabels = { dashboard: "Dashboard data", status: "Refresh status", prefs: "Your preferences" };
let prefsSaveQueue = Promise.resolve();
let watchlistResize;

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

function rememberInner(drawer, key) {
  drawer.open = session.innerDrawers.has(key);
  drawer.addEventListener("toggle", () => {
    if (!drawer.isConnected) return;
    if (drawer.open) session.innerDrawers.add(key);
    else session.innerDrawers.delete(key);
  });
  return drawer;
}

let panelSaveTimer = 0;

function schedulePanelSave() {
  window.clearTimeout(panelSaveTimer);
  panelSaveTimer = window.setTimeout(() => {
    savePrefs({ display: session.prefs.display }).then(() => {
      session.panelSaveError = "";
      root.querySelector("[data-panel-save-error]")?.remove();
    }).catch((error) => {
      if (error.status === 401) {
        settings.close();
        session.accessToken = null;
        showGate("Your session expired", "Sign in again to continue to your private dashboard.", "", true);
        return;
      }
      session.panelSaveError = error.message;
      renderDashboard();
    });
  }, 400);
}

function setPanelOpen(id, open) {
  const panels = withPanelState(session.prefs.display?.panels, id, open);
  session.prefs = { ...session.prefs, display: { ...session.prefs.display, panels } };
  schedulePanelSave();
}

function selectCatalyst(event) {
  session.focusCatalyst = event.id;
  if (!session.catalystCategories.includes(event.category)) session.catalystCategories.push(event.category);
  setPanelOpen("catalyst-calendar", true);
  session.mobileView = "calendar";
  renderDashboard();
  requestAnimationFrame(() => {
    const row = document.getElementById(`calendar-${event.id}`);
    const behavior = matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth";
    row?.focus();
    row?.scrollIntoView({ block: "nearest", behavior });
  });
}

function selectedChartBars(tickerData) {
  const history = tickerData?.price_history || [];
  const period = resolveChartPeriod(session.prefs.display?.chart_period, history);
  const intraday = tickerData?.intraday?.bars || [];
  if (period === "1D" && intraday.length >= 2) return intraday.map(bar => ({
    ts: bar.ts, date: bar.ts, close: bar.close, adj_close: bar.close, volume: bar.volume,
  }));
  const quote = periodQuote(history, period);
  return quote.available ? quote.window : validBars(history);
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
    if (parseSettingsHash(location.hash) || parseStockHash(location.hash)) {
      sessionStorage.setItem(authKeys.hash, location.hash);
    }
    sessionStorage.setItem(
      authKeys.path,
      session.route.page === "research" ? tickerResearchPath(session.selected) : "/",
    );
    sessionStorage.setItem(
      authKeys.dashboardTicker,
      session.route.page === "research"
        ? session.dashboardSelection || ""
        : session.selected || "",
    );
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
  const pendingPath = sessionStorage.getItem(authKeys.path) || "/";
  sessionStorage.removeItem(authKeys.path);
  const pendingHash = sessionStorage.getItem(authKeys.hash) || "";
  sessionStorage.removeItem(authKeys.hash);
  history.replaceState({}, "", pendingPath + pendingHash);
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
  const what = apiLabels[path] || (String(path).startsWith("stock/") ? "Company page" : "The request");
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

async function loadStockPage(ticker, force = false) {
  if (!ticker || (!force && session.stockPageState[ticker] === "loading")) return;
  if (!force && session.stockPageState[ticker] === "ready") return;
  session.stockPageState[ticker] = "loading";
  if (session.route.page === "stock" && session.selected === ticker) renderDashboard();
  try {
    session.stockPage[ticker] = await apiGet(`stock/${encodeURIComponent(ticker)}`);
    session.stockPageState[ticker] = "ready";
  } catch (error) {
    session.stockPageState[ticker] = "error";
    session.stockPage[ticker] = null;
    if (error.status === 401) {
      settings.close();
      session.accessToken = null;
      showGate("Your session expired", "Sign in again to continue to your private dashboard.", "", true);
      return;
    }
  }
  if (session.route.page === "stock" && session.selected === ticker) renderDashboard();
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

function seriesPath(values, xAt, yAt, step = false) {
  let path = "";
  let drawing = false;
  values.forEach((value, index) => {
    if (!isNumericValue(value)) {
      drawing = false;
      return;
    }
    path += drawing && step
      ? `H${xAt(index).toFixed(2)} V${yAt(Number(value), index).toFixed(2)} `
      : `${drawing ? "L" : "M"}${xAt(index).toFixed(2)} ${yAt(Number(value), index).toFixed(2)} `;
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

function appendTimeGrid(svg, bars, periodId, top, bottom) {
  chartTicks(bars, periodId).forEach((tick) => {
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("class", "chart-time-grid");
    line.setAttribute("x1", String(tick.x));
    line.setAttribute("x2", String(tick.x));
    line.setAttribute("y1", String(top));
    line.setAttribute("y2", String(bottom));
    svg.append(line);
  });
}

function appendChartInteraction(svg, { top, bottom, layer, viewHeight }) {
  svg.dataset.chartLayer = layer;
  svg.dataset.plotLeft = String(CHART_PLOT.left);
  svg.dataset.plotRight = String(CHART_PLOT.right);
  const hit = document.createElementNS(svgNS, "rect");
  hit.setAttribute("class", "chart-hit");
  hit.setAttribute("x", "0");
  hit.setAttribute("y", "0");
  hit.setAttribute("width", String(CHART_PLOT.width));
  hit.setAttribute("height", String(viewHeight));
  svg.insertBefore(hit, svg.firstChild);
  for (const [edge, x] of [["start", CHART_PLOT.left], ["end", CHART_PLOT.right]]) {
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("class", "chart-plot-edge");
    line.dataset.plotEdge = edge;
    line.setAttribute("x1", String(x));
    line.setAttribute("x2", String(x));
    line.setAttribute("y1", String(top));
    line.setAttribute("y2", String(bottom));
    svg.append(line);
  }
  const cross = document.createElementNS(svgNS, "line");
  cross.setAttribute("class", "chart-crosshair");
  cross.setAttribute("x1", String(CHART_PLOT.left));
  cross.setAttribute("x2", String(CHART_PLOT.left));
  cross.setAttribute("y1", String(top));
  cross.setAttribute("y2", String(bottom));
  cross.setAttribute("visibility", "hidden");
  svg.append(cross);
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
  const { left, right } = CHART_PLOT;
  const compact = window.matchMedia("(max-width: 640px)").matches;
  const top = 16;
  const bottom = compact ? 320 : 196;
  const viewHeight = compact ? 340 : 210;
  const xAt = (index) => plotX(index, bars.length);
  const yValue = (value) => bottom - ((value - min) / spread) * (bottom - top);
  const yNormalized = (value) => bottom - (value / 100) * (bottom - top);
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("class", "price-chart");
  svg.setAttribute("viewBox", `0 0 ${CHART_PLOT.width} ${viewHeight}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
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
  appendTimeGrid(svg, bars, periodId, top, bottom);

  const pricePath = seriesPath(prices, xAt, (value) => yValue(value));
  const area = document.createElementNS(svgNS, "path");
  area.setAttribute("class", "chart-area");
  area.setAttribute("d", `${pricePath} L${right} ${bottom} L${left} ${bottom} Z`);
  const priceLine = document.createElementNS(svgNS, "path");
  const priceStyle = seriesLineStyle("price");
  priceLine.setAttribute("class", "chart-path");
  priceLine.setAttribute("d", pricePath);
  priceLine.setAttribute("stroke-dasharray", priceStyle.dash);
  priceLine.setAttribute("stroke-width", String(priceStyle.width));
  svg.append(area, priceLine);

  configured.forEach(({ id, definition, scaled, values }) => {
    if (!definition || !scaled.some(isNumericValue)) return;
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("class", "chart-overlay-path");
    path.setAttribute("d", seriesPath(
      scaled, xAt,
      (value) => ["macro", "fundamental", "sentiment"].includes(definition.kind) ? yNormalized(value) : yValue(value),
      definition.kind === "fundamental",
    ));
    const style = seriesLineStyle(definition.kind);
    path.setAttribute("stroke", overlayColor(definition.color, session.prefs.display.theme));
    path.setAttribute("stroke-dasharray", style.dash);
    path.setAttribute("stroke-width", String(style.width));
    path.dataset.overlay = id;
    if (definition.kind === "fundamental") {
      const title = document.createElementNS(svgNS, "title");
      title.textContent = `${definition.label} · ${fundamentalSummary(id, chartContext.chartData, bars.at(-1)?.ts || bars.at(-1)?.date)}`;
      path.append(title);
    }
    svg.append(path);
    if (definition.kind === "sentiment") scaled.forEach((value, index) => {
      if (!isNumericValue(value)) return;
      const dot = document.createElementNS(svgNS, "circle");
      dot.setAttribute("cx", String(xAt(index)));
      dot.setAttribute("cy", String(yNormalized(value)));
      dot.setAttribute("r", "3");
      dot.setAttribute("fill", path.getAttribute("stroke"));
      dot.dataset.sentimentObservation = String(bars[index]?.ts || bars[index]?.date).slice(0, 10);
      const title = document.createElementNS(svgNS, "title");
      title.textContent = `${definition.label} · ${Number(values[index]).toFixed(2)} · ${dot.dataset.sentimentObservation}`;
      dot.append(title);
      svg.append(dot);
    });
  });

  const catalysts = catalystRows(chartContext.dashboard, ticker)
    .filter(event => session.catalystCategories.includes(event.category) && markerIndex(event, bars) >= 0);
  const occupied = new Map();
  catalysts.forEach(event => {
    const index = markerIndex(event, bars);
    if (!occupied.has(index)) occupied.set(index, []);
    occupied.get(index).push(event);
  });
  catalysts.forEach(event => {
    const index = markerIndex(event, bars);
    const peers = occupied.get(index);
    const spacing = Math.min(24, 132 / Math.max(1, peers.length - 1));
    const category = CATALYST_CATEGORIES.find(category => category.id === event.category);
    const marker = document.createElementNS(svgNS, "g");
    marker.id = `catalyst-${event.id}`;
    marker.dataset.category = category.id;
    marker.setAttribute("class", `catalyst-marker${session.focusCatalyst === event.id ? " selected" : ""}`);
    marker.setAttribute("tabindex", "0");
    marker.setAttribute("role", "button");
    marker.setAttribute("aria-label", `${category.label}: ${event.title}, ${event.date}`);
    marker.setAttribute("transform", `translate(${xAt(index)},${bottom + 4 - peers.indexOf(event) * spacing})`);
    const hit = document.createElementNS(svgNS, "circle");
    hit.setAttribute("r", String(Math.min(12, spacing / 2)));
    hit.setAttribute("fill", "transparent");
    const dot = document.createElementNS(svgNS, "circle");
    dot.setAttribute("r", "4");
    dot.setAttribute("fill", overlayColor(category.slot, session.prefs.display.theme));
    dot.dataset.catalystColor = dot.getAttribute("fill");
    const title = document.createElementNS(svgNS, "title");
    title.textContent = `${event.title} · ${event.date} · ${event.source}`;
    marker.append(hit, dot, title);
    marker.addEventListener("click", () => selectCatalyst(event));
    marker.addEventListener("keydown", key => {
      if (key.key === "Enter" || key.key === " ") { key.preventDefault(); marker.dispatchEvent(new Event("click")); }
    });
    svg.append(marker);
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
  appendChartInteraction(svg, { top, bottom, layer: "price", viewHeight });
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

function renderPanelSaveError() {
  if (!session.panelSaveError) return null;
  const alert = node("p", "data-state error", `${session.panelSaveError} `);
  alert.dataset.panelSaveError = "true";
  alert.setAttribute("role", "alert");
  alert.append(action("Try again", "button-link", () => {
    session.panelSaveError = "";
    schedulePanelSave();
  }));
  return alert;
}

function renderDrawer({ id, title, subtitle, summary, mobilePanel, panelId }, renderBody) {
  const open = isPanelOpen(session.prefs.display?.panels, id);
  const controls = drawerControls(id, open);
  const panel = node("section", `panel panel-drawer${open ? " is-open" : ""}`);
  panel.dataset.drawer = id;
  if (mobilePanel) panel.dataset.mobilePanel = mobilePanel;
  if (panelId) panel.id = panelId;
  const header = node("div", "panel-header drawer-header");
  const heading = node("h2", "");
  const button = node("button", "drawer-toggle");
  button.type = "button";
  button.dataset.drawerToggle = id;
  button.setAttribute("aria-expanded", controls.ariaExpanded);
  button.setAttribute("aria-controls", controls.ariaControls);
  const chevron = node("span", "drawer-chevron", controls.chevron);
  chevron.setAttribute("aria-hidden", "true");
  const titleEl = node("span", "drawer-title", title);
  titleEl.title = title;
  const summaryEl = node("span", "drawer-summary", summary);
  summaryEl.title = summary;
  const cue = node("span", "drawer-cue", controls.cue);
  button.append(chevron, titleEl, summaryEl, cue);
  heading.append(button);
  header.append(heading);
  const body = node("div", "panel-body drawer-body");
  body.id = controls.ariaControls;
  body.hidden = controls.bodyHidden;
  if (subtitle) body.append(node("p", "panel-subtitle", subtitle));
  renderBody(body);
  button.addEventListener("click", () => {
    setPanelOpen(id, !isPanelOpen(session.prefs.display?.panels, id));
    renderDashboard();
  });
  panel.append(header, body);
  return panel;
}

function shortReleaseDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value || "").slice(0, 10);
  return new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", timeZone: session.prefs.display?.time_zone || "UTC",
  }).format(date);
}

function observationText(row) {
  const value = signalNumber(row?.value);
  if (value == null) return null;
  if (row.unit === "%") return `${value.toFixed(2)}%`;
  if (row.unit === "USD") return formatPrice(value);
  return Math.abs(value) >= 100 ? String(Math.round(value)) : value.toFixed(1);
}

function trendWord(row) {
  return driverTrend(row).split(" - ")[0];
}

function directionMark(row) {
  if (!row || signalNumber(row.value) == null) return "—";
  if (row.trend_state === "up") return "▲";
  if (row.trend_state === "down") return "▼";
  return "·";
}

function ratesSummary(rows) {
  if (session.dashboardState === "loading") return "Loading…";
  const ten = rows.find((row) => row.series_id === "DGS10");
  const two = rows.find((row) => row.series_id === "DGS2");
  const regime = session.dashboard?.rates?.curve?.regime;
  if (!ten && !two && !regime) return "Unavailable";
  const parts = [];
  if (observationText(ten)) parts.push(`10Y ${observationText(ten)}`);
  if (observationText(two)) parts.push(`2Y ${observationText(two)}`);
  if (regime) parts.push(regime);
  else if (ten) parts.push(`1M ${driverChange(ten)}`);
  return parts.join(" · ") || "Unavailable";
}

function inflationSummary(tickerData) {
  if (session.dashboardState === "loading") return "Loading…";
  const latest = (tickerData?.release_links?.latest || []).find((row) => row.series_id === "CPI_YOY");
  const next = (session.dashboard?.releases?.next || [])[0];
  const parts = [];
  if (signalNumber(latest?.yoy) != null) parts.push(`CPI YoY ${Number(latest.yoy).toFixed(1)}%`);
  if (next?.release_ts) parts.push(`next release ${shortReleaseDate(next.release_ts)}`);
  return parts.join(" · ") || "Unavailable";
}

function marketSummary() {
  if (session.dashboardState === "loading") return "Loading…";
  const quote = (id) => periodQuote(session.dashboard?.tickers?.[id]?.price_history || [], "1M");
  const own = quote(session.selected);
  const spy = quote("SPY");
  if (!own.available && !spy.available) return "Unavailable";
  const text = (id, item) => `${id} ${item.available ? formatPercent(item.returnValue, 1) : "—"}`;
  return `${text(session.selected, own)} vs ${text("SPY", spy)} (1M)`;
}

function movingAverageSummary(history) {
  if (session.dashboardState === "loading") return "Loading…";
  const rows = movingAverageRows(history);
  const known = rows.filter((row) => row.distance != null);
  if (!known.length) return "Unavailable";
  const above = known.filter((row) => row.distance > 0).length;
  return `Above ${above} of ${rows.length} averages`;
}

function seriesSummary(rows, seriesId, label) {
  if (session.dashboardState === "loading") return "Loading…";
  const row = rows.find((item) => item.series_id === seriesId);
  const value = observationText(row);
  if (!value) return "Unavailable";
  return `${label} ${value} · ${trendWord(row)}`;
}

function dollarSummary(rows) {
  if (session.dashboardState === "loading") return "Loading…";
  const dollar = rows.find((row) => row.series_id === "DTWEXBGS");
  const oil = rows.find((row) => row.series_id === "DCOILWTICO");
  if (!dollar && !oil) return "Unavailable";
  return `Dollar ${directionMark(dollar)} · WTI ${directionMark(oil)}`;
}

function tariffSummary(rows) {
  if (session.dashboardState === "loading") return "Loading…";
  const row = rows.find((item) => item.series_id === "USEPUINDXD");
  const events = catalystRows(session.dashboard, session.selected).filter((event) => event.category === "policy").slice(-3);
  if (!row && !events.length) return "Unavailable";
  const value = observationText(row) || "Unavailable";
  return `Policy uncertainty ${value} · ${events.length} recent event${events.length === 1 ? "" : "s"}`;
}

function correlationSummary(rows, chartData, chartDataState) {
  if (session.dashboardState === "loading" || chartDataState === "loading") return "Loading…";
  const daily = rows.filter((row) => !row.series_id.endsWith("_YOY"));
  if (!daily.length) return "Unavailable";
  let known = 0;
  let strengthening = 0;
  let flips = 0;
  daily.forEach((row) => {
    const change = correlationDrift(row, chartData?.correlation_history?.[row.series_id] || []);
    if (!change) return;
    known += 1;
    if (change.stronger) strengthening += 1;
    if (change.signFlip) flips += 1;
  });
  if (!known) return "Unavailable";
  return `${strengthening} strengthening · ${flips} sign flip${flips === 1 ? "" : "s"}`;
}

function catalystSummary(events) {
  if (session.dashboardState === "loading") return "Loading…";
  if (!events.length) return session.dashboardState === "error" ? "Unavailable" : "No published events";
  const today = String(session.dashboard?.generated_at || "").slice(0, 10);
  const upcoming = events.filter((event) => String(event.date).slice(0, 10) >= today);
  const next = upcoming[0];
  return `${upcoming.length} upcoming${next ? ` · next: ${String(next.date).slice(0, 10)} ${next.title}` : ""}`;
}

function companySummary(tickerData, events) {
  if (session.dashboardState === "loading") return "Loading…";
  if (session.selected === "SPCX" && isNumericValue(tickerData?.contracts?.ttm_obligated)) {
    return `TTM obligations ${formatPrice(tickerData.contracts.ttm_obligated)}`;
  }
  if (session.dashboardState === "error" && !events.length) return "Unavailable";
  return `${events.length} event${events.length === 1 ? "" : "s"}`;
}

function contractsSummary(contracts) {
  if (!isNumericValue(contracts?.ttm_obligated)) return "Unavailable";
  const amount = new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1,
  }).format(Number(contracts.ttm_obligated));
  return `TTM ${amount}`;
}

function renderWatchlist() {
  const wrapper = node("nav", "watchlist-bar");
  wrapper.setAttribute("aria-label", "Watchlist");
  const list = node("div", "watchlist");
  list.id = "watchlist-scroll";
  const buttons = [];
  const ensureVisible = (button) => {
    if (!button) return;
    const left = button.getBoundingClientRect().left - list.getBoundingClientRect().left + list.scrollLeft - list.clientLeft;
    const right = left + button.offsetWidth;
    if (left < list.scrollLeft) list.scrollLeft = left;
    else if (right > list.scrollLeft + list.clientWidth) list.scrollLeft = right - list.clientWidth;
  };
  const { pinned, others } = stripOrder(session.prefs || {});
  const tickers = [...pinned, ...others];
  const hasSelected = tickers.includes(session.selected);
  tickers.forEach((ticker, index) => {
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
    button.dataset.ticker = ticker;
    button.tabIndex = ticker === session.selected || (!hasSelected && index === 0) ? 0 : -1;
    buttons.push(button);
    button.addEventListener("focus", () => ensureVisible(button));
    let pressTimer = 0;
    const cancelPress = () => window.clearTimeout(pressTimer);
    button.addEventListener("pointerdown", () => {
      cancelPress();
      pressTimer = window.setTimeout(() => {
        button.dataset.longPress = "1";
        navigateToStock(ticker);
      }, LONG_PRESS_MS);
    });
    button.addEventListener("pointerup", cancelPress);
    button.addEventListener("pointerleave", cancelPress);
    button.addEventListener("pointercancel", cancelPress);
    button.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        navigateToStock(ticker);
        return;
      }
      const index = buttons.indexOf(button);
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
        : event.key === "ArrowRight" ? Math.min(index + 1, buttons.length - 1)
        : event.key === "ArrowLeft" ? Math.max(index - 1, 0) : null;
      if (next === null) return;
      event.preventDefault();
      buttons.forEach((item, i) => { item.tabIndex = i === next ? 0 : -1; });
      buttons[next].focus();
    });
    button.setAttribute("aria-pressed", String(ticker === session.selected));
    const sparkKey = `${session.dashboard?.generated_at || ""}|${ticker}|${history.length}|${latest?.date || ""}`;
    const sparkModel = sparklineModel(history, sparkKey);
    const sparkText = sparklineSummary(sparkModel);
    const priceLabel = latest ? formatPrice(displayPrice(latest)) : "no price data";
    button.setAttribute("aria-label", sparkText ? `${ticker}, ${priceLabel}, ${sparkText}` : `${ticker}, ${priceLabel}`);
    button.addEventListener("click", () => {
      if (button.dataset.longPress === "1") {
        delete button.dataset.longPress;
        return;
      }
      if (session.route.page === "stock") {
        navigateToStock(ticker);
        return;
      } else if (session.route.page !== "dashboard") {
        navigateToDashboard(ticker);
      } else {
        session.selected = ticker;
        session.dashboardSelection = ticker;
        renderDashboard();
        loadChartData(ticker);
      }
      root.querySelector('.watchlist [aria-pressed="true"]')?.focus();
    });
    const symbol = node("span", "ticker-symbol", ticker);
    if (pinned.includes(ticker)) {
      const star = node("span", "ticker-pin", "★");
      star.setAttribute("aria-hidden", "true");
      symbol.prepend(star);
    }
    const spark = sparkline(history, { cacheKey: sparkKey });
    spark.classList.add("ticker-spark");
    button.append(symbol, spark);
    button.append(
      node(
        "span",
        `ticker-meta ${polarity(returns(history, 1))}`,
        latest ? `${formatPrice(displayPrice(latest))} · ${formatPercent(returns(history, 1))}` : "Waiting for history",
      ),
    );
    const item = node("div", "ticker-watch-item");
    item.append(button);
    const researchPath = tickerResearchPath(ticker);
    if (researchPath) {
      const researchLink = node("a", "ticker-research", "↗");
      researchLink.href = researchPath;
      researchLink.title = `Open ${ticker} research`;
      researchLink.setAttribute("aria-label", `Open ${ticker} research page`);
      researchLink.addEventListener("click", (event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigateToResearch(ticker);
      });
      item.append(researchLink);
    }
    if (ticker === session.selected && stockHash(ticker)) {
      const openPage = node("a", "ticker-stock-link", `Open ${ticker} page →`);
      openPage.href = `/${stockHash(ticker)}`;
      openPage.dataset.stockOpen = ticker;
      openPage.setAttribute("aria-label", `Open ${ticker} page`);
      let linkTimer = 0;
      const cancelLinkPress = () => window.clearTimeout(linkTimer);
      openPage.addEventListener("pointerdown", () => {
        cancelLinkPress();
        linkTimer = window.setTimeout(() => {
          openPage.dataset.longPress = "1";
          navigateToStock(ticker);
        }, LONG_PRESS_MS);
      });
      openPage.addEventListener("pointerup", cancelLinkPress);
      openPage.addEventListener("pointerleave", cancelLinkPress);
      openPage.addEventListener("pointercancel", cancelLinkPress);
      openPage.addEventListener("click", (event) => {
        if (openPage.dataset.longPress === "1") {
          delete openPage.dataset.longPress;
          event.preventDefault();
          return;
        }
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigateToStock(ticker);
      });
      item.append(openPage);
    }
    list.append(item);
  });
  const manage = node("button", "ticker-button ticker-manage");
  manage.type = "button";
  manage.dataset.opener = "strip";
  manage.setAttribute("aria-label", "My tickers: add, pin or reorder");
  manage.append(node("span", "ticker-symbol", "+ Add / pin"), node("span", "ticker-meta", "My tickers"));
  manage.addEventListener("click", () => settings.open("tickers", "strip"));
  const scrollButton = (label, direction) => {
    const button = node("button", "watchlist-arrow", direction < 0 ? "‹" : "›");
    button.type = "button";
    button.tabIndex = -1;
    button.setAttribute("aria-label", label);
    button.setAttribute("aria-controls", list.id);
    button.addEventListener("click", () => list.scrollBy({
      left: direction * list.clientWidth * 0.8,
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
    }));
    return button;
  };
  const left = scrollButton("Scroll tickers left", -1);
  const right = scrollButton("Scroll tickers right", 1);
  const update = () => {
    left.disabled = list.scrollLeft <= 1;
    right.disabled = list.scrollLeft + list.clientWidth >= list.scrollWidth - 1;
    wrapper.classList.toggle("can-scroll-left", !left.disabled);
    wrapper.classList.toggle("can-scroll-right", !right.disabled);
    session.watchlistScroll = list.scrollLeft;
  };
  watchlistResize?.disconnect();
  watchlistResize = new ResizeObserver(update);
  watchlistResize.observe(list);
  list.addEventListener("scroll", update, { passive: true });
  list.addEventListener("wheel", (event) => {
    if (event.deltaX || event.ctrlKey || !event.deltaY) return;
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? list.clientWidth : 1);
    if ((delta > 0 && list.scrollLeft + list.clientWidth < list.scrollWidth - 1)
      || (delta < 0 && list.scrollLeft > 1)) {
      event.preventDefault();
      list.scrollLeft += delta;
    }
  }, { passive: false });
  wrapper.append(left, list, right, manage);
  requestAnimationFrame(() => {
    if (!list.isConnected) return;
    list.scrollLeft = session.watchlistScroll;
    ensureVisible(list.querySelector('[aria-pressed="true"]'));
    update();
  });
  return wrapper;
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

function chartControlsFor(tickerData, chartData, chartDataState, bars) {
  const key = `${session.selected}|${session.prefs.display?.chart_period}|${bars[0]?.ts || bars[0]?.date}|${bars.at(-1)?.ts || bars.at(-1)?.date}`;
  const groups = overlayGroups(tickerData, chartDataState === "ready" ? chartData : null, session.dashboard, bars);
  const lanes = availableLanes(bars, chartDataState === "ready" ? chartData : null);
  const historyBacked = overlay => ["macro", "fundamental"].includes(overlay.kind);
  if (chartDataState === "ready") {
    chartAvailability.set(key, { groups, lanes });
    return { groups, lanes };
  }
  const saved = chartSettingsFor(session.prefs, session.selected);
  const previous = chartAvailability.get(key);
  const pending = previous?.groups.flatMap(group => group.overlays).filter(historyBacked)
    || OVERLAYS.filter(overlay => saved.overlays.includes(overlay.id) && historyBacked(overlay));
  pending.forEach(overlay => {
    let group = groups.find(group => group.id === overlay.group);
    if (!group) {
      group = { id: overlay.group, label: overlay.group, overlays: [] };
      groups.push(group);
    }
    group.overlays.push({ ...overlay, available: false });
  });
  const order = [...new Set(OVERLAYS.map(overlay => overlay.group))];
  groups.sort((left, right) => order.indexOf(left.id) - order.indexOf(right.id));
  const pendingLanes = previous?.lanes || CHART_LANES.filter(lane => saved.lanes.includes(lane.id));
  return { groups, lanes: CHART_LANES.filter(lane => lanes.some(item => item.id === lane.id)
    || (lane.id !== "VOL" && pendingLanes.some(item => item.id === lane.id))) };
}

function renderOverlayControls(groups, chartDataState, onSettingsChange) {
  const panel = node("section", "chart-controls");
  const settings = chartSettingsFor(session.prefs, session.selected);
  const activeGroup = groups.find((group) => group.id === session.overlayGroup) || groups[0];
  const tabs = node("div", "overlay-tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Chart overlay groups");
  groups.forEach((group, index) => {
    const button = node("button", "overlay-tab");
    button.type = "button";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(group.id === activeGroup?.id));
    button.setAttribute("aria-controls", "overlay-options");
    button.id = `overlay-tab-${index}`;
    button.tabIndex = group.id === activeGroup?.id ? 0 : -1;
    button.textContent = group.label;
    button.addEventListener("click", () => {
      session.overlayGroup = group.id;
      renderDashboard();
      root.querySelector(`[data-overlay-tab="${group.id}"]`)?.focus();
    });
    button.dataset.overlayTab = group.id;
    button.addEventListener("keydown", event => {
      const offsets = { ArrowRight: 1, ArrowLeft: -1, Home: -index, End: groups.length - 1 - index };
      if (!(event.key in offsets)) return;
      event.preventDefault();
      tabs.children[(index + offsets[event.key] + groups.length) % groups.length].click();
    });
    tabs.append(button);
  });
  const group = node("div", "overlay-options");
  group.id = "overlay-options";
  group.setAttribute("role", activeGroup ? "tabpanel" : "group");
  if (activeGroup) group.setAttribute("aria-labelledby", `overlay-tab-${groups.indexOf(activeGroup)}`);
  group.setAttribute("aria-label", `${activeGroup?.label || "Available"} chart overlays`);
  const overlays = activeGroup?.overlays || [];
  const available = overlays.filter(overlay => overlay.available);
  if (available.length && (available.length <= 5 || activeGroup?.id === "Market")) {
    const allSelected = available.every((overlay) => settings.overlays.includes(overlay.id));
    const allButton = node("button", "overlay-chip overlay-all", allSelected ? "Clear group" : "Add group");
    allButton.type = "button";
    allButton.title = "Add group selects at most five overlays.";
    allButton.addEventListener("click", () => {
      const selected = new Set(settings.overlays);
      if (allSelected) available.forEach((overlay) => selected.delete(overlay.id));
      let next = [...selected];
      if (!allSelected) available.forEach(overlay => { next = addOverlay(next, overlay.id, groups.flatMap(group => group.overlays).filter(item => item.available).map(item => item.id)); });
      onSettingsChange({ ...settings, overlays: next });
      root.querySelector(".overlay-all")?.focus();
    });
    group.append(allButton);
  }
  overlays.forEach((overlay) => {
    const selected = settings.overlays.includes(overlay.id);
    const button = node("button", `overlay-chip${selected ? " selected" : ""}`);
    button.type = "button";
    button.disabled = !overlay.available;
    button.setAttribute("aria-pressed", String(selected));
    button.title = !overlay.available ? `${overlay.label} is not available for ${session.selected}`
      : selected ? `Remove ${overlay.label} from the chart` : `Add ${overlay.label} to the chart`;
    button.setAttribute("aria-label", `${selected ? "Remove" : "Add"} ${overlay.label} ${selected ? "from" : "to"} the chart${overlay.available ? "" : `, not available for ${session.selected}`}`);
    button.dataset.overlay = overlay.id;
    button.append(node("span", "overlay-swatch", ""));
    button.querySelector(".overlay-swatch").style.setProperty("--overlay-color", overlayColor(overlay.color, session.prefs.display.theme));
    button.append(document.createTextNode(overlay.short || overlay.label));
    let longPress = false;
    let timer;
    button.addEventListener("click", () => {
      if (longPress) { longPress = false; return; }
      const next = selected ? settings.overlays.filter((id) => id !== overlay.id)
        : addOverlay(settings.overlays, overlay.id, groups.flatMap(group => group.overlays).filter(item => item.available).map(item => item.id));
      onSettingsChange({ ...settings, overlays: next });
      root.querySelector(`[data-overlay="${overlay.id}"]`)?.focus();
    });
    if (overlay.kind === "market") {
      const choice = node("span", "overlay-choice");
      const tooltip = node("span", "overlay-tooltip", overlay.label);
      tooltip.id = `overlay-tooltip-${overlay.id}`;
      tooltip.setAttribute("role", "tooltip");
      tooltip.hidden = true;
      button.setAttribute("aria-describedby", tooltip.id);
      const show = () => {
        tooltip.hidden = false;
        tooltip.style.left = "0px";
        const bounds = tooltip.getBoundingClientRect();
        if (bounds.right > innerWidth - 12) tooltip.style.left = `${innerWidth - 12 - bounds.right}px`;
      };
      const hide = () => { tooltip.hidden = true; clearTimeout(timer); };
      button.addEventListener("mouseenter", show);
      button.addEventListener("mouseleave", hide);
      button.addEventListener("focus", show);
      button.addEventListener("blur", hide);
      button.addEventListener("keydown", event => { if (event.key === "Escape") hide(); });
      button.addEventListener("pointerdown", event => {
        longPress = false;
        if (event.pointerType === "touch") timer = setTimeout(() => { longPress = true; show(); }, 500);
      });
      button.addEventListener("pointerup", () => clearTimeout(timer));
      button.addEventListener("pointercancel", hide);
      button.addEventListener("contextmenu", event => { if (longPress) event.preventDefault(); });
      choice.append(button, tooltip);
      group.append(choice);
    } else group.append(button);
  });
  if (!overlays.length) group.append(node("span", "overlay-empty", chartDataState === "loading" ? "Loading available series…"
    : chartDataState === "error" ? "Available series could not be loaded. Refresh data to retry." : "No overlays have data in this chart period."));
  const tabsRow = node("div", "overlay-groups-row");
  const clear = action("Clear all", "overlay-chip", () => {
    onSettingsChange({ ...settings, overlays: [] });
    (root.querySelector(".overlay-tab[aria-selected='true']") || root.querySelector(".period-chip[aria-pressed='true']"))?.focus();
  });
  clear.dataset.clear = "overlays";
  clear.setAttribute("aria-label", "Clear all chart overlays");
  clear.disabled = !settings.overlays.length;
  if (groups.length) tabsRow.append(tabs);
  tabsRow.append(clear);
  panel.append(tabsRow, group);
  if (chartDataState === "loading") panel.append(node("p", "overlay-empty", "Loading historical chart data…"));
  if (settings.overlays.length) {
    const selection = node("p", "chart-control-note", `${settings.overlays.length} of 5 overlays · market groups compare percentage change; indicators are normalized`);
    panel.append(selection);
  }

  if (session.chartSaveError) {
    const error = node("p", "chart-save-error", `Chart settings could not be saved: ${session.chartSaveError}`);
    error.setAttribute("role", "alert");
    panel.append(error);
  }
  return panel;
}

function formatCompactNumber(value) {
  if (!isNumericValue(value)) return "—";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(Number(value));
}

function formatLanePoint(lane, value) {
  if (!isNumericValue(value)) return "—";
  if (lane.id === "VOL") return `${formatCompactNumber(value)} sh`;
  if (lane.id === "PRESS") return `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(2)}`;
  if (lane.id === "SI") return lane.percent ? `${Number(value).toFixed(2)}%` : `${Math.round(Number(value)).toLocaleString()} sh`;
  return `${Number(value).toFixed(2)} P/C`;
}

function crosshairText(bar, index, periodId, lanes) {
  const stamp = barStamp(bar);
  const when = periodId === "1D" && stamp.includes("T") ? `${stamp.slice(0, 10)} ${stamp.slice(11, 16)}` : stamp.slice(0, 10);
  const traded = displayPrice(bar);
  const parts = [when, formatPrice(isNumericValue(traded) ? traded : chartValue(bar))];
  const volumeLane = lanes.find((lane) => lane.id === "VOL");
  parts.push(`Volume ${formatLanePoint({ id: "VOL" }, volumeLane ? volumeLane.values[index] : bar.volume)}`);
  lanes.filter((lane) => lane.id !== "VOL").forEach((lane) => {
    parts.push(`${lane.label} ${formatLanePoint(lane, lane.values[index])}`);
  });
  return parts.join(" · ");
}

function renderTimeAxis(bars, periodId) {
  const axis = node("div", "chart-time-axis");
  axis.dataset.chartLayer = "axis";
  chartTicks(bars, periodId).forEach((tick) => {
    const label = node("span", "chart-tick", tick.label);
    label.dataset.anchor = tick.anchor;
    label.style.left = `${(tick.x / CHART_PLOT.width) * 100}%`;
    axis.append(label);
  });
  [["start", CHART_PLOT.left], ["end", CHART_PLOT.right]].forEach(([edge, x]) => {
    const marker = node("span", "chart-axis-edge");
    marker.dataset.plotEdge = edge;
    marker.style.left = `${(x / CHART_PLOT.width) * 100}%`;
    axis.append(marker);
  });
  const cross = node("span", "chart-axis-crosshair");
  cross.hidden = true;
  axis.append(cross);
  return axis;
}

function attachSharedCrosshair(stack, bars, periodId, lanes) {
  const layers = [...stack.querySelectorAll("[data-chart-layer]")];
  const readout = stack.querySelector(".chart-readout");
  let index = null;
  const show = (next) => {
    if (!Number.isInteger(next) || next < 0 || next >= bars.length) return;
    index = next;
    const x = plotX(index, bars.length);
    layers.forEach((layer) => {
      const line = layer.querySelector(".chart-crosshair");
      if (line) {
        line.setAttribute("x1", String(x));
        line.setAttribute("x2", String(x));
        line.setAttribute("visibility", "visible");
      }
      const marker = layer.querySelector(".chart-axis-crosshair");
      if (marker) {
        marker.hidden = false;
        marker.style.left = `${(x / CHART_PLOT.width) * 100}%`;
      }
    });
    if (readout) readout.textContent = crosshairText(bars[index], index, periodId, lanes);
  };
  const clear = () => {
    index = null;
    layers.forEach((layer) => {
      layer.querySelector(".chart-crosshair")?.setAttribute("visibility", "hidden");
      const marker = layer.querySelector(".chart-axis-crosshair");
      if (marker) marker.hidden = true;
    });
    if (readout) readout.textContent = "Hover, tap, or use arrow keys to read a date across the charts.";
  };
  const indexFromEvent = (event, layer) => {
    const rect = layer.getBoundingClientRect();
    if (!rect.width) return null;
    const viewX = ((event.clientX - rect.left) / rect.width) * CHART_PLOT.width;
    return indexAtPlotX(viewX, bars.length);
  };
  layers.forEach((layer) => {
    layer.addEventListener("pointerdown", (event) => {
      if (event.button != null && event.button !== 0) return;
      try { layer.setPointerCapture?.(event.pointerId); } catch { /* synthetic or unsupported */ }
      const next = indexFromEvent(event, layer);
      if (next != null) show(next);
    });
    layer.addEventListener("pointermove", (event) => {
      const dragging = event.pointerType !== "mouse" && layer.hasPointerCapture?.(event.pointerId);
      if (event.pointerType !== "mouse" && !dragging) return;
      const next = indexFromEvent(event, layer);
      if (next != null) show(next);
    });
  });
  stack.addEventListener("pointerleave", (event) => {
    if (event.pointerType !== "mouse" || stack.contains(document.activeElement)) return;
    clear();
  });
  stack.addEventListener("keydown", (event) => {
    if (event.target !== stack) return;
    const current = index ?? bars.length - 1;
    const next = event.key === "ArrowLeft" ? Math.max(0, current - 1)
      : event.key === "ArrowRight" ? Math.min(bars.length - 1, current + 1)
      : event.key === "Home" ? 0
      : event.key === "End" ? bars.length - 1
      : null;
    if (next == null) return;
    event.preventDefault();
    show(next);
  });
  stack.addEventListener("focus", () => { if (index == null) show(bars.length - 1); });
  stack.addEventListener("blur", (event) => {
    if (event.relatedTarget && !stack.contains(event.relatedTarget)) clear();
  });
}

function renderLane(lane, bars, tickerData, chartData, chartDataState, periodId) {
  const row = node("section", "chart-lane");
  row.dataset.lane = lane.id;
  row.setAttribute("aria-label", `${lane.label} chart lane`);
  const info = node("div", "lane-info");
  info.append(node("h3", "lane-title", lane.label));
  const values = lane.id === "VOL" || chartDataState === "ready" ? laneValues(lane.id, bars, chartData) : [];
  let suffix = "";
  if (lane.id === "VOL") {
    suffix = "shares · IEX volume where reported";
  } else if (lane.id === "SI") {
    const percentagePoints = alignedLaneValues(chartData?.short_interest, bars, "short_pct_denominator").filter(isNumericValue);
    const latestDenominator = (chartData?.short_interest || []).filter(point => String(point.date).slice(0, 10)
      <= String(bars.at(-1)?.date || bars.at(-1)?.ts).slice(0, 10) && isNumericValue(point.short_pct_denominator))
      .sort((left, right) => String(left.date).localeCompare(String(right.date))).at(-1)?.denominator_type;
    suffix = latestDenominator === "estimated_public_float"
      ? "Short % of estimated public float · SEC market value ÷ measurement-date close"
      : percentagePoints.length
        ? "Short % of shares outstanding proxy · SEC public float unavailable"
        : "Shares sold short · denominator data unavailable";
  } else if (lane.id === "OPT") {
    suffix = "Put/call volume ratio · IV30 shown when available";
  } else {
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
        ? alignedLaneValues(chartData?.short_interest, bars, "short_pct_denominator").some(isNumericValue)
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

  const viewHeight = 64;
  const laneTop = 6;
  const laneBottom = 58;
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("class", `lane-chart lane-${lane.id.toLowerCase()}`);
  svg.setAttribute("viewBox", `0 0 ${CHART_PLOT.width} ${viewHeight}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${lane.label} over ${bars.length} chart observations`);
  const title = document.createElementNS(svgNS, "title");
  title.textContent = `${lane.label}: ${stat}`;
  svg.append(title);
  const { left, right } = CHART_PLOT;
  const xAt = (index) => plotX(index, bars.length);
  appendTimeGrid(svg, bars, periodId, laneTop, laneBottom);
  if (lane.id === "VOL") {
    const max = Math.max(...valid) || 1;
    const slot = bars.length > 1 ? (right - left) / (bars.length - 1) : right - left;
    const barWidth = Math.max(0.35, Math.min(slot * 0.72, 8));
    values.forEach((value, index) => {
      if (!isNumericValue(value)) return;
      const height = (Number(value) / max) * (laneBottom - laneTop);
      const bar = document.createElementNS(svgNS, "rect");
      const x = Math.min(Math.max(xAt(index) - barWidth / 2, left), right - barWidth);
      bar.setAttribute("x", String(x));
      bar.setAttribute("y", String(laneBottom - height));
      bar.setAttribute("width", String(barWidth));
      bar.setAttribute("height", String(height));
      bar.setAttribute("class", "lane-volume-bar");
      svg.append(bar);
    });
  } else {
    const min = lane.id === "PRESS" ? -1 : Math.min(...valid);
    const max = lane.id === "PRESS" ? 1 : Math.max(...valid);
    const spread = max - min || 1;
    const yAt = (value) => laneBottom - ((value - min) / spread) * (laneBottom - laneTop);
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
    path.setAttribute("d", seriesPath(values, xAt, (value) => yAt(value), lane.id === "SI"));
    path.setAttribute("class", `lane-line ${lane.id === "PRESS" ? "lane-pressure-line" : ""}`.trim());
    svg.append(path);
  }
  appendChartInteraction(svg, { top: laneTop, bottom: laneBottom, layer: "lane", viewHeight });
  row.append(info, svg);
  return row;
}

function renderChartLanes(settings, bars, tickerData, chartData, chartDataState, available, periodId) {
  const lanes = node("div", "chart-lanes");
  available.filter(lane => settings.lanes.includes(lane.id)).forEach(lane => {
    lanes.append(renderLane(lane, bars, tickerData, chartData, chartDataState, periodId));
  });
  return lanes;
}

function renderLaneControls(settings, available, chartDataState, onSettingsChange) {
  const group = node("div", "lane-controls");
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", "Under-chart lanes");
  available.forEach(lane => {
    const selected = settings.lanes.includes(lane.id);
    const button = action(lane.label, "lane-toggle", () => {
      onSettingsChange({ ...settings, lanes: selected ? settings.lanes.filter(id => id !== lane.id) : [...settings.lanes, lane.id] });
      root.querySelector(`[data-lane="${lane.id}"]`)?.focus();
    });
    button.dataset.lane = lane.id;
    button.setAttribute("aria-pressed", String(selected));
    button.disabled = lane.id !== "VOL" && chartDataState !== "ready";
    group.append(button);
  });
  const hide = action("Hide all", "lane-toggle bulk-control", () => {
    onSettingsChange({ ...settings, lanes: [] });
    (root.querySelector(".lane-controls [data-lane]:not(:disabled)") || root.querySelector(".period-chip[aria-pressed='true']"))?.focus();
  });
  hide.dataset.clear = "lanes";
  hide.setAttribute("aria-label", "Hide all under-chart lanes");
  hide.disabled = !available.some(lane => settings.lanes.includes(lane.id));
  group.append(hide);
  return group;
}

function legendKey(item, color) {
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("class", "legend-line");
  svg.setAttribute("viewBox", "0 0 28 8");
  svg.setAttribute("aria-hidden", "true");
  const line = document.createElementNS(svgNS, "line");
  line.setAttribute("x1", "1");
  line.setAttribute("y1", "4");
  line.setAttribute("x2", "27");
  line.setAttribute("y2", "4");
  line.setAttribute("stroke", color);
  line.setAttribute("stroke-width", String(item.width));
  line.setAttribute("stroke-dasharray", item.dash);
  line.setAttribute("stroke-linecap", "round");
  svg.append(line);
  return svg;
}

function renderPricePanel(tickerData, chartData, chartDataState, onSelectPeriod, onSettingsChange) {
  const history = tickerData?.price_history || [];
  const bars = validBars(history);
  const chartSettings = chartSettingsFor(session.prefs, session.selected);
  const activeId = resolveChartPeriod(session.prefs.display?.chart_period, history);
  const quote = periodQuote(history, activeId);
  const intradayBars = tickerData?.intraday?.bars || [];
  const chartHistory = selectedChartBars(tickerData);
  const controls = chartControlsFor(tickerData, chartData, chartDataState, chartHistory);
  const visibleOverlayIds = controls.groups.flatMap(group => group.overlays).filter(overlay => overlay.available).map(overlay => overlay.id);
  const visibleOverlays = chartSettings.overlays.filter(id => visibleOverlayIds.includes(id));
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
  panel.append(renderOverlayControls(controls.groups, chartDataState, onSettingsChange));
  const filters = node("div", "catalyst-filters");
  filters.setAttribute("role", "group");
  filters.setAttribute("aria-label", "Chart catalyst categories");
  filters.append(node("span", "signal-note", "Catalysts"));
  const categories = catalystCategoriesInWindow(session.dashboard, session.selected, chartHistory);
  categories.forEach(category => {
    const enabled = session.catalystCategories.includes(category.id);
    const color = overlayColor(category.slot, session.prefs.display.theme);
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "overlay-chip catalyst-chip";
    toggle.style.setProperty("--catalyst-color", color);
    toggle.dataset.catalystCategory = category.id;
    toggle.dataset.catalystColor = color;
    toggle.setAttribute("aria-pressed", String(enabled));
    const swatch = node("span", "catalyst-swatch");
    swatch.setAttribute("aria-hidden", "true");
    toggle.append(swatch, document.createTextNode(category.label));
    toggle.addEventListener("click", () => {
      session.catalystCategories = enabled ? session.catalystCategories.filter(id => id !== category.id)
        : [...session.catalystCategories, category.id];
      renderDashboard();
      root.querySelector(`[data-catalyst-category="${category.id}"]`)?.focus();
    });
    filters.append(toggle);
  });
  const clearCatalysts = action("Clear all", "overlay-chip bulk-control", () => {
    session.catalystCategories = [];
    renderDashboard();
    (root.querySelector("[data-catalyst-category]") || root.querySelector(".period-chip[aria-pressed='true']"))?.focus();
  });
  clearCatalysts.dataset.clear = "catalysts";
  clearCatalysts.setAttribute("aria-label", "Clear all catalyst markers");
  clearCatalysts.disabled = !session.catalystCategories.length;
  filters.append(clearCatalysts);
  if (chartDataState === "error") {
    const error = node("p", "data-state error chart-data-error", "Historical overlay data could not be loaded. Use Refresh data to retry.");
    error.setAttribute("role", "alert");
    panel.append(error);
  }

  let placedLanes = false;
  if (chartHistory.length >= 2) {
    const chart = drawChart(chartHistory, session.selected, activeId, visibleOverlays, {
      tickerData,
      chartData,
      dashboard: session.dashboard,
    });
    if (chart) {
      const stack = node("div", "chart-stack");
      stack.tabIndex = 0;
      stack.setAttribute("role", "group");
      stack.setAttribute("aria-label", `${session.selected} price chart and under-chart lanes. Left and right arrow keys move the date crosshair.`);
      const readout = node("p", "chart-readout", "Hover, tap, or use arrow keys to read a date across the charts.");
      readout.setAttribute("role", "status");
      readout.setAttribute("aria-live", "polite");
      const lanes = renderChartLanes(chartSettings, chartHistory, tickerData, chartData, chartDataState, controls.lanes, activeId);
      const laneSeries = controls.lanes.filter((lane) => chartSettings.lanes.includes(lane.id)).map((lane) => ({
        ...lane,
        values: lane.id === "VOL" || chartDataState === "ready" ? laneValues(lane.id, chartHistory, chartData) : [],
        percent: lane.id === "SI" && alignedLaneValues(chartData?.short_interest, chartHistory, "short_pct_denominator").some(isNumericValue),
      }));
      stack.append(readout, chart, lanes, renderTimeAxis(chartHistory, activeId));
      attachSharedCrosshair(stack, chartHistory, activeId, laneSeries);
      panel.append(stack);
      placedLanes = true;
      const legend = node("div", "chart-legend");
      const values = chartHistory.map(chartValue);
      const numericValues = values.filter(isNumericValue).map(Number);
      legend.append(
        node(
          "span",
          "chart-legend-summary",
          `${activeId} · ${formatPercent(activeId === "1D" && isNumericValue(live?.change_pct) ? live.change_pct : quote.returnValue, 1)} · ${chartHistory.length.toLocaleString()} ${activeId === "1D" && intradayBars.length >= 2 ? "hourly bars" : "sessions · daily closes"}`,
        ),
      );
      if (chart.dataset.scaleMode === "percent") {
        legend.append(node("span", "chart-legend-summary", "Price and market overlays · percent change from period start"));
      }
      const theme = session.prefs.display.theme;
      legendItems({
        ticker: session.selected,
        prices: values,
        adjustedMin: numericValues.length ? Math.min(...numericValues) : null,
        adjustedMax: numericValues.length ? Math.max(...numericValues) : null,
        overlays: visibleOverlays.map((id) => {
          const definition = overlayDefinition(id);
          const series = valuesForOverlay(id, { bars: chartHistory, tickerData, chartData, dashboard: session.dashboard });
          const lastObservationIndex = series.findLastIndex(isNumericValue);
          return {
            definition,
            values: series,
            correlation: overlayCorrelation(id, tickerData, chartData),
            summary: definition?.kind === "fundamental"
              ? fundamentalSummary(id, chartData, chartHistory.at(-1)?.ts || chartHistory.at(-1)?.date)
              : "",
            observedOn: lastObservationIndex >= 0
              ? String(chartHistory[lastObservationIndex]?.ts || chartHistory[lastObservationIndex]?.date || "").slice(0, 10)
              : "",
          };
        }),
      }, { money: formatPrice }).forEach((item) => {
        const label = node("span", "chart-legend-item");
        if (item.id !== "price") label.dataset.overlay = item.id;
        if (item.title) label.title = item.title;
        const color = item.kind === "price"
          ? "var(--price)"
          : overlayColor(overlayDefinition(item.id).color, theme);
        label.append(legendKey(item, color), node("span", "legend-copy", item.text));
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
  if (!placedLanes) panel.append(renderChartLanes(chartSettings, chartHistory, tickerData, chartData, chartDataState, controls.lanes, activeId));
  panel.append(renderLaneControls(chartSettings, controls.lanes, chartDataState, onSettingsChange));
  if (categories.length) panel.append(filters);
  return panel;
}

function renderContracts(tickerData) {
  const contracts = tickerData?.contracts;
  if (!contracts) return null;
  const panel = renderDrawer({
    id: "contracts",
    title: "Government contracts",
    subtitle: "Trailing 12 months · SpaceX and Tesla awards",
    summary: contractsSummary(contracts),
    mobilePanel: "more",
  }, (body) => {
  const total = isNumericValue(contracts.ttm_obligated)
    ? formatPrice(contracts.ttm_obligated)
    : "unavailable (no dated source rows)";
  body.append(node("p", "news-score", `TTM federal obligations ${total}`));
  const freshness = contracts.freshness === "stale" ? "stale (>7 days old)"
    : contracts.freshness === "fresh" ? "current"
      : "unavailable";
  const sourceIds = contracts.source_ids?.length ? contracts.source_ids.join(", ") : "source unavailable";
  const observed = contracts.observed_at ? formatTime(contracts.observed_at, session.prefs.display.time_zone)
    : "observation date unavailable";
  body.append(node("p", "signal-note",
    `Rollup ${freshness} · as of ${contracts.as_of || "date unavailable"} · sources ${sourceIds} · last source observation ${observed}`));
  if (contracts.coverage === "unavailable") {
    body.append(node("p", "data-state", "No dated source rows support a trailing-12-month amount; zero is not inferred."));
  }
  const quarters = (contracts.by_agency || []).map(row => `${row.quarter}: ${formatPrice(row.obligated)}`);
  if (quarters.length) body.append(node("p", "signal-note", `Agency/fiscal-quarter breakdown · ${quarters.join(" · ")}`));
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
    item.append(node("p", "news-meta",
      `${row.date || "award date unavailable"} · ${amount} · ${row.source_id || "source unavailable"} · collected ${
        row.observed_at ? String(row.observed_at).slice(0, 10) : "date unavailable"
      }`));
    list.append(item);
  });
  if (!list.childElementCount) {
    body.append(node("p", "data-state", "No awards in the last 30 days."));
  } else {
    body.append(list);
  }
  });
  return panel;
}

function catalystMoveLabel(move) {
  if (!isNumericValue(move)) return "—";
  const arrow = Number(move) > 0 ? "▲ " : Number(move) < 0 ? "▼ " : "";
  return `${arrow}${formatPercent(move, 1)}`;
}

function renderRecentCatalysts(events, tickerData) {
  const list = node("div", "catalyst-recent-list");
  const chartBars = selectedChartBars(tickerData);
  const daily = validBars(tickerData?.price_history || []);
  events.forEach((event) => {
    const category = CATALYST_CATEGORIES.find((item) => item.id === event.category);
    const move = catalystMove(event, daily);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "catalyst-recent-row";
    const date = node("span", "catalyst-recent-date", catalystDateLabel(event.date));
    const title = node("span", "catalyst-recent-title");
    const dot = node("span", "catalyst-dot");
    const color = overlayColor(category.slot, session.prefs.display.theme);
    dot.style.setProperty("--catalyst-color", color);
    dot.dataset.catalystColor = color;
    dot.title = category.label;
    dot.setAttribute("aria-hidden", "true");
    const name = node("span", "catalyst-recent-name", event.title);
    name.title = event.title;
    title.append(dot, node("span", "visually-hidden", category.label), name);
    button.append(date, title, node("span", `catalyst-recent-move mono ${polarity(move)}`, catalystMoveLabel(move)));
    button.disabled = markerIndex(event, chartBars) < 0;
    if (button.disabled) button.title = "Outside the selected chart period. Choose a longer period to focus this catalyst.";
    button.addEventListener("click", () => selectCatalyst(event));
    list.append(button);
  });
  return list;
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
    const regimes = node("div", "regime-pills");
    const priceTrend = resolveTrend(tickerData?.price_history || [], servingPriceTrend(tickerData, session.selected));
    regimes.append(node("span", "regime-pill", `Price · ${trendLabel(priceTrend)}`));
    drivers.filter(row => ["DGS10", "CPI_YOY", "VIXCLS", "DTWEXBGS", "DCOILWTICO"].includes(row.series_id))
      .forEach(row => regimes.append(node("span", "regime-pill", `${driverLabel(row.series_id)} · ${driverTrend(row)}`)));
    body.append(regimes);
    const sensitivity = sensitivityRows(tickerData);
    body.append(node("h3", "signal-label", "Catalyst sensitivity"));
    if (!sensitivity.length) body.append(node("p", "signal-note", "Sensitivity unavailable: at least 12 paired releases and finite variation are required."));
    sensitivity.slice(0, 3).forEach(row => body.append(node("p", "signal-note",
      `${driverLabel(row.series_id)} · ${RELEASE_WINDOWS[row.window] || row.window} · ${signed(row.correlation_surprise)} correlation · n=${row.n_releases}`)));
    body.append(node("p", "signal-note", "Historical association, not a causal forecast."));
    const recent = catalystRows(session.dashboard, session.selected)
      .filter(row => String(row.date).slice(0, 10) <= String(session.dashboard?.generated_at).slice(0, 10)).slice(-5).reverse();
    body.append(node("h3", "signal-label", `Recent ${session.selected} catalysts`));
    if (!recent.length) body.append(node("p", "signal-note", "No recent catalysts have been published."));
    else body.append(renderRecentCatalysts(recent, tickerData));
    const all = rememberInner(node("details", "signal-drawer"), "all-drivers");
    const sort = node("select", "driver-sort");
    sort.setAttribute("aria-label", "Sort all driver trends");
    [["effect", "Effect magnitude"], ["name", "Series name"], ["change", "1M change magnitude"], ["correlation", "90D correlation magnitude"]].forEach(([value, label]) => {
      const option = node("option", "", label);
      option.value = value;
      option.selected = session.driverSort === value;
      sort.append(option);
    });
    let list = renderDriverList(sortedDrivers(tickerData, session.driverSort));
    sort.addEventListener("change", () => {
      session.driverSort = sort.value;
      const next = renderDriverList(sortedDrivers(tickerData, session.driverSort));
      list.replaceWith(next);
      list = next;
    });
    all.append(node("summary", "", `All driver trends · ${drivers.length} observations`), sort, list);
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
    strength.append(document.createTextNode(effect == null ? "" : ` ${signed(effect)}`));
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

function renderCurveBlock(view) {
  const block = node("div", "rates-curve");
  block.append(node("h3", "signal-label", "Treasury yield curve"));
  if (view.kind !== "ready") {
    block.append(node("p", view.kind === "error" ? "data-state error" : "data-state", view.message));
    return block;
  }
  const heading = node("div", "signal-heading");
  heading.append(node("span", "", view.regime || "Regime unavailable"), node("span", "mono", view.dates));
  block.append(heading);
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 300 100");
  svg.setAttribute("class", "yield-curve");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", view.ariaLabel);
  [["curve-ago", view.agoPath], ["curve-today", view.todayPath]].forEach(([className, pathData]) => {
    if (!pathData) return;
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("class", className);
    path.setAttribute("d", pathData);
    svg.append(path);
  });
  const labels = node("div", "rates-tenors");
  const changes = node("div", "rates-bps");
  view.tenors.forEach((tenor) => {
    labels.append(node("span", "", tenor.tenor));
    changes.append(node("span", tenor.bpClass, tenor.bpText));
  });
  block.append(svg, labels, changes, node("p", "signal-note", "Today (solid) versus 1M ago (dashed). The bp change uses the print on or before 30 calendar days earlier."));
  return block;
}

function renderFomcBlock(view) {
  const block = node("div", "rates-fomc");
  block.append(node("h3", "signal-label", "Next FOMC · Kalshi"));
  if (view.kind !== "ready") {
    block.append(node("p", view.kind === "error" ? "data-state error" : "data-state", view.message));
    return block;
  }
  const row = node("div", "rates-fomc-row");
  const copy = node("div", "");
  copy.append(node("p", "signal-heading", view.meeting));
  const bar = node("div", "fomc-bar");
  bar.setAttribute("role", "img");
  bar.setAttribute("aria-label", view.ariaLabel);
  view.segments.forEach((segment) => {
    const piece = node("span", segment.className);
    piece.style.width = segment.width;
    piece.title = segment.title;
    bar.append(piece);
  });
  copy.append(bar, node("p", "signal-note", view.legend));
  row.append(copy, signalSparkline(view.history.map((point) => ({ value: point.cut })), "var(--price)", view.sparkLabel));
  block.append(row);
  return block;
}

function renderPolicyBlock(view) {
  const block = node("div", "rates-policy");
  block.append(node("h3", "signal-label", "Implied policy path"));
  if (view.kind !== "ready") {
    block.append(node("p", view.kind === "error" ? "data-state error" : "data-state", view.message));
    return block;
  }
  block.append(node("p", "signal-note", view.note));
  const chart = node("div", "policy-bars");
  chart.setAttribute("role", "img");
  chart.setAttribute("aria-label", view.ariaLabel);
  view.bars.forEach((bar) => {
    const column = node("div", "policy-bar");
    column.append(node("span", "mono", bar.rateText));
    const track = node("span", "policy-track");
    const fill = node("span", "policy-fill");
    fill.style.height = bar.height;
    if (bar.reference) {
      const reference = node("span", "policy-reference");
      reference.style.bottom = bar.reference;
      track.append(reference);
    }
    track.append(fill);
    column.append(track, node("span", "", bar.meeting), node("span", "mono", bar.changeText));
    chart.append(column);
  });
  block.append(chart);
  return block;
}

function renderMacroPanels(tickerData, chartData, chartDataState) {
  const grid = node("div", "macro-panel-grid");
  const rows = driverRows(tickerData);
  const rates = rows.filter(row => ["DGS2", "DGS10", "DGS30", "T10Y2Y", "DFII10", "SOFR", "T10YIE"].includes(row.series_id));
  const ratesPanel = renderDrawer({
    id: "rates",
    title: "Rates & yields",
    subtitle: "Latest available observations · 1M change in basis points",
    summary: ratesSummary(rates),
    mobilePanel: "signals",
  }, (rateBody) => {
  const ratesDocument = session.dashboard?.rates;
  rateBody.append(
    renderCurveBlock(curveView(ratesDocument, session.dashboardState)),
    renderFomcBlock(fomcView(ratesDocument, session.dashboardState)),
    renderPolicyBlock(policyPathView(ratesDocument, session.dashboardState)),
  );
  if (!rates.length) rateBody.append(node("p", "data-state", session.dashboardState === "loading" ? "Loading rates…"
    : session.dashboardState === "error" ? "Rates could not be loaded. Refresh data to retry."
      : "Rates are unavailable until D1 observations reach the trend build."));
  if (chartDataState === "loading") rateBody.append(node("p", "data-state", "Loading rate history…"));
  else if (chartDataState === "error") rateBody.append(node("p", "data-state error", "Rate history could not be loaded. Refresh data to retry."));
  rates.forEach(row => {
    const entry = node("div", "rate-entry");
    const copy = node("div", "signal-heading");
    copy.append(node("span", "", driverLabel(row.series_id)),
      node("span", "mono", `${driverValue(row)} · ${driverChange(row)}`));
    entry.append(copy, signalSparkline((chartData?.macro_series?.[row.series_id] || []).slice(-63),
      "var(--price)", `${driverLabel(row.series_id)} over the last 63 stored sessions`));
    rateBody.append(entry);
  });
  });
  const inflation = renderDrawer({
    id: "inflation",
    title: "Inflation & release links",
    subtitle: `YoY trend · historical links to ${session.selected}`,
    summary: inflationSummary(tickerData),
    mobilePanel: "calendar",
    panelId: "mobile-panel-calendar",
  }, (releaseBody) => {
  const links = tickerData?.release_links;
  const latest = (links?.latest || []).filter(row => /^(CORE_)?(CPI|PCE)_YOY$/.test(row.series_id));
  if (!latest.length) releaseBody.append(node("p", "data-state", session.dashboardState === "loading" ? "Loading releases…"
    : session.dashboardState === "error" ? "Releases could not be loaded. Refresh data to retry."
      : "Release history is unavailable until M1 has published release-dated data."));
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
    const drawer = rememberInner(node("details", "signal-drawer"), "release-calendar");
    drawer.append(node("summary", "", `Release calendar · ${calendar.length} upcoming`));
    calendar.forEach(row => drawer.append(node("p", "signal-note", `${row.series} · ${formatTime(row.release_ts, session.prefs.display.time_zone)}`)));
    releaseBody.append(drawer);
  }
  releaseBody.append(node("p", "signal-note", "Monthly releases are excluded from daily rolling correlations. Missing consensus uses YoY change, not a consensus surprise. Links describe historical association, not causation."));
  });
  const drift = renderDrawer({
    id: "correlation",
    title: "Correlation drift",
    subtitle: `${session.selected} · 30D / 90D rolling correlations`,
    summary: correlationSummary(rows, chartData, chartDataState),
    mobilePanel: "signals",
  }, (drawer) => {
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
  });
  grid.append(ratesPanel, inflation, ...renderDetailPanels(tickerData, chartData, chartDataState), drift, renderCatalystCalendar(tickerData));
  return grid;
}

function renderDetailPanels(tickerData, chartData, chartDataState) {
  const panels = [];
  const create = (id, title, subtitle, summary) => {
    let body;
    panels.push(renderDrawer({ id, title, subtitle, summary, mobilePanel: "signals" }, (target) => { body = target; }));
    return body;
  };
  const market = create("market-comparison", "Market comparison", `1M adjusted returns · ${session.selected} and ETF proxies`, marketSummary());
  if (session.dashboardState === "loading") market.append(node("p", "data-state", "Loading market prices…"));
  else if (session.dashboardState === "error") market.append(node("p", "data-state error", "Market prices could not be loaded. Refresh data to retry."));
  [session.selected, "SPY", "DIA", "QQQ", "IWM", "XLY", "ITA", "SMH"].filter((id, i, ids) => ids.indexOf(id) === i).forEach(id => {
    const history = session.dashboard?.tickers?.[id]?.price_history || [];
    const quote = periodQuote(history, "1M");
    market.append(node("p", "signal-heading", `${id} · ${quote.available ? formatPercent(quote.returnValue, 1) : "History unavailable"}`),
      signalSparkline(history.slice(-63).map(bar => ({ value: chartValue(bar) })), "var(--price)", `${id} adjusted closes`));
  });
  const averages = create("moving-averages", "Moving averages", `${session.selected} · adjusted daily closes`, movingAverageSummary(tickerData?.price_history));
  if (session.dashboardState === "loading") averages.append(node("p", "data-state", "Loading daily closes…"));
  else if (session.dashboardState === "error") averages.append(node("p", "data-state error", "Daily closes could not be loaded."));
  movingAverageRows(tickerData?.price_history).forEach(row => averages.append(node("p", "signal-heading",
    `${row.window}D · ${row.value == null ? "Insufficient history" : `${formatPrice(row.value)} · price ${formatPercent(row.distance, 1)} vs average`}`)));
  const drivers = driverRows(tickerData);
  [
    ["volatility", "Volatility", ["VIXCLS"], "VIX levels and observed trend", seriesSummary(drivers, "VIXCLS", "VIX")],
    ["dollar-oil", "Dollar & oil", ["DTWEXBGS", "DCOILWTICO"], "Observed macro series, not forecasts", dollarSummary(drivers)],
    ["tariffs", "Tariffs & geopolitics", ["USEPUINDXD"], "Policy-uncertainty proxy and curated policy events", tariffSummary(drivers)],
  ].forEach(([id, title, ids, subtitle, summary]) => {
    const body = create(id, title, subtitle, summary);
    if (chartDataState === "loading") body.append(node("p", "data-state", "Loading historical series…"));
    else if (chartDataState === "error") body.append(node("p", "data-state error", "Historical series could not be loaded. Refresh data to retry."));
    ids.forEach(id => {
      const row = drivers.find(row => row.series_id === id);
      const entry = node("div", "rate-entry");
      entry.append(node("h3", "signal-label", driverLabel(id)),
        node("p", "", row ? `${driverValue(row)} · ${driverChange(row)} · ${driverTrend(row)}` : "Latest observation unavailable"),
        signalSparkline((chartData?.macro_series?.[id] || []).slice(-63), "var(--price)", `${driverLabel(id)} history`));
      if (id === "VIXCLS" && signalNumber(row?.value) != null) {
        const value = Number(row.value);
        entry.append(node("p", "signal-note", `${value < 15 ? "Calm" : value <= 20 ? "Normal" : value <= 25 ? "Elevated" : "Stressed"} VIX band · <15 / 15–20 / 20–25 / >25`));
      }
      body.append(entry);
    });
    if (title === "Tariffs & geopolitics") {
      const events = catalystRows(session.dashboard, session.selected).filter(event => event.category === "policy");
      if (!events.length) body.append(node("p", "signal-note", "No curated policy events available. Policy uncertainty is not a tariff-rate or geopolitical-risk measurement."));
      events.slice(-3).reverse().forEach(event => body.append(node("p", "signal-note", `${String(event.date).slice(0, 10)} · ${event.title}`)));
    }
  });
  return panels;
}

function renderCatalystCalendar(tickerData) {
  const events = catalystRows(session.dashboard, session.selected);
  const panel = renderDrawer({
    id: "catalyst-calendar",
    title: "Catalyst calendar",
    subtitle: `Published events for ${session.selected} and the macro universe`,
    summary: catalystSummary(events),
    mobilePanel: "calendar",
  }, (drawer) => {
  if (!events.length) drawer.append(node("p", "data-state", session.dashboardState === "loading" ? "Loading catalysts…"
    : session.dashboardState === "error" ? "Catalysts could not be loaded. Refresh data to retry." : "No catalyst feed has been published."));
  const bars = selectedChartBars(tickerData);
  events.forEach(event => {
    const row = node("div", "catalyst-calendar-row");
    const inRange = markerIndex(event, bars) >= 0;
    const button = action(`${String(event.date).slice(0, 10)} · ${event.title}`, "button-link catalyst-row-button", () => selectCatalyst(event));
    button.id = `calendar-${event.id}`;
    button.disabled = !inRange;
    row.append(button, node("p", "signal-note", `${event.source} · ${inRange ? "Focus chart marker" : "Outside the selected chart period"}`));
    if (/^https?:\/\//i.test(event.url || "")) {
      const link = node("a", "button-link", "Source");
      link.href = event.url; link.target = "_blank"; link.rel = "noopener noreferrer";
      row.append(link);
    }
    drawer.append(row);
  });
  drawer.append(node("p", "signal-note", "Nontrading-day catalysts align to the next stored session. Intraday markers use the first stored bar on the event date. No demo events are included."));
  });
  return panel;
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
  const sentiment = news.sentiment_history || [];
  body.append(signalSparkline(sentiment, "var(--series-3)", `${session.selected} rolling seven-day news sentiment`));
  body.append(node("p", "signal-note", sentiment.length
    ? `${sentiment.length} stored sentiment observations · through ${sentiment.at(-1).date}. Scores mix provider sentiment and a headline lexicon fallback.`
    : "Rolling sentiment history has not been published yet."));
  const chartSettings = chartSettingsFor(session.prefs, session.selected);
  const overlaySelected = chartSettings.overlays.includes("NEWS:SENTIMENT");
  const toggle = action(overlaySelected ? "Remove sentiment from chart" : "Overlay sentiment on chart", "overlay-chip", () => {
    const overlays = overlaySelected ? chartSettings.overlays.filter(id => id !== "NEWS:SENTIMENT")
      : addOverlay(chartSettings.overlays, "NEWS:SENTIMENT", overlayGroups(tickerData,
        session.chartDataState[session.selected] === "ready" ? session.chartData[session.selected] : null,
        session.dashboard, selectedChartBars(tickerData)).flatMap(group => group.overlays).map(item => item.id));
    updateChartSettings(session.selected, { ...chartSettings, overlays });
  });
  toggle.disabled = !overlaySelected && !sentiment.some(point => isNumericValue(point.value));
  toggle.setAttribute("aria-pressed", String(overlaySelected));
  body.append(toggle);
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

function renderCompanyPanel(tickerData, chartData, chartDataState) {
  const events = catalystRows(session.dashboard, session.selected)
    .filter(event => event.category === (session.selected === "SPCX" ? "space" : "robotaxi"));
  const title = session.selected === "TSLA" ? "Tesla · robotaxi & company themes"
    : session.selected === "SPCX" ? "SpaceX · operations & contracts" : `${session.selected} · company themes`;
  const panel = renderDrawer({
    id: "company",
    title,
    subtitle: "Published lake data only · no design-demo facts",
    summary: companySummary(tickerData, events),
    mobilePanel: "more",
  }, (body) => {
  if (session.dashboardState === "loading") body.append(node("p", "data-state", "Loading company events…"));
  else if (session.dashboardState === "error") body.append(node("p", "data-state error", "Company events could not be loaded. Refresh data to retry."));
  if (session.selected === "TSLA") {
    body.append(node("p", "signal-note", "Robotaxi fleet size, state permit counts and regional FSD approval counts have not been published as structured live measurements. Design-demo values are not shown."));
    ["deliveries", "fsd_subscribers"].forEach(metric => {
      const id = `FUNDAMENTAL:${metric}`;
      const observation = chartDataState === "ready"
        ? fundamentalObservation(id, chartData, session.dashboard?.generated_at) : null;
      const value = chartDataState === "loading" ? "Loading…"
        : chartDataState === "error" ? "Quarterly data could not be loaded"
          : observation ? fundamentalSummary(id, chartData, session.dashboard?.generated_at)
            : "No reported observation available";
      const source = observation?.source_id || (observation ? "source unavailable" : null);
      const provenance = observation
        ? ` · ${source} · reported ${observation.date || "date unavailable"}`
        : "";
      body.append(node("p", "signal-note",
        `${metric === "deliveries" ? "Reported deliveries" : "Reported FSD subscribers"} · ${value}${provenance}`));
    });
  } else if (session.selected === "SPCX") {
    body.append(node("p", "signal-note", "Active-satellite counts and launch-cadence measurements are unavailable as verified structured observations. Curated launch events and sourced federal awards are shown when present."));
    if (!tickerData?.contracts) body.append(node("p", "data-state", "Government-award data is unavailable in this snapshot; no zero or trend is inferred."));
  } else {
    body.append(node("p", "signal-note", "No company-specific operating-metrics feed is configured for this symbol. Available SEC financial series remain in the Fundamentals overlays."));
  }
  if (!events.length) body.append(node("p", "data-state", "No matching company operating events are available."));
  events.slice(-5).reverse().forEach(event => {
    const observed = event.observed_at ? ` · collected ${String(event.observed_at).slice(0, 10)}` : "";
    const sourceName = event.source || "source unavailable";
    const row = node("p", "signal-note", `${String(event.date).slice(0, 10)} · ${event.title} · ${sourceName}${observed} `);
    if (/^https?:\/\//i.test(event.url || "")) {
      const source = node("a", "button-link", "Source");
      source.href = event.url; source.target = "_blank"; source.rel = "noopener noreferrer";
      row.append(source);
    }
    body.append(row);
  });
  body.append(action("Review reported Fundamentals on the chart", "button-link", () => {
    session.overlayGroup = "Fundamentals";
    session.mobileView = "chart";
    renderDashboard();
    root.querySelector('[data-overlay-tab="Fundamentals"]')?.focus();
  }));
  });
  return panel;
}

function renderAboutData() {
  const panel = renderDrawer({
    id: "about-data",
    title: "About this data",
    summary: "Sources, trend definitions, methodology",
    mobilePanel: "more",
  }, (drawer) => {
  drawer.append(
    node("p", "signal-note", "Source IDs identify FRED/Treasury macro series, price-provider histories, SEC EDGAR financial facts and filings, curated release/event calendars, and provider news. Observed and available dates appear in driver tooltips; collection health and schedules are separate from observation freshness."),
    node("p", "signal-note", "Prices use split/dividend-adjusted daily closes for historical comparisons. Intraday bars and live quotes are separate; ETF comparisons use their own stored price histories."),
    node("p", "signal-note", "Driver trends use 5/21/63-session changes and a 1M z-score, not stock moving averages. Stock trend uses 20/50-day averages. The 1Y range is a high-low position, not an empirical percentile."),
    node("p", "signal-note", "Daily correlation windows are 30/90 sessions with at least 20/60 paired values. Effect = 90D correlation × 1M z-score; pressure = tanh(sum of finite effects / 3). Entirely missing pressure stays unavailable."),
    node("p", "signal-note", "Monthly macro releases use separate adjusted-return event windows and at least 12 paired releases. Without consensus, the basis is YoY change, not consensus surprise. Current/revised observations are not a real-time-vintage backtest."),
    node("p", "signal-note", "Quarterly fundamentals change on filing/release dates. Public float is reported USD, usually annual; estimated float shares in the short-interest lane are a separate calculation."),
    node("p", "signal-note", "News sentiment is a rolling mean of available daily scores over seven calendar days, mixing provider sentiment with a headline lexicon fallback. Missing days are not fabricated. Catalyst feeds are bounded published events, not an exhaustive corporate calendar."),
    action("View collection schedules and source health", "button-link", () => settings.open("refresh", "about-data")));
  });
  return panel;
}

let marketTimer = 0;

function clearMarketTimer() {
  if (!marketTimer) return;
  clearTimeout(marketTimer);
  marketTimer = 0;
}

function paintHeaderClock() {
  const zone = session.prefs?.display?.time_zone || "UTC";
  const now = new Date();
  const view = marketLabel(session.dashboard?.market, now, zone);
  const badge = root.querySelector("[data-market-status]");
  const date = root.querySelector("[data-header-date]");
  if (!badge && !date) {
    clearMarketTimer();
    return;
  }
  if (badge) {
    badge.dataset.marketStatus = view.status;
    badge.setAttribute("aria-label", view.text);
    const dot = badge.querySelector(".dot");
    if (dot) dot.className = `dot${view.dot ? ` ${view.dot}` : ""}`;
    const full = badge.querySelector(".market-label");
    const compact = badge.querySelector(".market-compact");
    if (full && full.textContent !== view.text) full.textContent = view.text;
    if (compact && compact.textContent !== view.compact) compact.textContent = view.compact;
  }
  if (date) {
    const text = headerDate(now, zone);
    if (date.textContent !== text) date.textContent = text;
    date.dateTime = now.toISOString();
  }
  armMarketTimer(view.boundary);
}

function armMarketTimer(boundary) {
  clearMarketTimer();
  marketTimer = setTimeout(paintHeaderClock, clockDelay(boundary, Date.now()));
}

function renderAppHeader() {
  const zone = session.prefs?.display?.time_zone || "UTC";
  const now = new Date();
  const view = marketLabel(session.dashboard?.market, now, zone);
  const header = node("header", "app-header");
  const brand = node("div", "brand-line");
  brand.append(node("h1", "", "Investor Dashboard"));
  const market = node("span", "market-badge");
  market.dataset.marketStatus = view.status;
  market.setAttribute("role", "status");
  market.setAttribute("aria-label", view.text);
  const dot = node("span", `dot${view.dot ? ` ${view.dot}` : ""}`);
  dot.setAttribute("aria-hidden", "true");
  market.append(dot, node("span", "market-label", view.text), node("span", "market-compact", view.compact));
  brand.append(market);
  header.append(brand);
  const actions = node("div", "header-actions");
  const when = session.dashboard?.generated_at
    ? formatTime(session.dashboard.generated_at, zone)
    : "";
  const lastRefresh = when ? `Data ${when}` : "Refresh data";
  const schedule = node("button", "");
  schedule.type = "button";
  schedule.append(node("span", "schedule-word", "Data"));
  if (when) {
    schedule.append(document.createTextNode(" "));
    schedule.append(node("span", "schedule-when", when));
  }
  schedule.addEventListener("click", () => settings.open("refresh", "header-refresh"));
  schedule.dataset.opener = "header-refresh";
  schedule.setAttribute("aria-label", `${lastRefresh}; open collection schedules and data refresh status`);
  const refresh = action("↻", "", () => refreshData(true));
  refresh.setAttribute("aria-label", "Refresh dashboard data");
  refresh.dataset.refresh = "data";
  const date = node("time", "header-date", headerDate(now, zone));
  date.dataset.headerDate = "date";
  date.dateTime = now.toISOString();
  actions.append(date, schedule, refresh, settings.renderAccountButton());
  header.append(actions);
  armMarketTimer(view.boundary);
  return header;
}

function renderMetricCard(metric) {
  const card = node("article", `panel stock-metric${metric.unavailable ? " stock-metric-unavailable" : ""}`);
  card.dataset.metricId = metric.id;
  const title = node("h3", "stock-metric-title", metric.name);
  card.append(title);
  if (metric.unit && !metric.unavailable) card.append(node("p", "signal-note", metric.unit));
  if (metric.series?.length) {
    const bars = node("div", "stock-bars");
    const reported = metric.series.filter((point) => point.reported);
    const max = Math.max(...reported.map((point) => point.value), 0);
    bars.setAttribute("role", "img");
    bars.setAttribute("aria-label", `${metric.name} by quarter`);
    metric.series.forEach((point) => {
      const bar = node("span", point.reported ? "stock-bar" : "stock-bar stock-bar-missing");
      const height = point.reported && max ? Math.max(8, Math.round((point.value / max) * 100)) : 8;
      bar.style.height = `${height}%`;
      bar.title = point.reported ? `${point.fiscal_period} ${formatMetricValue(point.value, metric.unit)}` : `${point.fiscal_period} not reported`;
      bars.append(bar);
    });
    card.append(bars);
  }
  const latest = node("p", "stock-latest");
  latest.append(document.createTextNode(metric.unavailable ? "Unavailable. " : `Latest ${metric.latestPeriod ? `${metric.latestPeriod} ` : ""}`));
  latest.append(node("span", "", metric.unavailable ? metric.sourceTitle : metric.latest));
  if (!metric.unavailable && metric.qoq) {
    latest.append(document.createTextNode(" "));
    latest.append(node("span", metric.qoqValue > 0 ? "positive" : metric.qoqValue < 0 ? "negative" : "neutral", `QoQ ${metric.qoq}`));
  }
  if (!metric.unavailable && metric.yoy) {
    latest.append(document.createTextNode(" "));
    latest.append(node("span", metric.yoyValue > 0 ? "positive" : metric.yoyValue < 0 ? "negative" : "neutral", `YoY ${metric.yoy}`));
  }
  card.append(latest);
  const provenance = node("p", "stock-provenance");
  if (metric.sourceUrl) {
    const link = node("a", "", metric.sourceTitle);
    link.href = metric.sourceUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    provenance.append(link);
  } else {
    provenance.append(document.createTextNode(metric.sourceTitle || "Source unavailable"));
  }
  const details = [metric.published, metric.confidence, metric.approval].filter(Boolean).join(" · ");
  if (details) provenance.append(document.createTextNode(`${metric.sourceUrl || metric.sourceTitle ? " · " : ""}${details}`));
  card.append(provenance);
  if (metric.xbrl) card.append(node("p", metric.xbrl === "XBRL mismatch" ? "stock-xbrl mismatch" : "stock-xbrl", metric.xbrl));
  return card;
}

function renderStockPage() {
  const tickerData = selectedData();
  const chartData = session.chartData[session.selected] || null;
  const chartDataState = session.chartDataState[session.selected] || "loading";
  const payload = session.stockPage[session.selected] || null;
  const pageState = session.stockPageState[session.selected] || "loading";
  const panels = stockPanels(payload, chartData);
  const badge = freshnessBadge(payload, pageState);
  const history = tickerData?.price_history || [];
  const bars = validBars(history);
  const latest = bars.at(-1);
  const dayChange = returns(history, 1);
  root.replaceChildren();
  applyTheme(session.prefs.display);
  root.append(renderAppHeader(), renderWatchlist());

  const heading = node("section", "panel stock-heading");
  heading.dataset.stockPanel = "overview";
  const back = action("← Dashboard", "button-link", () => navigateToDashboard());
  back.dataset.stockBack = "dashboard";
  const copy = node("div", "stock-heading-copy");
  const title = node("h2", "", `${session.selected} · ${companyName(session.selected, payload)}`);
  title.id = "stock-title";
  title.tabIndex = -1;
  const price = node("p", "stock-price");
  price.append(node("span", "stock-price-value", latest ? formatPrice(displayPrice(latest)) : "—"));
  if (isNumericValue(dayChange)) {
    price.append(node("span", polarity(dayChange), ` ${formatPercent(dayChange)}`));
  }
  const updated = session.dashboard?.generated_at
    ? `Updated ${formatTime(session.dashboard.generated_at, session.prefs.display.time_zone)}`
    : "Updated time unavailable";
  const freshness = node("p", "stock-freshness", badge.text);
  freshness.dataset.tone = badge.tone;
  copy.append(title, price, node("p", "overview-meta", updated), freshness);
  heading.append(back, copy);
  root.append(heading);

  const page = node("main", "stock-page");
  page.dataset.stockTab = session.stockTab;
  page.setAttribute("aria-label", `${session.selected} company page`);

  const chart = renderPricePanel(
    tickerData,
    chartData,
    chartDataState,
    selectChartPeriod,
    (next) => updateChartSettings(session.selected, next),
  );
  chart.classList.add("price-panel-compact");
  chart.dataset.stockPanel = "overview";

  const section = (id, label, cards, empty) => {
    const block = node("section", "stock-section");
    block.dataset.stockPanel = id;
    block.id = `stock-panel-${id}`;
    block.append(node("h3", "stock-section-title", label));
    if (!cards.length) block.append(node("p", "data-state", empty));
    else {
      const grid = node("div", "stock-metric-grid");
      cards.forEach((metric) => grid.append(renderMetricCard(metric)));
      block.append(grid);
    }
    return block;
  };

  const calls = callsCopy();
  const callsBlock = node("section", "stock-section");
  callsBlock.dataset.stockPanel = "calls";
  callsBlock.id = "stock-panel-calls";
  callsBlock.append(node("h3", "stock-section-title", "Quarterly call notes"));
  if (panels.guidance.length) {
    const grid = node("div", "stock-metric-grid");
    panels.guidance.forEach((metric) => grid.append(renderMetricCard(metric)));
    callsBlock.append(grid);
  }
  callsBlock.append(node("p", "signal-note", calls.guidance), node("p", "signal-note", calls.transcripts));

  page.append(
    chart,
    section("kpis", "Operating KPIs", panels.operating, emptyKpiCopy()),
    section(
      "fundamentals",
      panels.fundamentalsSource === "edgar" ? "SEC filings" : "IR fundamentals",
      panels.fundamentals,
      "No IR fundamentals are approved, and no SEC XBRL series is in this snapshot.",
    ),
    callsBlock,
  );

  const tabs = node("div", "stock-page-tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Company page sections");
  STOCK_TABS.forEach((item) => {
    const tab = action(item.label, "", () => {
      session.stockTab = item.id;
      renderDashboard();
      root.querySelector(`#stock-tab-${item.id}`)?.focus();
    });
    tab.id = `stock-tab-${item.id}`;
    tab.dataset.stockTab = item.id;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", item.id === "overview" ? "stock-title" : `stock-panel-${item.id}`);
    tab.setAttribute("aria-selected", String(item.id === session.stockTab));
    tab.tabIndex = item.id === session.stockTab ? 0 : -1;
    tab.addEventListener("keydown", (event) => {
      const next = nextStockTab(item.id, event.key);
      if (!next) return;
      event.preventDefault();
      root.querySelector(`#stock-tab-${next}`)?.click();
    });
    tabs.append(tab);
  });
  page.append(tabs);
  root.append(page);
  settings.refresh();
}

function renderResearchPage() {
  const errorMessage = dashboardBanner(session);
  const tickerData = selectedData();
  const chartData = session.chartData[session.selected] || null;
  const chartDataState = session.chartDataState[session.selected] || "loading";
  root.replaceChildren();
  applyTheme(session.prefs.display);
  root.append(renderAppHeader(), renderWatchlist());

  const heading = node("section", "panel research-heading");
  const copy = node("div", "research-heading-copy");
  const company = session.selected === "TSLA" ? "Tesla" : "SpaceX";
  const title = node("h2", "", `${company} (${session.selected}) research`);
  title.id = "research-title";
  title.tabIndex = -1;
  copy.append(title, node("p", "signal-note", "Published and curated data only; unsupported measurements remain unavailable."));
  heading.append(copy, action("← Dashboard", "button-link", () => navigateToDashboard()));
  root.append(heading);

  if (session.settingsError) {
    const { message, reopen } = session.settingsError;
    const alert = node("p", "data-state error", `${message} `);
    alert.setAttribute("role", "alert");
    alert.append(action("Open settings", "button-link", reopen));
    root.append(alert);
  }
  const researchPanelError = renderPanelSaveError();
  if (researchPanelError) root.append(researchPanelError);

  if (errorMessage) {
    const alert = node("p", "data-state error");
    alert.setAttribute("role", "alert");
    alert.textContent = `${errorMessage} `;
    alert.append(action("Try again", "button-link", () => refreshData(true)));
    root.append(alert);
  }

  const main = node("main", "research-grid");
  main.setAttribute("aria-label", `${session.selected} research`);
  const primary = node("div", "research-column");
  const price = renderPricePanel(
    tickerData,
    chartData,
    chartDataState,
    selectChartPeriod,
    (next) => updateChartSettings(session.selected, next),
  );
  primary.append(price, renderCompanyPanel(tickerData, chartData, chartDataState));
  const contracts = renderContracts(tickerData);
  if (contracts) primary.append(contracts);
  primary.append(renderAboutData());

  const side = node("aside", "research-column");
  side.setAttribute("aria-label", "News, filings and source records");
  side.append(renderNews(tickerData), renderFilings(tickerData));
  main.append(primary, side);
  root.append(main);

  const footer = node("footer", "dashboard-footer");
  footer.append(node("span", "", "Information for research; not investment advice."));
  footer.append(node("span", "", session.dashboard?.generated_at
    ? `Snapshot ${session.dashboard.generated_at}` : "Serving snapshot unavailable"));
  root.append(footer);
  settings.refresh();
}

function renderNotFoundPage() {
  root.replaceChildren();
  applyTheme(session.prefs.display);
  root.append(renderAppHeader(), renderWatchlist());
  const message = node("main", "panel research-not-found");
  message.append(node("h2", "", "Research page not found"));
  message.append(node("p", "signal-note", "Dedicated research pages are currently available for TSLA and SPCX."));
  message.append(action("Return to dashboard", "button-primary", () => navigateToDashboard()));
  root.append(message);
  settings.refresh();
}

function navigateToStock(ticker) {
  const hash = stockHash(ticker);
  if (!hash) return;
  if (session.route.page === "dashboard") session.dashboardSelection = session.selected;
  const symbol = hash.slice("#stock/".length);
  history.pushState(history.state, "", `/${hash}`);
  session.route = routeFromLocation(location.pathname, hash);
  session.selected = symbol;
  session.stockTab = "overview";
  renderDashboard();
  loadChartData(symbol);
  loadStockPage(symbol);
  requestAnimationFrame(() => root.querySelector("#stock-title")?.focus());
}

function navigateToResearch(ticker) {
  const path = tickerResearchPath(ticker);
  if (!path) return;
  if (session.route.page !== "research") session.dashboardSelection = session.selected;
  history.pushState(history.state, "", path);
  session.route = routeFromPath(path);
  session.selected = session.route.ticker;
  renderDashboard();
  loadChartData(session.selected);
  requestAnimationFrame(() => root.querySelector("#research-title")?.focus());
}

function navigateToDashboard(ticker = session.dashboardSelection || session.selected) {
  session.dashboardSelection = fallbackSelection(session.prefs, ticker);
  session.selected = session.dashboardSelection;
  session.route = { page: "dashboard" };
  history.pushState(history.state, "", "/");
  renderDashboard();
  loadChartData(session.selected);
}

function handleRoutePopstate() {
  const state = routeStateForLocation(location.pathname, location.hash, session.dashboardSelection, session.selected);
  session.route = state.route;
  session.dashboardSelection = state.dashboardSelection
    || fallbackSelection(session.prefs, session.selected);
  session.selected = state.selected || fallbackSelection(session.prefs, session.dashboardSelection);
  renderDashboard();
  loadChartData(session.selected);
  if (session.route.page === "stock") loadStockPage(session.selected);
  settings.openFromHash();
}

function notify(message, isError = false) {
  let notice;
  notice = node("p", `data-state${isError ? " error" : ""}`, message);
  notice.setAttribute("role", isError ? "alert" : "status");
  root.prepend(notice);
  window.setTimeout(() => notice?.remove(), 6000);
}

async function selectChartPeriod(periodId) {
  if (session.periodSave || session.prefs.display?.chart_period === periodId) return;
  session.periodSave = true;
  try {
    await savePrefs({ display: { ...session.prefs.display, chart_period: periodId } });
    if (session.prefs.display?.chart_period !== periodId) {
      session.prefs = {
        ...session.prefs,
        display: { ...session.prefs.display, chart_period: periodId },
      };
    }
    renderDashboard();
    root.querySelector(`[data-period="${periodId}"]`)?.focus();
  } catch (error) {
    notify(error.message, true);
  } finally {
    session.periodSave = false;
  }
}

function restoreDrawerFocus(id) {
  if (!id) return;
  root.querySelector(`[data-drawer-toggle="${id}"]`)?.focus();
}

function renderDashboard() {
  const restoreDrawer = document.activeElement?.dataset?.drawerToggle || null;
  const errorMessage = dashboardBanner(session);
  if (!session.prefs) return;
  if (session.route.page === "stock") {
    renderStockPage();
    return;
  }
  if (session.route.page === "research") {
    renderResearchPage();
    restoreDrawerFocus(restoreDrawer);
    return;
  }
  if (session.route.page === "not-found") {
    renderNotFoundPage();
    return;
  }
  session.selected = fallbackSelection(session.prefs, session.selected);
  const focusedTicker = document.activeElement?.closest(".watchlist .ticker-button")?.dataset.ticker;
  root.replaceChildren();
  applyTheme(session.prefs.display);
  root.append(renderAppHeader(), renderWatchlist());
  if (focusedTicker) root.querySelector(`[data-ticker="${focusedTicker}"]`)?.focus();

  if (session.settingsError) {
    const { message, reopen } = session.settingsError;
    const alert = node("p", "data-state error", `${message} `);
    alert.setAttribute("role", "alert");
    // Opening the dialog clears settingsError and redraws the page without this alert.
    alert.append(action("Open settings", "button-link", reopen));
    root.append(alert);
  }
  const panelSaveError = renderPanelSaveError();
  if (panelSaveError) root.append(panelSaveError);

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
  const mainGrid = node("main", "dashboard-grid");
  mainGrid.setAttribute("aria-label", `${session.selected} investor dashboard`);
  const primary = node("div", "primary-column");
  const price = renderPricePanel(tickerData, chartData, chartDataState, selectChartPeriod, (next) => updateChartSettings(session.selected, next));
  price.dataset.mobilePanel = "chart";
  price.id = "mobile-panel-chart";
  primary.append(price);
  primary.append(renderMacroPanels(tickerData, chartData, chartDataState));
  primary.append(renderCompanyPanel(tickerData, chartData, chartDataState));
  const contracts = renderContracts(tickerData);
  if (contracts) {
    contracts.dataset.mobilePanel = "more";
    primary.append(contracts);
  }
  const side = node("aside", "side-column");
  side.setAttribute("aria-label", "Macro drivers, news and filings");
  const news = renderNews(tickerData), filings = renderFilings(tickerData);
  news.dataset.mobilePanel = "more";
  news.id = "mobile-panel-more";
  filings.dataset.mobilePanel = "more";
  side.append(renderDrivers(tickerData), news, filings);
  primary.append(renderAboutData());
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
  restoreDrawerFocus(restoreDrawer);
}

async function refreshData(showLoading) {
  if (showLoading) {
    const refreshButton = root.querySelector('[data-refresh="data"]');
    if (refreshButton) {
      refreshButton.disabled = true;
      refreshButton.textContent = "…";
      refreshButton.setAttribute("aria-label", "Refreshing dashboard data");
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
  if (session.route.page === "stock") await loadStockPage(session.selected, true);
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
    const returnTicker = sessionStorage.getItem(authKeys.dashboardTicker);
    sessionStorage.removeItem(authKeys.dashboardTicker);
    session.dashboardSelection = fallbackSelection(session.prefs, returnTicker);
    const routeState = routeStateForLocation(
      location.pathname,
      location.hash,
      session.dashboardSelection,
      session.dashboardSelection,
    );
    session.route = routeState.route;
    session.dashboardSelection = routeState.dashboardSelection;
    session.selected = routeState.selected;
    renderDashboard();
    window.addEventListener("popstate", handleRoutePopstate);
    window.addEventListener("hashchange", () => {
      const stock = parseStockHash(location.hash);
      if (stock || session.route.page === "stock") handleRoutePopstate();
      settings.openFromHash();
    });
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
