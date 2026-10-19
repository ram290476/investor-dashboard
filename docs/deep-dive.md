# Deep dive metrics

A company page shows a **Deep dive** tab only when that ticker's packaged catalog has a `deep_dive` block. The renderers and the collector do not branch on a ticker symbol. Tesla's layout lives in `services/data-jobs/src/functions/company_ir/catalog/TSLA.json`.

The block is served on the existing company document, `GET /stock/{ticker}` (`serving/stock/{TICKER}.json`), under `deep_dive`.

A second catalog, `catalog/SPCX.json`, uses the same block for the SpaceX page (#243). Collector code does not mention that ticker. "Terraform" and "Starmind" are voice labels stored in the catalog: the program names are unconfirmed, Starmind may be Starshield, and Terraform is not HashiCorp. Launch counts, satellite counts, factories, deals, and those two programs are manual and stay pending review. No launch-library key and no consensus key are added. Revenue and earnings use the same seasonal-naive placeholder as the other deep dive. The placeholder does not project launch counts.

## What is collected

| Series | Source | Approval |
|---|---|---|
| NHTSA SGO ADS and Level 2 counts, by month and structured kind | Public CSVs on `static.nhtsa.gov`. No API key. The catalog `sources[].reporting_entity` chooses the rows. Report ids keep the latest version. Narratives and VINs are not stored. | `proposed` |
| Production, deliveries, storage deployed, Supercharger totals, revenue, EPS | Already collected company metrics and SEC XBRL rows. The deep-dive page only reads them. | Whatever review state that metric already has. This job does not approve them. |
| Rarely disclosed figures (per-metro fleet, Semi orders, country energy, milestones, pipeline) | `manual/deep_dive/{TICKER}.json`, written by `scripts/add_deep_dive_entry.py` | Always `proposed`. The script ignores any approval flag. |

`deep-dive-collect` runs Monday 08:00 America/New_York and when a ticker is added. It writes `curated/deep_dive/ticker={TICKER}/observations.json` and then the company-IR serve job republishes `serving/stock/{TICKER}.json`. The job is created by the GitHub deploy workflow from `infra/terraform/variables.tf`. Do not apply Terraform locally.

## Forecast contract

`deep_dive.forecast.model_id` is `seasonal_naive_v0` and `model_status` is `placeholder`.

For each configured segment metric, the next four fiscal quarters copy the reported actual from the same quarter a year earlier when that actual exists. `band_low` and `band_high` are null. Consensus is null. There is no stored prior projection and no backtest. The disclaimer is on the serving document. This is not the in-house model described in issue #242.

## Deferred

- In-house forecast model, backtest, and method write-up.
- Licensed consensus estimates feed. No key is configured.
- NREL AFDC, supercharge.info, and OpenChargeMap charger locations. No key is configured. The site map stays empty.
- Per-site energy output, product split, and country deployments, until a person enters a sourced manual row.
- Energy-news topic tagging. The news collector does not label energy topics, and this change does not add a classifier.
- A reviewer UI for deep-dive manual rows. They stay pending review.

## Open questions (from the design notes)

1. Is a Deep dive tab on `/#stock/{ticker}`, driven only by catalog config, acceptable under the no-hard-coded-tickers rule? The code is generic. The catalog content and the NHTSA entity filter are company-specific.
2. Should low-confidence manual series (per-metro fleet, Semi orders, country energy, project pipeline) appear with a confidence marker, or stay off the page until the company discloses them? Who curates them, and how often?
3. Is "kind of issue" from structured SGO fields enough, given redacted narratives? The page defaults to both ADS and Level 2, with a toggle. A per-mile rate is not shown, because the miles denominator is not collected.
4. Charger locations: is an NREL AFDC key acceptable, and are supercharge.info / OpenChargeMap licences acceptable? The map is empty until that is decided.
5. FSD uses regional small multiples plus a small inset map, not a choropleth. Confirm that choice.
6. Forecast: confirm the segment list, whether an 80% band is required, where a licensed consensus feed would come from, and where the real method and backtest should live.
7. Default window is quarterly, with NHTSA monthly. Is 3Y the right default, or 5Y?
8. On a phone, should the secondary block start collapsed? The page does that.
9. Energy news needs a topic tag the current news collector does not write. Add a classifier, or drop the panel until then?
10. Are the twelve scoreboard tiles the right set, and should a tile scroll to its panel? Tiles do not scroll yet.
