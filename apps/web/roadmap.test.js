import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import {
  ageLabel, calendarPanelModel, calendarSummary, catalystCategory, catalystCategoriesInWindow, catalystDateLabel,
  catalystMove, catalystMoveLabel, catalystRowView, catalystRows, countdownLabel, edgarCompanyUrl, edgarIndexUrl,
  filingDisplayTitle, filingPanelModel, filingsEmptyMessage, formatSentimentScore, markerIndex, movingAverageRows,
  catalystSensitivityRows, newsClockTitle, newsEmptyMessage, newsGroupName, newsGroups, newsHeader, newsRowView, newsSummary, sensitivityRows,
  sentimentTone, sortedDrivers, stripPublisher,
} from "./roadmap.js";

const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

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

test("catalyst moves use the aligned session close and stay null outside history", () => {
  const bars = [
    { date: "2026-10-02", close: 50, adj_close: 100 },
    { date: "2026-10-05", adj_close: 90 },
    { date: "2026-10-06", adj_close: 99 },
    { date: "2026-10-07", adj_close: 99 },
  ];
  assert.equal(catalystMove({ date: "2026-10-02" }, bars), null);
  assert.equal(catalystMove({ date: "2026-10-03" }, bars), 90 / 100 - 1);
  assert.equal(catalystMove({ date: "2026-10-04" }, bars), 90 / 100 - 1);
  assert.equal(catalystMove({ date: "2026-10-05T19:00:00Z" }, bars), 90 / 100 - 1);
  assert.equal(catalystMove({ date: "2026-10-05T20:05:00Z" }, bars), 99 / 90 - 1);
  assert.equal(catalystMove({ date: "2026-10-06" }, bars), 99 / 90 - 1);
  assert.equal(catalystMove({ date: "2026-10-07" }, bars), 0);
  assert.equal(catalystMove({ date: "2026-09-01" }, bars), null);
  assert.equal(catalystMove({ date: "2026-10-08" }, bars), null);
  assert.equal(catalystMove({ date: "2026-10-05" }, []), null);
  assert.equal(catalystMove({ date: "2026-10-06" }, [
    { date: "2026-10-05", adj_close: 0 },
    { date: "2026-10-06", adj_close: 10 },
  ]), null);
  assert.equal(catalystMove({ date: "2026-10-06" }, [
    { date: "2026-10-05", close: 10 },
    { date: "2026-10-06" },
  ]), null);
  assert.equal(catalystDateLabel("2026-10-02"), "Oct 2");
  assert.equal(catalystDateLabel("2026-10-05T20:05:00Z"), "Oct 5");
  assert.equal(catalystDateLabel("not-a-date"), "—");
});

test("recent catalyst rows reuse the chart marker color and stay one line", () => {
  const block = styles.slice(styles.indexOf("/* recent catalyst rows (#91)"), styles.indexOf("/* end recent catalyst rows (#91) */"));
  assert.match(block, /\.catalyst-dot[\s\S]*background:\s*var\(--catalyst-color, var\(--subtle\)\)/);
  assert.match(block, /\.catalyst-recent-name[\s\S]*text-overflow:\s*ellipsis/);
  assert.match(block, /\.catalyst-recent-row[\s\S]*height:\s*28px/);
  assert.match(block, /\.catalyst-recent-date[\s\S]*color:\s*var\(--muted\)/);
  assert.match(block, /\.catalyst-recent-row:disabled[\s\S]*color:\s*var\(--muted\)/);
  assert.match(block, /@media \(pointer: coarse\)[\s\S]*min-height:\s*44px/);
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}/);
  const render = app.slice(app.indexOf("function renderCatalystRow"), app.indexOf("function renderDrivers"));
  assert.match(render, /overlayColor\(category\.slot/);
  assert.match(render, /visually-hidden/);
  assert.match(render, /catalystRowView\(event/);
  assert.match(render, /selectCatalyst\(event\)/);
  assert.match(render, /Outside the selected chart period/);
  assert.match(render, /renderCatalystList\(events, tickerData\)/);
  assert.match(app, /renderRecentCatalysts\(recent, tickerData\)/);
  assert.match(app, /function renderFilings[\s\S]*renderCatalystSection/);
  assert.match(app, /function renderCatalystCalendar[\s\S]*renderCatalystSection/);
});

test("row dates stay on the calendar day for date-only values and follow the time zone otherwise", () => {
  const now = new Date("2026-10-07T19:15:00-07:00");
  const zone = "America/Los_Angeles";
  assert.equal(catalystDateLabel("2026-10-02", { now, timeZone: zone }), "Oct 2");
  assert.equal(catalystDateLabel("2026-10-31T00:00:00Z", { now, timeZone: zone }), "Oct 31");
  assert.equal(catalystDateLabel("2026-10-08T02:06:20Z", { now, timeZone: zone }), "Oct 7");
  assert.equal(catalystDateLabel("2026-10-05T20:05:00Z", { now, timeZone: "Asia/Tokyo" }), "Oct 6");
  assert.equal(catalystDateLabel("2025-10-08", { now, timeZone: zone }), "Oct 8, 2025");
  assert.equal(catalystDateLabel("not-a-date", { now }), "—");
  const spring = catalystDateLabel("2026-03-08T07:30:00Z", { now: new Date("2026-03-08T12:00:00Z"), timeZone: zone });
  assert.equal(spring, "Mar 7");
});

test("past rows show an arrow or a flat mark, and upcoming rows count days without an arrow", () => {
  assert.deepEqual(catalystMoveLabel(0.046), { text: "▲ +4.6%", tone: "positive", label: "1-day move ▲ +4.6%" });
  assert.deepEqual(catalystMoveLabel(-0.013), { text: "▼ -1.3%", tone: "negative", label: "1-day move ▼ -1.3%" });
  assert.equal(catalystMoveLabel(0.0005).text.startsWith("▲"), true);
  assert.equal(catalystMoveLabel(-0.0005).text.startsWith("▼"), true);
  assert.equal(catalystMoveLabel(0.0004).text, "▬ 0.0%");
  assert.equal(catalystMoveLabel(-0.0004).tone, "neutral");
  assert.equal(catalystMoveLabel(null).text, "—");
  const now = new Date("2026-10-07T19:15:00-07:00");
  const zone = "America/Los_Angeles";
  assert.deepEqual(countdownLabel("2026-10-10", { now, timeZone: zone, precision: "day" }), { text: "in 3d", label: "in 3 days" });
  assert.equal(countdownLabel("2026-10-07", { now, timeZone: zone, precision: "day" }).text, "today");
  assert.equal(countdownLabel("2026-10-08T05:15:00Z", { now, timeZone: zone }).text, "in 3h");
  assert.equal(countdownLabel("2026-10-01", { now, timeZone: zone, precision: "day" }), null);
  const upcoming = catalystRowView({ date: "2026-10-31T00:00:00Z", title: "Starship flight", kind: "event" }, { now, timeZone: zone });
  assert.equal(upcoming.dateText, "Oct 31");
  assert.equal(upcoming.upcoming, true);
  assert.equal(upcoming.trailingText, "in 24d");
  assert.equal(upcoming.trailingLabel, "in 24 days");
  assert.doesNotMatch(upcoming.trailingText, /[▲▼▬]/);
  const past = catalystRowView({
    date: "2026-10-05", title: "A very long filing title that must stay intact for the tooltip even after the row ellipsizes it on screen", kind: "filing",
  }, {
    now, timeZone: zone, bars: [{ date: "2026-10-02", adj_close: 100 }, { date: "2026-10-05", adj_close: 110 }],
  });
  assert.equal(past.upcoming, false);
  assert.equal(past.trailingText, "▲ +10.0%");
  assert.equal(past.name.includes("must stay intact"), true);
  assert.equal(past.tooltip.startsWith(past.name), true);
  assert.equal(past.name.includes("\n"), false);
});

test("filing titles are readable and panels keep each ticker's own upcoming and past items apart", () => {
  assert.equal(filingDisplayTitle({ form: "8-K", title: "8-K", class: "earnings" }), "8-K · Item 2.02 Results of operations");
  assert.equal(filingDisplayTitle({ form: "8-K", title: "Results of operations", class: "earnings" }), "8-K · Item 2.02 Results of operations");
  assert.equal(filingDisplayTitle({ form: "10-Q", title: "10-Q", report_date: "2026-06-30" }), "10-Q · Quarterly report (period Jun 30)");
  assert.equal(filingDisplayTitle({ form: "10-K", title: "10-K", report_date: "2025-12-31" }), "10-K · Annual report (FY2025)");
  assert.equal(filingDisplayTitle({ form: "4", title: "OWNERSHIP DOCUMENT", class: "sell", insider_role: "director" }), "Form 4 · Insider sale · director");
  assert.equal(filingDisplayTitle({ form: "8-K", title: "8-K", items: ["2.02", "9.01", "5.02"] }), "8-K · Item 2.02 Results of operations +1");
  const index = edgarIndexUrl("https://www.sec.gov/Archives/edgar/data/1318605/000131860526000010/tsla-8k.htm");
  assert.equal(index, "https://www.sec.gov/Archives/edgar/data/1318605/000131860526000010/0001318605-26-000010-index.html");
  assert.match(edgarCompanyUrl(index), /CIK=0001318605/);
  const now = new Date("2026-10-07T19:15:00-07:00");
  const dashboard = {
    generated_at: "2026-10-07T19:15:00-07:00",
    rates: { fomc: { meeting_date: "2026-10-28" } },
    releases: { next: [{ series: "cpi", release_ts: "2026-10-15T12:35:00Z" }] },
    events: [
      { event_ts: "2026-10-31T00:00:00Z", type: "launch", title: "Starship flight", tickers: ["SPCX"], source: "launch-library" },
      { event_ts: "2026-10-11T23:00:00Z", type: "launch", title: "Falcon 9", tickers: ["SPCX"], source: "launch-library" },
      { event_ts: "2026-10-03T16:00:00Z", type: "other", title: "Columbus Day", tickers: [], source: "white-house" },
      { event_ts: "2026-10-02T15:00:00Z", type: "other", title: "Minutes of the Federal Open Market Committee", tickers: [], source: "federal-register" },
      { event_ts: "2026-09-01T15:00:00Z", type: "robotaxi", title: "Old permit", tickers: ["TSLA"], source: "federal-register" },
    ],
    tickers: {
      TSLA: { filings: [
        { form: "8-K", title: "8-K", filed_at: "2026-10-02", class: "earnings", url: "https://www.sec.gov/Archives/edgar/data/1318605/000131860526000010/tsla-8k.htm" },
        { form: "10-Q", title: "10-Q", filed_at: "2026-07-23", class: "periodic", url: "https://www.sec.gov/Archives/edgar/data/1318605/000131860526000020/tsla-10q.htm" },
      ] },
      SPCX: { filings: [] },
    },
  };
  const tsla = filingPanelModel({ dashboard, ticker: "TSLA", now, timeZone: "America/Los_Angeles" });
  assert.deepEqual(tsla.upcoming.map((row) => row.title), []);
  assert.equal(tsla.filings[0].title, "8-K · Item 2.02 Results of operations");
  assert.equal(tsla.filings.some((row) => row.title.includes("10-Q")), true);
  assert.equal(tsla.company.some((row) => /Starship|Falcon/.test(row.title)), false);
  assert.equal(tsla.company.some((row) => row.title === "Columbus Day"), false);
  assert.equal(filingsEmptyMessage("AAPL", []), "SEC filings are collected for TSLA and SPCX only.");
  const spcx = filingPanelModel({ dashboard, ticker: "SPCX", now, timeZone: "America/Los_Angeles" });
  assert.deepEqual(spcx.upcoming.map((row) => row.title), ["Falcon 9", "Starship flight"]);
  assert.equal(spcx.upcoming.some((row) => row.title === "Falcon 9" && row.date.startsWith("2026-10-11")), true);
  const calendar = calendarPanelModel({ dashboard, ticker: "TSLA", now, timeZone: "America/Los_Angeles" });
  assert.equal(calendar.upcoming.some((row) => row.title === "CPI (Sep)"), true);
  assert.equal(calendar.upcoming.some((row) => row.title === "FOMC decision"), true);
  assert.equal(calendar.upcoming.some((row) => row.title === "Starship flight"), false);
  assert.equal(calendar.past.some((row) => row.title.includes("8-K")), true);
  assert.equal(calendar.past.some((row) => row.title === "Columbus Day"), false);
  assert.equal(calendar.other.some((row) => row.title === "Columbus Day"), true);
  assert.equal(calendar.past.some((row) => row.title.startsWith("Minutes")), true);
  assert.equal(calendar.past.find((row) => row.title.startsWith("Minutes")).category, "rates");
  assert.match(calendarSummary(calendar.upcoming, { now, timeZone: "America/Los_Angeles" }), /^2 upcoming · next: .+ · Oct 15 · in \d+d$/);
  assert.equal(catalystCategory("other", "Minutes of the Federal Open Market Committee"), "rates");
  assert.equal(catalystCategory("other", "SAFE Vehicles Rule III"), "policy");
});

test("default catalyst rows stay byte-identical for filings, the calendar, and recent catalysts", () => {
  const now = new Date("2026-10-07T19:15:00-07:00");
  const zone = "America/Los_Angeles";
  assert.deepEqual(
    catalystRowView({ date: "2026-10-31T00:00:00Z", title: "Starship flight", kind: "event" }, { now, timeZone: zone }),
    {
      dateText: "Oct 31",
      dateTitle: "Sat, Oct 31, 2026",
      dateTime: "2026-10-31",
      name: "Starship flight",
      upcoming: true,
      tone: "neutral",
      trailingText: "in 24d",
      trailingLabel: "in 24 days",
      tooltip: "Starship flight · Sat, Oct 31, 2026 · in 24 days",
    },
  );
  assert.deepEqual(catalystRowView({
    date: "2026-10-05",
    title: "A very long filing title that must stay intact for the tooltip even after the row ellipsizes it on screen",
    kind: "filing",
  }, {
    now,
    timeZone: zone,
    bars: [{ date: "2026-10-02", adj_close: 100 }, { date: "2026-10-05", adj_close: 110 }],
  }), {
    dateText: "Oct 5",
    dateTitle: "Mon, Oct 5, 2026",
    dateTime: "2026-10-05",
    name: "A very long filing title that must stay intact for the tooltip even after the row ellipsizes it on screen",
    upcoming: false,
    tone: "positive",
    trailingText: "▲ +10.0%",
    trailingLabel: "1-day move ▲ +10.0%",
    tooltip: "A very long filing title that must stay intact for the tooltip even after the row ellipsizes it on screen · Mon, Oct 5, 2026",
  });
});

test("news ages, scores, and publisher suffixes follow the headline rules", () => {
  const now = new Date("2026-10-08T18:00:00Z");
  const zone = "UTC";
  assert.equal(ageLabel(new Date(now.getTime() - 30_000).toISOString(), { now, timeZone: zone }), "now");
  assert.equal(ageLabel(new Date(now.getTime() - 60_000).toISOString(), { now, timeZone: zone }), "1m");
  assert.equal(ageLabel(new Date(now.getTime() - 59 * 60_000).toISOString(), { now, timeZone: zone }), "59m");
  assert.equal(ageLabel(new Date(now.getTime() - 60 * 60_000).toISOString(), { now, timeZone: zone }), "1h");
  assert.equal(ageLabel(new Date(now.getTime() - 23 * 60 * 60_000).toISOString(), { now, timeZone: zone }), "23h");
  assert.equal(ageLabel(new Date(now.getTime() - 24 * 60 * 60_000).toISOString(), { now, timeZone: zone }), "Oct 7");
  assert.equal(ageLabel("2025-10-08T18:00:00Z", { now, timeZone: zone }), "Oct 8, 2025");
  assert.equal(stripPublisher("Tesla approval - BASENOR", "basenor"), "Tesla approval");
  assert.equal(stripPublisher("Tesla approval - Wire extra", "Wire"), "Tesla approval - Wire extra");
  assert.equal(formatSentimentScore(0.22), "+0.22");
  assert.equal(formatSentimentScore(-0.31), "-0.31");
  assert.equal(formatSentimentScore(0), "0.00");
  assert.equal(formatSentimentScore(null), "n/a");
  assert.equal(sentimentTone(0.15).label, "Neutral");
  assert.equal(sentimentTone(0.16).dot, "green");
  assert.equal(sentimentTone(-0.15).label, "Neutral");
  assert.equal(sentimentTone(-0.16).dot, "red");
  assert.equal(sentimentTone(0).dot, "muted");
  assert.equal(newsClockTitle("2026-10-08T02:06:20Z", {
    timeZone: "America/Los_Angeles", now: new Date("2026-10-08T02:15:00Z"),
  }), "Wed Oct 7 · 19:06 PDT");
});

test("news moves use the after-close session and stay pending until that session closes", () => {
  const bars = [
    { date: "2026-10-02", adj_close: 100 },
    { date: "2026-10-05", adj_close: 90 },
    { date: "2026-10-06", adj_close: 99 },
    { date: "2026-10-07", adj_close: 99 },
  ];
  const headline = {
    title: "Tesla approval - Wire",
    publisher: "Wire",
    url: "https://example.com/story",
    date_precision: "minute",
    score: 0.22,
    score_source: "provider",
    snippet: "Approval coverage",
  };
  const pending = newsRowView({
    ...headline, published_at: "2026-10-07T20:30:00Z",
  }, { bars, timeZone: "America/Los_Angeles", now: new Date("2026-10-07T22:15:00Z") });
  assert.equal(pending.name, "Tesla approval");
  assert.equal(pending.publisher, "Wire");
  assert.equal(pending.scoreText, "+0.22");
  assert.equal(pending.sentimentLabel, "Bullish");
  assert.equal(pending.dotToken, "green");
  assert.equal(pending.trailingText, "—");
  assert.equal(pending.trailingLabel, "1-day move pending, next session not closed");
  assert.equal(pending.tooltip.includes("Tesla approval"), true);
  assert.equal(pending.tooltip.includes("sentiment +0.22 (provider)"), true);
  assert.equal(pending.tooltip.split("Wire").length - 1, 1);
  assert.doesNotMatch(pending.dateText, /T\d{2}:/);
  assert.doesNotMatch(pending.tooltip, /T\d{2}:/);
  const closed = newsRowView({
    ...headline, published_at: "2026-10-05T20:05:00Z", score: -0.2, score_source: "lexicon",
  }, { bars, timeZone: "America/New_York", now: new Date("2026-10-07T22:00:00Z") });
  assert.equal(closed.trailingText, "▲ +10.0%");
  assert.match(closed.tooltip, /1-day move ▲ \+10\.0% \(Oct 6 session\)/);
  const weekend = newsRowView({
    ...headline, published_at: "2026-10-03T16:00:00Z", score: 0,
  }, { bars, timeZone: "America/New_York", now: new Date("2026-10-04T18:00:00Z") });
  assert.equal(weekend.trailingLabel, "1-day move pending, next session not closed");
  assert.equal(weekend.scoreText, "0.00");
  assert.equal(weekend.scoreTone, "neutral");
  const landed = newsRowView({
    ...headline, published_at: "2026-10-03T16:00:00Z", score: null,
  }, { bars, timeZone: "America/New_York", now: new Date("2026-10-07T22:00:00Z") });
  assert.equal(landed.trailingText, "▼ -10.0%");
  assert.equal(landed.scoreText, "n/a");
  const openSession = newsRowView({
    ...headline, published_at: "2026-10-06T18:00:00Z",
  }, { bars, timeZone: "America/New_York", now: new Date("2026-10-06T19:00:00Z") });
  assert.equal(openSession.trailingLabel, "1-day move pending, next session not closed");
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

test("catalyst sensitivity averages absolute one-day moves and marks the late trend", () => {
  const bars = [
    { date: "2026-10-01", close: 100 },
    { date: "2026-10-02", close: 101 },
    { date: "2026-10-03", close: 102.01 },
    { date: "2026-10-06", close: 122.412 },
  ];
  const events = [
    { date: "2026-10-02", category: "filings", type: "filing", title: "A" },
    { date: "2026-10-03", category: "filings", type: "filing", title: "B" },
    { date: "2026-10-06", category: "filings", type: "filing", title: "C" },
  ];
  const rows = catalystSensitivityRows(events, bars);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, "filings");
  assert.equal(rows[0].label, "Filings");
  assert.equal(rows[0].n, 3);
  assert.equal(rows[0].trend, "intensifying");
  assert.equal(rows[0].trendLabel, "▲ intensifying");
  assert.equal(rows[0].width, 1);
  assert.ok(rows[0].average > 0.05);
  const quiet = catalystSensitivityRows([
    { date: "2026-10-02", category: "space", type: "launch", title: "One" },
  ], bars);
  assert.equal(quiet[0].trendLabel, "one event");
  assert.deepEqual(catalystSensitivityRows([], bars), []);
});
