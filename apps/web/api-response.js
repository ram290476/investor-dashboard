// Reading site API responses. A 200 whose body cannot be read (cut off by the network,
// not JSON, not an object) is an error, never an empty payload: treating it as `{}`
// renders the dashboard as if no data had been published, with nothing to say why.

export class ApiError extends Error {
  constructor(message, status = 0, code = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

// Codes from prefs_api's error boundary. 502/503 responses use these instead of a generic failure.
export const API_CODE_MESSAGES = {
  STS_UNAVAILABLE: "The sign-in check is temporarily unavailable. Try again in a minute.",
  SERVING_READ_FAILED: "Dashboard data is being rebuilt; try again in a minute.",
  PREFS_STORE_UNAVAILABLE: "Preferences could not be reached. Try again in a minute.",
  SERVING_DOCUMENT_INVALID: "Dashboard data is being rebuilt; try again in a minute.",
  INTERNAL: "Something went wrong. Try again in a minute.",
};

export function messageForApiCode(code) {
  return API_CODE_MESSAGES[code] || "";
}

// fetch() rejected before any response (offline, blocked, CORS, Safari "Load failed").
export function networkError(what, cause) {
  const detail = cause?.message ? ` (${cause.message})` : "";
  return new ApiError(`${what} could not be reached${detail}. Check your connection and try again.`, 0);
}

export async function readApiResponse(response, what) {
  let text;
  try {
    text = await response.text();
  } catch (cause) {
    const detail = cause?.message ? ` (${cause.message})` : "";
    throw new ApiError(`${what} did not finish downloading${detail}. Try again.`, response.ok ? 0 : response.status);
  }
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!response.ok) {
    const code = body && typeof body.code === "string" ? body.code : "";
    const mapped = response.status === 502 || response.status === 503 ? messageForApiCode(code) : "";
    const fallback = body && typeof body.error === "string" ? body.error : `${what} failed (${response.status}).`;
    throw new ApiError(mapped || fallback, response.status, code);
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new ApiError(`${what} returned a response that could not be read (${text.length.toLocaleString()} bytes). Try again.`, response.status);
  }
  return body;
}

// A dashboard document the price panel can draw from: a tickers map keyed by symbol.
export function hasDashboardData(dashboard) {
  return Boolean(dashboard && typeof dashboard.tickers === "object" && dashboard.tickers !== null && !Array.isArray(dashboard.tickers));
}

export const UNPUBLISHED_DASHBOARD_MESSAGE =
  "Dashboard data has not been published. Start the historical backfill; the serving snapshot is rebuilt as data arrives.";

function settledFailures(dashboardResult, statusResult) {
  return [dashboardResult, statusResult]
    .filter((result) => result && result.status === "rejected" && result.reason)
    .map((result) => result.reason);
}

// The banner refreshData shows. Empty when both calls succeeded.
export function refreshErrorMessage(dashboardResult, statusResult) {
  const failures = settledFailures(dashboardResult, statusResult);
  const dashboardFailure = dashboardResult?.status === "rejected" ? dashboardResult.reason : null;
  if (dashboardFailure && messageForApiCode(dashboardFailure.code)) return dashboardFailure.message;
  if (dashboardFailure?.status === 503) return UNPUBLISHED_DASHBOARD_MESSAGE;
  const dashboardError = failures.find((error) => error.status !== 503) || failures[0];
  return dashboardError?.message || "";
}

// Fold one refresh into the session fields a later redraw will read.
// The error string stays until a later refresh has nothing to report.
// A 401 still applies any successful payload, then leaves the previous banner alone;
// the caller replaces the page with the sign-in gate.
// Ordinary redraws (watchlist, period, preferences) read this instead of a passed-in message.
export function dashboardBanner(session) {
  return session?.dashboardError || "";
}

export function applyRefresh(session, dashboardResult, statusResult) {
  const next = {
    dashboard: session?.dashboard ?? null,
    dashboardState: session?.dashboardState ?? "loading",
    status: session?.status ?? null,
    dashboardError: session?.dashboardError || "",
    unauthorized: false,
  };
  if (dashboardResult?.status === "fulfilled") {
    next.dashboard = dashboardResult.value;
    next.dashboardState = "ready";
  } else if (!session?.dashboard) {
    const reason = dashboardResult?.reason;
    const unpublished = reason?.status === 503 && !messageForApiCode(reason?.code);
    next.dashboardState = unpublished ? "unpublished" : "error";
  }
  if (statusResult?.status === "fulfilled") next.status = statusResult.value;
  if (settledFailures(dashboardResult, statusResult).some((error) => error.status === 401)) {
    next.unauthorized = true;
    return next;
  }
  next.dashboardError = refreshErrorMessage(dashboardResult, statusResult);
  return next;
}
