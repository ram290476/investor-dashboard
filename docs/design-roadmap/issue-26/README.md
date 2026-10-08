# Issue #26: implemented signals UX

Real application captures using synthetic data, not production market observations.
The pressure gauge, ranked driver rows, rate histories, release trends/windows,
and correlation drawer use the same schema as the serving layer. The reference
design remains the visual target; unavailable sources are not replaced with its
sample facts or synthetic causal sensitivity.

| Theme | Application screenshot |
| --- | --- |
| Industrial Dark | [Desktop](production-industrial-dark.png) |
| Terminal Amber | [Desktop](production-terminal-amber.png) |
| Charting Navy | [Desktop](production-charting-navy.png) |
| Clean Light | [Desktop](production-clean-light.png) / [Mobile chart](production-clean-light-mobile.png) / [Mobile signals](production-clean-light-mobile-signals.png) |
| Colorblind High Contrast | [Desktop](production-colorblind-hc.png) |
| Midnight Slate | [Desktop](production-midnight-slate.png) |

Run the [production browser validator](../themes/render-production-themes.js) with
`OUTPUT_DIR=docs/design-roadmap/issue-26` and a local application server on port
8716. It verifies all themes, persistence, rollback, mobile overflow, driver
warm-up states, insufficient release history and the correlation drawer.

See the [remediation plan and schema](../../trend-serving.md) for evidence,
scope, formulas and production verification steps.
