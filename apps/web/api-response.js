// Reading site API responses. A 200 whose body cannot be read (cut off by the network,
// not JSON, not an object) is an error, never an empty payload: treating it as `{}`
// renders the dashboard as if no data had been published, with nothing to say why.

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
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
    const message = body && typeof body.error === "string" ? body.error : `${what} failed (${response.status}).`;
    throw new ApiError(message, response.status);
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
