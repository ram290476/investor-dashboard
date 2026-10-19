import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  REFRESH_TOKEN_KEY,
  bootstrapAuth,
  clearRefreshToken,
  createAuthClient,
  locationAfterAuth,
  performAuthorizedRequest,
  readRefreshToken,
  signOutSession,
  writeRefreshToken,
} from "./auth-session.js";
import { createSafeStorage } from "./safe-storage.js";

const REFRESH = "refresh-token-sentinel-value";
const ACCESS = "access-token-sentinel-value";
const NEXT_ACCESS = "access-token-after-refresh-sentinel";
const ID_TOKEN = "id-token-sentinel-value";
const CODE = "auth-code-sentinel-value";
const CLIENT = "client-id-123";
const TOKEN_URL = "https://auth.example.com/oauth2/token";
const DOMAIN = "https://auth.example.com";
const REDIRECT = "https://investor.vellamsetti.com/auth/callback";
const LOGOUT_URI = "https://investor.vellamsetti.com/";

const PKCE_KEYS = {
  state: "oauth_state",
  verifier: "oauth_verifier",
  hash: "oauth_return_hash",
  path: "oauth_return_path",
  dashboardTicker: "oauth_return_dashboard_ticker",
};

function fakeStorage() {
  const map = new Map();
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
  };
}

function memoryStorage(initial = {}) {
  const store = fakeStorage();
  for (const [key, value] of Object.entries(initial)) store.setItem(key, value);
  return { ...store, backend: "web" };
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function tokenFetch(handler) {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    const body = new URLSearchParams(init.body?.toString?.() || "");
    const call = { url: String(url), method: init.method, body };
    calls.push(call);
    return handler(call, calls);
  };
  return { fetchImpl, calls };
}

function issuedTokens({ access = ACCESS, refresh = REFRESH, rotate = false } = {}) {
  return jsonResponse({
    access_token: access,
    id_token: ID_TOKEN,
    token_type: "Bearer",
    expires_in: 3600,
    ...(rotate ? { refresh_token: refresh } : {}),
  });
}

async function boot(overrides = {}) {
  const refreshStorage = overrides.refreshStorage || memoryStorage();
  const pkceStorage = overrides.pkceStorage || memoryStorage();
  const fetchBox = overrides.fetchBox || tokenFetch(() => issuedTokens());
  const outcome = await bootstrapAuth({
    pathname: "/",
    search: "",
    hash: "",
    fetchImpl: fetchBox.fetchImpl,
    tokenUrl: TOKEN_URL,
    clientId: CLIENT,
    redirectUri: REDIRECT,
    refreshStorage,
    pkceStorage,
    pkceKeys: PKCE_KEYS,
    ...overrides,
    refreshStorage,
    pkceStorage,
    fetchImpl: fetchBox.fetchImpl,
  });
  return { outcome, refreshStorage, pkceStorage, calls: fetchBox.calls };
}

test("a refresh restores the session and stays on the same route", async () => {
  const web = fakeStorage();
  const firstLoad = createSafeStorage("localStorage", { win: { localStorage: web } });
  writeRefreshToken(firstLoad, REFRESH);
  const reloaded = createSafeStorage("localStorage", { win: { localStorage: web } });
  const hash = "#stock/TSLA/deep-dive/overview";
  const fetchBox = tokenFetch((call) => {
    assert.equal(call.body.get("grant_type"), "refresh_token");
    assert.equal(call.body.get("refresh_token"), REFRESH);
    assert.equal(call.body.get("client_id"), CLIENT);
    assert.equal(call.body.get("client_secret"), null);
    return issuedTokens({ access: NEXT_ACCESS });
  });
  const { outcome } = await boot({
    refreshStorage: reloaded,
    pathname: "/",
    hash,
    fetchBox,
  });

  assert.equal(outcome.status, "signed-in");
  assert.equal(outcome.navigate, null);
  assert.equal(outcome.accessToken, NEXT_ACCESS);
  assert.equal(outcome.idToken, ID_TOKEN);
  assert.equal(outcome.replaceUrl, null);
  assert.equal(locationAfterAuth("/", hash, outcome), `/${hash}`);
  assert.equal(readRefreshToken(reloaded), REFRESH);
  assert.equal(reloaded.getItem(REFRESH_TOKEN_KEY), REFRESH);
  assert.equal(web.getItem(REFRESH_TOKEN_KEY), REFRESH);
  assert.equal(fetchBox.calls.length, 1);
});

test("a refresh of a research route does not rewrite the URL", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  const { outcome } = await boot({ refreshStorage, pathname: "/ticker/TSLA", hash: "" });
  assert.equal(outcome.status, "signed-in");
  assert.equal(outcome.replaceUrl, null);
  assert.equal(locationAfterAuth("/ticker/TSLA", "", outcome), "/ticker/TSLA");
});

test("reloading /auth/callback without code or state restores the session and does not loop", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  const fetchBox = tokenFetch(() => issuedTokens({ access: NEXT_ACCESS }));
  const first = await boot({
    refreshStorage,
    pathname: "/auth/callback",
    search: "",
    hash: "#stock/TSLA/deep-dive/overview",
    fetchBox,
  });
  assert.equal(first.outcome.status, "signed-in");
  assert.equal(first.outcome.navigate, null);
  assert.equal(first.outcome.replaceUrl, "/#stock/TSLA/deep-dive/overview");
  assert.doesNotMatch(first.outcome.replaceUrl, /code=|state=|access_token|refresh_token|authorize/);
  assert.equal(fetchBox.calls.length, 1);

  const again = await boot({
    refreshStorage,
    pathname: "/",
    hash: "#stock/TSLA/deep-dive/overview",
    fetchBox,
  });
  assert.equal(again.outcome.status, "signed-in");
  assert.equal(again.outcome.replaceUrl, null);
  assert.equal(again.outcome.navigate, null);
  assert.equal(fetchBox.calls.length, 2);
  assert.equal(readRefreshToken(refreshStorage), REFRESH);
});

test("a callback whose code was already consumed keeps the stored session", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  const pkceStorage = memoryStorage();
  const fetchBox = tokenFetch((call) => {
    assert.equal(call.body.get("grant_type"), "refresh_token");
    return issuedTokens({ access: NEXT_ACCESS });
  });
  const { outcome } = await boot({
    refreshStorage,
    pkceStorage,
    pathname: "/auth/callback",
    search: `?code=${CODE}&state=stale-state`,
    fetchBox,
  });
  assert.equal(outcome.status, "signed-in");
  assert.equal(outcome.replaceUrl, "/");
  assert.equal(outcome.replaceUrl.includes(CODE), false);
  assert.equal(fetchBox.calls.length, 1);
  assert.equal(readRefreshToken(refreshStorage), REFRESH);
});

test("the authorization-code exchange stores only the refresh token and returns to the saved route", async () => {
  const refreshStorage = memoryStorage();
  const pkceStorage = memoryStorage({
    oauth_state: "state-1",
    oauth_verifier: "verifier-1",
    oauth_return_path: "/",
    oauth_return_hash: "#stock/TSLA/deep-dive/overview",
    oauth_return_dashboard_ticker: "SPCX",
  });
  const fetchBox = tokenFetch((call) => {
    assert.equal(call.body.get("grant_type"), "authorization_code");
    assert.equal(call.body.get("code"), CODE);
    assert.equal(call.body.get("code_verifier"), "verifier-1");
    assert.equal(call.body.get("redirect_uri"), REDIRECT);
    return issuedTokens({ rotate: true, refresh: REFRESH, access: ACCESS });
  });
  const { outcome } = await boot({
    refreshStorage,
    pkceStorage,
    pathname: "/auth/callback",
    search: `?code=${CODE}&state=state-1`,
    fetchBox,
  });

  assert.equal(outcome.status, "signed-in");
  assert.equal(outcome.accessToken, ACCESS);
  assert.equal(outcome.replaceUrl, "/#stock/TSLA/deep-dive/overview");
  assert.equal(readRefreshToken(refreshStorage), REFRESH);
  assert.equal(refreshStorage.getItem(REFRESH_TOKEN_KEY), REFRESH);
  assert.equal(pkceStorage.getItem("oauth_state"), null);
  assert.equal(pkceStorage.getItem("oauth_verifier"), null);
  assert.equal(pkceStorage.getItem("oauth_return_dashboard_ticker"), "SPCX");
  assert.equal(outcome.replaceUrl.includes(CODE), false);
  assert.equal(outcome.replaceUrl.includes(REFRESH), false);
});

test("a used authorization code does not wipe a refresh token saved by the first exchange", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  const pkceStorage = memoryStorage({ oauth_state: "state-1", oauth_verifier: "verifier-1" });
  let attempts = 0;
  const fetchBox = tokenFetch((call) => {
    attempts += 1;
    if (call.body.get("grant_type") === "authorization_code") {
      return jsonResponse({ error: "invalid_grant", error_description: "Invalid authorization code" }, 400);
    }
    return issuedTokens({ access: NEXT_ACCESS });
  });
  const { outcome } = await boot({
    refreshStorage,
    pkceStorage,
    pathname: "/auth/callback",
    search: `?code=${CODE}&state=state-1`,
    fetchBox,
  });
  assert.equal(outcome.status, "signed-in");
  assert.equal(outcome.accessToken, NEXT_ACCESS);
  assert.equal(readRefreshToken(refreshStorage), REFRESH);
  assert.equal(attempts, 2);
});

test("explicit sign-out clears tokens and calls Cognito logout without putting tokens in the URL", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  const pkceStorage = memoryStorage({
    oauth_state: "state-1",
    oauth_verifier: "verifier-1",
    oauth_return_path: "/ticker/TSLA",
    oauth_return_hash: "#stock/TSLA",
    oauth_return_dashboard_ticker: "TSLA",
  });
  const fetchBox = tokenFetch(() => jsonResponse({}));
  const logoutUrl = await signOutSession({
    fetchImpl: fetchBox.fetchImpl,
    cognitoDomain: DOMAIN,
    clientId: CLIENT,
    logoutUri: LOGOUT_URI,
    refreshStorage,
    pkceStorage,
    pkceKeys: PKCE_KEYS,
  });

  assert.equal(readRefreshToken(refreshStorage), null);
  for (const key of Object.values(PKCE_KEYS)) assert.equal(pkceStorage.getItem(key), null);
  assert.equal(fetchBox.calls.length, 1);
  assert.equal(fetchBox.calls[0].url, `${DOMAIN}/oauth2/revoke`);
  assert.equal(fetchBox.calls[0].body.get("token"), REFRESH);
  assert.equal(fetchBox.calls[0].body.get("client_id"), CLIENT);

  const logout = new URL(logoutUrl);
  assert.equal(logout.origin + logout.pathname, `${DOMAIN}/logout`);
  assert.deepEqual([...logout.searchParams.keys()].sort(), ["client_id", "logout_uri"]);
  assert.equal(logout.searchParams.get("client_id"), CLIENT);
  assert.equal(logout.searchParams.get("logout_uri"), LOGOUT_URI);
  assert.equal(logoutUrl.includes(REFRESH), false);
  assert.equal(logoutUrl.includes("access_token"), false);
  assert.equal(logoutUrl.includes("refresh_token"), false);

  const again = await boot({ refreshStorage, pkceStorage, fetchBox });
  assert.equal(again.outcome.status, "signed-out");
  assert.equal(again.outcome.navigate, null);
  assert.equal(fetchBox.calls.length, 1);
});

test("an expired or revoked refresh token signs out once and does not loop", async () => {
  for (const description of ["Refresh Token has expired", "Refresh Token has been revoked"]) {
    const refreshStorage = memoryStorage();
    writeRefreshToken(refreshStorage, REFRESH);
    const fetchBox = tokenFetch(() => jsonResponse({
      error: "invalid_grant",
      error_description: description,
    }, 400));
    const first = await boot({
      refreshStorage,
      pathname: "/auth/callback",
      hash: "#stock/TSLA/deep-dive/overview",
      fetchBox,
    });

    assert.equal(first.outcome.status, "signed-out");
    assert.equal(first.outcome.reason, "rejected");
    assert.equal(first.outcome.navigate, null);
    assert.equal(first.outcome.replaceUrl, "/#stock/TSLA/deep-dive/overview");
    assert.doesNotMatch(first.outcome.replaceUrl, /authorize|logout|code=/);
    assert.equal(readRefreshToken(refreshStorage), null);
    assert.equal(fetchBox.calls.length, 1);
    assert.equal(String(first.outcome.message || "").includes(REFRESH), false);

    const second = await boot({
      refreshStorage,
      pathname: "/",
      hash: "#stock/TSLA/deep-dive/overview",
      fetchBox,
    });
    assert.equal(second.outcome.status, "signed-out");
    assert.equal(second.outcome.navigate, null);
    assert.equal(second.outcome.replaceUrl, null);
    assert.equal(fetchBox.calls.length, 1);
  }
});

test("a network failure while refreshing keeps the refresh token", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  const fetchBox = tokenFetch(() => { throw new TypeError("Load failed"); });
  const { outcome } = await boot({
    refreshStorage,
    pathname: "/",
    hash: "#stock/TSLA/deep-dive/overview",
    fetchBox,
  });
  assert.equal(outcome.status, "unavailable");
  assert.equal(outcome.navigate, null);
  assert.equal(outcome.replaceUrl, null);
  assert.equal(locationAfterAuth("/", "#stock/TSLA/deep-dive/overview", outcome), "/#stock/TSLA/deep-dive/overview");
  assert.equal(readRefreshToken(refreshStorage), REFRESH);

  fetchBox.fetchImpl = undefined;
  const recovered = tokenFetch(() => issuedTokens({ access: NEXT_ACCESS }));
  const second = await boot({ refreshStorage, pathname: "/", hash: "#stock/TSLA/deep-dive/overview", fetchBox: recovered });
  assert.equal(second.outcome.status, "signed-in");
  assert.equal(second.outcome.accessToken, NEXT_ACCESS);
  assert.equal(readRefreshToken(refreshStorage), REFRESH);
});

test("a Cognito 503 while refreshing does not sign the user out", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  const fetchBox = tokenFetch(() => jsonResponse({ error: "temporarily_unavailable" }, 503));
  const { outcome } = await boot({ refreshStorage, fetchBox });
  assert.equal(outcome.status, "unavailable");
  assert.equal(readRefreshToken(refreshStorage), REFRESH);
});

test("a transient API 401 refreshes once and keeps the session", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  const sent = [];
  const fetchBox = tokenFetch(() => issuedTokens({ access: NEXT_ACCESS }));
  const outcome = await performAuthorizedRequest({
    accessToken: ACCESS,
    refresh: () => bootstrapRefresh(refreshStorage, fetchBox),
    send: async (accessToken) => {
      sent.push(accessToken);
      if (sent.length === 1) {
        const error = new Error("Not signed in");
        error.status = 401;
        throw error;
      }
      return { ok: true };
    },
  });
  assert.equal(outcome.result.ok, true);
  assert.equal(outcome.accessToken, NEXT_ACCESS);
  assert.deepEqual(sent, [ACCESS, NEXT_ACCESS]);
  assert.equal(readRefreshToken(refreshStorage), REFRESH);
  assert.equal(fetchBox.calls.length, 1);
  assert.equal(outcome.sessionExpired, undefined);
});

test("a network error on an API call does not refresh or sign the user out", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  let refreshed = false;
  await assert.rejects(
    performAuthorizedRequest({
      accessToken: ACCESS,
      refresh: async () => {
        refreshed = true;
        return { ok: true, accessToken: NEXT_ACCESS };
      },
      send: async () => {
        const error = new Error("Dashboard data could not be reached (Load failed).");
        error.status = 0;
        throw error;
      },
    }),
    (error) => {
      assert.equal(error.status, 0);
      assert.equal(error.sessionExpired, undefined);
      return true;
    },
  );
  assert.equal(refreshed, false);
  assert.equal(readRefreshToken(refreshStorage), REFRESH);
});

test("a 401 that is still 401 after a successful refresh does not sign the user out", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  const fetchBox = tokenFetch(() => issuedTokens({ access: NEXT_ACCESS }));
  await assert.rejects(
    performAuthorizedRequest({
      accessToken: ACCESS,
      refresh: () => bootstrapRefresh(refreshStorage, fetchBox),
      send: async () => {
        const error = new Error("Not signed in");
        error.status = 401;
        throw error;
      },
    }),
    (error) => {
      assert.equal(error.status, 401);
      assert.equal(error.sessionExpired, undefined);
      assert.equal(error.transientAuth, true);
      return true;
    },
  );
  assert.equal(readRefreshToken(refreshStorage), REFRESH);
  assert.equal(fetchBox.calls.length, 1);
});

test("a 401 whose refresh token is expired or revoked ends the session", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  const fetchBox = tokenFetch(() => jsonResponse({
    error: "invalid_grant",
    error_description: "Refresh Token has been revoked",
  }, 400));
  await assert.rejects(
    performAuthorizedRequest({
      accessToken: ACCESS,
      refresh: () => bootstrapRefresh(refreshStorage, fetchBox),
      send: async () => {
        const error = new Error("Not signed in");
        error.status = 401;
        throw error;
      },
    }),
    (error) => {
      assert.equal(error.sessionExpired, true);
      assert.equal(error.status, 401);
      return true;
    },
  );
  assert.equal(readRefreshToken(refreshStorage), null);
});

test("a transient failure of the refresh call after an API 401 keeps the refresh token", async () => {
  const refreshStorage = memoryStorage();
  writeRefreshToken(refreshStorage, REFRESH);
  const fetchBox = tokenFetch(() => { throw new TypeError("Load failed"); });
  await assert.rejects(
    performAuthorizedRequest({
      accessToken: ACCESS,
      refresh: () => bootstrapRefresh(refreshStorage, fetchBox),
      send: async () => {
        const error = new Error("Not signed in");
        error.status = 401;
        throw error;
      },
    }),
    (error) => {
      assert.equal(error.status, 401);
      assert.equal(error.sessionExpired, undefined);
      return true;
    },
  );
  assert.equal(readRefreshToken(refreshStorage), REFRESH);
});

test("sign-out during a refresh does not write the refresh token back", async () => {
  const web = fakeStorage();
  const refreshStorage = createSafeStorage("localStorage", { win: { localStorage: web } });
  writeRefreshToken(refreshStorage, REFRESH);
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const fetchBox = tokenFetch(async (call) => {
    if (String(call.url).endsWith("/oauth2/revoke")) return jsonResponse({});
    await gate;
    return issuedTokens({ access: NEXT_ACCESS, refresh: "rotated-refresh-token-sentinel", rotate: true });
  });
  const client = createAuthClient({
    fetchImpl: fetchBox.fetchImpl,
    cognitoDomain: () => DOMAIN,
    clientId: () => CLIENT,
    redirectUri: () => REDIRECT,
    refreshStorage,
    pkceStorage: memoryStorage(),
    pkceKeys: PKCE_KEYS,
  });
  const pending = client.refresh();
  const logoutUrl = await client.signOut(LOGOUT_URI);
  release();
  const restored = await pending;
  assert.equal(restored.ok, false);
  assert.equal(readRefreshToken(refreshStorage), null);
  assert.equal(logoutUrl.includes(REFRESH), false);
  assert.match(logoutUrl, /\/logout\?/);
});

test("refresh tokens are origin-scoped and are not copied into cookies", () => {
  const originA = fakeStorage();
  const originB = fakeStorage();
  const storeA = createSafeStorage("localStorage", { win: { localStorage: originA } });
  const storeB = createSafeStorage("localStorage", { win: { localStorage: originB } });
  writeRefreshToken(storeA, REFRESH);
  assert.equal(readRefreshToken(storeA), REFRESH);
  assert.equal(readRefreshToken(storeB), null);

  const win = {};
  const throwing = () => { throw new DOMException("The operation is insecure.", "SecurityError"); };
  Object.defineProperty(win, "localStorage", { get: throwing });
  const doc = { cookie: "" };
  Object.defineProperty(doc, "cookie", {
    get: () => "",
    set: () => { throw new Error("refresh token must not be written to a cookie"); },
  });
  const blocked = createSafeStorage("localStorage", { win, doc });
  assert.equal(blocked.backend, "memory");
  assert.doesNotThrow(() => writeRefreshToken(blocked, REFRESH));
  clearRefreshToken(blocked);
});

test("the dashboard wires startup restore, localStorage, and sign-out without logging tokens", () => {
  const app = readFileSync(new URL("./app.js", import.meta.url), "utf8");
  const auth = readFileSync(new URL("./auth-session.js", import.meta.url), "utf8");
  assert.match(app, /const tokenStorage = createSafeStorage\("localStorage"\)/);
  assert.match(app, /\.bootstrap\(\{/);
  assert.match(app, /outcome\.replaceUrl/);
  assert.match(app, /\.signOut\(/);
  assert.match(app, /error\.sessionExpired/);
  assert.doesNotMatch(app, /tokenStorage = createSafeStorage\("localStorage", \{[^}]*cookieFallback/);
  assert.doesNotMatch(app, /console\.(log|info|debug|warn|error)\([^)]*(refresh_token|access_token|accessToken|refreshToken)/);
  assert.doesNotMatch(auth, /console\./);
});

function bootstrapRefresh(refreshStorage, fetchBox) {
  return import("./auth-session.js").then(({ refreshSession }) => refreshSession({
    fetchImpl: fetchBox.fetchImpl,
    tokenUrl: TOKEN_URL,
    clientId: CLIENT,
    storage: refreshStorage,
  }));
}
