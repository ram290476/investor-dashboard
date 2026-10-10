import assert from "node:assert/strict";
import test from "node:test";

import {
  ApiError,
  applyRefresh,
  dashboardBanner,
  hasDashboardData,
  networkError,
  readApiResponse,
  UNPUBLISHED_DASHBOARD_MESSAGE,
} from "./api-response.js";

const json = (body, status = 200) =>
  new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

test("a readable 200 returns the parsed object", async () => {
  const body = await readApiResponse(json({ tickers: { TSLA: { price_history: [] } } }), "Dashboard data");
  assert.deepEqual(body, { tickers: { TSLA: { price_history: [] } } });
});

test("a 200 whose JSON is cut off is an error, not an empty dashboard", async () => {
  const full = JSON.stringify({ tickers: { TSLA: { price_history: [{ date: "2026-10-06", close: 380.68 }] } } });
  await assert.rejects(readApiResponse(json(full.slice(0, 40)), "Dashboard data"), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 200);
    assert.match(error.message, /Dashboard data returned a response that could not be read \(40 bytes\)/);
    return true;
  });
});

test("an empty or non-object 200 body is an error", async () => {
  await assert.rejects(readApiResponse(json(""), "Dashboard data"), /could not be read \(0 bytes\)/);
  await assert.rejects(readApiResponse(json("[]"), "Dashboard data"), /could not be read/);
  await assert.rejects(readApiResponse(json("null"), "Dashboard data"), /could not be read/);
});

test("non-2xx responses keep the API message and status", async () => {
  await assert.rejects(readApiResponse(json({ error: "Not signed in" }, 401), "Dashboard data"), (error) => {
    assert.equal(error.status, 401);
    assert.equal(error.message, "Not signed in");
    return true;
  });
  await assert.rejects(readApiResponse(json("<html>bad gateway</html>", 502), "Dashboard data"), (error) => {
    assert.equal(error.status, 502);
    assert.equal(error.message, "Dashboard data failed (502).");
    return true;
  });
});

test("a body that fails mid-download reports the transport error", async () => {
  const response = { ok: true, status: 200, text: () => Promise.reject(new TypeError("Load failed")) };
  await assert.rejects(readApiResponse(response, "Dashboard data"), (error) => {
    assert.equal(error.status, 0);
    assert.match(error.message, /Dashboard data did not finish downloading \(Load failed\)/);
    return true;
  });
});

test("network failures carry status 0 and the browser's reason", () => {
  const error = networkError("Dashboard data", new TypeError("Load failed"));
  assert.equal(error.status, 0);
  assert.match(error.message, /Dashboard data could not be reached \(Load failed\)/);
});

test("a failed first dashboard load keeps its message until a later load succeeds", () => {
  const loading = { dashboard: null, dashboardState: "loading", status: null, dashboardError: "" };
  const failed = applyRefresh(
    loading,
    { status: "rejected", reason: networkError("Dashboard data", new TypeError("Load failed")) },
    { status: "fulfilled", value: { jobs: [] } },
  );
  assert.equal(failed.dashboard, null);
  assert.equal(failed.dashboardState, "error");
  assert.match(dashboardBanner(failed), /Dashboard data could not be reached \(Load failed\)/);
  assert.equal(dashboardBanner(failed), failed.dashboardError);

  const recovered = applyRefresh(
    failed,
    { status: "fulfilled", value: { tickers: { TSLA: { price_history: [] } }, generated_at: "2026-10-07T00:00:00Z" } },
    { status: "fulfilled", value: { jobs: [] } },
  );
  assert.equal(recovered.dashboardState, "ready");
  assert.equal(recovered.dashboard.tickers.TSLA.price_history.length, 0);
  assert.equal(dashboardBanner(recovered), "");
});

test("502 and 503 codes replace the generic failure text", async () => {
  const codes = {
    STS_UNAVAILABLE: "The sign-in check is temporarily unavailable. Try again in a minute.",
    SERVING_READ_FAILED: "Dashboard data is being rebuilt; try again in a minute.",
    PREFS_STORE_UNAVAILABLE: "Preferences could not be reached. Try again in a minute.",
    SERVING_DOCUMENT_INVALID: "Dashboard data is being rebuilt; try again in a minute.",
  };
  for (const [code, message] of Object.entries(codes)) {
    for (const status of [502, 503]) {
      await assert.rejects(readApiResponse(json({ error: "Internal Server Error", code }, status), "Saving preferences"), (error) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, status);
        assert.equal(error.code, code);
        assert.equal(error.message, message);
        return true;
      });
    }
  }
});

test("a 500 keeps the API error text", async () => {
  await assert.rejects(
    readApiResponse(json({ error: "Something went wrong. Try again in a minute.", code: "INTERNAL" }, 500), "Dashboard data"),
    (error) => {
      assert.equal(error.status, 500);
      assert.equal(error.code, "INTERNAL");
      assert.equal(error.message, "Something went wrong. Try again in a minute.");
      return true;
    },
  );
});

test("an unpublished 503 stays distinct from a serving-document failure", () => {
  const loading = { dashboard: null, dashboardState: "loading", status: null, dashboardError: "" };
  const unpublished = applyRefresh(
    loading,
    { status: "rejected", reason: new ApiError("Dashboard data has not been published yet", 503, "DASHBOARD_NOT_READY") },
    { status: "fulfilled", value: { jobs: [] } },
  );
  assert.equal(unpublished.dashboardState, "unpublished");
  assert.equal(dashboardBanner(unpublished), UNPUBLISHED_DASHBOARD_MESSAGE);

  const rebuilding = applyRefresh(
    loading,
    {
      status: "rejected",
      reason: new ApiError("Dashboard data is being rebuilt; try again in a minute.", 503, "SERVING_READ_FAILED"),
    },
    { status: "fulfilled", value: { jobs: [] } },
  );
  assert.equal(rebuilding.dashboardState, "error");
  assert.equal(dashboardBanner(rebuilding), "Dashboard data is being rebuilt; try again in a minute.");
});

test("hasDashboardData requires a tickers map", () => {
  assert.equal(hasDashboardData({ tickers: { TSLA: {} } }), true);
  assert.equal(hasDashboardData({ tickers: {} }), true);
  assert.equal(hasDashboardData({}), false);
  assert.equal(hasDashboardData(null), false);
  assert.equal(hasDashboardData({ tickers: [] }), false);
});
