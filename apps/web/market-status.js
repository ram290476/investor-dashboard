// NYSE header label (#90). The snapshot supplies next_open / next_close; the clock picks the words.

const STATUS_WORD = {
  open: "open",
  closed: "closed",
  pre: "pre-market",
  post: "after-hours",
};

const COMPACT_WORD = {
  open: "Open",
  closed: "Closed",
  pre: "Pre",
  post: "Post",
  unavailable: "Unavailable",
};

const ZONE_LABEL = {
  "America/Los_Angeles": "PT",
  "America/New_York": "ET",
};

function parts(date, timeZone, options) {
  try {
    return new Intl.DateTimeFormat("en-US", { ...options, timeZone }).formatToParts(date);
  } catch {
    return new Intl.DateTimeFormat("en-US", { ...options, timeZone: "UTC" }).formatToParts(date);
  }
}

function part(date, timeZone, type, options) {
  return parts(date, timeZone, options).find((item) => item.type === type)?.value || "";
}

export function zoneAbbrev(timeZone, now = new Date()) {
  if (ZONE_LABEL[timeZone]) return ZONE_LABEL[timeZone];
  const name = part(now, timeZone || "UTC", "timeZoneName", { timeZoneName: "short" });
  return name || "UTC";
}

function calendarKey(date, timeZone) {
  const values = parts(date, timeZone, { year: "numeric", month: "2-digit", day: "2-digit" });
  const get = (type) => values.find((item) => item.type === type)?.value || "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function formatSessionClock(value, timeZone) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  const hour = part(date, timeZone, "hour", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const minute = part(date, timeZone, "minute", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  return hour && minute ? `${hour}:${minute}` : "";
}

/** Status the header should show at `now`, following the served boundaries. */
export function liveSession(market, now = new Date()) {
  const status = market?.status;
  const openMs = Date.parse(market?.next_open);
  const closeMs = Date.parse(market?.next_close);
  if (!["open", "closed", "pre", "post"].includes(status) || !Number.isFinite(openMs) || !Number.isFinite(closeMs)) {
    return { status: "unavailable", cue: null, at: null };
  }
  const nowMs = now.getTime();
  if (status === "open") {
    if (nowMs < closeMs) return { status: "open", cue: "closes", at: market.next_close };
    if (nowMs < openMs) return { status: "post", cue: "opens", at: market.next_open };
    return { status: "open", cue: null, at: null };
  }
  if (nowMs < openMs) return { status, cue: "opens", at: market.next_open };
  if (nowMs < closeMs) return { status: "open", cue: "closes", at: market.next_close };
  return { status: "post", cue: null, at: null };
}

function eventText(live, now, timeZone) {
  if (!live.cue || !live.at) return "";
  const clock = formatSessionClock(live.at, timeZone);
  if (!clock) return "";
  const zone = zoneAbbrev(timeZone, new Date(live.at));
  if (live.cue === "closes") return ` · closes ${clock} ${zone}`;
  const sameDay = calendarKey(new Date(live.at), timeZone) === calendarKey(now, timeZone);
  if (sameDay) return ` · opens ${clock} ${zone}`;
  const weekday = part(new Date(live.at), timeZone, "weekday", { weekday: "short" });
  return ` · opens ${weekday} ${clock} ${zone}`;
}

export function marketLabel(market, now, timeZone) {
  const zone = timeZone || "UTC";
  const live = liveSession(market, now);
  if (live.status === "unavailable") {
    return {
      status: "unavailable",
      text: "Market status unavailable",
      compact: COMPACT_WORD.unavailable,
      dot: "",
      boundary: null,
    };
  }
  const boundary = live.at ? Date.parse(live.at) : NaN;
  return {
    status: live.status,
    text: `NYSE ${STATUS_WORD[live.status]}${eventText(live, now, zone)}`,
    compact: COMPACT_WORD[live.status],
    dot: live.status === "open" ? "ok" : live.status === "pre" || live.status === "post" ? "partial" : "",
    boundary: Number.isFinite(boundary) ? boundary : null,
  };
}

export function headerDate(now, timeZone) {
  const zone = timeZone || "UTC";
  const weekday = part(now, zone, "weekday", { weekday: "short", month: "short", day: "numeric" });
  const month = part(now, zone, "month", { weekday: "short", month: "short", day: "numeric" });
  const day = part(now, zone, "day", { weekday: "short", month: "short", day: "numeric" });
  return `${weekday} ${month} ${day} · ${zoneAbbrev(zone, now)}`;
}

/** Delay until the next session boundary, capped at one minute. */
export function clockDelay(boundary, nowMs) {
  if (!Number.isFinite(boundary)) return 60_000;
  return Math.max(250, Math.min(60_000, boundary - nowMs));
}
