import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { describe, test } from "node:test";
import { createAccountSettings } from "./account-settings.js";

const account = await readFile(new URL("./account-settings.js", import.meta.url), "utf8");
const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");

const TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "XOM", "BRK.B", "LLY"];

function matchSimple(node, selector) {
  if (!node || node.tagName === "#text") return false;
  let rest = selector.trim();
  let tag = null;
  if (/^[a-zA-Z]/.test(rest)) {
    const found = /^[a-zA-Z][\w-]*/.exec(rest);
    tag = found[0];
    rest = rest.slice(tag.length);
  }
  if (tag && node.tagName !== tag) return false;
  const classes = [];
  while (rest.startsWith(".")) {
    const found = /^\.([a-zA-Z0-9_-]+)/.exec(rest);
    if (!found) return false;
    classes.push(found[1]);
    rest = rest.slice(found[0].length);
  }
  if (classes.length) {
    const have = new Set(String(node.className || "").split(/\s+/).filter(Boolean));
    if (!classes.every((name) => have.has(name))) return false;
  }
  if (rest.startsWith("#")) {
    const found = /^#([a-zA-Z0-9_-]+)/.exec(rest);
    if (!found || node.id !== found[1]) return false;
    rest = rest.slice(found[0].length);
  }
  while (rest.startsWith("[")) {
    const found = /^\[([^\]=]+)(?:=(?:"([^"]*)"|'([^']*)'))?\]/.exec(rest);
    if (!found) return false;
    const actual = node.getAttribute(found[1]);
    const expected = found[2] ?? found[3];
    if (expected === undefined) {
      if (actual == null) return false;
    } else if (actual !== expected) return false;
    rest = rest.slice(found[0].length);
  }
  return rest === "";
}

function walk(node, visit) {
  for (const child of node.children || []) {
    visit(child);
    walk(child, visit);
  }
}

function queryAll(root, selector) {
  const groups = String(selector).split(",").map((part) => part.trim()).filter(Boolean);
  const out = [];
  const seen = new Set();
  for (const group of groups) {
    let current = [root];
    for (const step of group.split(/\s+/)) {
      const next = [];
      for (const node of current) walk(node, (child) => { if (matchSimple(child, step)) next.push(child); });
      current = next;
    }
    for (const node of current) {
      if (!seen.has(node)) {
        seen.add(node);
        out.push(node);
      }
    }
  }
  return out;
}

function detach(node) {
  const parent = node.parentElement;
  if (!parent) return;
  parent.children = parent.children.filter((child) => child !== node);
  node.parentElement = null;
}

class Node {
  constructor(tag, doc) {
    this.tagName = tag;
    this.ownerDocument = doc;
    this.children = [];
    this.parentElement = null;
    this.attrs = {};
    this._data = {};
    this._text = "";
    this.className = "";
    this.listeners = {};
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.clientWidth = 0;
    this.offsetLeft = 0;
    this.offsetWidth = 0;
    this.hidden = false;
    this.disabled = false;
    this.id = "";
    this.type = "";
    this.value = "";
    this.title = "";
    this.tabIndex = 0;
    this.style = { setProperty() {}, colorScheme: "" };
    const node = this;
    this.dataset = new Proxy(this._data, {
      get: (data, key) => data[key],
      set: (data, key, value) => {
        data[key] = String(value);
        return true;
      },
    });
    this.classList = {
      contains: (name) => String(node.className || "").split(/\s+/).includes(name),
      add: (...names) => {
        const next = String(node.className || "").split(/\s+/).filter(Boolean);
        for (const name of names) if (!next.includes(name)) next.push(name);
        node.className = next.join(" ");
      },
      remove: (...names) => {
        node.className = String(node.className || "").split(/\s+/).filter((name) => name && !names.includes(name)).join(" ");
      },
      toggle: (name, force) => {
        const has = String(node.className || "").split(/\s+/).includes(name);
        const on = force === undefined ? !has : Boolean(force);
        const next = String(node.className || "").split(/\s+/).filter((item) => item && item !== name);
        if (on) next.push(name);
        node.className = next.join(" ");
        return on;
      },
    };
  }

  get textContent() {
    return `${this._text}${this.children.map((child) => child.textContent || "").join("")}`;
  }

  set textContent(value) {
    this._text = value == null ? "" : String(value);
    for (const child of this.children) child.parentElement = null;
    this.children = [];
  }

  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  getAttribute(name) {
    if (name === "class") return this.className || null;
    if (name === "id") return this.id || null;
    if (name === "type" && this.type) return String(this.type);
    if (name.startsWith("data-")) {
      const prop = name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      if (prop in this._data) return this._data[prop];
    }
    return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
  }

  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }

  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== fn);
  }

  append(...kids) {
    for (const kid of kids.flat()) {
      if (!kid) continue;
      if (kid.parentElement) detach(kid);
      kid.parentElement = this;
      this.children.push(kid);
    }
  }

  prepend(...kids) {
    const flat = kids.flat().filter(Boolean);
    for (const kid of flat) if (kid.parentElement) detach(kid);
    for (const kid of flat) kid.parentElement = this;
    this.children = [...flat, ...this.children];
  }

  insertBefore(node, before) {
    if (node.parentElement) detach(node);
    node.parentElement = this;
    const index = before ? this.children.indexOf(before) : -1;
    if (index < 0) this.children.push(node);
    else this.children.splice(index, 0, node);
  }

  // Chrome drops scrollTop when an overflow box's children are replaced, and a
  // removed focused node blurs. The settings code has to put both back.
  replaceChildren(...kids) {
    const doc = this.ownerDocument;
    const dropFocus = Boolean(doc?.activeElement && this.contains(doc.activeElement));
    for (const child of this.children) child.parentElement = null;
    this.children = [];
    this.scrollTop = 0;
    this.scrollLeft = 0;
    if (kids.length) this.append(...kids);
    if (dropFocus) doc.activeElement = doc.body || null;
  }

  remove() { detach(this); }

  replaceWith(next) {
    const parent = this.parentElement;
    if (!parent) return;
    const index = parent.children.indexOf(this);
    this.parentElement = null;
    parent.children.splice(index, 1);
    if (next.parentElement) detach(next);
    next.parentElement = parent;
    parent.children.splice(index, 0, next);
  }

  contains(node) {
    let current = node;
    while (current) {
      if (current === this) return true;
      current = current.parentElement;
    }
    return false;
  }

  closest(selector) {
    let node = this;
    while (node) {
      if (node.matches?.(selector)) return node;
      node = node.parentElement;
    }
    return null;
  }

  matches(selector) { return matchSimple(this, selector); }
  querySelectorAll(selector) { return queryAll(this, selector); }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  focus() { if (this.ownerDocument) this.ownerDocument.activeElement = this; }
}

function installDocument() {
  const doc = {
    activeElement: null,
    body: null,
    createElement(tag) {
      return new Node(tag, doc);
    },
    createElementNS(_ns, tag) {
      return new Node(tag, doc);
    },
    createTextNode(text) {
      const node = new Node("#text", doc);
      node._text = String(text);
      return node;
    },
    addEventListener() {},
    removeEventListener() {},
    querySelector(selector) { return doc.body?.querySelector(selector) || null; },
    querySelectorAll(selector) { return doc.body?.querySelectorAll(selector) || []; },
  };
  doc.body = new Node("body", doc);
  doc.activeElement = doc.body;
  globalThis.window = {
    setTimeout: (fn, ms, ...args) => globalThis.setTimeout(fn, ms, ...args),
    clearTimeout: (id) => globalThis.clearTimeout(id),
  };
  globalThis.document = doc;
  globalThis.history = { state: null, replaceState() {} };
  globalThis.location = { hash: "", pathname: "/", search: "" };
  return doc;
}

function control(root, key) {
  return [...root.querySelectorAll("[data-key]")].find((node) => node.dataset.key === key) || null;
}

function click(node) {
  node.focus();
  for (const fn of node.listeners.click || []) fn({ preventDefault() {}, target: node, currentTarget: node });
}

function submit(form) {
  for (const fn of form.listeners.submit || []) fn({ preventDefault() {}, target: form, currentTarget: form });
}

function mount({ admin = false } = {}) {
  installDocument();
  const session = {
    prefs: {
      tickers: [...TICKERS],
      pinned: ["AAPL", "MSFT"],
      display: {
        theme: "industrial-dark",
        updown_palette: "green-red",
        chart_period: "1M",
        time_zone: "America/Los_Angeles",
      },
      chart_settings: {},
      version: 1,
    },
    email: "owner@example.com",
    dashboardState: "loading",
    dashboard: { tickers: {} },
  };
  let api;
  api = createAccountSettings({
    session,
    putPrefs: async (body) => ({ ...body, version: (body.version || 1) + 1 }),
    getPrefs: async () => structuredClone(session.prefs),
    onPrefsChange: () => api.refresh(),
    onTickerAdded: () => {},
    onUnauthorized: () => {},
    onSaveError: () => {},
    signOut: () => {},
    formatPrice: (value) => `$${value}`,
    formatPercent: (value) => `${value}%`,
    displayPrice: (row) => row?.close,
    formatTime: (value) => String(value ?? ""),
    browserTimeZone: () => "Europe/Paris",
    isAdmin: () => admin,
    fundFlagRows: () => session.prefs.tickers.map((ticker, index) => ({ ticker, is_fund: index % 2 === 0 })),
    resetFundFlag: async () => ({ state: "done" }),
    confirm: () => true,
  });
  return { api, session };
}

function panelOf() {
  return document.body.querySelector(".settings-panel");
}

test("settings rebuilds always name keepPlace, and selection styles stay on theme tokens", () => {
  const calls = [...account.matchAll(/renderPanel\s*\(([^)]*)\)/g)].map((match) => match[1].trim());
  assert.deepEqual(calls, [
    "{ keepPlace: activeTab === activeTabBefore }",
    "{ keepPlace = true } = {}",
    "{ keepPlace: true }",
    "{ keepPlace: true }",
    "{ keepPlace: true }",
  ]);
  const row = css.match(/\.my-ticker\.is-selected,\s*\n\.my-ticker\[aria-selected="true"\] \{[^}]*\}/)[0];
  const segment = css.match(/\.segment\[aria-pressed="true"\],\s*\n\.segment\.is-selected \{[^}]*\}/)[0];
  const theme = css.match(/\.theme-choice\[aria-pressed="true"\],\s*\n\.theme-choice\.is-selected \{[^}]*\}/)[0];
  for (const rule of [row, segment, theme]) assert.doesNotMatch(rule, /#[0-9a-fA-F]{3,8}\b/);
  assert.match(row, /color-mix\(in srgb, var\(--blue\) 14%, var\(--surface-raised\)\)/);
  assert.match(row, /box-shadow:\s*inset 3px 0 0 var\(--blue\)/);
  assert.match(segment, /background:\s*var\(--selected\)/);
  assert.match(segment, /font-weight:\s*600/);
  assert.match(theme, /box-shadow:\s*inset 0 0 0 1px var\(--blue\)/);
  assert.match(theme, /font-weight:\s*600/);
});

describe("account settings clicks keep scroll, focus and selection", { concurrency: false }, () => {
  test("a theme, palette and period pick updates the pressed control in place", () => {
    const { api, session } = mount();
    api.open("theme");
    const panel = panelOf();
    panel.scrollTop = 160;
    const choices = panel.querySelector(".theme-choices");
    const theme = control(choices, "theme-clean-light");
    const paletteGroup = panel.querySelector('[aria-labelledby="palette-label"]');
    const palette = control(paletteGroup, "palette-blue-orange");
    const periodGroup = panel.querySelector('[aria-labelledby="period-label"]');
    const period = control(periodGroup, "period-5Y");
    click(theme);
    assert.equal(panel.scrollTop, 160);
    assert.equal(panel.contains(theme), true);
    assert.equal(document.activeElement, theme);
    assert.equal(theme.getAttribute("aria-pressed"), "true");
    assert.equal(theme.classList.contains("is-selected"), true);
    assert.equal(control(choices, "theme-industrial-dark").getAttribute("aria-pressed"), "false");
    assert.equal(session.prefs.display.theme, "clean-light");
    click(palette);
    assert.equal(panel.scrollTop, 160);
    assert.equal(panel.contains(palette), true);
    assert.equal(document.activeElement, palette);
    assert.equal(palette.getAttribute("aria-pressed"), "true");
    assert.equal(palette.classList.contains("is-selected"), true);
    assert.equal(control(paletteGroup, "palette-green-red").getAttribute("aria-pressed"), "false");
    assert.equal(panel.querySelector(".settings-preview").dataset.palette, "blue-orange");
    click(period);
    assert.equal(panel.scrollTop, 160);
    assert.equal(panel.contains(period), true);
    assert.equal(document.activeElement, period);
    assert.equal(period.getAttribute("aria-pressed"), "true");
    assert.equal(period.classList.contains("is-selected"), true);
    assert.equal(control(periodGroup, "period-1M").getAttribute("aria-pressed"), "false");
    assert.equal(session.prefs.display.chart_period, "5Y");
    click(control(choices, "theme-industrial-dark"));
    assert.equal(session.prefs.display.theme, "industrial-dark");
    assert.equal(panel.scrollTop, 160);
  });

  test("a time zone pick and a custom zone keep the same controls and scroll", () => {
    const { api, session } = mount();
    api.open("profile");
    const panel = panelOf();
    panel.scrollTop = 130;
    const group = panel.querySelector('[aria-labelledby="zone-label"]');
    const eastern = control(group, "zone-America/New_York");
    click(eastern);
    assert.equal(panel.scrollTop, 130);
    assert.equal(panel.contains(group), true);
    assert.equal(panel.contains(eastern), true);
    assert.equal(document.activeElement, eastern);
    assert.equal(eastern.getAttribute("aria-pressed"), "true");
    assert.equal(eastern.classList.contains("is-selected"), true);
    assert.equal(control(group, "zone-America/Los_Angeles").getAttribute("aria-pressed"), "false");
    assert.equal(session.prefs.display.time_zone, "America/New_York");
    const form = panel.querySelector(".zone-search");
    const input = control(panel, "zone-input");
    input.value = "europe/london";
    input.focus();
    submit(form);
    const other = control(group, "zone-other");
    assert.equal(panel.scrollTop, 130);
    assert.equal(panel.contains(form), true);
    assert.equal(panel.contains(other), true);
    assert.equal(other.getAttribute("aria-pressed"), "true");
    assert.equal(other.classList.contains("is-selected"), true);
    assert.equal(other.textContent, "Europe/London");
    assert.equal(eastern.getAttribute("aria-pressed"), "false");
    assert.equal(session.prefs.display.time_zone, "Europe/London");
    assert.equal(input.value, "");
    const browser = control(panel, "zone-browser");
    click(browser);
    assert.equal(panel.scrollTop, 130);
    assert.equal(panel.contains(browser), true);
    assert.equal(session.prefs.display.time_zone, "Europe/Paris");
    assert.equal(other.getAttribute("aria-pressed"), "true");
    assert.equal(other.textContent, "Europe/Paris");
  });

  test("pin, move and add update the watchlist rows in place", () => {
    const { api, session } = mount();
    api.open("tickers");
    const panel = panelOf();
    const list = panel.querySelector(".my-tickers");
    panel.scrollTop = 220;
    const row = [...list.querySelectorAll(".my-ticker")].find((item) => item.dataset.ticker === "BRK.B");
    const pin = control(row, "pin-BRK.B");
    click(pin);
    assert.equal(panel.querySelector(".my-tickers"), list);
    assert.equal(panel.contains(row), true);
    assert.equal(panel.scrollTop, 220);
    assert.equal(document.activeElement, pin);
    assert.equal(pin.getAttribute("aria-pressed"), "true");
    assert.equal(pin.textContent, "★");
    assert.equal(row.classList.contains("pinned"), true);
    assert.equal(row.classList.contains("is-selected"), true);
    assert.equal(row.getAttribute("aria-selected"), "true");
    assert.equal([...list.querySelectorAll(".my-ticker")].filter((item) => item.getAttribute("aria-selected") === "true").length, 1);
    assert.ok(session.prefs.pinned.includes("BRK.B"));
    assert.match(panel.querySelector(".settings-intro h3").textContent, /3 of 6 pinned/);

    const down = control(row, "down-BRK.B");
    click(down);
    assert.equal(panel.querySelector(".my-tickers"), list);
    assert.equal(panel.contains(row), true);
    assert.equal(panel.scrollTop, 220);
    // BRK.B is now last, so ▼ disables and focus stays on that row's handle.
    assert.equal(document.activeElement?.dataset?.key, "handle-BRK.B");
    assert.equal(down.disabled, true);
    assert.equal(row.classList.contains("is-selected"), true);
    const order = [...list.querySelectorAll(".my-ticker")].map((item) => item.dataset.ticker);
    assert.deepEqual(order.slice(-2), ["LLY", "BRK.B"]);
    assert.deepEqual(session.prefs.tickers.slice(-2), ["LLY", "BRK.B"]);

    const form = panel.querySelector(".add-ticker");
    const input = control(panel, "add-input");
    input.value = "??";
    input.focus();
    submit(form);
    assert.equal(panel.querySelector(".my-tickers"), list);
    assert.equal(panel.scrollTop, 220);
    assert.equal(document.activeElement, input);
    assert.equal(input.getAttribute("aria-invalid"), "true");
    assert.match(panel.querySelector("#settings-add-help").textContent, /not a valid ticker symbol/);
    assert.equal(session.prefs.tickers.includes("??"), false);

    input.value = "ORCL";
    submit(form);
    assert.equal(panel.querySelector(".my-tickers"), list);
    assert.equal(panel.contains(row), true);
    assert.equal(panel.scrollTop, 220);
    const added = [...list.querySelectorAll(".my-ticker")].find((item) => item.dataset.ticker === "ORCL");
    assert.ok(added);
    assert.equal(added.classList.contains("is-selected"), true);
    assert.equal(added.getAttribute("aria-selected"), "true");
    assert.equal(row.getAttribute("aria-selected"), null);
    assert.equal(session.prefs.tickers.at(-1), "ORCL");
  });

  test("a pin-limit notice does not rebuild the list", () => {
    const { api } = mount();
    api.open("tickers");
    const panel = panelOf();
    const list = panel.querySelector(".my-tickers");
    panel.scrollTop = 180;
    const pinned = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"];
    for (const ticker of pinned) {
      if (!api) break;
      const row = [...list.querySelectorAll(".my-ticker")].find((item) => item.dataset.ticker === ticker);
      const pin = control(row, `pin-${ticker}`);
      if (pin.getAttribute("aria-pressed") !== "true") click(pin);
    }
    panel.scrollTop = 180;
    const row = [...list.querySelectorAll(".my-ticker")].find((item) => item.dataset.ticker === "TSLA");
    click(control(row, "pin-TSLA"));
    assert.equal(panel.querySelector(".my-tickers"), list);
    assert.equal(panel.contains(row), true);
    assert.equal(panel.scrollTop, 180);
    assert.equal(row.classList.contains("is-selected"), true);
    assert.equal(row.getAttribute("aria-selected"), "true");
    assert.match(panel.querySelector(".settings-alert").textContent, /Pin limit reached/);
    assert.equal(control(row, "pin-TSLA").getAttribute("aria-pressed"), "false");
  });

  test("the admin list survives a dashboard refresh and a forced rebuild restores scroll and focus", () => {
    const { api } = mount({ admin: true });
    api.open("admin");
    const panel = panelOf();
    const section = panel.querySelector(".fund-flag-admin");
    const wrap = section.querySelector(".fund-flag-table-wrap");
    const table = section.querySelector(".fund-flag-table");
    panel.scrollTop = 90;
    wrap.scrollTop = 44;
    table.scrollTop = 18;
    const radio = control(section, "fund-flag-XOM");
    for (const fn of radio.listeners.change || []) fn();
    assert.equal(section.querySelector(".fund-flag-table"), table);
    assert.equal(panel.scrollTop, 90);
    assert.equal(wrap.scrollTop, 44);
    assert.equal(table.scrollTop, 18);
    const selected = [...section.querySelectorAll(".ffa-row")].filter((row) => row.classList.contains("is-selected"));
    assert.equal(selected.length, 1);
    assert.equal(selected[0].dataset.ticker, "XOM");
    assert.equal(selected[0].getAttribute("aria-selected"), "true");
    api.refresh();
    assert.equal(section.querySelector(".fund-flag-table"), table);
    assert.equal(panel.scrollTop, 90);
    assert.equal(wrap.scrollTop, 44);
    assert.equal(table.scrollTop, 18);
    assert.equal(section.querySelector(".ffa-row.is-selected").dataset.ticker, "XOM");

    const reset = control(section, "fund-flag-reset");
    reset.focus();
    panel.dataset.activeTab = "stale";
    panel.scrollTop = 90;
    wrap.scrollTop = 44;
    api.refresh();
    const rebuilt = panel.querySelector(".fund-flag-table-wrap");
    assert.notEqual(rebuilt, wrap);
    assert.equal(panel.scrollTop, 90);
    assert.equal(rebuilt.scrollTop, 44);
    assert.equal(document.activeElement.dataset.key, "fund-flag-reset");
    assert.equal(panel.querySelector(".ffa-row.is-selected").dataset.ticker, "XOM");
  });
});
