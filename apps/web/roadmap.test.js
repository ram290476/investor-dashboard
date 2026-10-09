import assert from "node:assert/strict";
import test from "node:test";
import { catalystCategory, catalystCategoriesInWindow, catalystRows, markerIndex, movingAverageRows, sensitivityRows, sortedDrivers } from "./roadmap.js";

test("category controls include only events aligned inside the selected window in catalog order", () => {
  const dashboard = { events: [
    { event_ts: "2026-10-03", type: "launch", title: "Launch", tickers: ["TSLA"] },
    { event_ts: "2026-10-02", type: "filing", title: "Filing", tickers: ["TSLA"] },
    { event_ts: "2026-10-07", type: "cpi", title: "Future", tickers: ["TSLA"] },
    { event_ts: "2026-10-02", type: "tariff", title: "Other ticker", tickers: ["SPCX"] },
  ] };
  assert.deepEqual(catalystCategoriesInWindow(dashboard, "TSLA", [
    { date: "2026-10-02" }, { date: "2026-10-05" },
  ]).map(category => category.id), ["filings", "space"]);
  assert.deepEqual(catalystCategoriesInWindow(dashboard, "TSLA", []), []);
});

test("catalysts combine scoped events, filings and global macro releases without demo facts", () => {
  const dashboard = {
    events: [
      {
        event_ts: "2026-10-05",
        type: "launch",
        title: "Space launch",
        source: "launch-library",
        source_url: "https://ll.thespacedevs.com/launch/1",
        ingested_at: "2026-10-05T12:00:00Z",
        observed_at: "2026-10-05T12:00:00Z",
        tickers: ["SPCX"],
      },
      { event_ts: "2026-10-02", type: "av_permit", title: "Permit", tickers: ["TSLA"] },
      { event_ts: "bad", title: "Invalid date", tickers: [] },
    ],
    releases: { next: [{ series: "CPI", release_ts: "2026-10-10" }] },
    tickers: { TSLA: { filings: [{ filed_at: "2026-10-01", form: "10-Q", url: "https://www.sec.gov/report" }] } },
  };
  const rows = catalystRows(dashboard, "TSLA");
  assert.deepEqual(rows.map(row => row.category), ["filings", "robotaxi", "inflation"]);
  const spaceLaunch = catalystRows(dashboard, "SPCX").find(row => row.title === "Space launch");
  assert.equal(spaceLaunch?.source, "launch-library");
  assert.equal(spaceLaunch?.observed_at, "2026-10-05T12:00:00Z");
  assert.equal(rows.find(row => row.title === "Permit").source, null);
  assert.ok(rows.every(row => row.id));
  assert.equal(catalystRows({}, "TSLA").length, 0);
  assert.equal(catalystCategory("tariff"), "policy");
  assert.equal(catalystCategory("FOMC"), "rates");
  assert.equal(catalystCategory("corporate_update"), "other");
  assert.equal(catalystCategory("interest_rate"), "rates");
});

test("identical filing titles preserve distinct source documents and discard exact duplicates", () => {
  const first = { filed_at: "2026-10-01", form: "4", url: "https://www.sec.gov/a" };
  const second = { ...first, url: "https://www.sec.gov/b" };
  const rows = catalystRows({ tickers: { TSLA: { filings: [first, second, first] } } }, "TSLA");
  assert.equal(rows.length, 2);
  assert.notEqual(rows[0].id, rows[1].id);
});

test("markers use the next stored session on nontrading days and exclude out-of-range events", () => {
  const bars = [{ date: "2026-10-02" }, { date: "2026-10-05" }];
  assert.equal(markerIndex({ date: "2026-10-03" }, bars), 1);
  assert.equal(markerIndex({ date: "2026-09-01" }, bars), -1);
  assert.equal(markerIndex({ date: "2026-10-06" }, bars), -1);
  assert.equal(markerIndex({ date: "2026-10-05" }, []), -1);
  assert.equal(markerIndex({ date: "2026-10-05T15:00:00Z" }, [
    { ts: "2026-10-05T14:00:00Z" }, { ts: "2026-10-05T15:00:00Z" },
  ]), 0);
});

test("driver drawer sorts finite values ahead of unavailable ones", () => {
  const data = { trend: { rows: [
    { series_id: "Z", effect: null, corr_90d: null },
    { series_id: "B", effect: -0.8, corr_90d: 0.2 },
    { series_id: "A", effect: 0.1, corr_90d: -0.9 },
  ] } };
  assert.deepEqual(sortedDrivers(data, "name").map(row => row.series_id), ["A", "B", "Z"]);
  assert.deepEqual(sortedDrivers(data, "effect").map(row => row.series_id), ["B", "A", "Z"]);
  assert.deepEqual(sortedDrivers(data, "correlation").map(row => row.series_id), ["A", "B", "Z"]);
});

test("moving averages require full history and sensitivity requires twelve paired releases", () => {
  const history = Array.from({ length: 20 }, (_, i) => ({ date: `2026-09-${String(i + 1).padStart(2, "0")}`, close: i + 1 }));
  const averages = movingAverageRows(history);
  assert.equal(averages[1].value, 10.5);
  assert.equal(averages[2].value, null);
  assert.equal(movingAverageRows(history.map(bar => ({ ...bar, close: 200, adj_close: bar.close })))[1].value, 10.5);
  assert.deepEqual(sensitivityRows({ release_links: { summaries: [
    { n_releases: 11, correlation_surprise: 0.9 },
    { n_releases: 12, correlation_surprise: null },
    { n_releases: 20, correlation_surprise: -0.5 },
  ] } }), [{ n_releases: 20, correlation_surprise: -0.5 }]);
});
