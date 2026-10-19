import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createSafeStorage } from "./safe-storage.js";

const throwing = () => { throw new DOMException("The operation is insecure.", "SecurityError"); };

function fakeStorage() {
  const map = new Map();
  return { getItem: (k) => map.get(k) ?? null, setItem: (k, v) => map.set(k, String(v)), removeItem: (k) => map.delete(k) };
}

function fakeCookieDoc() {
  const jar = new Map();
  return {
    get cookie() { return [...jar].map(([k, v]) => `${k}=${v}`).join("; "); },
    set cookie(line) {
      const [pair, ...attrs] = line.split("; ");
      const [k, v] = pair.split("=");
      if (attrs.includes("Max-Age=0")) jar.delete(k); else jar.set(k, v);
    },
  };
}

test("uses Web Storage when it works", () => {
  const store = createSafeStorage("sessionStorage", { win: { sessionStorage: fakeStorage() } });
  assert.equal(store.backend, "web");
  store.setItem("a", "1");
  assert.equal(store.getItem("a"), "1");
});

test("private mode: storage getter throws -> cookie fallback keeps PKCE state across the redirect", () => {
  const win = {};
  Object.defineProperty(win, "sessionStorage", { get: throwing });
  const doc = fakeCookieDoc();
  const store = createSafeStorage("sessionStorage", { win, doc, cookieFallback: true });
  assert.equal(store.backend, "cookie");
  store.setItem("oauth_state", "abc=/+");
  // A new page load after the Cognito redirect reads the same cookie jar.
  const afterRedirect = createSafeStorage("sessionStorage", { win, doc, cookieFallback: true });
  assert.equal(afterRedirect.getItem("oauth_state"), "abc=/+");
  afterRedirect.removeItem("oauth_state");
  assert.equal(afterRedirect.getItem("oauth_state"), null);
});

test("Safari private mode: setItem throws QuotaExceededError -> never throws, falls back to memory", () => {
  const quota = { getItem: () => null, setItem: () => { throw new DOMException("quota", "QuotaExceededError"); }, removeItem() {} };
  const store = createSafeStorage("localStorage", { win: { localStorage: quota } });
  assert.equal(store.backend, "memory");
  assert.doesNotThrow(() => store.setItem("k", "v"));
  assert.equal(store.getItem("k"), "v");
  assert.doesNotThrow(() => store.removeItem("k"));
});

test("everything blocked: no call throws", () => {
  const win = {};
  Object.defineProperty(win, "sessionStorage", { get: throwing });
  const doc = {};
  Object.defineProperty(doc, "cookie", { get: () => "", set: throwing });
  const store = createSafeStorage("sessionStorage", { win, doc, cookieFallback: true });
  assert.equal(store.backend, "memory");
  assert.doesNotThrow(() => { store.setItem("x", "1"); store.getItem("x"); store.removeItem("x"); });
});

test("app.js never touches Web Storage directly, and the default theme is set on first paint", () => {
  const app = readFileSync(new URL("./app.js", import.meta.url), "utf8");
  assert.doesNotMatch(app, /\b(window\.)?(sessionStorage|localStorage)\s*\./);
  const html = readFileSync(new URL("./index.html", import.meta.url), "utf8");
  assert.match(html, /<html[^>]*data-theme="industrial-dark"/);
});
