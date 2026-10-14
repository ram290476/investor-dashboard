// Storage access that never throws (private / incognito browsing).
// Some browsers (older Safari private mode, Chrome or Firefox with site data
// blocked) throw on reading `window.sessionStorage` / `localStorage`, or on
// setItem. Each store tries Web Storage, then a short-lived first-party cookie
// (so the Cognito PKCE state still survives the sign-in redirect), then memory.

function webStorage(win, kind) {
  try {
    const store = win[kind];
    const probe = "__invdash_probe__";
    store.setItem(probe, probe);
    store.removeItem(probe);
    return store;
  } catch {
    return null;
  }
}

function cookieStore(doc, prefix, maxAge) {
  const name = (key) => `${prefix}${encodeURIComponent(key)}`;
  const write = (key, value, age) => {
    doc.cookie = `${name(key)}=${encodeURIComponent(value)}; Max-Age=${age}; Path=/; SameSite=Lax; Secure`;
  };
  try {
    write("__probe__", "1", 60);
    const ok = String(doc.cookie).includes(`${name("__probe__")}=1`);
    write("__probe__", "", 0);
    if (!ok) return null;
  } catch {
    return null;
  }
  return {
    getItem(key) {
      const match = String(doc.cookie).split(/;\s*/).find((part) => part.startsWith(`${name(key)}=`));
      return match ? decodeURIComponent(match.slice(name(key).length + 1)) : null;
    },
    setItem(key, value) { write(key, String(value), maxAge); },
    removeItem(key) { write(key, "", 0); },
  };
}

function memoryStore() {
  const map = new Map();
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => { map.set(key, String(value)); },
    removeItem: (key) => { map.delete(key); },
  };
}

export function createSafeStorage(kind, { win = globalThis, doc = globalThis.document, cookieFallback = false } = {}) {
  const web = webStorage(win, kind);
  const cookie = !web && cookieFallback && doc ? cookieStore(doc, `invdash_${kind === "localStorage" ? "l" : "s"}_`, 600) : null;
  const memory = memoryStore();
  const backend = web || cookie || memory;
  const safe = (fn, fallback) => { try { return fn(); } catch { return fallback(); } };
  return {
    // "web" | "cookie" | "memory"; memory does not survive a reload or redirect.
    backend: web ? "web" : cookie ? "cookie" : "memory",
    getItem: (key) => safe(() => backend.getItem(key) ?? memory.getItem(key), () => memory.getItem(key)),
    setItem: (key, value) => safe(() => { backend.setItem(key, value); }, () => memory.setItem(key, value)),
    removeItem: (key) => { safe(() => backend.removeItem(key), () => {}); memory.removeItem(key); },
  };
}
