#!/usr/bin/env node
/**
 * Render MOCKS of the selected-stock overview row in the real app styles for
 * issue #38 (revised Oct 7, 2026): trend pill + "vs 20-day avg" UNDER the last
 * price (as in design/ai-generated/Main.dc.html) and a compact period-chip strip
 * that fits on one line next to the price.
 *
 * How it works (same method as render-trend-pill.js): serves the repo over a
 * local HTTP server, opens a blank page in headless Chrome at 2x device scale,
 * links apps/web/styles.css (unchanged), builds the overview markup that
 * renderPricePanel()/renderPeriodChips() in apps/web/app.js produce, adds the
 * design's trend pill row under the price, and (for the mocks) layers the
 * PROPOSED compact-chip CSS below on top. Nothing in apps/web is modified.
 *
 *   overview-chips-current-1280.png        - today's app CSS at 1280px: the 7 chips wrap to 2 rows
 *   trend-pill-under-price-mock-1280.png   - mock: pill under price, compact chips on one row (1280px)
 *   trend-pill-under-price-mock-1440.png   - same at 1440px
 *   trend-pill-under-price-mock-360.png    - same at 360px mobile (chips wrap under the price)
 *
 * Usage (from repo root):
 *   npm i --no-save puppeteer-core     # or NODE_PATH=<dir with puppeteer-core>
 *   CHROME=/usr/bin/google-chrome node docs/design-roadmap/render-trend-pill-layout.js
 *
 * Values are design sample data (TSLA $370.84, Uptrend · 4d), not live data.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const http = require('http');
const puppeteer = require('puppeteer-core');

const HERE = __dirname;
const ROOT = path.resolve(HERE, '../..');
const CHROME = process.env.CHROME || '/usr/bin/google-chrome';
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' };

// Proposed compact period-chip CSS (issue #38). Not applied to apps/web here.
const PROPOSED_CSS = `
.overview-quote { flex: 1 1 auto; }
.period-chips { flex: 0 0 auto; flex-wrap: nowrap; gap: 3px; }
.period-chip { min-width: 50px; min-height: 36px; padding: 2px 6px; gap: 0; border-radius: 7px; }
.period-label { font-size: 11px; letter-spacing: 0.04em; line-height: 1.25; }
.period-chip .period-return { font-size: 11px; line-height: 1.25; }
@media (pointer: coarse) { .period-chip { min-height: 44px; } }
@media (max-width: 640px) { .period-chips { flex: 1 1 100%; flex-wrap: wrap; justify-content: flex-start; } }
`;

// Design trend pill row (Main.dc.html: 11px, 1px 8px padding, pill radius, UP at ~53%/12% alpha), on app tokens.
const PILL_CSS = `
.overview-trend { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 6px 0 0; font-size: 11px; }
.trend-pill { padding: 1px 8px; border-radius: 999px; border: 1px solid color-mix(in srgb, var(--green) 53%, transparent);
  color: var(--green); background: color-mix(in srgb, var(--green) 12%, transparent); }
.trend-vs { color: var(--muted); }
`;

const CHIPS = [['1D', '-1.1%', 'negative'], ['1W', '+0.2%', 'positive'], ['1M', '+3.0%', 'positive'], ['3M', '+18.4%', 'positive'],
  ['1Y', '+30.1%', 'positive'], ['3Y', '-14.1%', 'negative'], ['5Y', '+54.9%', 'positive']];

function page({ proposed, pill, label }) {
  const chips = CHIPS.map(([l, v, c]) => `<button type="button" class="period-chip" aria-pressed="${l === '1M'}"><span class="period-label">${l}</span><span class="period-return mono ${c}">${v}</span></button>`).join('');
  const trend = pill ? `<p class="overview-trend mock-moved"><span class="trend-pill">Uptrend · 4d</span><span class="trend-vs">vs 20-day avg <span class="mono positive">+0.6%</span></span></p>` : '';
  return `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/apps/web/styles.css">
<style>${PILL_CSS}${proposed ? PROPOSED_CSS : ''}
.mock-label { margin: 0 0 6px; font: 600 11px/1.4 "IBM Plex Sans", sans-serif; color: #e3b341; letter-spacing: .02em; }
.mock-moved { outline: 1px dashed #e3b341; outline-offset: 3px; border-radius: 4px; width: fit-content; }
body { background: var(--page); }</style></head><body>
<div class="app-shell"><main class="dashboard-grid"><div id="primary"><p class="mock-label">${label}</p>
<section class="panel price-panel" id="target"><div class="overview">
<div class="overview-quote"><h2 class="overview-kicker">TSLA · daily closes</h2><p class="overview-price">$370.84</p>${trend}
<p class="overview-meta">As of 2026-10-06 · <span class="positive">+3.0% 1M</span> on the chart</p></div>
<div class="period-chips" role="group" aria-label="Period: return over each period; select one to chart it">${chips}</div>
</div></section></div><aside class="side-column"><section class="panel" style="height:60px"></section></aside></main></div></body></html>`;
}

function serve(routes) {
  return new Promise(resolve => {
    const srv = http.createServer((req, res) => {
      const rel = decodeURIComponent(new URL(req.url, 'http://x').pathname);
      if (routes[rel]) { res.writeHead(200, { 'Content-Type': 'text/html' }); return res.end(routes[rel]); }
      const file = path.join(ROOT, rel.replace(/^\/+/, ''));
      if (!file.startsWith(path.join(ROOT, 'apps/web')) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
      fs.createReadStream(file).pipe(res);
    }).listen(0, '127.0.0.1', () => resolve(srv));
  });
}

const SHOTS = [
  { file: 'overview-chips-current-1280.png', width: 1280, proposed: false, pill: false,
    label: 'CURRENT app (main) at 1280px: 7 period chips wrap to 2 rows' },
  { file: 'trend-pill-under-price-mock-1280.png', width: 1280, proposed: true, pill: true,
    label: 'MOCK (not shipped) at 1280px: trend pill under the price (dashed), compact chips on one row' },
  { file: 'trend-pill-under-price-mock-1440.png', width: 1440, proposed: true, pill: true,
    label: 'MOCK (not shipped) at 1440px: trend pill under the price (dashed), compact chips on one row' },
  { file: 'trend-pill-under-price-mock-360.png', width: 360, proposed: true, pill: true,
    label: 'MOCK (not shipped) at 360px: chips wrap under the price' },
];

(async () => {
  const routes = {};
  SHOTS.forEach((s, i) => { routes[`/mock-${i}.html`] = page(s); });
  const srv = await serve(routes);
  const port = srv.address().port;
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none', '--hide-scrollbars'] });
  try {
    const p = await browser.newPage();
    for (const [i, s] of SHOTS.entries()) {
      await p.setViewport({ width: s.width, height: 900, deviceScaleFactor: 2 });
      await p.goto(`http://127.0.0.1:${port}/mock-${i}.html`, { waitUntil: 'networkidle0' });
      await p.evaluate(async () => { try { await document.fonts.ready; } catch (_) {} });
      const info = await p.evaluate(() => {
        const label = document.querySelector('.mock-label').getBoundingClientRect();
        const panel = document.getElementById('target').getBoundingClientRect();
        const chips = [...document.querySelectorAll('.period-chip')].map(c => c.getBoundingClientRect());
        return { x: panel.x, y: label.y, w: panel.width, h: panel.bottom - label.y,
          rows: new Set(chips.map(c => Math.round(c.top))).size, chipH: Math.round(chips[0].height),
          chipW: chips.map(c => Math.round(c.width)).join('/') };
      });
      const n = 10;
      const clip = { x: Math.max(0, info.x - n), y: Math.max(0, info.y - n), width: info.w + 2 * n, height: info.h + 2 * n };
      const dest = path.join(HERE, s.file);
      await p.screenshot({ path: dest, type: 'png', clip });
      console.log('wrote', s.file, `chip rows=${info.rows} chipH=${info.chipH} chipW=${info.chipW}`, fs.statSync(dest).size);
    }
  } finally {
    await browser.close();
    srv.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
