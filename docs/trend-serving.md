# Issue #26: collector remediation and trend serving plan

## Evidence and scope

The October 8, 2026 code audit found the following. These are source/test findings,
not claims about today's production collector health: local AWS credentials are
expired, so CloudWatch logs and live lake contents could not be inspected.

| Finding | Why data is missing or partially useful | Remediation |
| --- | --- | --- |
| D1 and M1 exist and write canonical macro IDs | Earlier issue notes predate the shipped collectors; deploying another collector would duplicate work | Reuse the existing collectors and verify their output contracts |
| Trend input uses `obs_date`, ignoring `available_date` | Next-day published observations can influence a stock before they were available | Align macro observations to availability, preserving observation dates for provenance |
| Series changes/z/range are recalculated on each ticker's calendar | Short ticker histories produce different series metrics and prevent shared `(series_id, date)` storage | Compute once on the common stored trading calendar; compute correlations/effects separately per ticker |
| Summing entirely null effects produces zero pressure | Warm-up/missing correlations appear as a neutral signal | Keep pressure null until at least one finite effect exists; report coverage |
| BACKFILL triggers dashboard/M1 links but not TREND | New tickers can retain missing trend documents after their prices arrive | Trigger TREND only for backfills that wrote batches |
| M1 links are rebuilt on releases/backfills, not closes/reconciliation | T0 returns remain missing after release-day prices arrive; adjustments remain stale | Rebuild links without provider calls after D4 and RECONCILE |
| Release links remain in the lake | The frontend cannot show the release trend or ticker/window relationship | Serve per-ticker summaries and latest release information in schema v3 |
| Driver UI filters out every null effect | Valid observations disappear during correlation warm-up | Show values, units, changes, z-score trend/run length and explicitly unavailable links |
| Job events describe suppressed source failures, but not the individual failed sources | Partial status is visible but not actionable; a skipped collector looks successful | Preserve successful/partial chaining and expose safe source IDs and skipped state |
| M1 has no recurring calendar bootstrap | Only price backfill events start a links-only run; no provider/calendar run is guaranteed | Add a 06:35 ET federal-business-day refresh; keep release-morning one-offs |
| Status lists D2/D3/W1 despite no corresponding deployed job definitions | Planned catalog tiers appear permanently broken | Remove unimplemented placeholders, not real collector errors |
| Successful `PutEvents` HTTP responses can contain failed entries | The collector finishes but dependent builds never start | Check `FailedEntryCount`, log and retry rejected publishes |
| D4 collects ETF proxies but dashboard snapshots serve only equities | Market comparison overlays have no ETF price history despite successful collection | Serve proxy histories alongside equities without changing user watchlists |
| TREND iterates collection equities, which deliberately exclude ETF proxies | An ETF added to My tickers has no own price trend or driver links | Use the capped watchlist universe, as M1 already does; exclude each ticker's own ETF driver |

## Implementation sequence

1. Add regression tests for publication-date alignment, shared series metrics,
   zero-variance/non-finite inputs, pressure warm-up, source failure reporting,
   and release-link serving.
2. Refactor trend computation into shared series history plus per-ticker
   correlation history. Retain z-score driver arrows per the issue owner's
   decision; keep 20/50-day averages only for the stock price trend.
3. Repair event refresh paths and least-privilege lake access. Filter idle
   backfill events. Serialize D1/M1/TREND/DASHBOARD with conditional S3 leases,
   avoiding globally reserved concurrency on accounts whose ten-execution limit
   cannot support it.
4. Publish a backwards-compatible dashboard v3 and chart history. Add source
   coverage/provenance, release links, and correlation drift history.
5. Match the reference signals hierarchy: pressure gauge, five ranked drivers,
   detailed driver drawer, correlation drift, rates/curve and inflation release
   panels. Respect all six themes, mobile layouts and keyboard navigation.
   Do not reintroduce the removed above-chart summary pills.
6. Run focused and full regression tests, Terraform validation and CI; capture
   the real UI with synthetic fixture data clearly identified. Open a PR from
   the new branch, resolve conflicts if present, and merge only after CI passes.

## Live verification and operational remediation

After restoring AWS access with `aws login`, inspect the deployed job functions
and CloudWatch source-run records in the configured production Region. Group
failures by job/source/error type; distinguish authentication/quota/HTTP/parser
failures from legitimate holiday skips, no new releases, missing ticker coverage,
and rolling-statistic warm-up. Never log provider keys or authenticated URLs.

Check successful source row counts and the latest curated observations, not just
Lambda success. Verify D1 watermarks, M1 release/vintage dates, price backfill
coverage and TREND/dashboard generation times. Fix provider credentials/quotas
through the existing parameter-store process, not source code. Re-run only the
affected collector within provider budgets, then TREND/dashboard after its output
lands. An idle backfill must not rebuild either. A release-day close must update
release links without fetching agencies again.

Do not fabricate consensus, historical releases, causal sensitivity, or
robotaxi/geopolitical data from the design's sample assets. Regime classification,
catalyst sensitivity and additional company-source collectors remain separately
scoped roadmap work; empty states must explain unavailable inputs.

## Schema

### Shared and per-ticker histories

`serving/trend_metrics/series/metrics.parquet` is keyed by `(series_id, date)`.
It contains `value`, `obs_date`, `available_date`, `chg_1w/1m/3m`,
`z_1w/1m/3m`, `range_pct_1y`, `trend_state`, `days_in_state`, `unit`,
`change_unit`, and `change_1m_display`. The calendar is the union of stored
trading sessions, not a new ticker's short history. Available dates are used for
the backward as-of join. Legacy observations lacking availability metadata fall
back to observation dates; historical revisions are not a point-in-time backtest.

Changes use 5/21/63 sessions. Percentage drivers use fractional returns; rate and
spread drivers use level differences. Z-scores use the trailing 252-session
change distribution with at least 126 samples. The one-year range uses the
trailing high/low, requires 60 samples, and is not an empirical rank percentile.
Zero variance, zero denominators, nonfinite data and insufficient samples are null.
Driver trend uses `z_1m > 0.5` / `< -0.5` / otherwise flat; missing z means no state.

`serving/trend_metrics/ticker=<T>/trend_metrics.parquet` retains all shared fields
and adds `ticker`, `corr_30d`, `corr_90d`, `effect`, `net_pressure`,
`pressure_direction`, `strength`, and `effect_count`.
Correlations pair driver changes with adjusted-close daily returns and require
20/60 paired samples in 30/90-session windows. Monthly YoY series have null daily
correlations/effects. An ETF is not its own driver.
`effect = corr_90d * z_1m`; `net_pressure = tanh(sum(effect)/3)` is null when no
finite effects exist. Positive/negative/zero effects are tailwind/headwind/neutral.
Strength bars use absolute effect: below 0.5 weak, 0.5 to below 1 moderate,
at least 1 strong. These are descriptive categories, not causal confidence.

`serving/trend_metrics/latest/<T>.json` keeps `ticker`, `date`, and `rows`,
including the separate `PX:<T>` 20/50-day price trend. `coverage` adds observed
driver count, finite linked-driver count and missing driver IDs.

### Dashboard v3 and chart history

`serving/dashboard.json` now has `schema_version = 3`. Existing fields remain.
Its ticker map also includes the collected ETF proxies needed by market overlays,
without adding them to the user's ticker preferences.
Each ticker adds `release_links`, either null when not supplied or:

- `latest`: latest release event per series, with date, YoY, surprise/change,
  `surprise_basis`, trend direction and consecutive-release count.
- `summaries`: per-series/window rows (`week_before`, `days_before`, `release_day`)
  with `n_releases`, surprise/trend correlations, average positive/negative moves
  and counts. Stats require at least 12 paired releases, use adjusted closes,
  and remain null rather than zero when unavailable.
- `surprise_basis` distinguishes consensus surprise from the fallback change
  in YoY, or mixed historical inputs. Missing consensus is never displayed as a
  measured consensus surprise.

`serving/chart_data/<T>.json` keeps existing chart fields and adds
`correlation_history[series_id]`: up to 90 dated points with nullable 30D/90D
correlations. Monthly points stay null. Null pressure is excluded from the
renderable pressure history. UI correlation drift compares the latest 90D link
with 21 stored linked sessions earlier, requiring 22 finite points.

Job status includes `skipped`, `reason` and safe `failed_source_ids`. D2/D3/W1
remain unimplemented catalog tiers, not failing deployed jobs.

### Validation and delivery

Eight failing regression tests first reproduced the publication-date leak, false
neutral pressure, missing shared series history, nonfinite direction state and
missing release-link schema, missing ETF overlay histories and missing watchlist
ETF trends. The implemented fixes pass the focused regression
suite, including adjusted-price release links, concurrent-publisher exclusion,
lease recovery, rejected event publishes and BLS/FRED application-error handling.

Delivery is split into a backend/collector PR followed by a design-signals UX PR
on top of the verified backend. The UX implements the signals/rates/release/drift
hierarchy and the design's four mobile views (Chart, Signals, Calendar, More),
with keyboard-accessible tabs and explicit missing-data states.
Provider logs, production lake coverage, external-source repair and live
post-deployment verification remain pending authenticated AWS access.

Verified locally: 198 backend tests (15 empty-metrics warnings), 64 JavaScript
tests, full Ruff, all application JavaScript syntax, Terraform format/validation,
and browser checks for six themes at 1440px/390px, four keyboard mobile tabs,
missing/zero-variation release links, persistence and failed-save rollback.
The backend also passed PR #66 CI on Python 3.12, including dependency audits,
the ARM64 Lambda image build and infrastructure/workflow checks.

### Authenticated production checks (2026-10-09 UTC)

Read-only verification confirmed account `308639168050` and deployment Region
`us-west-1` (the CLI default Region is `us-east-1`). Before the queued #66/#67
deployment, the dashboard was schema v2, lacked release links/ETF proxy histories,
and the shared series artifact/morning M1 schedule were absent. TREND failed with
a Date/string join mismatch, matching the macro normalization fixed in #66.

Logs distinguish actual provider issues from unimplemented catalog placeholders:

| Source | Observed failure | Operational disposition |
| --- | --- | --- |
| FINRA DS-91 | Token JSON/key failures followed by `UnboundLocalError` for `new_dates` | Validate token HTTP status/body; propagate an explicit failed collection after the source bulkhead; never advance the watermark on failure |
| Yahoo DS-05 | APPL HTTP404; SPCX historical window HTTP400 | APPL is not AAPL; do not silently rewrite user symbols. SPCX's cursor is before its stored price inception; confirm the provider response before treating it as a terminal history boundary |
| DoD | HTTP403 | Provider access restriction; do not bypass it |
| SAM opportunities | HTTP400 | Validate the request against provider requirements before spending more daily quota |
| NASA / USAspending | HTTP429 / HTTP422 in the sampled period | Respect provider throttling; verify request fields for the rejected query |
| Census | HTTP400 | Validate the foreign-trade dataset/variables, rather than rotating a key without evidence |
| BLS calendar | HTTP404 from Lambda; public local request HTTP403 | Provider endpoint/access issue; no fabricated release dates |
| Cleveland Fed | `LayoutChangedError`; public HTML has measures in columns, not rows | Adapter layout needs separately tested migration, retaining MoM/YoY/quarterly distinctions and not manufacturing historical consensus from current nowcasts |

These are source-specific follow-ups, not proof that every collector is unhealthy.
Existing FRED, price, news, regulatory, fundamentals and options jobs had successful
status records. A successful status alone still does not prove complete coverage.
Deployments use the existing GitHub OIDC IAM role; no root credential or provider
secret was copied into source or workflow configuration.
