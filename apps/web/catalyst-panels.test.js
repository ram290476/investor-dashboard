import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { calendarPanelModel, edgarCompanyUrl, edgarIndexUrl, filingPanelModel } from "./roadmap.js";

const app = readFileSync(new URL("./app.js", import.meta.url), "utf8");

function slice(start, end) {
  return app.slice(app.indexOf(start), app.indexOf(end));
}

test("filings and the calendar use the shared row, and recent catalysts stays a plain row", () => {
  const row = slice("function renderCatalystRow", "function renderCatalystList");
  assert.match(row, /className = "catalyst-recent-row"/);
  assert.match(row, /catalyst-dot/);
  assert.match(row, /visually-hidden/);
  assert.match(row, /calendar-\$\{event\.id\}/);
  assert.match(row, /Open SEC filing/);
  assert.match(row, /link && event\.kind === "filing"/);
  const recent = slice("function renderRecentCatalysts", "function renderDrivers");
  assert.match(recent, /renderCatalystList\(events, tickerData\)/);
  assert.doesNotMatch(recent, /link:\s*true/);
  const filings = slice("function renderFilings", "function renderCompanyPanel");
  const calendar = slice("function renderCatalystCalendar", "function newsDrawerSummary");
  assert.match(filings, /id: "filings-events"/);
  assert.match(filings, /renderCatalystSection/);
  assert.match(filings, /link: true/);
  assert.match(filings, /All filings on SEC EDGAR/);
  assert.doesNotMatch(filings, /calendarId: true/);
  assert.match(calendar, /calendarId: true, link: true/);
  assert.match(calendar, /"Upcoming"/);
  assert.match(calendar, /"Past 14 days"/);
  assert.match(calendar, /Show \$\{model\.other\.length\} other events/);
  for (const source of [filings, calendar, row]) {
    assert.doesNotMatch(source, /null ·/);
    assert.doesNotMatch(source, /catalyst-row-button/);
  }
});

test("each ticker keeps its own filings, and the SEC index is built from the accession", () => {
  const now = new Date("2026-10-10T15:00:00Z");
  const zone = "America/Los_Angeles";
  const dashboard = {
    generated_at: "2026-10-10T15:00:00Z",
    rates: { fomc: { meeting_date: "2026-10-28" } },
    releases: { next: [{ series: "cpi", release_ts: "2026-10-15T12:30:00Z" }] },
    events: [
      { event_ts: "2026-10-20T23:00:00Z", type: "launch", title: "Falcon 9", tickers: ["SPCX"], source: "launch-library" },
      { event_ts: "2026-10-03T16:00:00Z", type: "other", title: "Columbus Day", tickers: [], source: "white-house" },
    ],
    tickers: {
      TSLA: { filings: [{ form: "8-K", title: "8-K", filed_at: "2026-10-02", class: "earnings", url: "https://www.sec.gov/Archives/edgar/data/1318605/000131860526000010/tsla-8k.htm" }] },
      SPCX: { filings: [] },
    },
  };
  const tsla = filingPanelModel({ dashboard, ticker: "TSLA", now, timeZone: zone });
  assert.equal(tsla.upcoming.some((row) => row.title === "Falcon 9"), false);
  assert.equal(tsla.filings.some((row) => /Item 2\.02/.test(row.title)), true);
  assert.equal(tsla.company.some((row) => row.title === "Columbus Day"), false);
  const spcx = filingPanelModel({ dashboard, ticker: "SPCX", now, timeZone: zone });
  assert.deepEqual(spcx.upcoming.map((row) => row.title), ["Falcon 9"]);
  const calendar = calendarPanelModel({ dashboard, ticker: "TSLA", now, timeZone: zone });
  assert.equal(calendar.upcoming.some((row) => row.title === "CPI (Sep)"), true);
  assert.equal(calendar.upcoming.some((row) => row.title === "Falcon 9"), false);
  assert.equal(calendar.past.some((row) => row.title === "Columbus Day"), false);
  assert.equal(calendar.other.some((row) => row.title === "Columbus Day"), true);
  const index = edgarIndexUrl(tsla.filings[0].url);
  assert.equal(index, "https://www.sec.gov/Archives/edgar/data/1318605/000131860526000010/0001318605-26-000010-index.html");
  assert.match(edgarCompanyUrl(index), /CIK=0001318605/);
  assert.equal(String(calendar.upcoming[0].title).includes("null"), false);
});
