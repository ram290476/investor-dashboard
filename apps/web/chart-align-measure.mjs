// Pixel check for issue #84. Serves the dashboard with a local fixture and asserts
// that the price chart, every mini chart, and the time axis share plot edges.
// Requires Chrome and puppeteer-core (not a runtime dependency):
//   npm install --no-save puppeteer-core
//   node apps/web/chart-align-measure.mjs

import { createServer } from "node:http";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)));
const OUT = process.env.SHOT_DIR || "";
const API = "https://api.mock.invdash.test";
const COG = "https://auth.mock.invdash.test";
const WIDTHS = [1440, 1280, 930, 390, 360];
const PERIODS = ["1D", "1W", "1M", "3M", "YTD", "1Y", "3Y", "5Y"];
const types = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
};

function businessDays(start, count) {
  const days = [];
  const cursor = new Date(`${start}T00:00:00Z`);
  while (days.length < count) {
    if (cursor.getUTCDay() % 6 !== 0) days.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return days;
}

function buildFixture() {
  const days = businessDays("2021-10-01", 1260);
  let price = 240;
  const history = days.map((date, index) => {
    price = Math.max(80, price * (1 + Math.sin(index / 18) * 0.004 + ((index * 17) % 7 - 3) * 0.0015));
    const close = Math.round(price * 100) / 100;
    const volume = Math.round(28_000_000 + Math.sin(index / 5) * 8_000_000 + (index % 9) * 400_000);
    return { date, close, adj_close: close, close_raw: close, volume };
  });
  const last = history.at(-1);
  const intraday = [9, 10, 11, 12, 13, 14, 15, 16].map((hour, index) => ({
    ts: `${last.date}T${String(hour).padStart(2, "0")}:30:00Z`,
    close: Math.round((last.close - 4 + index * 0.7) * 100) / 100,
    volume: 1_200_000 + index * 80_000,
  }));
  return {
    dashboard: {
      schema_version: 1,
      generated_at: "2026-10-07T19:15:00-07:00",
      tickers: {
        TSLA: {
          price_history: history,
          price_as_of: last.date,
          intraday: { bars: intraday, last: intraday.at(-1).close, change_pct: 0.012 },
        },
        SPCX: { price_history: history.slice(-30), price_as_of: last.date },
      },
      events: [
        ["FOMC", "Rates decision"], ["CPI", "CPI release"], ["tariff", "Trade policy"],
        ["robotaxi", "Robotaxi update"], ["filing", "Quarterly filing"],
        ["launch", "Launch window"], ["corporate_update", "Company update"],
      ].map(([type, title], index) => ({
        event_ts: history.at(-12 + index).date, type, title, source: "Fixture", tickers: ["TSLA"],
      })),
    },
    chart: {
      schema_version: 1,
      ticker: "TSLA",
      macro_pressure: history.map((bar, index) => ({ date: bar.date, value: Math.round(Math.sin(index / 14) * 42) / 100 })),
      short_interest: history.filter((_, index) => index % 15 === 0).map((bar, index) => ({
        date: bar.date,
        shares_short: 30_000_000 + index * 50_000,
        short_pct_denominator: Math.round((1.8 + (index % 6) * 0.07) * 100) / 100,
        denominator_type: "shares_outstanding_proxy",
        days_to_cover: 1.2,
      })),
      options: history.slice(-180).map((bar, index) => ({
        date: bar.date,
        put_call_volume_ratio: Math.round((0.72 + (index % 11) * 0.04) * 100) / 100,
        iv30: 0.42,
        iv_available: true,
      })),
      macro_series: {},
      fundamentals: [],
    },
    status: { jobs: [] },
  };
}

const fixture = buildFixture();
let prefs = {
  version: 1,
  tickers: ["TSLA", "SPCX"],
  pinned: ["TSLA"],
  display: { theme: "industrial-dark", updown_palette: "green-red", chart_period: "1M", time_zone: "America/Los_Angeles" },
  chart_settings: { TSLA: { overlays: [], lanes: ["VOL", "SI", "PRESS", "OPT"] } },
};

const server = createServer((req, res) => {
  let pathname = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (pathname === "/config.json") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({
      apiBaseUrl: API, cognitoDomain: COG, clientId: "mock-client",
      callbackUrl: `http://127.0.0.1:${port}/auth/callback`, logoutUrl: `http://127.0.0.1:${port}/`,
    }));
    return;
  }
  if (pathname === "/" || pathname.startsWith("/auth/")) pathname = "/index.html";
  const file = path.join(WEB, pathname);
  if (!file.startsWith(WEB) || !existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { "content-type": types[path.extname(file)] || "application/octet-stream" });
  res.end(readFileSync(file));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;
const site = `http://127.0.0.1:${port}`;
const b64 = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
const idToken = `${b64({ alg: "none" })}.${b64({ email: "investor@example.com", sub: "mock" })}.sig`;
const failures = [];

function check(condition, message) {
  if (!condition) failures.push(message);
}

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH || "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

async function openPage(width, height, theme) {
  prefs = { ...prefs, version: 1, display: { ...prefs.display, theme, chart_period: "1M" } };
  const page = await browser.newPage();
  page.on("pageerror", (error) => failures.push(`pageerror ${theme} ${width}: ${error.message}`));
  await page.setViewport({ width, height, deviceScaleFactor: 1, isMobile: width < 700, hasTouch: width < 700 });
  const cors = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "authorization,content-type,accept",
    "access-control-allow-methods": "GET,PUT,OPTIONS",
  };
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = request.url();
    if (!url.startsWith(API) && !url.startsWith(COG)) { request.continue(); return; }
    if (request.method() === "OPTIONS") { request.respond({ status: 204, headers: cors, body: "" }); return; }
    const pathname = new URL(url).pathname;
    if (url.startsWith(COG)) {
      request.respond({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify({ access_token: "stub", id_token: idToken, token_type: "Bearer", expires_in: 3600 }) });
      return;
    }
    if (request.method() === "PUT") {
      prefs = { ...prefs, ...JSON.parse(request.postData() || "{}"), version: prefs.version + 1 };
      request.respond({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(prefs) });
      return;
    }
    const body = pathname.endsWith("/prefs") ? prefs
      : pathname.endsWith("/dashboard") ? fixture.dashboard
      : pathname.includes("/chart/") ? fixture.chart
      : pathname.endsWith("/status") ? fixture.status : null;
    request.respond({ status: body ? 200 : 404, contentType: "application/json", headers: cors, body: JSON.stringify(body || {}) });
  });
  await page.goto(site);
  await page.evaluate(() => { sessionStorage.setItem("oauth_state", "S"); sessionStorage.setItem("oauth_verifier", "V"); });
  await page.goto(`${site}/auth/callback?code=abc&state=S`);
  await page.waitForSelector(".price-chart");
  await page.waitForSelector(".lane-chart");
  return page;
}

function measureExpression() {
  const edge = (root) => {
    const start = root.querySelector('[data-plot-edge="start"]').getBoundingClientRect();
    const end = root.querySelector('[data-plot-edge="end"]').getBoundingClientRect();
    return { start: start.x + start.width / 2, end: end.x + end.width / 2 };
  };
  const price = edge(document.querySelector(".price-chart"));
  const lanes = [...document.querySelectorAll(".lane-chart")].map((svg) => ({ id: svg.closest(".chart-lane")?.dataset.lane, ...edge(svg) }));
  const axis = edge(document.querySelector(".chart-time-axis"));
  const bars = [...document.querySelectorAll(".lane-vol .lane-volume-bar")].map((bar) => bar.getBoundingClientRect());
  const options = document.querySelector(".lane-opt .lane-line")?.getAttribute("d") || "";
  const optionsX = Number(options.match(/M\s*([0-9.]+)/)?.[1]);
  const laneOrder = [...document.querySelectorAll(".chart-lane")].map((lane) => lane.dataset.lane);
  const priceBox = document.querySelector(".price-chart").getBoundingClientRect();
  const volumeBox = document.querySelector('.chart-lane[data-lane="VOL"]').getBoundingClientRect();
  const legendBox = document.querySelector(".chart-legend").getBoundingClientRect();
  const ticks = [...document.querySelectorAll(".chart-tick")].map((tick) => tick.textContent);
  return { price, lanes, axis, bars: bars.length ? { first: bars[0].x, last: bars.at(-1).right } : null, optionsX, laneOrder, priceBottom: priceBox.bottom, volumeTop: volumeBox.top, legendTop: legendBox.top, ticks, period: document.querySelector(".price-chart").dataset.period };
}

async function selectPeriod(page, periodId) {
  const clicked = await page.evaluate((id) => {
    const button = document.querySelector(`[data-period="${id}"]`);
    if (!button || button.getAttribute("aria-disabled") === "true") {
      return { ok: false, text: document.body.innerText.slice(0, 400) };
    }
    button.click();
    return { ok: true };
  }, periodId);
  if (!clicked.ok) throw new Error(`Cannot select ${periodId}: ${clicked.text}`);
  await page.waitForFunction((id) => document.querySelector(".price-chart")?.dataset.period === id, {}, periodId);
}

async function assertLayout(page, label) {
  const measured = await page.evaluate(measureExpression);
  const limit = 1;
  for (const lane of measured.lanes) {
    check(Math.abs(lane.start - measured.price.start) <= limit && Math.abs(lane.end - measured.price.end) <= limit,
      `${label} ${lane.id} plot ${lane.start.toFixed(2)}-${lane.end.toFixed(2)} vs price ${measured.price.start.toFixed(2)}-${measured.price.end.toFixed(2)}`);
  }
  check(Math.abs(measured.axis.start - measured.price.start) <= limit && Math.abs(measured.axis.end - measured.price.end) <= limit,
    `${label} axis ${measured.axis.start.toFixed(2)}-${measured.axis.end.toFixed(2)} vs price`);
  if (measured.bars) {
    check(Math.abs(measured.bars.first - measured.price.start) <= limit && Math.abs(measured.bars.last - measured.price.end) <= limit,
      `${label} volume bars ${measured.bars.first.toFixed(2)}-${measured.bars.last.toFixed(2)} vs price`);
  }
  check(measured.laneOrder[0] === "VOL", `${label} volume is not the first lane (${measured.laneOrder.join(",")})`);
  check(measured.volumeTop >= measured.priceBottom - 1 && measured.legendTop >= measured.volumeTop,
    `${label} volume is not directly under the price chart`);
  check(measured.ticks.length >= 2, `${label} time axis has ${measured.ticks.length} ticks`);
  return measured;
}

const tickPattern = {
  "1D": /^\d{2}:\d{2}$/,
  "1W": /^[A-Z][a-z]{2} \d{1,2}$/,
  "1M": /^[A-Z][a-z]{2} \d{1,2}$/,
  "3M": /^[A-Z][a-z]{2} \d{1,2}$/,
  "YTD": /^[A-Z][a-z]{2}( \d{2})?$/,
  "1Y": /^[A-Z][a-z]{2}( \d{2})?$/,
  "3Y": /^\d{4}$/,
  "5Y": /^\d{4}$/,
};

async function shot(page, name) {
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  const clip = await page.evaluate(() => {
    const chart = document.querySelector(".chart-readout");
    const end = document.querySelector(".catalyst-filters") || document.querySelector(".lane-controls");
    const top = chart.getBoundingClientRect();
    const bottom = end.getBoundingClientRect();
    const x = Math.max(0, top.left - 8);
    const y = Math.max(0, top.top - 8);
    return { x, y, width: Math.min(window.innerWidth - x, top.width + 16), height: bottom.bottom - y + 12 };
  });
  await page.screenshot({ path: path.join(OUT, name), clip });
  console.log("wrote", name);
}

async function exercise(page, label, onReady) {
  await selectPeriod(page, "1M");
  const chartBox = await page.$eval(".price-chart", (svg) => {
    const rect = svg.getBoundingClientRect();
    return { x: rect.x + rect.width * 0.45, y: rect.y + rect.height * 0.45 };
  });
  await page.mouse.move(chartBox.x, chartBox.y);
  const hover = await page.evaluate(() => {
    const lines = [...document.querySelectorAll(".chart-crosshair")].map((line) => {
      const box = line.getBoundingClientRect();
      return box.x + box.width / 2;
    });
    const axis = document.querySelector(".chart-axis-crosshair").getBoundingClientRect();
    return { text: document.querySelector(".chart-readout").textContent, lines, axis: axis.x };
  });
  check(/Volume/.test(hover.text) && /\d{4}-\d{2}-\d{2}/.test(hover.text), `${label} hover readout: ${hover.text}`);
  check(hover.lines.length >= 4 && hover.lines.every((x) => Math.abs(x - hover.lines[0]) <= 1),
    `${label} hover crosshair xs ${hover.lines.map((x) => x.toFixed(2)).join(",")}`);
  check(Math.abs(hover.axis - hover.lines[0]) <= 1, `${label} axis crosshair ${hover.axis.toFixed(2)}`);
  await page.focus(".chart-stack");
  await page.keyboard.press("ArrowLeft");
  const keyed = await page.$eval(".chart-readout", (node) => node.textContent);
  if (onReady) await onReady();
  check(keyed !== hover.text && /Volume/.test(keyed), `${label} keyboard readout: ${keyed}`);
  return hover;
}

try {
for (const width of WIDTHS) {
  const page = await openPage(width, width < 700 ? 1800 : 1500, "industrial-dark");
  for (const periodId of PERIODS) {
    await selectPeriod(page, periodId);
    const measured = await assertLayout(page, `${width}px ${periodId}`);
    check(measured.ticks.every((label) => tickPattern[periodId].test(label)),
      `${width}px ${periodId} ticks ${measured.ticks.join(", ")}`);
    if (periodId === "1Y") {
      check(measured.optionsX > 70, `${width}px options series starts at viewBox ${measured.optionsX}, expected after the plot origin`);
    }
  }
  await selectPeriod(page, "1M");
  if (width === 1440) {
    await shot(page, "after-industrial-dark-1440.png");
    const clip = await page.evaluate(() => {
      const chart = document.querySelector(".chart-readout");
      const end = document.querySelector(".catalyst-filters") || document.querySelector(".lane-controls");
      const top = chart.getBoundingClientRect();
      const bottom = end.getBoundingClientRect();
      const x = Math.max(0, top.left - 8);
      const y = Math.max(0, top.top - 8);
      return { x, y, width: Math.min(window.innerWidth - x, top.width + 16), height: bottom.bottom - y + 12 };
    });
    await exercise(page, "1440", async () => {
      const box = {
        x: Math.max(0, Math.floor(clip.x)),
        y: Math.max(0, Math.floor(clip.y)),
        width: Math.floor(clip.width),
        height: Math.floor(clip.height),
      };
      await page.screenshot({ path: path.join(OUT, "after-industrial-dark-1440-crosshair.png"), clip: box });
      console.log("wrote after-industrial-dark-1440-crosshair.png");
    });
    const swatches = await page.evaluate(() => {
      const probe = document.createElement("span");
      document.body.append(probe);
      const css = (color) => { probe.style.color = color; return getComputedStyle(probe).color; };
      return [...document.querySelectorAll("[data-catalyst-category]")].map((chip) => {
        const marker = document.querySelector(`.catalyst-marker[data-category="${chip.dataset.catalystCategory}"] [data-catalyst-color]`);
        const swatch = chip.querySelector(".catalyst-swatch");
        return {
          id: chip.dataset.catalystCategory,
          pressed: chip.getAttribute("aria-pressed"),
          label: chip.textContent,
          marker: marker ? css(marker.getAttribute("fill")) : "",
          border: getComputedStyle(chip).borderTopColor,
          swatchBorder: getComputedStyle(swatch).borderTopColor,
          swatchFill: getComputedStyle(swatch).backgroundColor,
        };
      });
    });
    check(swatches.length >= 7, `expected catalyst chips, found ${swatches.length}`);
    for (const chip of swatches) {
      check(chip.pressed === "true" && chip.label.trim().length > 0, `${chip.id} chip label/state`);
      check(chip.border === chip.marker && chip.swatchBorder === chip.marker && chip.swatchFill === chip.marker,
        `${chip.id} on-state color marker ${chip.marker} border ${chip.border} swatch ${chip.swatchBorder}/${chip.swatchFill}`);
    }
    await page.evaluate(() => document.querySelector('[data-catalyst-category="rates"]').click());
    await page.waitForSelector('[data-catalyst-category="rates"][aria-pressed="false"]');
    const off = await page.evaluate(() => {
      const chip = document.querySelector('[data-catalyst-category="rates"]');
      const swatch = getComputedStyle(chip.querySelector(".catalyst-swatch"));
      return { pressed: chip.getAttribute("aria-pressed"), border: getComputedStyle(chip).borderTopColor, fill: swatch.backgroundColor, swatchBorder: swatch.borderTopColor, label: chip.textContent };
    });
    check(off.pressed === "false" && off.label.includes("Rates") && off.swatchBorder === off.border && off.fill !== off.swatchBorder,
      `rates off-state ${JSON.stringify(off)}`);
  }
  if (width === 390) {
    await shot(page, "after-industrial-dark-390.png");
    const lane = await page.$eval(".lane-chart", (svg) => {
      const rect = svg.getBoundingClientRect();
      return { x: rect.x + rect.width * 0.6, y: rect.y + rect.height * 0.5 };
    });
    await page.touchscreen.tap(lane.x, lane.y);
    const touchText = await page.$eval(".chart-readout", (node) => node.textContent);
    check(/Volume/.test(touchText), `touch readout: ${touchText}`);
  }
  await page.close();
}
for (const [width, height, name] of [[1440, 1500, "after-clean-light-1440.png"], [390, 1800, "after-clean-light-390.png"]]) {
  const light = await openPage(width, height, "clean-light");
  await assertLayout(light, `clean-light ${width} 1M`);
  await shot(light, name);
  await light.close();
}

} catch (error) {
  failures.push(error.stack || error.message);
}
await browser.close();
server.close();
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("alignment, axis, crosshair, and catalyst colors passed");
