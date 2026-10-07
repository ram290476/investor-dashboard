#!/usr/bin/env node
/**
 * Render reference crops of the design's selected-stock overview row
 * (last price, trend-state pill, "vs 20-day avg", period chips) from
 * design/ai-generated/Main.dc.html.
 *
 * How it works: serves design/ai-generated/ over a local HTTP server, loads
 * Main.dc.html in headless Chrome at 1440px (same as the other
 * docs/design-roadmap/*.png), waits for support.js to bind the artboard's
 * sample data (data-dc-preview="ready"), then:
 *   1. trend-pill-overview.png        - the overview row exactly as designed (TSLA)
 *   2. trend-pill-states.png          - the quote block for each sample ticker in My tickers
 *                                       (the sample strip only lands on Uptrend and Range)
 *   3. trend-pill-placement-mock.png  - the same row with the pill block moved to the
 *                                       RIGHT of the period chips (requested placement).
 *                                       DOM is rearranged in the headless page only; the
 *                                       mockup source is never modified.
 *
 * Usage (from repo root):
 *   npm i --no-save puppeteer-core     # or NODE_PATH=<dir with puppeteer-core>
 *   CHROME=/usr/bin/google-chrome node docs/design-roadmap/render-trend-pill.js
 *
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

// The overview row: first child of the chart column inside <section aria-label="… overview">.
const ROW = () => {
  const sec = [...document.querySelectorAll('section[aria-label]')].find(s => / overview$/.test(s.getAttribute('aria-label')));
  return sec.firstElementChild.firstElementChild;
};

(async () => {
  const srv = await serve();
  const port = srv.address().port;
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none', '--hide-scrollbars'] });
  try {
    const p = await browser.newPage();
    await p.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 2 });
    await p.goto(`http://127.0.0.1:${port}/Main.dc.html`, { waitUntil: 'networkidle0', timeout: 90000 });
    await p.waitForFunction(() => document.documentElement.getAttribute('data-dc-preview') === 'ready');
    await p.evaluate(async () => { try { await document.fonts.ready; } catch (_) {} });
    await sleep(500);
    const rowBox = () => p.evaluate(`(${ROW})().getBoundingClientRect().toJSON()`);
    const pad = (r, n = 10) => ({ x: Math.max(0, r.x - n), y: Math.max(0, r.y - n), width: r.width + 2 * n, height: r.height + 2 * n });
    const shot = async (file, clip) => {
      const dest = path.join(HERE, file);
      await p.screenshot({ path: dest, type: 'png', clip });
      console.log('wrote', file, JSON.stringify(clip), fs.statSync(dest).size);
    };

    // 1. Overview row as designed
    await shot('trend-pill-overview.png', pad(await rowBox()));

    // 2. Pill text per sample ticker in the "My tickers" strip
    const syms = await p.evaluate(() => [...document.querySelectorAll('[aria-label="My tickers: pick a stock"] button')].map(b => b.title));
    const states = [];
    for (let i = 0; i < syms.length; i++) {
      await p.evaluate(j => document.querySelectorAll('[aria-label="My tickers: pick a stock"] button')[j].click(), i);
      await sleep(300);
      states.push(await p.evaluate(`(() => { const row = (${ROW})(); const q = row.firstElementChild; return { kicker: q.children[0].textContent, price: q.children[1].textContent, pill: q.children[2].firstElementChild.textContent, vs: q.children[2].lastElementChild.textContent }; })()`));
    }
    console.log(JSON.stringify(states, null, 1));
    // Contact sheet of the quote block for each ticker
    await p.evaluate(`(() => {
      const row = (${ROW})();
      const sheet = document.createElement('div');
      sheet.id = 'pill-sheet';
      sheet.style.cssText = 'position:fixed;left:0;top:0;z-index:99999;display:flex;gap:28px;padding:16px 20px;background:#12161D;border:1px solid #222A35;border-radius:12px;font-family:inherit;color:#E6EAF0';
      const cs = getComputedStyle(row);
      sheet.style.fontFamily = cs.fontFamily;
      sheet.style.fontSize = cs.fontSize;
      sheet.style.lineHeight = cs.lineHeight;
      document.body.appendChild(sheet);
    })()`);
    for (let i = 0; i < syms.length; i++) {
      await p.evaluate(j => document.querySelectorAll('[aria-label="My tickers: pick a stock"] button')[j].click(), i);
      await sleep(300);
      await p.evaluate(`(() => { const q = (${ROW})().firstElementChild.cloneNode(true); document.getElementById('pill-sheet').appendChild(q); })()`);
    }
    const sb = await p.evaluate(() => document.getElementById('pill-sheet').getBoundingClientRect().toJSON());
    await shot('trend-pill-states.png', { x: 0, y: 0, width: Math.ceil(sb.width), height: Math.ceil(sb.height) });
    await p.evaluate(() => document.getElementById('pill-sheet').remove());

    // Back to TSLA (first button)
    await p.evaluate(() => document.querySelectorAll('[aria-label="My tickers: pick a stock"] button')[0].click());
    await sleep(300);

    // 3. Requested placement mock: pill + "vs 20-day avg" moved to the right of the chips
    await p.evaluate(`(() => {
      const row = (${ROW})();
      const quote = row.firstElementChild, chips = row.children[1], trend = quote.children[2];
      const right = document.createElement('div');
      right.style.cssText = 'display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;margin-left:auto';
      row.style.justifyContent = 'flex-start';
      row.insertBefore(right, chips);
      right.appendChild(chips);
      trend.style.outline = '1px dashed #E3B341';
      trend.style.outlineOffset = '4px';
      trend.style.borderRadius = '4px';
      const pill = trend.firstElementChild;
      pill.textContent = '\\u25B2 ' + pill.textContent;
      right.appendChild(trend);
    })()`);
    await sleep(300);
    await shot('trend-pill-placement-mock.png', pad(await rowBox(), 14));
  } finally {
    await browser.close();
    srv.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
