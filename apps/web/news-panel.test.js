import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { newsEmptyMessage, newsGroupName, newsGroups, newsHeader, newsSummary } from "./roadmap.js";

const app = readFileSync(new URL("./app.js", import.meta.url), "utf8");
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

function slice(start, end) {
  return app.slice(app.indexOf(start), app.indexOf(end));
}

test("news rows reuse the catalyst row with a sentiment dot, one publisher, and no raw timestamps", () => {
  const row = slice("function renderCatalystRow", "function renderCatalystList");
  assert.match(row, /className = "catalyst-recent-row"/);
  assert.match(row, /catalyst-recent-row--news/);
  assert.match(row, /newsRowView\(event/);
  assert.match(row, /catalystRowView\(event/);
  assert.match(row, /var\(--\$\{view\.dotToken\}\)/);
  assert.match(row, /overlayColor\(category\.slot/);
  assert.match(row, /visually-hidden/);
  assert.match(row, /catalyst-recent-publisher/);
  assert.match(row, /catalyst-recent-score/);
  assert.match(row, /target = "_blank"/);
  assert.equal(row.split("catalyst-recent-publisher").length - 1, 1);
  const news = slice("function renderNewsHeadlineGroups", "function filingsSummary");
  assert.match(news, /variant: "news"/);
  assert.match(news, /\$\{group\.name\} · \$\{group\.rows\.length\}/);
  assert.match(news, /Show \$\{hidden\} more/);
  assert.match(news, /mobilePanel: "more"/);
  assert.match(news, /last 7 days/);
  assert.match(news, /History unavailable/);
  assert.match(news, /Overlay sentiment on chart/);
  assert.doesNotMatch(news, /published_at/);
  assert.doesNotMatch(news, /news-list/);
  assert.doesNotMatch(news, /48 hours/);
  const recent = slice("function renderRecentCatalysts", "function renderDrivers");
  const filings = slice("function renderFilings", "function renderCompanyPanel");
  const calendar = slice("function renderCatalystCalendar", "function newsDrawerSummary");
  for (const source of [recent, filings, calendar]) assert.doesNotMatch(source, /variant:\s*"news"/);
  assert.match(app, /function renderResearchPage[\s\S]*renderNews\(tickerData\)/);
  assert.match(app, /const news = renderNews\(tickerData\), filings = renderFilings\(tickerData\)/);
  const block = styles.slice(styles.indexOf("/* recent catalyst rows (#91)"), styles.indexOf("/* end recent catalyst rows (#91) */"));
  assert.match(block, /\.catalyst-recent-row--news\s*\{[^}]*grid-template-columns:\s*46px minmax\(0,\s*1fr\) 40px 62px/);
  assert.match(block, /max-width:\s*480px[\s\S]*40px minmax\(0,\s*1fr\) 38px 58px/);
  assert.match(block, /\.catalyst-recent-publisher\s*\{[^}]*max-width:\s*84px/);
  assert.match(block, /max-width:\s*480px[\s\S]*\.catalyst-recent-publisher\s*\{[^}]*max-width:\s*58px/);
  assert.match(block, /\.catalyst-recent-row[\s\S]*height:\s*28px/);
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}/);
});

test("grouping follows the profile calendar day and other tickers stay out of the list", () => {
  // 20:30 PT Oct 7 is 23:30 ET Oct 7. An hour later it is still Oct 7 in PT and already Oct 8 in ET.
  const published = "2026-10-08T03:30:00Z";
  const now = new Date("2026-10-08T05:00:00Z");
  assert.equal(newsGroupName(published, { now, timeZone: "America/Los_Angeles" }), "Today");
  assert.equal(newsGroupName(published, { now, timeZone: "America/New_York" }), "This week");
  const late = "2026-10-08T06:30:00Z";
  assert.equal(newsGroupName(late, { now: new Date("2026-10-08T06:45:00Z"), timeZone: "America/Los_Angeles" }), "Today");
  const headlines = [
    { title: "Tesla now", publisher: "Wire", published_at: late, tickers: ["TSLA"], score: 0.2 },
    { title: "Apple event", publisher: "Wire", published_at: late, tickers: ["AAPL"], score: 0.4 },
    { title: "Uber stake", publisher: "Wire", published_at: "2026-10-02T18:00:00Z", tickers: ["TSLA"], relevance: "sector" },
    { title: "Older Tesla", publisher: "Wire", published_at: "2026-10-01T18:00:00Z", tickers: ["TSLA"], score: -0.4 },
  ];
  const groups = newsGroups(headlines, { ticker: "TSLA", now, timeZone: "America/Los_Angeles" });
  const titles = groups.flatMap((group) => group.rows.map((row) => row.title));
  assert.deepEqual(titles, ["Tesla now", "Older Tesla"]);
  assert.equal(titles.includes("Apple event"), false);
  assert.equal(titles.includes("Uber stake"), false);
  assert.equal(groups[0].name, "Today");
  assert.equal(newsGroups(headlines, { ticker: "AAPL", now, timeZone: "America/Los_Angeles" })
    .flatMap((group) => group.rows.map((row) => row.title)).includes("Tesla now"), false);
});

test("coverage empty states name who is collected, and the drawer summary stays short", () => {
  assert.equal(newsEmptyMessage("TSLA", []), "No TSLA headlines in the last 7 days.");
  assert.equal(newsEmptyMessage("SPCX", []), "No SPCX headlines in the last 7 days.");
  assert.equal(
    newsEmptyMessage("AAPL", []),
    "Company news is collected for TSLA and SPCX. AAPL gets Alpha Vantage headlines on rotation, every few days.",
  );
  assert.equal(newsEmptyMessage("AAPL", [{ title: "On rotation" }]), "");
  const news = {
    sentiment_7d: 0.06,
    sentiment_7d_prior: 0.03,
    count_7d: 14,
    sentiment_history: [{ date: "2026-10-07", value: 0.06 }],
    headlines: [{ title: "Tesla", tickers: ["TSLA"], published_at: "2026-10-07T20:00:00Z" }],
  };
  assert.equal(newsSummary(news), "+0.06 · 14 headlines");
  assert.equal(newsSummary(null, { state: "loading" }), "Loading…");
  assert.equal(newsSummary(null, { state: "error" }), "Unavailable");
  const header = newsHeader(news, { timeZone: "America/Los_Angeles", now: new Date("2026-10-08T02:00:00Z") });
  assert.equal(header.scoreText, "+0.06");
  assert.equal(header.scoreTone, "neutral");
  assert.equal(header.count, 14);
  assert.equal(header.change.text, "▲ +0.03");
  assert.equal(header.historyEmpty, false);
  assert.equal(header.sourceNote, "Through Oct 7 · provider scores with headline-lexicon fallback");
  assert.equal(newsHeader({ sentiment_history: [] }).historyEmpty, true);
  assert.equal(newsSummary({ sentiment_7d: 0.2, count_7d: 1 }), "+0.20 · 1 headline");
});
