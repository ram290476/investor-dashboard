#!/usr/bin/env node
/**
 * Render reference screenshots of the design's header account menu and the
 * tabbed "Account settings" dialog from design/ai-generated/Main.dc.html.
 *
 * How it works: serves design/ai-generated/ over a local HTTP server, loads
 * Main.dc.html in headless Chrome at 1440px (same as the other
 * docs/design-roadmap/*.png), waits for support.js to bind the artboard's
 * sample data (data-dc-preview="ready"), then clicks the account button
 * (aria-haspopup="menu"), captures the open menu, opens "My tickers" and
 * captures each dialog tab. The mockup source is never modified.
 *
 * Usage (from repo root):
 *   npm i --no-save puppeteer-core     # or NODE_PATH=<dir with puppeteer-core>
 *   CHROME=/usr/bin/google-chrome node docs/design-roadmap/render-account-settings.js
 *
 * Output (docs/design-roadmap/): account-menu.png, account-settings-tickers.png,
 * account-settings-refresh.png, account-settings-display.png.
 * Values are design sample data, not live lake / market data.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const http = require('http');
const puppeteer = require('puppeteer-core');

const HERE = __dirname;
const DESIGN = path.resolve(HERE, '../../design/ai-generated');
const CHROME = process.env.CHROME || '/usr/bin/google-chrome';
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png' };
const sleep = ms => new Promise(r => setTimeout(r, ms));

function serve() {
  return new Promise(resolve => {
    const srv = http.createServer((req, res) => {
      const rel = decodeURIComponent(new URL(req.url, 'http://x').pathname).replace(/^\/+/, '');
      const file = path.join(DESIGN, rel);
      if (!file.startsWith(DESIGN) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); return res.end(); }
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
      fs.createReadStream(file).pipe(res);
    }).listen(0, '127.0.0.1', () => resolve(srv));
  });
}

(async () => {
  const srv = await serve();
  const port = srv.address().port;
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none', '--hide-scrollbars'] });
  try {
    const p = await browser.newPage();
    await p.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 1 });
    await p.goto(`http://127.0.0.1:${port}/Main.dc.html`, { waitUntil: 'networkidle0', timeout: 90000 });
    await p.waitForFunction(() => document.documentElement.getAttribute('data-dc-preview') === 'ready');
    await p.evaluate(async () => { try { await document.fonts.ready; } catch (_) {} });
    await sleep(500);
    const shot = async (file, clip) => {
      const dest = path.join(HERE, file);
      await p.screenshot({ path: dest, type: 'png', clip });
      console.log('wrote', file, JSON.stringify(clip), fs.statSync(dest).size);
    };

    // 1. Header account menu (signed in)
    await p.click('button[aria-haspopup="menu"]');
    await sleep(400);
    const menu = await p.evaluate(() => {
      const r = document.querySelector('[role=menu][aria-label=Account]').getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    });
    const x0 = Math.max(0, Math.floor(menu.x + menu.w - 760));
    await shot('account-menu.png', { x: x0, y: 0, width: Math.min(1440 - x0, 784), height: Math.ceil(menu.y + menu.h + 24) });

    // 2. Account settings dialog, one capture per tab
    await p.evaluate(() => { [...document.querySelectorAll('[role=menuitem]')].find(e => /^My tickers/.test(e.textContent.trim())).click(); });
    await sleep(400);
    const tabs = [['My tickers', 'account-settings-tickers.png'], ['Data refresh', 'account-settings-refresh.png'], ['Display', 'account-settings-display.png']];
    for (const [tab, file] of tabs) {
      await p.evaluate(t => { [...document.querySelectorAll('[role=dialog] [role=tab]')].find(e => e.textContent.trim() === t).click(); }, tab);
      await sleep(400);
      const d = await p.evaluate(() => {
        const m = document.querySelector('[role=dialog][aria-label="Account settings"]');
        const r = m.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height, holes: (m.innerText.match(/\{\{/g) || []).length };
      });
      if (d.holes) throw new Error(`${tab}: ${d.holes} unbound {{holes}} in dialog`);
      const pad = 24;
      await shot(file, { x: Math.max(0, Math.floor(d.x - pad)), y: Math.max(0, Math.floor(d.y - pad)), width: Math.ceil(d.w + 2 * pad), height: Math.ceil(d.h + 2 * pad) });
    }
  } finally {
    await browser.close();
    srv.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
