import { chromium } from "playwright";
import http from "http"; import fs from "fs"; import path from "path";
const WEB = "/workspace/invdash/wt-final/apps/web", PAY = "/workspace/invdash/acct/payloads", OUT = "/workspace/invdash/acct/shots";
const API = "https://api.mock.invdash.test", COG = "https://auth.mock.invdash.test";
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png" };
const srv = http.createServer((req, res) => {
  let p = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (p === "/config.json") { res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ apiBaseUrl: API, cognitoDomain: COG, clientId: "mock-client", callbackUrl: `http://127.0.0.1:${port}/auth/callback`, logoutUrl: `http://127.0.0.1:${port}/` })); }
  if (p === "/" || p.startsWith("/auth/")) p = "/index.html";
  const f = path.join(WEB, p);
  if (!f.startsWith(WEB) || !fs.existsSync(f)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { "content-type": types[path.extname(f)] || "application/octet-stream" }); fs.createReadStream(f).pipe(res);
});
await new Promise((r) => srv.listen(0, "127.0.0.1", r)); const port = srv.address().port; const SITE = `http://127.0.0.1:${port}`;
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
const idToken = `${b64({ alg: "none" })}.${b64({ email: "investor@example.com", sub: "mock" })}.sig`;
const browser = await chromium.launch({ executablePath: "/usr/bin/google-chrome" });
const log = [];
async function session(viewport, mobile) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: mobile ? 2 : 1, isMobile: !!mobile, hasTouch: !!mobile, timezoneId: "America/Los_Angeles" });
  let prefs = JSON.parse(fs.readFileSync(`${PAY}/prefs.json`, "utf8"));
  const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "authorization,content-type,accept", "access-control-allow-methods": "GET,PUT,OPTIONS" };
  await context.route(`${COG}/**`, (r) => r.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify({ access_token: "stub", id_token: idToken, token_type: "Bearer", expires_in: 3600 }) }));
  await context.route(`${API}/**`, async (r) => {
    const req = r.request(); const p = new URL(req.url()).pathname.slice(1);
    if (req.method() === "OPTIONS") return r.fulfill({ status: 204, headers: cors });
    if (req.method() === "PUT") { const body = JSON.parse(req.postData()); prefs = { ...body, version: body.version + 1, updated_at: new Date().toISOString() }; return r.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(prefs) }); }
    const body = p === "prefs" ? JSON.stringify(prefs) : fs.readFileSync(`${PAY}/${p}.json`, "utf8");
    r.fulfill({ status: 200, contentType: "application/json", headers: cors, body });
  });
  const page = await context.newPage();
  page.on("pageerror", (e) => log.push(`PAGEERROR ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") log.push(`console.error ${m.text()}`); });
  await page.goto(`${SITE}/`);
  await page.evaluate(() => { sessionStorage.setItem("oauth_state", "S"); sessionStorage.setItem("oauth_verifier", "V"); });
  await page.goto(`${SITE}/auth/callback?code=abc&state=S`);
  await page.waitForSelector(".account-button", { timeout: 15000 });
  await page.waitForTimeout(2500);
  return { context, page };
}
async function dialogShot(page, file, pad = 24, full = false) {
  await page.waitForTimeout(600);
  if (full) return page.screenshot({ path: `${OUT}/${file}` });
  const d = page.locator('[role=dialog]').first();
  const box = await d.boundingBox();
  const vp = page.viewportSize();
  const clip = { x: Math.max(0, box.x - pad), y: Math.max(0, box.y - pad) };
  clip.width = Math.min(vp.width - clip.x, box.width + 2 * pad); clip.height = Math.min(vp.height - clip.y, box.height + 2 * pad);
  await page.screenshot({ path: `${OUT}/${file}`, clip });
}
async function openTab(page, label) {
  await page.locator('[role=dialog] [role=tab]', { hasText: label }).click();
  await page.waitForTimeout(500);
}
// Desktop
{ const { context, page } = await session({ width: 1440, height: 1200 });
  await page.screenshot({ path: `${OUT}/_desktop-page.png` });
  await page.locator(".account-button").click(); await page.waitForTimeout(500);
  const menu = await page.locator('[role=menu]').first().boundingBox();
  const x0 = Math.max(0, Math.floor(menu.x + menu.width - 760));
  await page.screenshot({ path: `${OUT}/account-menu-1440.png`, clip: { x: x0, y: 0, width: 1440 - x0, height: Math.ceil(menu.y + menu.height + 24) } });
  await page.locator('[role=menuitem]', { hasText: "My tickers" }).first().click();
  await page.waitForSelector('[role=dialog]');
  await dialogShot(page, "my-tickers-1440.png");
  await openTab(page, "Profile & time zone"); await dialogShot(page, "profile-time-zone-1440.png");
  await openTab(page, "Theme & display"); await dialogShot(page, "theme-display-1440.png");
  await openTab(page, "Data refresh"); await dialogShot(page, "data-refresh-1440.png");
  console.log("desktop dialog text sample:", (await page.locator('[role=dialog]').innerText()).slice(0, 600).replace(/\n/g, " | "));
  await context.close(); }
// Mobile
{ const { context, page } = await session({ width: 390, height: 844 }, true);
  await page.locator(".account-button").click(); await page.waitForTimeout(500);
  await page.locator('[role=menuitem]', { hasText: "My tickers" }).first().click();
  await page.waitForSelector('[role=dialog]');
  await dialogShot(page, "my-tickers-390.png", 0, true);
  await openTab(page, "Data refresh"); await dialogShot(page, "data-refresh-390.png", 0, true);
  await page.evaluate(() => { const w = document.querySelector('.refresh-table-wrap'); w.scrollLeft = w.scrollWidth; });
  await dialogShot(page, "data-refresh-390-scrolled.png", 0, true);
  const sw = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, dlg: document.querySelector('[role=dialog]')?.scrollWidth }));
  console.log("mobile scrollWidth", JSON.stringify(sw));
  await context.close(); }
console.log(log.join("\n") || "no page errors");
await browser.close(); srv.close();
