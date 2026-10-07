# Design roadmap screenshots

Reference captures from the AI-generated design canvases in `design/ai-generated/` (`Main.dc.html`, `Mobile.dc.html`). Used by GitHub roadmap issues for features not yet shipped in `apps/web`.

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
| `account-settings-refresh.png` | Account settings dialog, **Data refresh** tab (job schedule / status) |
| `account-settings-display.png` | Account settings dialog, **Display** tab (time zone PT/ET, up/down colors) |
| `trend-pill-overview.png` | Selected-stock overview row as designed: last price, trend-state pill (`Uptrend · 4d`) + `vs 20-day avg`, period chips |
| `trend-pill-states.png` | The same quote block for each sample ticker in My tickers (`Uptrend · Nd`, `Range · 2d`) |
| `trend-pill-placement-mock.png` | **Mock, not the design:** same row with the pill block (▲ icon added) moved to the right of the period chips — requested placement |
| `themes/*-desktop-full.png` | Full desktop view in each proposed color theme (see [`themes/README.md`](themes/README.md)) |

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
