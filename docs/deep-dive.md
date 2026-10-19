# Deep dive metrics

A company page shows a **Deep dive** tab only when that ticker's packaged catalog has a `deep_dive` block. The renderers and the collector do not branch on a ticker symbol. Tesla's layout lives in `services/data-jobs/src/functions/company_ir/catalog/TSLA.json`.

The block is served on the existing company document, `GET /stock/{ticker}` (`serving/stock/{TICKER}.json`), under `deep_dive`.

A second catalog, `catalog/SPCX.json`, uses the same block for the SpaceX page (#243). Collector code does not mention that ticker. "Terraform" and "Starmind" are voice labels stored in the catalog: the program names are unconfirmed, Starmind may be Starshield, and Terraform is not HashiCorp. Launch counts, the launch schedule, Starship flights, and the Starlink on-orbit count come from public no-key feeds declared in that catalog. Factory sites and AI deals can be news-derived estimates. Starmind, Terraform, and undisclosed revenue stay manual. No consensus key is added. Revenue and earnings use the same seasonal-naive placeholder as the other deep dive. The placeholder does not project launch counts; upcoming launch counts are the public schedule, not the revenue model.

## What is collected

| Series | Source | Approval |
|---|---|---|
| NHTSA SGO ADS and Level 2 counts, by month and structured kind | Public CSVs on `static.nhtsa.gov`. No API key. The catalog `sources[].reporting_entity` chooses the rows. Report ids keep the latest version. Narratives and VINs are not stored. | `proposed` |
| Production, deliveries, storage deployed, Supercharger totals, revenue, EPS | Already collected company metrics and SEC XBRL rows. The deep-dive page only reads them. | Whatever review state that metric already has. This job does not approve them. |
| Launch cadence, schedule, Starship flights, and upcoming (projected) counts | Launch Library 2 public API, no key, only when the catalog declares `launch_library`. A few pages per weekly run, inside the free hourly cap. Booster reuse stays blank unless that payload includes it. | `proposed` |
| Satellites in orbit | CelesTrak GP group named by the catalog `celestrak` source. No key. One download per weekly run. Earlier quarters of the count are kept. | `proposed` |
| Energy news volume and sentiment | Catalog topic terms tagged by the news collector. The news job writes `curated/news_topics/ticker={TICKER}/topics.json`. | `proposed` |
| News-derived estimates (orders, pipeline, fleet, milestones, factories, deals) | Same stored articles. A number is kept only when the catalog terms sit next to it. Each estimate has source links and a confidence of low or medium. | Always `proposed`. Never approved. Drawn separately from reported figures. |
| Rarely disclosed figures a person enters | `manual/deep_dive/{TICKER}.json`, written by `scripts/add_deep_dive_entry.py` | Always `proposed`. The script ignores any approval flag. |

`deep-dive-collect` runs Monday 08:00 America/New_York and when a ticker is added. It writes `curated/deep_dive/ticker={TICKER}/observations.json` and then the company-IR serve job republishes `serving/stock/{TICKER}.json`. The job is created by the GitHub deploy workflow from `infra/terraform/variables.tf`. Do not apply Terraform locally.

## Forecast contract

`deep_dive.forecast.model_id` is `seasonal_naive_v0` and `model_status` is `placeholder`.

For each configured segment metric, the next four fiscal quarters copy the reported actual from the same quarter a year earlier when that actual exists. `band_low` and `band_high` are null. Consensus is null. This is not the in-house model described in issue #242.

Deep-dive collect locks each next-quarter headline the first time it is projected, on `stored_headlines` inside `curated/deep_dive/ticker={TICKER}/observations.json`. A locked headline is not replaced. After that quarter's actual is on the company series, the page shows the stored headline next to the actual. The disclaimer is on the serving document.

## Deferred

- In-house forecast model, backtest, and method write-up.
- Licensed consensus estimates feed. No key is configured.
- NREL AFDC, supercharge.info, and OpenChargeMap charger locations. No key is configured. The site map stays empty.
- Per-site energy output, until a person enters a sourced manual row. Product split and country deployments can appear as news estimates; they are not a census.
- Charger coordinates. No location feed is configured.
- A reviewer UI for deep-dive manual rows and news estimates. They stay pending review.

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
