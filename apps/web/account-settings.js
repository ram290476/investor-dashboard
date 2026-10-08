// Account menu and the tabbed Account settings dialog (issue #36). Every user setting lives here:
// My tickers (add, pin, reorder, remove), Theme & display, Profile & time zone, Data refresh.
// Changes apply at once and autosave through PUT /prefs, one request at a time.

import { PERIODS, chartValue, isNumericValue, sessionReturn, validBars } from "./chart-period.js";
import { DEFAULT_THEME, THEMES, themeProperties } from "./theme.js";
import {
  MAX_PINNED,
  MAX_TICKERS,
  PALETTES,
  PIN_LIMIT_MESSAGE,
  SETTINGS_TABS,
  TIME_ZONE_PICKS,
  addTicker,
  canRemove,
  historyState,
  moveTicker,
  parseSettingsHash,
  refreshSummary,
  removeTicker,
  restoreTicker,
  settingsHash,
  tabAfterKey,
  timeZoneLabel,
  togglePin,
  validateNewTicker,
} from "./settings-model.js";

const svgNS = "http://www.w3.org/2000/svg";
const REORDER_DEBOUNCE_MS = 400;
const UNDO_MS = 5000;
const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function button(label, className, onClick, key) {
  const element = el("button", className, label);
  element.type = "button";
  if (key) element.dataset.key = key;
  element.addEventListener("click", onClick);
  return element;
}

function copyPrefs(prefs) {
  return {
    ...prefs,
    tickers: [...prefs.tickers],
    pinned: [...(prefs.pinned || [])],
    display: { ...prefs.display },
    chart_settings: Object.fromEntries(
      Object.entries(prefs.chart_settings || {}).map(([ticker, settings]) => [
        ticker,
        { overlays: [...(settings.overlays || [])], lanes: [...(settings.lanes || [])] },
      ]),
    ),
  };
}

function sparkline(history) {
  const values = validBars(history).slice(-22).map(chartValue);
  if (values.length < 2) return el("span", "spark-empty", "—");
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 64 20");
  svg.setAttribute("class", `spark ${values.at(-1) >= values[0] ? "positive" : "negative"}`);
  svg.setAttribute("aria-hidden", "true");
  const line = document.createElementNS(svgNS, "polyline");
  line.setAttribute(
    "points",
    values.map((value, index) => `${((index / (values.length - 1)) * 64).toFixed(1)},${(18 - ((value - min) / span) * 16).toFixed(1)}`).join(" "),
  );
  svg.append(line);
  return svg;
}

/**
 * ctx: { session, putPrefs(body), getPrefs(), onPrefsChange(), onTickerAdded(ticker), onUnauthorized(),
 *        onSaveError(message, reopen),
 *        signOut(), formatPrice(n), formatPercent(n, digits), displayPrice(row), formatTime(value, timeZone),
 *        browserTimeZone() }
 */
export function createAccountSettings(ctx) {
  const { session } = ctx;
  let backdrop = null;
  let dialog = null;
  let panel = null;
  let tabButtons = [];
  let saveNote = null;
  let live = null;
  let toast = null;
  let activeTab = "tickers";
  let opener = null;
  let confirmed = null;
  let saving = false;
  let dirty = false;
  let timer = null;
  let undoTimer = null;
  let tabError = null;
  let addedSinceSave = [];
  let addDraft = "";
  let addError = "";
  let dragTicker = null;

  const isOpen = () => Boolean(backdrop && !backdrop.hidden);

  function announce(message) {
    if (!live) return;
    live.textContent = "";
    window.setTimeout(() => (live.textContent = message), 30);
  }

  function setSaveNote(text, tone = "") {
    if (!saveNote) return;
    saveNote.textContent = text;
    saveNote.className = `settings-save ${tone}`.trim();
  }

  // ---------------------------------------------------------------- persistence
  function commit(next, { debounce = false, message = "", added = null } = {}) {
    session.prefs = next;
    tabError = null;
    if (added) addedSinceSave.push(added);
    dirty = true;
    setSaveNote("Saving…");
    if (message) announce(message);
    ctx.onPrefsChange();
    renderPanel();
    window.clearTimeout(timer);
    if (debounce) timer = window.setTimeout(flush, REORDER_DEBOUNCE_MS);
    else flush();
  }

  async function flush() {
    window.clearTimeout(timer);
    timer = null;
    if (saving || !dirty) return;
    saving = true;
    dirty = false;
    const sent = copyPrefs(session.prefs);
    const added = addedSinceSave;
    addedSinceSave = [];
    try {
      const saved = await ctx.putPrefs({
        tickers: sent.tickers,
        pinned: sent.pinned,
        display: sent.display,
        chart_settings: sent.chart_settings,
        version: confirmed.version,
      });
      confirmed = copyPrefs(saved);
      // Keep edits made while this request was in flight; they go out next with the new version.
      session.prefs = dirty ? { ...session.prefs, version: saved.version } : copyPrefs(saved);
      setSaveNote(dirty ? "Saving…" : "Saved", dirty ? "" : "ok");
      added.forEach((ticker) => ctx.onTickerAdded(ticker));
    } catch (error) {
      window.clearTimeout(timer);
      dirty = false;
      addedSinceSave = [];
      if (error.status === 401) {
        ctx.onUnauthorized();
        return;
      }
      if (error.status === 409) {
        try {
          const fresh = await ctx.getPrefs();
          confirmed = copyPrefs(fresh);
          session.prefs = copyPrefs(fresh);
          tabError = { message: "Settings changed in another tab. Reloaded.", tone: "notice" };
        } catch (reloadError) {
          session.prefs = copyPrefs(confirmed);
          tabError = { message: reloadError.message, retry: () => commit(sent) };
        }
      } else {
        session.prefs = copyPrefs(confirmed);
        tabError = { message: error.message, retry: () => commit({ ...sent, version: confirmed.version }) };
      }
      setSaveNote(error.status === 409 ? "Reloaded" : "Not saved", "error");
      ctx.onPrefsChange();
      if (isOpen()) {
        announce(tabError.message);
        renderPanel();
      } else {
        // The dialog closed before the save finished; say so on the page, with a way back.
        ctx.onSaveError(`Settings not saved: ${tabError.message}`, () => open(activeTab, "account"));
      }
    } finally {
      saving = false;
      if (dirty && !timer) flush();
    }
  }

  // ---------------------------------------------------------------- dialog shell
  function build() {
    backdrop = el("div", "settings-backdrop");
    backdrop.hidden = true;
    backdrop.addEventListener("mousedown", (event) => {
      if (event.target !== backdrop) return;
      // Keep the browser from moving focus to the backdrop so close() can restore it to the opener.
      event.preventDefault();
      close();
    });
    dialog = el("div", "settings-dialog");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "settings-title");
    dialog.addEventListener("keydown", onDialogKeydown);

    const head = el("div", "settings-head");
    head.append(el("h2", "", "Account settings"));
    head.querySelector("h2").id = "settings-title";
    saveNote = el("span", "settings-save");
    saveNote.setAttribute("aria-hidden", "true");
    const closeButton = button("✕", "settings-close", close);
    closeButton.setAttribute("aria-label", "Close account settings");
    head.append(saveNote, closeButton);

    const tablist = el("div", "settings-tabs");
    tablist.setAttribute("role", "tablist");
    tablist.setAttribute("aria-label", "Settings sections");
    tabButtons = SETTINGS_TABS.map((tab) => {
      const tabButton = button(tab.label, "settings-tab", () => selectTab(tab.id));
      tabButton.id = `settings-tab-${tab.id}`;
      tabButton.dataset.tab = tab.id;
      tabButton.setAttribute("role", "tab");
      tabButton.setAttribute("aria-controls", "settings-panel");
      tabButton.addEventListener("keydown", (event) => {
        const next = tabAfterKey(tab.id, event.key);
        if (!next) return;
        event.preventDefault();
        selectTab(next);
        tabButtons.find((item) => item.dataset.tab === next)?.focus();
      });
      return tabButton;
    });
    tablist.append(...tabButtons);

    panel = el("div", "settings-panel");
    panel.id = "settings-panel";
    panel.setAttribute("role", "tabpanel");
    panel.tabIndex = 0;

    toast = el("div", "settings-toast");
    toast.hidden = true;
    live = el("p", "visually-hidden");
    live.setAttribute("aria-live", "polite");

    dialog.append(head, tablist, panel, toast, live);
    backdrop.append(dialog);
    document.body.append(backdrop);
  }

  function onDialogKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const items = [...dialog.querySelectorAll(FOCUSABLE)].filter((item) => !item.closest("[hidden]"));
    if (!items.length) return;
    const first = items[0];
    const last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function selectTab(tabId) {
    const activeTabBefore = activeTab;
    activeTab = SETTINGS_TABS.some((tab) => tab.id === tabId) ? tabId : "tickers";
    tabButtons.forEach((tabButton) => {
      const selected = tabButton.dataset.tab === activeTab;
      tabButton.setAttribute("aria-selected", String(selected));
      tabButton.tabIndex = selected ? 0 : -1;
    });
    panel.setAttribute("aria-labelledby", `settings-tab-${activeTab}`);
    if (tabId !== activeTabBefore) tabError = null;
    history.replaceState(history.state, "", settingsHash(activeTab));
    renderPanel();
  }

  function open(tabId = "tickers", from = null) {
    if (!session.prefs) return;
    if (!backdrop) build();
    opener = from || "account";
    confirmed = copyPrefs(session.prefs);
    if (session.settingsError) {
      session.settingsError = null;
      ctx.onPrefsChange();
    }
    setSaveNote("");
    backdrop.hidden = false;
    document.body.classList.add("modal-open");
    document.querySelector("#app")?.setAttribute("inert", "");
    selectTab(tabId);
    tabButtons.find((tabButton) => tabButton.dataset.tab === activeTab)?.focus();
  }

  function close() {
    if (!isOpen()) return;
    flush();
    hideToast();
    backdrop.hidden = true;
    document.body.classList.remove("modal-open");
    document.querySelector("#app")?.removeAttribute("inert");
    if (parseSettingsHash(location.hash)) history.replaceState(history.state, "", location.pathname + location.search);
    document.querySelector(`[data-opener="${opener}"]`)?.focus();
  }

  // Re-render the active tab and keep focus on the same control when it still exists.
  function renderPanel() {
    if (!panel) return;
    const focusKey = panel.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    panel.replaceChildren();
    if (tabError) panel.append(renderTabError());
    if (activeTab === "tickers") panel.append(...renderTickersTab());
    if (activeTab === "theme") panel.append(...renderThemeTab());
    if (activeTab === "profile") panel.append(...renderProfileTab());
    if (activeTab === "refresh") panel.append(...renderRefreshTab());
    if (focusKey) {
      const target = panel.querySelector(`[data-key="${CSS.escape(focusKey)}"]`);
      (target && !target.disabled ? target : panel).focus();
    }
  }

  function renderTabError() {
    const box = el("p", `settings-alert ${tabError.tone || "error"}`);
    box.setAttribute("role", tabError.tone === "notice" ? "status" : "alert");
    box.append(document.createTextNode(`${tabError.message} `));
    if (tabError.retry) box.append(button("Retry", "button-link", tabError.retry, "retry"));
    return box;
  }

  // ---------------------------------------------------------------- My tickers
  function renderTickersTab() {
    const prefs = session.prefs;
    const intro = el("div", "settings-intro");
    const count = `${prefs.tickers.length} ticker${prefs.tickers.length === 1 ? "" : "s"}`;
    intro.append(el("h3", "", `My tickers · ${count} · ${prefs.pinned.length} of ${MAX_PINNED} pinned`));
    intro.append(el("p", "settings-hint", "Pinned tickers come first in the ticker bar. The order here is the bar order."));

    const list = el("ol", "my-tickers");
    list.setAttribute("aria-label", "My tickers");
    prefs.tickers.forEach((ticker, index) => list.append(renderTickerRow(ticker, index)));
    return [intro, list, renderAddSection()];
  }

  function renderTickerRow(ticker, index) {
    const prefs = session.prefs;
    const pinned = prefs.pinned.includes(ticker);
    const atLimit = !pinned && prefs.pinned.length >= MAX_PINNED;
    const record = session.dashboard?.tickers?.[ticker];
    const history = record?.price_history || [];
    const latest = validBars(history).at(-1);
    const change = sessionReturn(validBars(history), 1);
    const row = el("li", `my-ticker${pinned ? " pinned" : ""}`);
    row.dataset.ticker = ticker;
    row.draggable = true;
    row.addEventListener("dragstart", (event) => {
      dragTicker = ticker;
      row.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", ticker);
    });
    row.addEventListener("dragend", () => {
      dragTicker = null;
      row.classList.remove("dragging");
      panel.querySelectorAll(".drop-before, .drop-after").forEach((item) => item.classList.remove("drop-before", "drop-after"));
    });
    row.addEventListener("dragover", (event) => {
      if (!dragTicker || dragTicker === ticker) return;
      event.preventDefault();
      const after = event.offsetY > row.offsetHeight / 2;
      row.classList.toggle("drop-after", after);
      row.classList.toggle("drop-before", !after);
    });
    row.addEventListener("dragleave", () => row.classList.remove("drop-before", "drop-after"));
    row.addEventListener("drop", (event) => {
      event.preventDefault();
      const after = row.classList.contains("drop-after");
      row.classList.remove("drop-before", "drop-after");
      if (dragTicker) dropTicker(dragTicker, ticker, after);
    });

    const handle = button("⋮⋮", "row-handle", () => {}, `handle-${ticker}`);
    handle.setAttribute("aria-label", `Reorder ${ticker}, position ${index + 1} of ${prefs.tickers.length}. Alt plus arrow keys move it.`);
    handle.addEventListener("keydown", (event) => {
      if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
      event.preventDefault();
      move(ticker, event.key === "ArrowUp" ? -1 : 1, `handle-${ticker}`);
    });

    const pin = button(pinned ? "★" : "☆", "row-pin", () => pinToggle(ticker), `pin-${ticker}`);
    pin.setAttribute("aria-pressed", String(pinned));
    pin.setAttribute("aria-label", pinned ? `Unpin ${ticker}` : `Pin ${ticker} to the front of the ticker bar`);
    if (atLimit) {
      pin.setAttribute("aria-disabled", "true");
      pin.title = PIN_LIMIT_MESSAGE;
      pin.setAttribute("aria-label", `Pin ${ticker}. ${PIN_LIMIT_MESSAGE}`);
    }

    const name = el("div", "row-name");
    name.append(el("strong", "", ticker));
    if (pinned) name.append(el("span", "row-tag", "Pinned"));
    const state = historyState(record, session.dashboardState === "ready");
    if (state === "loading") name.append(el("span", "row-tag loading", "Loading history…"));

    const price = el("span", "row-price mono", latest ? ctx.formatPrice(ctx.displayPrice(latest)) : "—");
    const tone = !isNumericValue(change) || Number(change) === 0 ? "neutral" : Number(change) > 0 ? "positive" : "negative";
    const day = el("span", `row-change mono ${tone}`, latest ? `${ctx.formatPercent(change)} 1D` : "—");

    const up = button("▲", "row-move", () => move(ticker, -1, `up-${ticker}`), `up-${ticker}`);
    up.setAttribute("aria-label", `Move ${ticker} up`);
    up.disabled = index === 0;
    const down = button("▼", "row-move", () => move(ticker, 1, `down-${ticker}`), `down-${ticker}`);
    down.setAttribute("aria-label", `Move ${ticker} down`);
    down.disabled = index === prefs.tickers.length - 1;
    const remove = button("✕", "row-remove", () => removeWithUndo(ticker), `remove-${ticker}`);
    remove.setAttribute("aria-label", `Remove ${ticker} from My tickers`);
    if (!canRemove(prefs)) {
      remove.disabled = true;
      remove.title = "Keep at least one ticker.";
      remove.setAttribute("aria-label", `Remove ${ticker}. Keep at least one ticker.`);
    }

    row.append(handle, pin, name, sparkline(history), price, day, up, down, remove);
    return row;
  }

  function pinToggle(ticker) {
    const result = togglePin(session.prefs, ticker);
    if (result.error) {
      announce(result.error);
      tabError = { message: result.error, tone: "notice" };
      renderPanel();
      return;
    }
    commit(result.prefs, { message: result.pinned ? `${ticker} pinned.` : `${ticker} unpinned.` });
  }

  function move(ticker, delta, focusKey) {
    const result = moveTicker(session.prefs, ticker, delta);
    if (!result) return;
    commit(result.prefs, { debounce: true, message: `${ticker} moved to position ${result.position} of ${result.total}.` });
    // The ▲ / ▼ that was pressed may now be disabled at the end of the list; keep focus on the row.
    const target = panel.querySelector(`[data-key="${focusKey}"]`);
    if (!target || target.disabled) panel.querySelector(`[data-key="handle-${ticker}"]`)?.focus();
  }

  function dropTicker(ticker, target, after) {
    const order = session.prefs.tickers.filter((item) => item !== ticker);
    const index = order.indexOf(target) + (after ? 1 : 0);
    order.splice(index, 0, ticker);
    if (order.join() === session.prefs.tickers.join()) return;
    commit({ ...copyPrefs(session.prefs), tickers: order }, {
      debounce: true,
      message: `${ticker} moved to position ${order.indexOf(ticker) + 1} of ${order.length}.`,
    });
  }

  function removeWithUndo(ticker) {
    const result = removeTicker(session.prefs, ticker);
    if (result.error) return;
    commit(result.prefs, { message: `${ticker} removed.` });
    showToast(`${ticker} removed from My tickers.`, () => {
      commit(restoreTicker(session.prefs, result.undo), { message: `${ticker} restored.` });
    });
    panel.querySelector('[data-key="add-input"]')?.focus();
  }

  function showToast(message, onUndo) {
    hideToast();
    toast.hidden = false;
    toast.append(el("span", "", message));
    toast.append(
      button("Undo", "button-link", () => {
        hideToast();
        onUndo();
      }, "undo"),
    );
    undoTimer = window.setTimeout(hideToast, UNDO_MS);
  }

  function hideToast() {
    window.clearTimeout(undoTimer);
    if (!toast) return;
    toast.hidden = true;
    toast.replaceChildren();
  }

  function renderAddSection() {
    const section = el("form", "add-ticker");
    section.setAttribute("aria-label", "Add a ticker");
    const label = el("label", "settings-label", "Add tickers");
    label.htmlFor = "settings-add-ticker";
    const row = el("div", "add-row");
    const input = el("input");
    input.id = "settings-add-ticker";
    input.dataset.key = "add-input";
    input.placeholder = "Ticker symbol · Enter adds it";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.maxLength = 10;
    input.value = addDraft;
    input.setAttribute("aria-describedby", "settings-add-help");
    const full = session.prefs.tickers.length >= MAX_TICKERS;
    const submit = el("button", "button-primary", "Add");
    submit.type = "submit";
    submit.dataset.key = "add-submit";
    submit.disabled = full;
    input.addEventListener("input", () => {
      addDraft = input.value;
      if (addError) {
        addError = "";
        help.textContent = helpText(full);
        help.classList.remove("error");
        input.removeAttribute("aria-invalid");
      }
    });
    row.append(input, submit);
    const help = el("p", `settings-hint${addError ? " error" : ""}`, addError || helpText(full));
    help.id = "settings-add-help";
    if (addError) input.setAttribute("aria-invalid", "true");
    section.addEventListener("submit", (event) => {
      event.preventDefault();
      const result = validateNewTicker(input.value, session.prefs);
      if (result.error) {
        addError = result.error;
        renderPanel();
        panel.querySelector('[data-key="add-input"]')?.focus();
        announce(result.error);
        return;
      }
      addDraft = "";
      addError = "";
      commit(addTicker(session.prefs, result.ticker), {
        message: `${result.ticker} added. Its history will backfill automatically.`,
        added: result.ticker,
      });
      panel.querySelector('[data-key="add-input"]')?.focus();
    });
    section.append(label, row, help);
    section.append(el("p", "settings-foot", "Adding a ticker starts its price, news and filings collectors on their next run."));
    return section;
  }

  function helpText(full) {
    return full ? `At most ${MAX_TICKERS} tickers. Remove one to add another.` : "New tickers go to the end of the list, unpinned. History backfills automatically.";
  }

  // ---------------------------------------------------------------- Theme & display
  function segmented(labelText, options, current, onPick, keyPrefix) {
    const wrap = el("div", "settings-field");
    const label = el("p", "settings-label", labelText);
    label.id = `${keyPrefix}-label`;
    const group = el("div", "segmented");
    group.setAttribute("role", "group");
    group.setAttribute("aria-labelledby", label.id);
    options.forEach((option) => {
      const item = button(option.label, "segment", () => onPick(option.id), `${keyPrefix}-${option.id}`);
      item.setAttribute("aria-pressed", String(option.id === current));
      group.append(item);
    });
    wrap.append(label, group);
    return wrap;
  }

  function setDisplay(update, message) {
    const next = copyPrefs(session.prefs);
    next.display = { ...next.display, ...update };
    commit(next, { message });
  }

  function renderThemeTab() {
    const display = session.prefs.display;
    const themes = el("div", "settings-field");
    themes.append(el("p", "settings-label", "Application theme"));
    const choices = el("div", "theme-choices");
    choices.setAttribute("role", "group");
    choices.setAttribute("aria-label", "Application theme");
    THEMES.forEach((theme) => {
      const choice = button(theme.label, "theme-choice", () => {
        if (theme.id !== (display.theme ?? DEFAULT_THEME)) setDisplay({ theme: theme.id }, `${theme.label} saved.`);
      }, `theme-${theme.id}`);
      choice.dataset.themeChoice = theme.id;
      choice.setAttribute("aria-pressed", String(theme.id === (display.theme ?? DEFAULT_THEME)));
      const colors = themeProperties(theme.id, "theme");
      choice.style.setProperty("--preview-bg", colors["--surface"]);
      choice.style.setProperty("--preview-text", colors["--text"]);
      choice.style.setProperty("--preview-accent", colors["--price"]);
      choices.append(choice);
    });
    themes.append(choices, el("p", "settings-hint", "Applies to all panels and charts. Cognito sign-in uses the default application branding."));
    const palette = segmented(
      "Up / down colors",
      PALETTES,
      display.updown_palette,
      (id) => id !== display.updown_palette && setDisplay({ updown_palette: id }, "Up and down colors saved."),
      "palette",
    );
    palette.append(el("p", "settings-hint", "Theme default uses the selected theme's direction colors. Other palettes override direction colors only. Blue / orange is easier to tell apart for red-green color blindness."));

    const preview = el("div", "settings-preview");
    preview.dataset.palette = display.updown_palette;
    preview.setAttribute("aria-label", "Preview of up and down colors");
    preview.setAttribute("role", "img");
    preview.append(el("span", "preview-chip positive", "▲ +1.24%"), el("span", "preview-chip negative", "▼ −0.86%"));

    const period = segmented(
      "Default chart period",
      PERIODS.map((item) => ({ id: item.id, label: item.id })),
      display.chart_period || "1M",
      (id) => id !== display.chart_period && setDisplay({ chart_period: id }, `Default chart period ${id} saved.`),
      "period",
    );
    period.append(el("p", "settings-hint", "The chart opens on this period. Periods without enough history fall back to the longest one available."));
    return [themes, palette, preview, period];
  }

  // ---------------------------------------------------------------- Profile & time zone
  function renderProfileTab() {
    const display = session.prefs.display;
    const profile = el("div", "settings-field profile-card");
    profile.append(el("p", "settings-label", "Signed in as"));
    profile.append(el("p", "profile-email", session.email || "Email unavailable for this session"));
    profile.append(el("p", "settings-hint", "Cognito session active"));

    const quickIds = TIME_ZONE_PICKS.map((pick) => pick.id);
    const zone = segmented(
      "Time zone",
      [...TIME_ZONE_PICKS, { id: "other", label: quickIds.includes(display.time_zone) ? "Other…" : timeZoneLabel(display.time_zone) }],
      quickIds.includes(display.time_zone) ? display.time_zone : "other",
      (id) => {
        if (id === "other") {
          panel.querySelector('[data-key="zone-input"]')?.focus();
          return;
        }
        if (id !== display.time_zone) setDisplay({ time_zone: id }, `Time zone set to ${timeZoneLabel(id)}.`);
      },
      "zone",
    );
    zone.append(el("p", "settings-hint", "Used for job times, the calendar and intraday axis labels."));

    const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
    const other = el("form", "zone-search");
    other.setAttribute("aria-label", "Choose another time zone");
    const label = el("label", "settings-label", "Other time zone");
    label.htmlFor = "settings-zone";
    const input = el("input");
    input.id = "settings-zone";
    input.dataset.key = "zone-input";
    input.setAttribute("list", "settings-zone-list");
    input.placeholder = "Search, e.g. Europe/London";
    input.autocomplete = "off";
    const datalist = el("datalist");
    datalist.id = "settings-zone-list";
    zones.forEach((name) => {
      const option = el("option");
      option.value = name;
      datalist.append(option);
    });
    const help = el("p", "settings-hint");
    help.id = "settings-zone-help";
    input.setAttribute("aria-describedby", help.id);
    const apply = el("button", "button-primary", "Use");
    apply.type = "submit";
    apply.dataset.key = "zone-apply";
    other.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = input.value.trim();
      const match = zones.find((name) => name.toLowerCase() === value.toLowerCase()) || (zones.length ? null : value);
      if (!match) {
        help.textContent = `${value || "That"} is not a known time zone.`;
        help.classList.add("error");
        input.setAttribute("aria-invalid", "true");
        return;
      }
      setDisplay({ time_zone: match }, `Time zone set to ${match}.`);
    });
    const browserZone = ctx.browserTimeZone();
    const useBrowser = button(`Use my browser's time zone (${browserZone})`, "button-link", () => {
      if (browserZone !== display.time_zone) setDisplay({ time_zone: browserZone }, `Time zone set to ${browserZone}.`);
    }, "zone-browser");
    const row = el("div", "add-row");
    row.append(input, apply);
    other.append(label, row, datalist, help, useBrowser);

    const signOutField = el("div", "settings-field");
    signOutField.append(button("Sign out", "", ctx.signOut, "sign-out"));
    return [profile, zone, other, signOutField];
  }

  // ---------------------------------------------------------------- Data refresh
  function renderRefreshTab() {
    const status = session.status || session.dashboard?.status;
    const intro = el("div", "settings-intro");
    intro.append(el("h3", "", "Data refresh jobs"));
    intro.append(el(
      "p",
      "settings-hint",
      status?.generated_at
        ? `Updated ${ctx.formatTime(status.generated_at, session.prefs.display.time_zone)}`
        : "Collector status",
    ));

    const jobs = status?.jobs || [];
    if (!jobs.length) {
      return [
        intro,
        el("p", "data-state", "Refresh status is not available yet. It appears after the first collector run."),
      ];
    }

    const table = el("table", "refresh-table");
    table.setAttribute("aria-label", "Data refresh job status");
    const head = el("thead");
    const headerRow = el("tr");
    ["Job", "Name", "Last run", "Next run", "Status"].forEach((label) => {
      headerRow.append(el("th", "", label));
    });
    head.append(headerRow);
    table.append(head);

    const body = el("tbody");
    jobs
      .slice()
      .sort((a, b) => String(a.job).localeCompare(String(b.job)))
      .forEach((job) => {
        const row = el("tr");
        row.append(el("td", "mono", job.job || "—"));
        row.append(el("td", "", job.name || job.job || "—"));
        row.append(el("td", "mono", ctx.formatTime(job.last_run, session.prefs.display.time_zone)));
        row.append(el(
          "td",
          "mono",
          job.next_run ? ctx.formatTime(job.next_run, session.prefs.display.time_zone) : "not scheduled",
        ));

        const state = ["ok", "partial", "failed"].includes(job.status) ? job.status : "never_run";
        const statusCell = el("td");
        const statusLabel = state === "never_run" ? "never run" : state;
        const indicator = el("span", `dot ${state === "never_run" ? "" : state}`.trim());
        indicator.setAttribute("aria-hidden", "true");
        statusCell.append(indicator, document.createTextNode(` ${statusLabel}`));
        statusCell.className = `refresh-status ${state}`;
        statusCell.title = job.last_outcome
          ? `Last outcome: ${job.last_outcome}; failed sources: ${job.failed_sources || 0}`
          : "No successful run has been recorded.";
        row.append(statusCell);
        body.append(row);
      });
    table.append(body);

    const wrap = el("div", "refresh-table-wrap");
    wrap.append(table);
    return [intro, wrap];
  }

  // ---------------------------------------------------------------- account menu
  function renderAccountButton() {
    const wrap = el("div", "account");
    const summary = refreshSummary(session.status || session.dashboard?.status);
    const trigger = el("button", "account-button");
    trigger.type = "button";
    trigger.dataset.opener = "account";
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-label", `Account menu · signed in · data refresh: ${summary.text}`);
    const icon = el("span", "account-icon");
    icon.setAttribute("aria-hidden", "true");
    icon.append(el("span", `dot ${summary.tone}`.trim()));
    trigger.append(icon, el("span", "account-text", "Account"));

    const menu = el("div", "account-menu");
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "Account");
    menu.hidden = true;
    const head = el("div", "menu-head");
    head.setAttribute("role", "presentation");
    head.append(el("strong", "", "Signed in"), el("span", "", `${session.email || "Cognito account"} · Cognito session active`));
    const refresh = el("button", "menu-refresh");
    refresh.type = "button";
    refresh.setAttribute("role", "menuitem");
    refresh.tabIndex = -1;
    refresh.setAttribute("aria-label", `Data refresh: ${summary.text}`);
    refresh.append(el("span", `dot ${summary.tone}`.trim()), document.createTextNode(` Data refresh: ${summary.text}`));
    refresh.addEventListener("click", () => {
      hideMenu(false);
      open("refresh", "account");
    });

    const items = [refresh, ...[
      ["My tickers", "add · pin · reorder", () => open("tickers", "account")],
      ["Theme & display", "colors · chart period", () => open("theme", "account")],
      ["Profile & time zone", `email · ${timeZoneLabel(session.prefs.display.time_zone)}`, () => open("profile", "account")],
      ["All refresh jobs", "job schedule · status", () => open("refresh", "account")],
      ["Sign out", "", ctx.signOut],
    ].map(([label, hint, run]) => {
      const item = el("button", "menu-item");
      item.type = "button";
      item.setAttribute("role", "menuitem");
      item.tabIndex = -1;
      item.append(el("span", "", label));
      if (hint) item.append(el("span", "menu-hint", hint));
      item.addEventListener("click", () => {
        hideMenu(false);
        run();
      });
      return item;
    })];
    menu.append(head, ...items);

    function showMenu(focusIndex = 0) {
      menu.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      items.at(focusIndex)?.focus();
      document.addEventListener("mousedown", outside, true);
    }
    function hideMenu(returnFocus = true) {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      document.removeEventListener("mousedown", outside, true);
      if (returnFocus) trigger.focus();
    }
    function outside(event) {
      if (!wrap.contains(event.target)) hideMenu(false);
    }
    trigger.addEventListener("click", () => (menu.hidden ? showMenu() : hideMenu()));
    trigger.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        showMenu(event.key === "ArrowUp" ? -1 : 0);
      }
    });
    menu.addEventListener("keydown", (event) => {
      const index = items.indexOf(document.activeElement);
      let next = null;
      if (event.key === "ArrowDown") next = (index + 1) % items.length;
      else if (event.key === "ArrowUp") next = (index - 1 + items.length) % items.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = items.length - 1;
      else if (event.key === "Escape") {
        event.preventDefault();
        hideMenu();
        return;
      } else if (event.key === "Tab") {
        hideMenu(false);
        return;
      } else if (event.key.length === 1 && /\S/.test(event.key)) {
        const letter = event.key.toLowerCase();
        const order = [...items.slice(index + 1), ...items.slice(0, index + 1)];
        const found = order.find((item) => item.textContent.trim().toLowerCase().startsWith(letter));
        if (found) next = items.indexOf(found);
      }
      if (next !== null) {
        event.preventDefault();
        items[next].focus();
      }
    });
    wrap.append(trigger, menu);
    return wrap;
  }

  return {
    open,
    close,
    isOpen,
    renderAccountButton,
    // The dashboard re-rendered (new data); refresh prices and history chips in the open tab.
    refresh() {
      if (isOpen()) renderPanel();
    },
    openFromHash() {
      const tab = parseSettingsHash(location.hash);
      if (tab && !isOpen()) open(tab, "account");
      else if (tab && isOpen() && tab !== activeTab) selectTab(tab);
      else if (!tab && isOpen()) close();
    },
  };
}
