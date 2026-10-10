import { chartValue, isNumericValue, validBars } from "./chart-period.js";
import { driverRows, number } from "./signals.js";

export const CATALYST_CATEGORIES = [
  { id: "rates", label: "Rates", slot: 0 },
  { id: "inflation", label: "Inflation", slot: 1 },
  { id: "policy", label: "Policy & geopolitics", slot: 2 },
  { id: "robotaxi", label: "Robotaxi", slot: 3 },
  { id: "filings", label: "Filings", slot: 4 },
  { id: "space", label: "Space operations", slot: 5 },
  { id: "other", label: "Other events", slot: 6 },
];

export function catalystCategory(type, title = "") {
  const key = `${String(type || "")} ${String(title || "")}`.toLowerCase();
  if (/cpi|pce|inflation|consumer price/.test(key)) return "inflation";
  if (/fomc|federal open market|(?:^|[\s_-])rates?(?:$|[\s_-])|treasury|fed funds/.test(key)) return "rates";
  if (/robotaxi|autonomous|av_permit|fsd/.test(key)) return "robotaxi";
  if (/filing|earnings|^form_/.test(key)) return "filings";
  if (/launch|space|starlink|starship/.test(key)) return "space";
  if (/tariff|sanction|policy|geopolitic|trade|fuel economy|nhtsa|safe vehicles/.test(key)) return "policy";
  return "other";
}

export function catalystRows(dashboard, ticker) {
  const data = dashboard?.tickers?.[ticker];
  const records = [
    ...(dashboard?.events || []).filter(row => !row.tickers?.length || row.tickers.includes(ticker))
      .map(row => ({
        date: row.event_ts,
        type: row.type,
        title: row.title,
        url: row.source_url,
        source: row.source || null,
        observed_at: row.observed_at || null,
        tickers: row.tickers || [],
        date_precision: row.date_precision || null,
        kind: "event",
      })),
    ...(data?.filings || []).map(row => ({
      date: row.filed_at,
      type: "filing",
      title: filingDisplayTitle(row),
      url: row.url,
      source: "SEC filing",
      tickers: [ticker],
      date_precision: "day",
      kind: "filing",
      form: row.form,
    })),
    ...(dashboard?.releases?.next || []).map(row => ({
      date: row.release_ts,
      type: row.series,
      title: releaseDisplayTitle(row),
      source: row.source || "Published release calendar",
      tickers: [],
      date_precision: null,
      kind: "release",
    })),
    ...(data?.release_links?.latest || []).map(row => ({
      date: row.release_date,
      type: row.series_id,
      title: releaseDisplayTitle({ series: row.series_id, period: row.period, release_ts: row.release_date }),
      source: "Stored macro release",
      tickers: [],
      date_precision: "day",
      kind: "release",
    })),
  ];
  const unique = new Map();
  records.forEach(row => {
    if (!row.date || !Number.isFinite(Date.parse(row.date))) return;
    const key = `${row.type}|${row.date}|${row.title}|${row.url || ""}`;
    if (!unique.has(key)) unique.set(key, { ...row, id: encodeURIComponent(key), category: catalystCategory(row.type, row.title) });
  });
  return [...unique.values()].sort((a, b) => Date.parse(a.date) - Date.parse(b.date) || a.id.localeCompare(b.id));
}

export function markerIndex(event, bars) {
  const day = String(event.date).slice(0, 10);
  const days = bars.map(bar => String(bar.ts || bar.date || "").slice(0, 10));
  if (!days.length || day < days[0] || day > days.at(-1)) return -1;
  return days.findIndex(date => date >= day);
}

// 1-session percent change around the aligned session.
// A weekend or holiday lands on the next stored session. A timestamp at or after
// 16:00 America/New_York uses the following stored session (after-close prints).
// Date-only values stay on the session markerIndex chooses.
export function catalystMove(event, bars) {
  const index = moveSessionIndex(event, bars || []);
  if (index < 1) return null;
  const previous = chartValue(bars[index - 1]);
  const close = chartValue(bars[index]);
  if (previous == null || close == null || previous === 0) return null;
  return close / previous - 1;
}

const MARKET_CLOSE_MINUTES = 16 * 60;
const CLOCK_TIME = /T\d{2}:\d{2}/;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const MIDNIGHT_UTC = /^\d{4}-\d{2}-\d{2}T00:00:00(?:\.0+)?(?:Z|\+00:00)$/;

function easternMinutes(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const hour = Number(parts.find((part) => part.type === "hour")?.value);
  const minute = Number(parts.find((part) => part.type === "minute")?.value);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
  return (hour % 24) * 60 + minute;
}

export function moveSessionIndex(event, bars) {
  const list = bars || [];
  const stamp = String(event?.date || "");
  if (CLOCK_TIME.test(stamp) && resolvePrecision(stamp, event?.date_precision) === "minute") {
    const minutes = easternMinutes(stamp);
    if (minutes != null && minutes >= MARKET_CLOSE_MINUTES) {
      const day = stamp.slice(0, 10);
      return list.map((bar) => String(bar.ts || bar.date || "").slice(0, 10)).findIndex((date) => date > day);
    }
  }
  return markerIndex(event, list);
}

export function resolvePrecision(value, explicit) {
  const text = String(value || "").trim();
  if (DATE_ONLY.test(text)) return "day";
  if (explicit === "day") return "day";
  if (explicit === "minute") return "minute";
  if (MIDNIGHT_UTC.test(text)) return "day";
  return "minute";
}

export function calendarDay(value, { timeZone = "UTC", precision } = {}) {
  const text = String(value instanceof Date ? value.toISOString() : value || "").trim();
  if (!text) return "";
  const kind = resolvePrecision(text, precision);
  if (kind === "day") return (text.match(/^(\d{4}-\d{2}-\d{2})/) || [])[1] || "";
  const date = new Date(text);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timeZone || "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function dayNumber(day) {
  const [year, month, date] = String(day || "").split("-").map(Number);
  if (!year || !month || !date) return null;
  return Math.round(Date.UTC(year, month - 1, date) / 86400000);
}

export function calendarDayDiff(later, earlier) {
  const end = dayNumber(later);
  const start = dayNumber(earlier);
  if (end == null || start == null) return null;
  return end - start;
}

export function catalystDateLabel(value, options = {}) {
  const day = calendarDay(value, options);
  if (!DATE_ONLY.test(day)) return "—";
  const nowDay = options.now ? calendarDay(options.now, { timeZone: options.timeZone, precision: "minute" }) : day;
  const formatted = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: nowDay.slice(0, 4) === day.slice(0, 4) ? undefined : "numeric",
    timeZone: "UTC",
  }).format(new Date(`${day}T00:00:00Z`));
  return formatted;
}

export function fullDateTitle(value, options = {}) {
  const day = calendarDay(value, options);
  if (!DATE_ONLY.test(day)) return "";
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
  }).format(new Date(`${day}T00:00:00Z`));
}

export function clockLabel(value, timeZone, precision) {
  if (resolvePrecision(value, precision) !== "minute") return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: timeZone || "UTC", timeZoneName: "short",
  }).format(date);
}

export function isUpcomingItem(event, now, timeZone = "UTC") {
  const precision = resolvePrecision(event?.date, event?.date_precision);
  const today = calendarDay(now, { timeZone, precision: "minute" });
  const day = calendarDay(event?.date, { timeZone, precision });
  if (!today || !day) return false;
  if (precision === "day") return event?.kind === "filing" ? day > today : day >= today;
  const instant = new Date(event.date);
  const current = now instanceof Date ? now : new Date(now);
  return !Number.isNaN(instant.valueOf()) && instant.getTime() > current.getTime();
}

export function countdownLabel(value, { now, timeZone = "UTC", precision } = {}) {
  const current = now instanceof Date ? now : new Date(now || Date.now());
  if (Number.isNaN(current.valueOf())) return null;
  const kind = resolvePrecision(value, precision);
  const today = calendarDay(current, { timeZone, precision: "minute" });
  const day = calendarDay(value, { timeZone, precision: kind });
  if (!today || !day) return null;
  if (kind === "minute") {
    const instant = new Date(value);
    const hours = (instant.getTime() - current.getTime()) / 3600000;
    if (hours < 0) return null;
    if (hours < 24) {
      if (hours < 1) {
        const minutes = Math.max(1, Math.round(hours * 60));
        return { text: `in ${minutes}m`, label: `in ${minutes} ${minutes === 1 ? "minute" : "minutes"}` };
      }
      const rounded = Math.max(1, Math.round(hours));
      return { text: `in ${rounded}h`, label: `in ${rounded} ${rounded === 1 ? "hour" : "hours"}` };
    }
  }
  const days = calendarDayDiff(day, today);
  if (days == null || days < 0) return null;
  if (days === 0) return { text: "today", label: "today" };
  return { text: `in ${days}d`, label: `in ${days} ${days === 1 ? "day" : "days"}` };
}

const MOVE_FLAT = 0.0005;

function movePercent(value) {
  const number = Number(value) * 100;
  return `${number > 0 ? "+" : ""}${number.toFixed(1)}%`;
}

export function catalystMoveLabel(move) {
  if (!isNumericValue(move)) return { text: "—", tone: "neutral", label: "1-day move unavailable" };
  if (Math.abs(Number(move)) < MOVE_FLAT) {
    return { text: "▬ 0.0%", tone: "neutral", label: "1-day move flat, under 0.05 percent" };
  }
  const up = Number(move) > 0;
  const text = `${up ? "▲ " : "▼ "}${movePercent(move)}`;
  return { text, tone: up ? "positive" : "negative", label: `1-day move ${text.trim()}` };
}

export function singleLine(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

const AGE_MINUTE = 60 * 1000;
const AGE_HOUR = 60 * AGE_MINUTE;
const AGE_DAY = 24 * AGE_HOUR;

// #113 D2: now / Nm / Nh inside a day, then the calendar day in the profile zone.
export function ageLabel(value, { now, timeZone = "UTC" } = {}) {
  const current = now instanceof Date ? now : new Date(now || Date.now());
  const instant = new Date(value);
  if (Number.isNaN(current.valueOf()) || Number.isNaN(instant.valueOf())) {
    return catalystDateLabel(value, { now: current, timeZone });
  }
  const delta = current.getTime() - instant.getTime();
  if (delta < 0) return catalystDateLabel(value, { now: current, timeZone, precision: "minute" });
  if (delta < AGE_MINUTE) return "now";
  if (delta < 60 * AGE_MINUTE) return `${Math.floor(delta / AGE_MINUTE)}m`;
  if (delta < AGE_DAY) return `${Math.floor(delta / AGE_HOUR)}h`;
  return catalystDateLabel(value, { now: current, timeZone, precision: resolvePrecision(value) });
}

export const BULLISH_ABOVE = 0.15;
export const BEARISH_BELOW = -0.15;

export function sentimentTone(score) {
  if (!isNumericValue(score)) return { tone: "neutral", label: "Neutral", dot: "muted" };
  const value = Number(score);
  if (value > BULLISH_ABOVE) return { tone: "positive", label: "Bullish", dot: "green" };
  if (value < BEARISH_BELOW) return { tone: "negative", label: "Bearish", dot: "red" };
  return { tone: "neutral", label: "Neutral", dot: "muted" };
}

export function formatSentimentScore(score) {
  if (!isNumericValue(score)) return "n/a";
  const rounded = Math.round(Number(score) * 100) / 100;
  if (rounded === 0) return "0.00";
  const text = rounded.toFixed(2);
  return rounded > 0 ? `+${text}` : text;
}

export function sentimentChangeLabel(change) {
  if (!isNumericValue(change)) return null;
  const value = Number(change);
  if (Math.abs(value) < 0.005) return { text: "▬ 0.00", tone: "neutral" };
  const up = value > 0;
  return { text: `${up ? "▲ " : "▼ "}${value > 0 ? "+" : ""}${value.toFixed(2)}`, tone: up ? "positive" : "negative" };
}

export function stripPublisher(title, publisher) {
  const text = singleLine(title);
  const source = singleLine(publisher);
  const suffix = ` - ${source}`;
  if (source && text.toLowerCase().endsWith(suffix.toLowerCase())) return text.slice(0, -suffix.length).trim();
  return text;
}

function shiftDay(day, count) {
  const [year, month, date] = String(day || "").split("-").map(Number);
  if (!year || !month || !date) return "";
  return new Date(Date.UTC(year, month - 1, date + count)).toISOString().slice(0, 10);
}

function weekdayOnOrAfter(day) {
  let cursor = day;
  for (let guard = 0; guard < 7 && cursor; guard += 1) {
    const [year, month, date] = cursor.split("-").map(Number);
    const weekday = new Date(Date.UTC(year, month - 1, date)).getUTCDay();
    if (weekday !== 0 && weekday !== 6) return cursor;
    cursor = shiftDay(cursor, 1);
  }
  return cursor;
}

function targetSessionDay(event) {
  const stamp = String(event?.date || "");
  const precision = resolvePrecision(stamp, event?.date_precision);
  if (precision === "minute" && CLOCK_TIME.test(stamp)) {
    const minutes = easternMinutes(stamp);
    const etDay = calendarDay(stamp, { timeZone: "America/New_York", precision: "minute" });
    if (!DATE_ONLY.test(etDay)) return "";
    const start = minutes != null && minutes >= MARKET_CLOSE_MINUTES ? shiftDay(etDay, 1) : etDay;
    return weekdayOnOrAfter(start);
  }
  const day = (stamp.match(/^(\d{4}-\d{2}-\d{2})/) || [])[1] || "";
  return day ? weekdayOnOrAfter(day) : "";
}

function sessionHasClosed(day, now) {
  if (!DATE_ONLY.test(String(day || "")) || !(now instanceof Date) || Number.isNaN(now.valueOf())) return false;
  const today = calendarDay(now, { timeZone: "America/New_York", precision: "minute" });
  if (!today) return false;
  if (day < today) return true;
  if (day > today) return false;
  const minutes = easternMinutes(now.toISOString());
  return minutes != null && minutes >= MARKET_CLOSE_MINUTES;
}

// The session a headline aligns to, using the same after-close rule as catalystMove.
// An unclosed session is pending instead of an intraday print.
export function publicationMove(event, bars, now) {
  const list = bars || [];
  const index = moveSessionIndex(event, list);
  const storedDay = index >= 0 ? String(list[index]?.ts || list[index]?.date || "").slice(0, 10) : "";
  const sessionDay = DATE_ONLY.test(storedDay) ? storedDay : targetSessionDay(event);
  const current = now instanceof Date ? now : new Date(now || Date.now());
  if (sessionDay && !sessionHasClosed(sessionDay, current)) {
    return {
      text: "—",
      tone: "neutral",
      label: "1-day move pending, next session not closed",
      pending: true,
      session: sessionDay,
    };
  }
  const move = index >= 1 ? catalystMove(event, list) : null;
  return { ...catalystMoveLabel(move), pending: false, session: DATE_ONLY.test(storedDay) ? storedDay : "" };
}

export function newsClockTitle(value, { timeZone = "UTC", now } = {}) {
  const day = calendarDay(value, { timeZone, precision: "minute" });
  if (!DATE_ONLY.test(day)) return "";
  const nowDay = now ? calendarDay(now, { timeZone, precision: "minute" }) : day;
  const formatted = new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: nowDay.slice(0, 4) === day.slice(0, 4) ? undefined : "numeric",
    timeZone: "UTC",
  }).format(new Date(`${day}T00:00:00Z`)).replace(/^(\w+),/, "$1");
  const clock = clockLabel(value, timeZone, "minute");
  return clock ? `${formatted} · ${clock}` : formatted;
}

export function catalystRowView(event, { bars = [], timeZone = "UTC", now, dateMode = "date" } = {}) {
  const precision = resolvePrecision(event?.date, event?.date_precision);
  const dateOptions = { timeZone, precision, now };
  const dateText = dateMode === "age"
    ? ageLabel(event?.date, { now, timeZone })
    : catalystDateLabel(event?.date, dateOptions);
  const dateTitle = fullDateTitle(event?.date, dateOptions);
  const upcoming = isUpcomingItem(event, now, timeZone);
  const clock = clockLabel(event?.date, timeZone, precision);
  const name = singleLine(event?.title || "Untitled");
  const trailing = upcoming
    ? { ...(countdownLabel(event?.date, { now, timeZone, precision }) || { text: "—", label: "Upcoming" }), tone: "neutral" }
    : catalystMoveLabel(catalystMove(event, bars));
  const tooltip = [name, dateTitle, clock, event?.source || "", event?.note || "", upcoming ? trailing.label : ""].filter(Boolean).join(" · ");
  return {
    dateText,
    dateTitle,
    dateTime: precision === "day" ? String(event?.date || "").slice(0, 10) : String(event?.date || ""),
    name,
    upcoming,
    tone: trailing.tone,
    trailingText: trailing.text,
    trailingLabel: trailing.label,
    tooltip,
  };
}

export function newsRowView(headline, { bars = [], timeZone = "UTC", now } = {}) {
  const publisher = singleLine(headline?.publisher || "");
  const event = {
    date: headline?.published_at,
    date_precision: headline?.date_precision || "minute",
    title: stripPublisher(headline?.title, publisher),
    kind: "news",
    url: headline?.url || "",
    source: publisher,
  };
  const base = catalystRowView(event, { bars, timeZone, now, dateMode: "age" });
  const sentiment = sentimentTone(headline?.score);
  const score = formatSentimentScore(headline?.score);
  const move = publicationMove(event, bars, now);
  const timeTitle = newsClockTitle(event.date, { timeZone, now });
  const moveBit = !move.pending && move.session && move.text !== "—"
    ? `1-day move ${singleLine(move.text)} (${catalystDateLabel(move.session)} session)`
    : move.label;
  const knownSource = headline?.score_source === "provider" || headline?.score_source === "lexicon"
    ? ` (${headline.score_source})` : "";
  const sentimentBit = score === "n/a" ? "sentiment n/a" : `sentiment ${score}${knownSource}`;
  const snippet = singleLine(headline?.snippet || "");
  const tooltip = [base.name, publisher, timeTitle, sentimentBit, moveBit, snippet].filter(Boolean).join(" · ");
  return {
    ...base,
    timeTitle,
    publisher,
    sentimentLabel: sentiment.label,
    dotToken: sentiment.dot,
    scoreText: score,
    scoreTone: sentiment.tone,
    scoreLabel: score === "n/a" ? "Sentiment score unavailable" : `Sentiment ${score}`,
    tone: move.tone,
    trailingText: move.text,
    trailingLabel: move.label,
    tooltip,
  };
}

export const COMPANY_NEWS_TICKERS = ["TSLA", "SPCX"];
export const NEWS_ROW_LIMIT = 5;

export function visibleHeadlines(headlines, ticker) {
  return (Array.isArray(headlines) ? headlines : []).filter((item) => {
    if (!item || item.relevance === "sector") return false;
    const symbols = Array.isArray(item.tickers) ? item.tickers.map((symbol) => String(symbol).toUpperCase()) : [];
    if (!symbols.length || !ticker) return true;
    return symbols.includes(String(ticker).toUpperCase());
  });
}

export function newsGroupName(value, { now, timeZone = "UTC" } = {}) {
  const current = now instanceof Date ? now : new Date(now || Date.now());
  const today = calendarDay(current, { timeZone, precision: "minute" });
  const day = calendarDay(value, { timeZone, precision: resolvePrecision(value, "minute") });
  const diff = calendarDayDiff(today, day);
  if (diff == null || diff <= 0) return "Today";
  if (diff <= 6) return "This week";
  return "Earlier";
}

export function newsGroups(headlines, { ticker, now, timeZone = "UTC" } = {}) {
  const buckets = { Today: [], "This week": [], Earlier: [] };
  visibleHeadlines(headlines, ticker)
    .slice()
    .sort((left, right) => Date.parse(right?.published_at || "") - Date.parse(left?.published_at || "")
      || String(left?.title || "").localeCompare(String(right?.title || "")))
    .forEach((row) => {
      buckets[newsGroupName(row.published_at, { now, timeZone })].push(row);
    });
  return ["Today", "This week", "Earlier"]
    .filter((name) => buckets[name].length)
    .map((name) => ({ name, rows: buckets[name] }));
}

export function newsCount(news) {
  const count = Number(news?.count_7d);
  if (Number.isFinite(count)) return count;
  return visibleHeadlines(news?.headlines, "").length;
}

export function newsSummary(news, { state } = {}) {
  if (state === "loading") return "Loading…";
  const count = newsCount(news);
  const hasScore = isNumericValue(news?.sentiment_7d);
  if (state === "error" && !hasScore && !count) return "Unavailable";
  if (!news) return "Headlines and 7-day sentiment";
  const headlines = `${count} headline${count === 1 ? "" : "s"}`;
  return hasScore ? `${formatSentimentScore(news.sentiment_7d)} · ${headlines}` : headlines;
}

export function newsSourceNote(news, { timeZone = "UTC", now } = {}) {
  const history = Array.isArray(news?.sentiment_history) ? news.sentiment_history : [];
  const day = String(history.at(-1)?.date || news?.as_of || "").slice(0, 10);
  const when = DATE_ONLY.test(day) ? catalystDateLabel(day, { timeZone, precision: "day", now }) : "";
  return when && when !== "—"
    ? `Through ${when} · provider scores with headline-lexicon fallback`
    : "provider scores with headline-lexicon fallback";
}

export function newsHeader(news, { timeZone = "UTC", now } = {}) {
  const scoreValue = isNumericValue(news?.sentiment_7d) ? Number(news.sentiment_7d) : null;
  const prior = isNumericValue(news?.sentiment_7d_prior) ? Number(news.sentiment_7d_prior) : null;
  const change = scoreValue != null && prior != null ? scoreValue - prior : null;
  return {
    scoreText: scoreValue == null ? "—" : formatSentimentScore(scoreValue),
    scoreTone: sentimentTone(scoreValue).tone,
    count: newsCount(news),
    change: change == null ? null : sentimentChangeLabel(change),
    sourceNote: newsSourceNote(news, { timeZone, now }),
    historyEmpty: !(Array.isArray(news?.sentiment_history) && news.sentiment_history.length),
  };
}

export function newsEmptyMessage(ticker, headlines) {
  if (headlines.length) return "";
  if (!COMPANY_NEWS_TICKERS.includes(ticker)) {
    return `Company news is collected for TSLA and SPCX. ${ticker} gets Alpha Vantage headlines on rotation, every few days.`;
  }
  return `No ${ticker} headlines in the last 7 days.`;
}

const GENERIC_FILING_TITLES = new Set([
  "", "8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "4", "FORM 4",
  "OWNERSHIP DOCUMENT", "STATEMENT OF CHANGES", "STATEMENT OF CHANGES IN BENEFICIAL OWNERSHIP", "CURRENT REPORT",
]);
const ITEM_PHRASES = {
  "2.02": "Item 2.02 Results of operations",
  "5.02": "Item 5.02 Officer change",
  "1.01": "Item 1.01 Material agreement",
  "7.01": "Item 7.01 Regulation FD",
  "8.01": "Item 8.01 Other events",
};
const ITEM_ORDER = ["2.02", "5.02", "1.01", "7.01", "8.01"];
const CLASS_ITEMS = { earnings: "2.02", officer: "5.02", agreement: "1.01", other: "8.01" };
const FORM4_PHRASES = {
  insider: "Insider transaction", buy: "Insider purchase", sell: "Insider sale", grant: "Insider grant", other: "Insider transaction",
};

function filingPhrase(row, form) {
  const raw = singleLine(row.title);
  const generic = GENERIC_FILING_TITLES.has(raw.toUpperCase()) || raw.toUpperCase() === form;
  const items = Array.isArray(row.items) ? row.items.map(String) : [];
  const material = ITEM_ORDER.filter((code) => items.includes(code));
  const klass = row.class || row.filing_class || "";
  if (material.length) {
    const extra = material.length > 1 ? ` +${material.length - 1}` : "";
    return `${ITEM_PHRASES[material[0]]}${extra}`;
  }
  if (form === "4") return generic ? (FORM4_PHRASES[klass] || "Insider transaction") : raw;
  if (form.startsWith("10-Q")) return generic ? "Quarterly report" : raw;
  if (form.startsWith("10-K")) return generic ? "Annual report" : raw;
  if (form.startsWith("8-K")) {
    const code = CLASS_ITEMS[klass];
    if (!generic && code && !/item\s+\d/i.test(raw)) return `${ITEM_PHRASES[code].split(" ").slice(0, 2).join(" ")} ${raw}`;
    if (generic && code) return ITEM_PHRASES[code];
    return generic ? "Current report" : raw;
  }
  return generic ? "" : raw;
}

export function filingDisplayTitle(row = {}) {
  const form = String(row.form || "Filing").toUpperCase();
  const formLabel = form === "4" ? "Form 4" : form;
  const phrase = filingPhrase(row, form);
  let title = phrase && phrase.toUpperCase() !== formLabel.toUpperCase() ? `${formLabel} · ${phrase}` : formLabel;
  const report = String(row.report_date || "").slice(0, 10);
  const filed = String(row.filed_at || "").slice(0, 10);
  if (form.startsWith("10-K") && DATE_ONLY.test(report)) title += ` (FY${report.slice(0, 4)})`;
  else if (form.startsWith("10-Q") && DATE_ONLY.test(report)) title += ` (period ${catalystDateLabel(report)})`;
  else if (form.startsWith("8-K") && DATE_ONLY.test(report) && report !== filed) {
    title += ` · event ${catalystDateLabel(report)}`;
  }
  const role = singleLine(row.insider_role);
  if (form === "4" && role) title += ` · ${role}`;
  return title;
}

const RELEASE_NAMES = {
  cpi: "CPI", pce: "PCE", trade: "Trade balance",
  CPI: "CPI", PCE: "PCE", CPI_YOY: "CPI", CORE_CPI_YOY: "Core CPI", PCE_YOY: "PCE", CORE_PCE_YOY: "Core PCE",
  CUSR0000SA0: "CPI", CUSR0000SA0L1E: "Core CPI",
};
const PRIOR_MONTH = new Set(Object.keys(RELEASE_NAMES));

export function releaseDisplayTitle(row = {}) {
  const series = String(row.series || row.series_id || "");
  const name = RELEASE_NAMES[series] || RELEASE_NAMES[series.toLowerCase()] || series || "Release";
  const period = String(row.period || "");
  let when = "";
  if (/^\d{4}-\d{2}/.test(period)) {
    when = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" }).format(new Date(`${period.slice(0, 7)}-01T00:00:00Z`));
  } else if (PRIOR_MONTH.has(series) || PRIOR_MONTH.has(series.toLowerCase())) {
    const day = String(row.release_ts || "").slice(0, 10);
    if (DATE_ONLY.test(day)) {
      const date = new Date(`${day}T00:00:00Z`);
      date.setUTCMonth(date.getUTCMonth() - 1);
      when = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" }).format(date);
    }
  }
  return when ? `${name} (${when})` : name;
}

const EDGAR_PATH = /\/Archives\/edgar\/data\/(\d+)\/(\d{10,})\/([^/?#]+)/i;

export function edgarIndexUrl(primaryUrl) {
  const match = String(primaryUrl || "").match(EDGAR_PATH);
  if (!match) return /^https?:\/\//i.test(primaryUrl || "") ? primaryUrl : "";
  const bare = match[2];
  if (bare.length < 12) return primaryUrl;
  const accession = `${bare.slice(0, 10)}-${bare.slice(10, 12)}-${bare.slice(12)}`;
  return `https://www.sec.gov/Archives/edgar/data/${Number(match[1])}/${bare}/${accession}-index.html`;
}

export function edgarCompanyUrl(primaryUrl) {
  const match = String(primaryUrl || "").match(EDGAR_PATH);
  if (!match) return "";
  return `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${match[1].padStart(10, "0")}&owner=include&count=40`;
}

const PROCLAMATION = /\b(proclamation|columbus day|gold star|mother'?s day|father'?s day|thanksgiving|christmas day|veterans day|memorial day|independence day|juneteenth)\b|national\s+.+\s+month/i;

export function isGenericProclamation(event) {
  const category = event?.category || catalystCategory(event?.type, event?.title);
  if (category !== "other") return false;
  const source = String(event?.source || "").toLowerCase();
  if (source.includes("white-house") || source.includes("white house")) return true;
  return PROCLAMATION.test(String(event?.title || ""));
}

export function fomcCalendarEvent(dashboard) {
  const meeting = String(dashboard?.rates?.fomc?.meeting_date || "").slice(0, 10);
  if (!DATE_ONLY.test(meeting)) return null;
  return {
    id: "fomc-decision",
    date: meeting,
    type: "fomc",
    title: "FOMC decision",
    source: "Kalshi",
    date_precision: "day",
    kind: "release",
    category: "rates",
    tickers: [],
  };
}

export function earningsCalendarEvent(tickerData, ticker) {
  const next = tickerData?.next_earnings;
  const date = String(next?.date || "").slice(0, 10);
  if (!DATE_ONLY.test(date)) return null;
  const when = next.time === "amc" ? "after close" : next.time === "bmo" ? "before open" : "";
  return {
    id: `earnings-${ticker}`,
    date,
    type: "earnings",
    title: next.title || `${ticker} earnings`,
    source: next.source || "Earnings calendar",
    note: when,
    date_precision: "day",
    kind: "event",
    category: "filings",
    tickers: [ticker],
  };
}

function byDate(direction = 1) {
  return (left, right) => (Date.parse(left.date) - Date.parse(right.date)) * direction || String(left.id).localeCompare(String(right.id));
}

function withinPastDays(event, days, now, timeZone) {
  if (isUpcomingItem(event, now, timeZone)) return false;
  const precision = resolvePrecision(event.date, event.date_precision);
  const diff = calendarDayDiff(calendarDay(now, { timeZone, precision: "minute" }), calendarDay(event.date, { timeZone, precision }));
  return diff != null && diff >= 0 && diff <= days;
}

function withinUpcomingDays(event, days, now, timeZone) {
  if (!isUpcomingItem(event, now, timeZone)) return false;
  const precision = resolvePrecision(event.date, event.date_precision);
  const diff = calendarDayDiff(calendarDay(event.date, { timeZone, precision }), calendarDay(now, { timeZone, precision: "minute" }));
  return diff != null && diff >= 0 && diff <= days;
}

export const EDGAR_FILING_TICKERS = ["TSLA", "SPCX"];
export const EARNINGS_UNPUBLISHED = "Earnings date not published yet.";
export const UPCOMING_DAYS = 45;
export const PAST_DAYS = 14;
export const COMPANY_EVENT_DAYS = 30;
export const FILING_DAYS = 90;

export function filingsEmptyMessage(ticker, filings) {
  if (filings.length) return "";
  if (!EDGAR_FILING_TICKERS.includes(ticker)) return "SEC filings are collected for TSLA and SPCX only.";
  return "No SEC filings in the last 90 days.";
}

export function upcomingEmptyMessage(ticker) {
  return `No upcoming ${ticker} events in the next ${UPCOMING_DAYS} days.`;
}

function withId(row) {
  if (row.id) return row;
  const key = `${row.type}|${row.date}|${row.title}|${row.url || ""}`;
  return { ...row, id: encodeURIComponent(key), category: row.category || catalystCategory(row.type, row.title) };
}

export function filingPanelModel({ dashboard, ticker, now, timeZone = "UTC" }) {
  const rows = catalystRows(dashboard, ticker);
  const earnings = earningsCalendarEvent(dashboard?.tickers?.[ticker], ticker);
  const filings = rows.filter((row) => row.kind === "filing" && withinPastDays(row, FILING_DAYS, now, timeZone)).sort(byDate(-1)).slice(0, 8);
  const companyEvents = rows.filter((row) => row.kind === "event" && !isGenericProclamation(row));
  const upcoming = companyEvents
    .filter((row) => (row.tickers || []).includes(ticker) && withinUpcomingDays(row, UPCOMING_DAYS, now, timeZone))
    .concat(earnings && withinUpcomingDays(earnings, UPCOMING_DAYS, now, timeZone) ? [withId(earnings)] : [])
    .sort(byDate(1));
  const company = companyEvents
    .filter((row) => withinPastDays(row, COMPANY_EVENT_DAYS, now, timeZone)
      && ((row.tickers || []).includes(ticker) || ["robotaxi", "space", "policy"].includes(row.category)))
    .sort(byDate(-1));
  return { filings, upcoming, company, earningsNote: earnings ? "" : EARNINGS_UNPUBLISHED };
}

export function calendarPanelModel({ dashboard, ticker, now, timeZone = "UTC" }) {
  const rows = catalystRows(dashboard, ticker).map(withId);
  const extras = [fomcCalendarEvent(dashboard), earningsCalendarEvent(dashboard?.tickers?.[ticker], ticker)].filter(Boolean);
  extras.forEach((extra) => {
    const sameDay = rows.some((row) => row.category === extra.category && String(row.date).slice(0, 10) === extra.date && /fomc|earnings/i.test(`${row.type} ${row.title}`));
    if (!sameDay) rows.push(withId(extra));
  });
  const other = rows.filter(isGenericProclamation).sort(byDate(-1));
  const main = rows.filter((row) => !isGenericProclamation(row));
  return {
    upcoming: main.filter((row) => withinUpcomingDays(row, UPCOMING_DAYS, now, timeZone)).sort(byDate(1)),
    past: main.filter((row) => withinPastDays(row, PAST_DAYS, now, timeZone)).sort(byDate(-1)),
    other,
    earningsNote: extras.some((row) => row.type === "earnings") ? "" : EARNINGS_UNPUBLISHED,
  };
}

export function calendarSummary(upcoming, { timeZone = "UTC", now } = {}) {
  if (!upcoming.length) return "0 upcoming";
  const next = upcoming[0];
  const date = catalystDateLabel(next.date, { timeZone, now, precision: next.date_precision });
  const count = countdownLabel(next.date, { now, timeZone, precision: next.date_precision });
  return `${upcoming.length} upcoming · next: ${next.title} · ${date}${count ? ` · ${count.text}` : ""}`;
}

export function catalystCategoriesInWindow(dashboard, ticker, bars) {
  const categories = new Set(catalystRows(dashboard, ticker)
    .filter(event => markerIndex(event, bars) >= 0).map(event => event.category));
  return CATALYST_CATEGORIES.filter(category => categories.has(category.id));
}

export function sortedDrivers(tickerData, sort = "effect") {
  const rows = driverRows(tickerData);
  if (sort === "name") return rows.sort((a, b) => a.series_id.localeCompare(b.series_id));
  const key = sort === "correlation" ? "corr_90d" : sort === "change" ? "change_1m_display" : "effect";
  return rows.sort((a, b) => {
    const left = number(a[key]), right = number(b[key]);
    if (left == null && right != null) return 1;
    if (right == null && left != null) return -1;
    return Math.abs(right ?? 0) - Math.abs(left ?? 0) || a.series_id.localeCompare(b.series_id);
  });
}

export function movingAverageRows(history) {
  const bars = validBars(history);
  const latest = bars.length ? chartValue(bars.at(-1)) : null;
  return [10, 20, 50, 100, 200].map(window => {
    const value = bars.length < window ? null : bars.slice(-window).reduce((sum, bar) => sum + chartValue(bar), 0) / window;
    return { window, value, distance: value && isNumericValue(latest) ? latest / value - 1 : null };
  });
}

export function sensitivityRows(tickerData) {
  return (tickerData?.release_links?.summaries || [])
    .filter(row => number(row.correlation_surprise) != null && number(row.n_releases) >= 12)
    .slice().sort((a, b) => Math.abs(Number(b.correlation_surprise)) - Math.abs(Number(a.correlation_surprise)));
}

const SENSITIVITY_TREND = {
  intensifying: "▲ intensifying",
  fading: "▼ fading",
  stable: "→ stable",
};

export function catalystSensitivityRows(events, bars) {
  const grouped = new Map();
  (events || []).forEach((event) => {
    const move = catalystMove(event, bars);
    if (move == null || !event?.category) return;
    const list = grouped.get(event.category) || [];
    list.push(Math.abs(move));
    grouped.set(event.category, list);
  });
  const rows = CATALYST_CATEGORIES.flatMap((category) => {
    const moves = grouped.get(category.id);
    if (!moves?.length) return [];
    const average = moves.reduce((sum, value) => sum + value, 0) / moves.length;
    let trend = "stable";
    if (moves.length >= 2) {
      const mid = Math.ceil(moves.length / 2);
      const early = moves.slice(0, mid).reduce((sum, value) => sum + value, 0) / mid;
      const lateCount = moves.length - mid;
      const late = moves.slice(mid).reduce((sum, value) => sum + value, 0) / (lateCount || 1);
      if (late > early * 1.2) trend = "intensifying";
      else if (late < early * 0.8) trend = "fading";
    }
    return [{
      id: category.id,
      label: category.label,
      n: moves.length,
      average,
      trend,
      trendLabel: moves.length < 2 ? "one event" : SENSITIVITY_TREND[trend],
    }];
  });
  const max = Math.max(...rows.map((row) => row.average), 0) || 1;
  return rows.map((row) => ({ ...row, width: row.average / max }));
}
