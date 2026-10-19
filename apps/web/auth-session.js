// Session persistence for the Cognito PKCE client.
//
// The access token stays in memory. The refresh token is stored in localStorage
// (via createSafeStorage, with no cookie fallback) because it is the 12-hour
// session credential: a reload and a new tab on the same origin should stay
// signed in until that token expires, is revoked, or the user signs out.
// localStorage is origin-scoped, so investor.vellamsetti.com and the CloudFront
// hostname keep separate sessions and the token is not attached to requests.
// sessionStorage would drop the session when the tab closes, which is shorter
// than the refresh token's lifetime. Cookies are not used for the refresh token.
//
// Tokens are never placed in URLs or written to logs. A rejected refresh token
// (expired or revoked) clears storage and returns the user to the in-app sign-in
// gate once. A network error or a second API 401 after a successful refresh
// keeps the stored token.

export const REFRESH_TOKEN_KEY = "oauth_refresh_token";

const TERMINAL_TOKEN_ERRORS = new Set(["invalid_grant", "invalid_token"]);

export class TokenRequestError extends Error {
  constructor(message, { kind = "transient", errorCode = "", status = 0 } = {}) {
    super(message);
    this.name = "TokenRequestError";
    this.kind = kind;
    this.errorCode = errorCode;
    this.status = status;
  }
}

export function readRefreshToken(storage) {
  const value = storage?.getItem(REFRESH_TOKEN_KEY);
  return typeof value === "string" && value ? value : null;
}

export function writeRefreshToken(storage, refreshToken) {
  if (!refreshToken) return;
  storage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function clearRefreshToken(storage) {
  storage?.removeItem(REFRESH_TOKEN_KEY);
}

function redact(text, secrets) {
  let out = String(text || "");
  for (const secret of secrets) {
    if (typeof secret === "string" && secret.length >= 8) out = out.split(secret).join("[redacted]");
  }
  return out.slice(0, 300);
}

function safeHash(hash) {
  const value = String(hash || "");
  if (!value.startsWith("#") || value.startsWith("#//")) return "";
  return value;
}

function safeReturnUrl(path, hash) {
  const kept = safeHash(hash);
  const raw = String(path || "/");
  if (!raw.startsWith("/") || raw.startsWith("//")) return `/${kept}`;
  return `${raw}${kept}`;
}

// Drop ?code / ?state / ?error. /auth/callback itself is not a page the user stays on.
export function locationWithoutAuthQuery(pathname, hash) {
  const kept = safeHash(hash);
  if (pathname === "/auth/callback") return `/${kept}`;
  return safeReturnUrl(pathname || "/", kept);
}

// Where the app should be after bootstrap. A null replaceUrl means the current
// path and hash stay put, which is what a refresh of a dashboard route needs.
export function locationAfterAuth(pathname, hash, outcome) {
  if (outcome?.replaceUrl == null) return `${pathname || "/"}${safeHash(hash)}`;
  return outcome.replaceUrl;
}

export async function requestToken(fetchImpl, tokenUrl, params) {
  const secrets = [params.refresh_token, params.code, params.code_verifier].filter(Boolean);
  let response;
  try {
    response = await fetchImpl(tokenUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Accept: "application/json",
      },
      body: new URLSearchParams(params),
    });
  } catch {
    throw new TokenRequestError("Cognito could not be reached.", { kind: "transient", status: 0 });
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const accessToken = body && typeof body.access_token === "string" ? body.access_token : "";
  if (!response.ok || !accessToken) {
    const errorCode = body && typeof body.error === "string" ? body.error : "";
    const description = body && typeof body.error_description === "string" ? body.error_description : "";
    const terminal = TERMINAL_TOKEN_ERRORS.has(errorCode);
    throw new TokenRequestError(
      redact(description || errorCode || "Cognito did not return an access token.", secrets),
      { kind: terminal ? "rejected" : "transient", errorCode, status: response.status },
    );
  }
  return {
    accessToken,
    idToken: typeof body.id_token === "string" ? body.id_token : "",
    refreshToken: typeof body.refresh_token === "string" ? body.refresh_token : "",
    expiresIn: Number(body.expires_in) || 0,
  };
}

export async function exchangeAuthorizationCode({
  fetchImpl, tokenUrl, clientId, code, redirectUri, verifier, storage,
}) {
  const tokens = await requestToken(fetchImpl, tokenUrl, {
    grant_type: "authorization_code",
    client_id: clientId,
    code,
    redirect_uri: redirectUri,
    code_verifier: verifier,
  });
  if (tokens.refreshToken) writeRefreshToken(storage, tokens.refreshToken);
  return tokens;
}

export async function refreshSession({ fetchImpl, tokenUrl, clientId, storage, stillCurrent = () => true }) {
  const refreshToken = readRefreshToken(storage);
  if (!refreshToken || !stillCurrent()) return { ok: false, reason: "signed-out" };
  try {
    const tokens = await requestToken(fetchImpl, tokenUrl, {
      grant_type: "refresh_token",
      client_id: clientId,
      refresh_token: refreshToken,
    });
    if (!stillCurrent()) return { ok: false, reason: "signed-out" };
    if (tokens.refreshToken) writeRefreshToken(storage, tokens.refreshToken);
    return {
      ok: true,
      accessToken: tokens.accessToken,
      idToken: tokens.idToken,
      refreshToken: tokens.refreshToken || refreshToken,
      expiresIn: tokens.expiresIn,
    };
  } catch (error) {
    if (error instanceof TokenRequestError && error.kind === "rejected") {
      if (stillCurrent()) clearRefreshToken(storage);
      return { ok: false, reason: "rejected", error };
    }
    return { ok: false, reason: "transient", error };
  }
}

function sessionExpiredError() {
  const error = new Error("Your session expired. Sign in again to continue.");
  error.name = "SessionExpiredError";
  error.sessionExpired = true;
  error.status = 401;
  error.code = "session_expired";
  return error;
}

// One API call with a silent refresh on a 401. A second 401 after Cognito
// accepted the refresh token is treated as transient: the refresh token stays.
export async function performAuthorizedRequest({ accessToken, send, refresh }) {
  let token = accessToken || "";
  let idToken = "";
  if (!token) {
    const restored = await refresh();
    if (!restored.ok) {
      if (restored.reason === "transient") {
        const error = restored.error instanceof Error ? restored.error : new Error("Cognito could not be reached.");
        error.transientAuth = true;
        throw error;
      }
      throw sessionExpiredError();
    }
    token = restored.accessToken;
    idToken = restored.idToken || "";
  }

  try {
    return { accessToken: token, idToken, result: await send(token) };
  } catch (error) {
    if (!error || error.status !== 401) throw error;
    const restored = await refresh();
    if (!restored.ok) {
      if (restored.reason === "transient") throw error;
      throw sessionExpiredError();
    }
    try {
      return {
        accessToken: restored.accessToken,
        idToken: restored.idToken || "",
        result: await send(restored.accessToken),
      };
    } catch (retryError) {
      if (retryError && retryError.status === 401) retryError.transientAuth = true;
      throw retryError;
    }
  }
}

function signInFailed(pkceStorage, pathname, hash) {
  const blocked = pkceStorage?.backend === "memory";
  return {
    status: "sign-in-failed",
    navigate: null,
    message: blocked
      ? "This browser is blocking site storage and cookies (private browsing), so the sign-in state was lost. Allow site data for this site and sign in again."
      : "The sign-in state did not match. Start sign-in again.",
    replaceUrl: locationWithoutAuthQuery(pathname, hash),
  };
}

export async function bootstrapAuth({
  pathname = "/",
  search = "",
  hash = "",
  fetchImpl,
  tokenUrl,
  clientId,
  redirectUri,
  refreshStorage,
  pkceStorage,
  pkceKeys,
  refresh,
}) {
  const query = new URLSearchParams(String(search || "").replace(/^\?/, ""));
  const code = query.get("code");
  const oauthError = query.get("error");
  const state = query.get("state");
  const doRefresh = refresh || (() => refreshSession({ fetchImpl, tokenUrl, clientId, storage: refreshStorage }));

  const signedIn = (tokens, replaceUrl = null) => ({
    status: "signed-in",
    navigate: null,
    accessToken: tokens.accessToken,
    idToken: tokens.idToken || "",
    replaceUrl,
  });

  if (code) {
    const expected = pkceStorage.getItem(pkceKeys.state);
    const verifier = pkceStorage.getItem(pkceKeys.verifier);
    const stateOk = Boolean(expected && state === expected && verifier);
    if (!stateOk) {
      // A reload of /auth/callback after the one-time code was consumed has no
      // verifier left. That is not a sign-out when a refresh token is stored.
      if (!readRefreshToken(refreshStorage)) return signInFailed(pkceStorage, pathname, hash);
    } else {
      try {
        const tokens = await exchangeAuthorizationCode({
          fetchImpl, tokenUrl, clientId, code, redirectUri, verifier, storage: refreshStorage,
        });
        pkceStorage.removeItem(pkceKeys.state);
        pkceStorage.removeItem(pkceKeys.verifier);
        const pendingPath = pkceStorage.getItem(pkceKeys.path) || "/";
        const pendingHash = pkceStorage.getItem(pkceKeys.hash) || "";
        pkceStorage.removeItem(pkceKeys.path);
        pkceStorage.removeItem(pkceKeys.hash);
        return signedIn(tokens, safeReturnUrl(pendingPath, pendingHash));
      } catch (error) {
        if (!(error instanceof TokenRequestError) || error.kind !== "rejected") {
          return {
            status: "unavailable",
            navigate: null,
            message: "The sign-in service could not be reached. Your sign-in attempt is still saved on this device.",
            replaceUrl: null,
          };
        }
        pkceStorage.removeItem(pkceKeys.state);
        pkceStorage.removeItem(pkceKeys.verifier);
      }
    }
  } else if (oauthError && !readRefreshToken(refreshStorage)) {
    const description = query.get("error_description") || oauthError;
    return {
      status: "sign-in-failed",
      navigate: null,
      message: redact(description, []),
      replaceUrl: locationWithoutAuthQuery(pathname, hash),
    };
  }

  const restored = await doRefresh();
  if (restored.ok) {
    let replaceUrl = null;
    if (code || oauthError || pathname === "/auth/callback") {
      const pendingPath = pkceStorage.getItem(pkceKeys.path);
      const pendingHash = pkceStorage.getItem(pkceKeys.hash) || "";
      if (pendingPath) {
        replaceUrl = safeReturnUrl(pendingPath, pendingHash);
        pkceStorage.removeItem(pkceKeys.path);
        pkceStorage.removeItem(pkceKeys.hash);
      } else {
        replaceUrl = locationWithoutAuthQuery(pathname, hash);
      }
    }
    return signedIn(restored, replaceUrl);
  }

  if (restored.reason === "transient") {
    return {
      status: "unavailable",
      navigate: null,
      message: restored.error?.message || "The sign-in service could not be reached.",
      replaceUrl: null,
    };
  }

  return {
    status: "signed-out",
    reason: restored.reason === "rejected" ? "rejected" : "signed-out",
    navigate: null,
    replaceUrl: code || oauthError || pathname === "/auth/callback"
      ? locationWithoutAuthQuery(pathname, hash)
      : null,
  };
}

export async function signOutSession({
  fetchImpl,
  cognitoDomain,
  clientId,
  logoutUri,
  refreshStorage,
  pkceStorage,
  pkceKeys,
}) {
  const refreshToken = readRefreshToken(refreshStorage);
  clearRefreshToken(refreshStorage);
  if (pkceStorage && pkceKeys) {
    for (const key of Object.values(pkceKeys)) pkceStorage.removeItem(key);
  }
  if (refreshToken && fetchImpl && cognitoDomain) {
    try {
      await fetchImpl(new URL("/oauth2/revoke", cognitoDomain), {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ token: refreshToken, client_id: clientId }),
        keepalive: true,
      });
    } catch {
      // Local tokens are already gone. Hosted UI logout still runs.
    }
  }
  const logout = new URL("/logout", cognitoDomain);
  logout.search = new URLSearchParams({
    client_id: clientId,
    logout_uri: logoutUri,
  }).toString();
  return logout.toString();
}

export function createAuthClient(options) {
  let generation = 0;
  let inflight = null;

  function tokenUrl() {
    return new URL("/oauth2/token", options.cognitoDomain()).toString();
  }

  function refresh() {
    const gen = generation;
    if (inflight) return inflight;
    const run = refreshSession({
      fetchImpl: options.fetchImpl,
      tokenUrl: tokenUrl(),
      clientId: options.clientId(),
      storage: options.refreshStorage,
      stillCurrent: () => gen === generation,
    }).finally(() => {
      if (inflight === run) inflight = null;
    });
    inflight = run;
    return run;
  }

  return {
    refresh,
    bootstrap(location) {
      return bootstrapAuth({
        pathname: location.pathname,
        search: location.search,
        hash: location.hash,
        fetchImpl: options.fetchImpl,
        tokenUrl: tokenUrl(),
        clientId: options.clientId(),
        redirectUri: options.redirectUri(),
        refreshStorage: options.refreshStorage,
        pkceStorage: options.pkceStorage,
        pkceKeys: options.pkceKeys,
        refresh,
      });
    },
    signOut(logoutUri) {
      generation += 1;
      return signOutSession({
        fetchImpl: options.fetchImpl,
        cognitoDomain: options.cognitoDomain(),
        clientId: options.clientId(),
        logoutUri,
        refreshStorage: options.refreshStorage,
        pkceStorage: options.pkceStorage,
        pkceKeys: options.pkceKeys,
      });
    },
  };
}
