# Issue #26: implemented signals UX

Real application captures using synthetic data, not production market observations.
The pressure gauge, ranked driver rows, rate histories, release trends/windows,
and correlation drawer use the same schema as the serving layer. The reference
design remains the visual target; unavailable sources are not replaced with its
sample facts or synthetic causal sensitivity.

The signals panel was restyled on October 10, 2026 (#134): regime pills and catalyst sensitivity
match the layout in [`../issue-133/`](../issue-133/README.md). The table points at a local preview
of that app. The `production-*.png` files in this folder are the October 8 captures, from before
catalyst rows, news rows, lane colors, and those signal changes. The side-by-side signal crops are
[`../issue-133/07-panel-original-after-1440.png`](../issue-133/07-panel-original-after-1440.png)
and [`../issue-133/07-panel-clean-light-1440.png`](../issue-133/07-panel-clean-light-1440.png).

| Theme | Application screenshot |
| --- | --- |
| Industrial Dark | [Desktop](../current-app/industrial-dark-1440.png) |
| Terminal Amber | [Desktop](../current-app/terminal-amber-1440.png) |
| Charting Navy | [Desktop](../current-app/charting-navy-1440.png) |
| Clean Light | [Desktop](../current-app/clean-light-1440.png) / [Mobile chart](../current-app/clean-light-390-chart.png) / [Mobile signals](../current-app/clean-light-390-signals.png) |
| Colorblind High Contrast | [Desktop](../current-app/colorblind-hc-1440.png) |
| Midnight Slate | [Desktop](../current-app/midnight-slate-1440.png) |

Run the [production browser validator](../themes/render-production-themes.js) with
`OUTPUT_DIR=docs/design-roadmap/issue-26` and a local application server on port
8716. It verifies all themes, persistence, rollback, mobile overflow, driver
warm-up states, insufficient release history and the correlation drawer.

See the [remediation plan and schema](../../trend-serving.md) for evidence,
scope, formulas and production verification steps.
