# Investor Dashboard UI: design canvas source

Last updated 2026-10-03. Live canvas (editable, private until shared):
https://claude.ai/artifact/Ps6vDzMW5fVX9RMLkDyxKX

## Files

| File | What it is |
| --- | --- |
| `ui/investor-dashboard-ui/Main.dc.html` | Desktop stock view (1440 wide, fluid). Stock selector, unified chart area, macro panels, company panel, drawers. |
| `ui/investor-dashboard-ui/Mobile.dc.html` | Phone glance view (390×844), four tabs. Still on the earlier two-stock layout. |
| `ui/investor-dashboard-ui/canvas.json` | Canvas index: artboard positions and sizes. |

All values are sample data except dated facts (Texas fleet 476 on Sep 21, Nevada permit Aug 21, 8 EU FSD approvals Sep 29, SPCX listing Jun 12, FOMC dates).

## Desktop layout (Main.dc.html)

1. Header: market status, data-freshness button (opens the Schedule drawer with all batch jobs).
2. Stock selector: TSLA or SPCX. Everything below shows only the selected stock.
3. Unified area: price chart (moving average, macro overlay, catalyst markers) with a macro-pressure lane on the same time axis; side column with net pressure, regime pills, top 5 drivers, catalyst sensitivity, recent catalysts.
4. Macro panels: rates and curve, inflation surprise, tariffs and geopolitics.
5. Company panel (robotaxi for TSLA, ops and contracts for SPCX) and news sentiment.
6. Drawers, collapsed by default: catalyst calendar, all driver trends, correlation drift, about this data.

Tweaks: up/down palette (green/red or blue/orange), time zone (PT/ET).

## Trend calculations the serving layer must provide

The page computes these from sample series; in production read them from a `trend_metrics` table keyed by (series_id, date):
1W/1M/3M change and z-score, 1-year range percentile, trend state (price vs 20/50-day averages) and days in state,
rolling 30D and 90D correlation with TSLA and SPCX daily returns, effect = ρ(90D) × 1M z, net pressure = tanh(Σ effect / 3).
Refresh after D4 (daily series) and M1 (release series).

## Resuming in a new chat

Give Claude the canvas link (it reads the artboards with the Artifact tool), or attach these files.
To rebuild the canvas from files: create a Design canvas and publish `project/Main.dc.html`, `project/Mobile.dc.html`, `project/canvas.json`.
