# Design roadmap screenshots

Reference captures from the AI-generated design canvases in `design/ai-generated/` (`Main.dc.html`, `Mobile.dc.html`). Some show features now implemented in `apps/web`; others remain roadmap designs.

Rendered locally with `design/ai-generated/support.js` (a Design-canvas preview shim) so the artboard’s `renderVals()` sample/demo series bind into charts and labels.

| File | What it shows |
| --- | --- |
| `desktop-full.png` | Full desktop stock view (header, selector, chart, signals, panels, drawers) |
| `desktop-chart-overlays.png` | Period chips, overlay tabs, under-chart lanes, catalyst markers |
| `desktop-signals.png` | Net macro pressure, regimes, drivers, catalyst sensitivity |
| `desktop-macro-panels.png` | Macro / theme / news sentiment panels |
| `mobile-glance.png` | Four-tab phone glance layout (Chart / Signals / Calendar & more) |
| `account-menu.png` | Header account button with the signed-in account menu open (data refresh summary, My tickers, Display, Sign out) |
| `account-settings-tickers.png` | Account settings dialog, **My tickers** tab (pin ★ max 6, reorder ▲▼, remove ✕, search-add) |
| `account-settings-refresh.png` | Account settings dialog, **Data refresh** tab (job schedule / status; implemented in `apps/web` for #52) |
| `account-settings-display.png` | Account settings dialog, **Display** tab (time zone PT/ET, up/down colors) |
| `trend-pill-overview.png` | Selected-stock overview row as designed: last price, trend-state pill (`Uptrend · 4d`) + `vs 20-day avg`, period chips |
| `trend-pill-states.png` | The same quote block for each sample ticker in My tickers (`Uptrend · Nd`, `Range · 2d`) |
| `trend-pill-placement-mock.png` | **Rejected option (mock, not the design):** same row with the pill block (▲ icon added) moved to the right of the period chips. Superseded on Oct 7, 2026: no room there; the pill stays under the price (#38) |
| `overview-chips-current-1280.png` | Current app (`apps/web/styles.css` on main) at 1280px: the 7 period chips wrap to 2 rows |
| `trend-pill-under-price-mock-1280.png` | **Mock, not shipped:** app styles + design trend pill under the price + proposed compact chips on one row, 1280px (#38) |
| `trend-pill-under-price-mock-1440.png` | Same mock at 1440px |
| `trend-pill-under-price-mock-360.png` | Same mock at 360px mobile: chips wrap under the price |
| `themes/*-desktop-full.png` | Full desktop view in each proposed color theme (see [`themes/README.md`](themes/README.md)) |
| `issue-26/production-*.png` | Implemented signals/rates/release/drift panels in six themes and four mobile views (see [`issue-26/README.md`](issue-26/README.md)) |

**Note:** Values in these shots are **design sample data** from the artboard’s inline demo generator (dated demo facts and synthetic series), **not live lake / market data**. See the [Dashboard features](https://github.com/ram290476/investor-dashboard/wiki/Dashboard-features) wiki for what ships today vs design-only.

## Regenerating

```sh
cd design/ai-generated
python3 -m http.server 8765
# In another shell, open Main.dc.html / Mobile.dc.html in Chrome and capture,
# or use a headless script against http://127.0.0.1:8765/…
```

Account menu / settings dialog shots (self-contained, starts its own static server):

```sh
npm i --no-save puppeteer-core     # or NODE_PATH=<dir with puppeteer-core>
CHROME=/usr/bin/google-chrome node docs/design-roadmap/render-account-settings.js
```

Trend-pill overview crops (same setup; renders at 2× device scale):

```sh
CHROME=/usr/bin/google-chrome node docs/design-roadmap/render-trend-pill.js
```

Trend pill under the price + compact period chips (app styles mock, same setup):

```sh
CHROME=/usr/bin/google-chrome node docs/design-roadmap/render-trend-pill-layout.js
```
