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
| `themes/*-desktop-full.png` | Full desktop view in each proposed color theme (see [`themes/README.md`](themes/README.md)) |

**Note:** Values in these shots are **design sample data** from the artboard’s inline demo generator (dated demo facts and synthetic series), **not live lake / market data**. See the [Dashboard features](https://github.com/ram290476/investor-dashboard/wiki/Dashboard-features) wiki for what ships today vs design-only.

## Regenerating

```sh
cd design/ai-generated
python3 -m http.server 8765
# In another shell, open Main.dc.html / Mobile.dc.html in Chrome and capture,
# or use a headless script against http://127.0.0.1:8765/…
```
