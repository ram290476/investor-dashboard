// Run against a local apps/web server. All authentication and data responses are synthetic fixtures.
const assert = require("node:assert/strict");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

async function main() {
  const origin = "http://127.0.0.1:8716";
  const output = process.env.OUTPUT_DIR ? path.resolve(process.env.OUTPUT_DIR) : __dirname;
  const history = Array.from({ length: 260 }, (_, i) => ({
    date: new Date(Date.UTC(2025, 9, i + 1)).toISOString().slice(0, 10),
    close: 220 + i * 0.25 + Math.sin(i / 4) * 8,
    adj_close: 220 + i * 0.25 + Math.sin(i / 4) * 8,
    volume: 1200000 + i * 1000,
  }));
  let prefs = {
    tickers: ["TSLA", "SPCX"], pinned: ["TSLA"], version: 1,
    display: { theme: "industrial-dark", updown_palette: "green-red", chart_period: "1M", time_zone: "America/New_York" },
    chart_settings: { TSLA: { overlays: ["MA20"], lanes: ["VOL", "PRESS"] } },
  };
  let failSave = false;
  const driverRows = [
    { series_id: "DGS2", value: 3.61, chg_1m: -0.22, effect: 0.9, corr_30d: -0.6, corr_90d: -0.7,
      z_1m: -1.2857, trend_state: "down", days_in_state: 50, unit: "%", change_unit: "bp", change_1m_display: -22 },
    { series_id: "VIXCLS", value: 17.4, effect: -0.7, corr_30d: -0.4, corr_90d: -0.5,
      z_1m: 1.4, trend_state: "up", days_in_state: 26, unit: "index", change_unit: "%", change_1m_display: 2.7 },
    { series_id: "DGS10", value: 4.18, effect: 0.6, corr_30d: -0.5, corr_90d: -0.65,
      z_1m: -0.9231, trend_state: "down", days_in_state: 17, unit: "%", change_unit: "bp", change_1m_display: -15 },
    { series_id: "DFII10", value: 1.86, effect: 0.3, corr_30d: -0.4, corr_90d: -0.5,
      z_1m: -0.6, trend_state: "down", days_in_state: 18, unit: "%", change_unit: "bp", change_1m_display: -7 },
    { series_id: "DGS30", value: 4.61, effect: 0.2, corr_30d: -0.3, corr_90d: -0.4,
      z_1m: -0.5, trend_state: "flat", days_in_state: 4, unit: "%", change_unit: "bp", change_1m_display: -8 },
    { series_id: "CPI_YOY", value: 2.8, effect: null, corr_30d: null, corr_90d: null,
      z_1m: null, trend_state: null, days_in_state: null, unit: "%", change_unit: "pp", change_1m_display: -0.1 },
  ].map(row => ({ ...row, net_pressure: Math.tanh(1.3 / 3), obs_date: "2026-06-16", available_date: "2026-06-17" }));
  const ticker = {
    ticker: "TSLA", price_history: history,
    trend: { date: "2026-06-17", rows: driverRows, coverage: { available: 6, linked: 5, missing: ["SOFR"] } },
    release_links: {
      latest: [{ series_id: "CPI_YOY", release_date: "2026-06-11", yoy: 2.8, surprise: -0.1,
        surprise_basis: "yoy_change", trend_direction: "decelerating", consecutive_releases: 2 }],
      summaries: ["week_before", "days_before", "release_day"].map(window => ({
        series_id: "CPI_YOY", ticker: "TSLA", window, n_releases: window === "days_before" ? 12 : 3, correlation_surprise: null,
      })),
    },
    news: { sentiment_7d: 0.3, headlines: [{
      title: "Preview fixture: quarterly outlook", publisher: "Sample data",
      published_at: "2026-06-17", label: "bullish", url: "https://example.com",
    }] },
    filings: [{ form: "10-Q", filing_date: "2026-06-16", title: "Quarterly report (sample)", url: "https://example.com" }],
  };
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setRequestInterception(true);
    page.on("request", async (request) => {
      const url = new URL(request.url());
      let document;
      let status = 200;
      if (url.pathname === "/config.json") {
        document = { apiBaseUrl: `${origin}/api`, cognitoDomain: origin, clientId: "fixture-client" };
      } else if (url.pathname === "/oauth2/token") {
        document = { access_token: "fixture-only", id_token: "e30.eyJlbWFpbCI6InByZXZpZXdAZXhhbXBsZS5jb20ifQ.signature" };
      } else if (url.pathname === "/api/prefs") {
        if (request.method() === "PUT") {
          if (failSave) {
            status = 500;
            document = { error: "Fixture save failure" };
          } else prefs = { ...JSON.parse(request.postData()), version: prefs.version + 1 };
        }
        document ??= prefs;
      } else if (url.pathname === "/api/dashboard") {
        document = {
          generated_at: "2026-06-17T20:00:00Z",
          tickers: { TSLA: ticker, SPCX: { ticker: "SPCX", price_history: history } },
          fundamentals: [{ ticker: "TSLA", metric: "revenue", fiscal_quarter: "Q1 2026", release_date: "2026-04-22", value: 22000000000, unit: "USD" }],
          releases: { next: [{series: "CPI", release_ts: "2026-07-14T12:30:00Z"}] },
        };
      } else if (url.pathname === "/api/status") {
        document = { jobs: [{ job: "D4", status: "ok", last_run: "2026-06-17T20:00:00Z" }] };
      } else if (url.pathname.startsWith("/api/chart/")) {
        document = {
          macro_pressure: history.map((bar, i) => ({ date: bar.date, value: Math.sin(i / 10) * 0.4 })),
          macro_series: Object.fromEntries(driverRows.map(row => [row.series_id, history.map((bar, i) => ({
            date: bar.date, value: row.value + Math.sin(i / 10) * 0.05,
          }))])),
          correlation_history: Object.fromEntries(driverRows.map(row => [row.series_id, history.slice(-90).map((bar, i) => ({
            date: bar.date, corr_90d: row.corr_90d == null ? null : row.corr_90d + Math.sin(i / 10) * 0.05,
          }))])),
        };
      }
      if (document) await request.respond({ status, contentType: "application/json", body: JSON.stringify(document) });
      else if (url.origin === origin) await request.continue();
      else await request.abort();
    });
    await page.evaluateOnNewDocument(() => {
      sessionStorage.setItem("oauth_state", "fixture-state");
      sessionStorage.setItem("oauth_verifier", "fixture-verifier");
    });
    const load = async () => {
      await page.goto(`${origin}/?code=fixture&state=fixture-state`, { waitUntil: "networkidle0" });
      await page.waitForSelector(".chart-overlay-path");
    };
    const openSettings = async () => {
      await page.click(".account-button");
      await page.click(".menu-item:nth-of-type(3)");
      await page.waitForSelector(".theme-choices");
    };
    await page.setViewport({ width: 1440, height: 1100 });
    await load();
    assert.match(await page.$eval(".signals-panel", element => element.textContent), /Tailwind/);
    assert.match(await page.$eval(".macro-panel-grid", element => element.textContent), /at least 12 paired releases required/);
    assert.match(await page.$eval(".macro-panel-grid", element => element.textContent), /insufficient usable history or variation/);
    await page.click(".signals-panel summary");
    assert.match(await page.$eval(".signals-panel", element => element.textContent), /Link unavailable/);
    await page.click(".signals-panel summary");
    await page.click(".macro-panel-grid > :last-child summary");
    assert.match(await page.$eval(".macro-panel-grid > :last-child", element => element.textContent), /Strengthening|Fading/);
    await page.click(".macro-panel-grid > :last-child summary");
    await openSettings();
    for (const id of ["terminal-amber", "industrial-dark", "charting-navy", "clean-light", "colorblind-hc", "midnight-slate"]) {
      const saved = page.waitForResponse((response) => response.url().endsWith("/api/prefs") && response.request().method() === "PUT");
      await page.click(`[data-theme-choice="${id}"]`);
      assert.equal((await (await saved).json()).display.theme, id);
      await page.click(".settings-close");
      const state = await page.evaluate(() => ({
        theme: document.documentElement.dataset.theme,
        width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
        stroke: getComputedStyle(document.querySelector(".chart-overlay-path")).stroke,
        swatch: getComputedStyle(document.querySelector(".chart-legend .overlay-swatch")).backgroundColor,
      }));
      assert.equal(state.theme, id);
      assert.equal(state.stroke, state.swatch);
      assert.ok(state.scrollWidth <= state.width);
      assert.deepEqual(prefs.chart_settings.TSLA, { overlays: ["MA20"], lanes: ["VOL", "PRESS"] });
      await page.screenshot({ path: path.join(output, `production-${id}.png`), fullPage: true });
      await openSettings();
    }
    failSave = true;
    await page.click('[data-theme-choice="clean-light"]');
    await page.waitForSelector(".settings-alert");
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "midnight-slate");
    assert.match(await page.$eval(".settings-alert", (element) => element.textContent), /500|Fixture save failure/);
    failSave = false;
    await page.click('[data-theme-choice="clean-light"]');
    await page.waitForFunction(() => document.querySelector(".settings-save").textContent === "Saved");
    await page.click(".settings-close");
    await page.setViewport({ width: 390, height: 844 });
    await load();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "clean-light");
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, "production-clean-light-mobile.png"), fullPage: true });
    await page.click("#mobile-tab-signals");
    assert.ok(await page.$eval(".signals-panel", element => getComputedStyle(element).display !== "none"));
    assert.ok(await page.$eval("#mobile-panel-chart", element => getComputedStyle(element).display === "none"));
    const colors = await page.evaluate(() => ({
      positive: getComputedStyle(document.querySelector(".signal-strength.positive")).color,
      negative: getComputedStyle(document.querySelector(".signal-strength.negative")).color,
    }));
    assert.notEqual(colors.positive, colors.negative);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, "production-clean-light-mobile-signals.png"), fullPage: true });
    await page.focus("#mobile-tab-signals");
    await page.keyboard.press("ArrowRight");
    assert.equal(await page.$eval("#mobile-tab-calendar", element => element.getAttribute("aria-selected")), "true");
    assert.ok(await page.$eval("#mobile-panel-calendar", element => getComputedStyle(element).display !== "none"));
    await page.keyboard.press("ArrowRight");
    assert.ok(await page.$eval("#mobile-panel-more", element => getComputedStyle(element).display !== "none"));
    await page.keyboard.press("Home");
    assert.equal(await page.$eval("#mobile-tab-chart", element => element.getAttribute("aria-selected")), "true");
    assert.deepEqual(errors, []);
    console.log("Six themes: desktop 1440px, mobile 390px, four keyboard-accessible tabs, signals, persistence, chart preservation, and failed-save recovery passed.");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
