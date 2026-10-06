const root = document.querySelector("#app");
const svgNS = "http://www.w3.org/2000/svg";
const authKeys = { state: "oauth_state", verifier: "oauth_verifier" };
const session = { config: null, accessToken: null, prefs: null, dashboard: null, status: null, selected: null };
const aliases = {
  DGS2: "2Y Treasury",
  DGS10: "10Y Treasury",
  DGS30: "30Y Treasury",
  T10Y2Y: "10Y–2Y curve",
  DFII10: "10Y real yield",
  VIXCLS: "VIX",
  CPI_YOY: "CPI year over year",
  CORE_CPI_YOY: "Core CPI year over year",
  PCE_YOY: "PCE year over year",
};

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
  history.replaceState({}, "", location.pathname);
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
  const response = await fetch(apiUrl(path), {
    headers: {
      Authorization: `Bearer ${session.accessToken}`,
      Accept: "application/json",
    },
    cache: "no-store",
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return body;
}

async function savePrefs(update) {
  const candidate = { ...session.prefs, ...update, version: session.prefs.version };
  const response = await fetch(apiUrl("prefs"), {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${session.accessToken}`,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(candidate),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || `Saving preferences failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  session.prefs = body;
}

function isNumericValue(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

// Charts and returns use adj_close (split- and dividend-adjusted, continuous across corporate
// actions); displayed prices use close_raw (the actual traded close). Both fall back to close,
// which older rows carry alone.
function chartValue(row) {
  if (!row) return null;
  return isNumericValue(row.adj_close) ? Number(row.adj_close) : isNumericValue(row.close) ? Number(row.close) : null;
}

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
  if (!history || history.length <= offset) return null;
  const before = chartValue(history[history.length - 1 - offset]);
  const latest = chartValue(history.at(-1));
  if (before === null || latest === null) return null;
  return before ? latest / before - 1 : null;
}

function netPressure(tickerData) {
  const rows = tickerData?.trend?.rows || [];
  const row = rows.find((item) => isNumericValue(item.net_pressure));
  return row ? Number(row.net_pressure) : null;
}

function polarity(value) {
  if (!isNumericValue(value) || Number(value) === 0) return "neutral";
  return Number(value) > 0 ? "positive" : "negative";
}

function drawChart(history, ticker) {
  if (!history?.length) return null;
  const values = history.map(chartValue).filter((value) => value !== null);
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = max - min || 1;
  const left = 70;
  const right = 890;
  const top = 18;
  const bottom = 178;
  const points = values.map((value, index) => {
    const x = left + (index / (values.length - 1)) * (right - left);
    const y = bottom - ((value - min) / spread) * (bottom - top);
    return [x, y];
  });
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("class", "price-chart");
  svg.setAttribute("viewBox", "0 0 940 210");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${ticker} daily closing prices over ${values.length} sessions`);
  const title = document.createElementNS(svgNS, "title");
  title.textContent = `${ticker} daily close, adjusted for splits and dividends`;
  const desc = document.createElementNS(svgNS, "desc");
  desc.textContent = `Adjusted price range ${formatPrice(min)} to ${formatPrice(max)}. Most recent close ${formatPrice(displayPrice(history.at(-1)))}.`;
  svg.append(title, desc);

  const defs = document.createElementNS(svgNS, "defs");
  const gradient = document.createElementNS(svgNS, "linearGradient");
  gradient.setAttribute("id", "price-fill");
  gradient.setAttribute("x1", "0");
  gradient.setAttribute("x2", "0");
  gradient.setAttribute("y1", "0");
  gradient.setAttribute("y2", "1");
  const stopTop = document.createElementNS(svgNS, "stop");
  stopTop.setAttribute("offset", "0%");
  stopTop.setAttribute("stop-color", "#7db4ff");
  stopTop.setAttribute("stop-opacity", "0.28");
  const stopBottom = document.createElementNS(svgNS, "stop");
  stopBottom.setAttribute("offset", "100%");
  stopBottom.setAttribute("stop-color", "#7db4ff");
  stopBottom.setAttribute("stop-opacity", "0");
  gradient.append(stopTop, stopBottom);
  defs.append(gradient);
  svg.append(defs);

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
    label.setAttribute("fill", "#8997a9");
    label.setAttribute("font-size", "11");
    label.setAttribute("font-family", "IBM Plex Mono, monospace");
    label.textContent = formatPrice(max - ((max - min) / 3) * index);
    svg.append(label);
  }
  const linePath = points.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
  const area = document.createElementNS(svgNS, "path");
  area.setAttribute("class", "chart-area");
  area.setAttribute("d", `${linePath} L${right} ${bottom} L${left} ${bottom} Z`);
  const path = document.createElementNS(svgNS, "path");
  path.setAttribute("class", "chart-path");
  path.setAttribute("d", linePath);
  svg.append(area, path);

  const last = points.at(-1);
  const marker = document.createElementNS(svgNS, "circle");
  marker.setAttribute("cx", String(last[0]));
  marker.setAttribute("cy", String(last[1]));
  marker.setAttribute("r", "4");
  marker.setAttribute("fill", "#d8e9ff");
  marker.setAttribute("stroke", "#7db4ff");
  marker.setAttribute("stroke-width", "2");
  svg.append(marker);
  return svg;
}

function statCard(label, value, foot, valueClass = "") {
  const card = node("div", "stat-card");
  card.append(node("span", "stat-label", label));
  card.append(node("strong", `stat-value ${valueClass}`.trim(), value));
  card.append(node("span", "stat-foot", foot));
  return card;
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
  const tickers = session.prefs?.tickers || [];
  tickers.forEach((ticker) => {
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
    });
    button.append(node("span", "ticker-symbol", ticker));
    button.append(
      node(
        "span",
        `ticker-meta ${polarity(returns(history, 1))}`,
        latest ? `${formatPrice(displayPrice(latest))} · ${formatPercent(returns(history, 1))}` : "Waiting for history",
      ),
    );
    list.append(button);
  });
  return list;
}

function renderPricePanel(tickerData) {
  const panel = node("section", "panel");
  const history = (tickerData?.price_history || []).filter((row) => chartValue(row) !== null);
  const heading = sectionHeader(
    "Price history",
    tickerData?.price_as_of ? `Daily close · as of ${tickerData.price_as_of}` : "Daily close · five-year history",
  );
  const latest = history.at(-1);
  if (latest) {
    const summary = node("div", "price-summary");
    summary.append(node("strong", "", formatPrice(displayPrice(latest))));
    summary.append(node("span", `mono ${polarity(returns(history, 1))}`, `${formatPercent(returns(history, 1))} 1D`));
    heading.append(summary);
  }
  panel.append(heading);
  if (history.length >= 2) {
    const wrap = node("div", "chart-wrap");
    const chart = drawChart(history, session.selected);
    if (chart) {
      wrap.append(chart);
      panel.append(wrap);
      const legend = node("div", "chart-legend");
      legend.append(node("span", "", `${history.length.toLocaleString()} sessions`));
      legend.append(
        node(
          "span",
          "",
          `Adjusted ${formatPrice(Math.min(...history.map(chartValue)))} – ${formatPrice(Math.max(...history.map(chartValue)))}`,
        ),
      );
      panel.append(legend);
    } else {
      panel.append(node("p", "data-state", "Price history is incomplete; waiting for more valid observations."));
    }
  } else {
    panel.append(
      node(
        "p",
        "data-state",
        history.length === 1
          ? "Only one daily close is available; a line chart needs at least two points."
          : "No historical closes are published yet. Start the initial backfill and this chart will fill as batches complete.",
      ),
    );
  }
  return panel;
}

function renderStats(tickerData) {
  const history = tickerData?.price_history || [];
  const oneDay = returns(history, 1);
  const oneMonth = returns(history, 21);
  const pressure = netPressure(tickerData);
  const grid = node("section", "stats-grid");
  grid.setAttribute("aria-label", "Selected ticker summary");
  grid.append(
    statCard("LAST CLOSE", history.length ? formatPrice(displayPrice(history.at(-1))) : "—", tickerData?.price_as_of || "No data"),
    statCard("1D CHANGE", formatPercent(oneDay), "Previous session", polarity(oneDay)),
    statCard("1M CHANGE", formatPercent(oneMonth), "21 trading sessions", polarity(oneMonth)),
    statCard(
      "MACRO PRESSURE",
      pressure === null ? "—" : pressure.toFixed(2),
      "Trend model · −1 to +1",
      polarity(pressure),
    ),
  );
  return grid;
}

function renderDrivers(tickerData) {
  const panel = node("section", "panel");
  panel.append(sectionHeader("Macro drivers", "Trend model · latest published observation"));
  const rows = tickerData?.trend?.rows || [];
  const drivers = [...rows]
    .filter((row) => row.series_id && isNumericValue(row.effect))
    .sort((a, b) => Math.abs(Number(b.effect)) - Math.abs(Number(a.effect)))
    .slice(0, 5);
  if (!drivers.length) {
    panel.append(node("p", "data-state", "Driver trends are unavailable until daily prices and macro observations have been processed."));
    return panel;
  }
  const list = node("ul", "driver-list");
  drivers.forEach((row) => {
    const item = node("li", "driver-row");
    item.append(node("span", "driver-name", aliases[row.series_id] || row.series_id));
    const effect = Number(row.effect);
    item.append(node("span", `driver-value ${polarity(effect)}`, `${effect > 0 ? "+" : ""}${effect.toFixed(2)}`));
    list.append(item);
  });
  const body = node("div", "panel-body");
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

function renderStatus() {
  const panel = node("section", "panel");
  const status = session.status || session.dashboard?.status;
  panel.append(sectionHeader("Data freshness", status?.generated_at ? `Updated ${formatTime(status.generated_at, session.prefs.display.time_zone)}` : "Collector status"));
  const jobs = status?.jobs || [];
  if (!jobs.length) {
    panel.append(node("p", "data-state", "Refresh status is not available yet. It appears after the first collector run."));
    return panel;
  }
  const list = node("ul", "status-list");
  jobs
    .slice()
    .sort((a, b) => String(a.job).localeCompare(String(b.job)))
    .forEach((job) => {
      const item = node("li", "status-row");
      const label = node("span");
      const dot = node("span", `dot ${job.status === "ok" ? "ok" : job.status === "partial" ? "partial" : job.status === "failed" ? "failed" : ""}`);
      label.append(dot, document.createTextNode(` ${job.name || job.job}`));
      const last = formatTime(job.last_run, session.prefs.display.time_zone);
      item.append(label, node("span", "mono", last));
      item.title = job.last_outcome ? `Last outcome: ${job.last_outcome}; failed sources: ${job.failed_sources || 0}` : "No successful run has been recorded.";
      list.append(item);
    });
  const body = node("div", "panel-body");
  body.append(list);
  panel.append(body);
  return panel;
}

function renderSettings(onMessage) {
  const panel = node("section", "panel");
  panel.append(sectionHeader("Watchlist & display", "Preferences are private to your signed-in account."));
  const form = node("form", "control-row");
  form.setAttribute("aria-label", "Add a ticker to the watchlist");
  const field = node("div", "field-row");
  const label = node("label", "visually-hidden", "Ticker symbol");
  label.htmlFor = "new-ticker";
  const input = node("input");
  input.id = "new-ticker";
  input.name = "ticker";
  input.placeholder = "Add ticker (e.g. NVDA)";
  input.autocomplete = "off";
  input.maxLength = 10;
  input.required = true;
  field.append(label, input);
  const add = node("button", "button-primary", "Add ticker");
  add.type = "submit";
  form.append(field, add);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const ticker = input.value.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9.-]{0,9}$/.test(ticker)) {
      onMessage("Enter a valid ticker symbol.", true);
      return;
    }
    if (session.prefs.tickers.includes(ticker)) {
      onMessage(`${ticker} is already on your watchlist.`, true);
      return;
    }
    add.disabled = true;
    try {
      await savePrefs({ tickers: [...session.prefs.tickers, ticker] });
      session.selected = ticker;
      onMessage(`${ticker} added. Its historical backfill will start automatically.`);
      await refreshData(false);
    } catch (error) {
      onMessage(error.message, true);
    } finally {
      add.disabled = false;
    }
  });

  const paletteLabel = node("label", "", "Up/down colors");
  paletteLabel.htmlFor = "palette";
  const palette = document.createElement("select");
  palette.id = "palette";
  [
    ["green-red", "Green / red"],
    ["red-green", "Red / green"],
    ["blue-orange", "Blue / orange"],
  ].forEach(([value, labelText]) => {
    const option = node("option", "", labelText);
    option.value = value;
    option.selected = session.prefs.display.updown_palette === value;
    palette.append(option);
  });
  palette.addEventListener("change", async () => {
    try {
      await savePrefs({
        display: { ...session.prefs.display, updown_palette: palette.value },
      });
      onMessage("Display preference saved.");
      renderDashboard();
    } catch (error) {
      onMessage(error.message, true);
      palette.value = session.prefs.display.updown_palette;
    }
  });
  const timezoneLabel = node("label", "", "Time zone");
  timezoneLabel.htmlFor = "timezone";
  const timezone = document.createElement("select");
  timezone.id = "timezone";
  [
    ["America/Los_Angeles", "Pacific"],
    ["America/New_York", "Eastern"],
    ["UTC", "UTC"],
  ].forEach(([value, labelText]) => {
    const option = node("option", "", labelText);
    option.value = value;
    option.selected = session.prefs.display.time_zone === value;
    timezone.append(option);
  });
  timezone.addEventListener("change", async () => {
    try {
      await savePrefs({ display: { ...session.prefs.display, time_zone: timezone.value } });
      onMessage("Display preference saved.");
      renderDashboard();
    } catch (error) {
      onMessage(error.message, true);
      timezone.value = session.prefs.display.time_zone;
    }
  });
  const controls = node("div", "control-row");
  controls.append(paletteLabel, palette, timezoneLabel, timezone);
  panel.append(form, controls);
  return panel;
}

function renderDashboard(errorMessage = "") {
  if (!session.prefs) return;
  if (!session.selected || !session.prefs.tickers.includes(session.selected)) {
    session.selected = session.prefs.pinned?.[0] || session.prefs.tickers[0];
  }
  root.replaceChildren();
  root.dataset.palette = session.prefs.display.updown_palette;

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
  actions.append(action("Sign out", "", signOut));
  header.append(actions);
  root.append(header);
  root.append(renderWatchlist());

  if (errorMessage) {
    const alert = node("p", "data-state error");
    alert.setAttribute("role", "alert");
    alert.textContent = errorMessage;
    root.append(alert);
  }

  const tickerData = selectedData();
  const mainGrid = node("main", "dashboard-grid");
  mainGrid.setAttribute("aria-label", `${session.selected} investor dashboard`);
  const primary = node("div");
  primary.append(renderStats(tickerData), renderPricePanel(tickerData), renderFundamentals());
  const side = node("aside", "side-column");
  side.setAttribute("aria-label", "Macro drivers and data status");
  side.append(renderDrivers(tickerData), renderStatus());
  mainGrid.append(primary, side);
  root.append(mainGrid);

  let notice;
  const onMessage = (message, isError = false) => {
    notice = node("p", `data-state${isError ? " error" : ""}`, message);
    notice.setAttribute("role", isError ? "alert" : "status");
    root.prepend(notice);
    window.setTimeout(() => notice?.remove(), 6000);
  };
  root.append(renderSettings(onMessage));
  const footer = node("footer", "dashboard-footer");
  footer.append(node("span", "", "Information for research; not investment advice."));
  footer.append(node("span", "", session.dashboard?.generated_at ? `Snapshot ${session.dashboard.generated_at}` : "Serving snapshot unavailable"));
  root.append(footer);

  if (!session.dashboard && !errorMessage) {
    root.querySelector(".price-panel-state")?.remove();
  }
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
  if (dashboardResult.status === "fulfilled") session.dashboard = dashboardResult.value;
  if (statusResult.status === "fulfilled") session.status = statusResult.value;
  const failures = [dashboardResult, statusResult]
    .filter((result) => result.status === "rejected")
    .map((result) => result.reason);
  if (failures.some((error) => error.status === 401)) {
    session.accessToken = null;
    showGate("Your session expired", "Sign in again to continue to your private dashboard.", "", true);
    return;
  }
  const dashboardError = failures.find((error) => error.status !== 503) || failures[0];
  const message =
    dashboardResult.status === "rejected" && dashboardResult.reason.status === 503
      ? "Dashboard data has not been published. Start the historical backfill; the serving snapshot is rebuilt as data arrives."
      : dashboardError
        ? dashboardError.message
        : "";
  renderDashboard(message);
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
    session.selected = session.prefs.pinned?.[0] || session.prefs.tickers[0];
    renderDashboard();
    await refreshData(false);
  } catch (error) {
    if (error.status === 401) {
      showGate("Your session expired", "Sign in again to continue to your private dashboard.", "", true);
      return;
    }
    showGate("Dashboard could not load", "The preferences service did not return your account settings.", error.message, true);
  }
}

start();
