# Investor Dashboard: observability, reliability and security baseline

Terraform configuration for the Investor Dashboard's AWS foundation and application services:
logging, monitoring, alerting, backup/security controls, private CloudFront site hosting,
Cognito sign-in, preferences/data API and container-image jobs. These controls are not a
FedRAMP authorization; see the scope note below.

See the [deployment and operations guide](../../docs/operations.md) for the UI, API, historical
backfill, scheduled refreshes, CI/CD setup, monitoring and troubleshooting.

Everything deploys to `us-west-1` by default (DR copy in `us-west-2`); the `us_east_1` module adds the few pieces AWS only offers in us-east-1.

Expected cost: **about $22-36 a month** in year 1 (see the cost table in the [architecture doc](../../docs/architecture-infrastructure.md#cost-estimate)). Use the
30-day GuardDuty and Security Hub trials and the 15-day Inspector trial to see your real numbers.

## What it deploys

| Module | What it creates | Main controls |
| --- | --- | --- |
| `kms` | Data key (multi-Region, with a DR replica) and a separate audit key, both rotated yearly | SC-12, SC-13, SC-28 |
| `data_lake` | Versioned, KMS-encrypted lake bucket replicated to `us-west-2`; job dead-letter queue; ECR repo with immutable tags | CP-6, CP-9, CP-10, SC-8, SC-28, SI-11 |
| `audit_logging` | Multi-Region CloudTrail (management + lake data events + Lambda invokes, integrity validation, Insights); Object-Locked audit bucket for CloudTrail; AWS Config continuous recording into a separate versioned config bucket (Config cannot write to Object Lock default-retention buckets); both kept 30 months with delete-deny policies | AU-2, AU-3, AU-6, AU-9, AU-11, AU-12, CM-2, CM-8 |
| `security_services` | GuardDuty (S3 + Lambda protection, malware scanning of `raw/`); Security Hub with the NIST 800-53 Rev 5 standard; Inspector for ECR and Lambda; Access Analyzer; account public-access block; EBS default encryption; IAM password policy | CA-7, RA-5, SI-2, SI-3, SI-4, AC-3, AC-6, CM-6, IA-5 |
| `alerting` | Encrypted ops and security SNS topics with email subscriptions; 9 EventBridge rules (GuardDuty, Security Hub, Inspector, audit tampering, S3 exposure, IAM changes, risky sign-in, AWS Health, API key changes) | IR-4, IR-5, IR-6, AU-5, SI-4(5), AC-2(4) |
| `us_east_1` | What AWS only offers in us-east-1: the CloudFront 5xx alarm with its own KMS-encrypted topic (same alert emails, confirmed separately), and rules that forward IAM-change and root/global sign-in events to the primary region's bus, where `alerting` sends them to the security topic. Each event reaches one region's bus, so alerts are not doubled | SI-4, IR-6, AC-2(4), IA-2(1) |
| `observability` | KMS-encrypted log groups (400-day retention); saved Logs Insights queries; alarms incl. SLO burn rates; ops and SLO dashboards; optional external Synthetics canary; monthly budget | AU-4, AU-6, SI-4, CP-2, SA-9 |
| `api_keys` | 12 provider credentials as SSM SecureStrings (data key), each tagged with its rotation period; daily check (8:00 PT) that emails reminders 14, 7, 3, 1 and 0 days before a key is due, daily when overdue | IA-5, IA-5(h), SC-28 |
| `site_auth` | Invite-only Cognito user pool (Plus tier, MFA off / password-only for now, 15-char passwords), public PKCE web client with 1-hour tokens, 12-hour refresh and a 15-minute sign-in session, hosted sign-in domain | IA-2(1), IA-2(2), IA-5, AC-7, AC-12 |
| `site` | Private, versioned S3 static-site bucket; CloudFront Origin Access Control, HTTPS, SPA routing and security headers | SC-8, SC-28 |
| `user_prefs` | DynamoDB `user_prefs` table; site API `GET/PUT /prefs`, authenticated `GET /dashboard` and `/status`; Cognito JWT authorizer; per-user isolation via session-tagged role and `dynamodb:LeadingKeys`; 5xx alarm | AC-3, AC-6, SC-28, CP-9 |
| `jobs` | Container-image job Lambdas with per-job roles, schedules and event triggers: daily prices, split/dividend reconcile, trends, fundamentals, short interest, options, five-year backfill, dashboard build and status feed; failures go to the dead-letter queue. Created once `jobs_image_uri` is set | AC-6, SI-11, CP-10 |
| `workload_boundary` | `invdash-workload-boundary` permissions boundary set on every IAM role in the stack: no IAM writes, no role assumption except the prefs API hop, explicit denies on the deploy role, OIDC provider, the boundary itself, IAM users/keys and boundary changes | AC-6, AC-6(10) |
| `github_deploy` | GitHub Actions OIDC provider (`token.actions.githubusercontent.com`, audience `sts.amazonaws.com`) and the `invdash-terraform-deploy` role, trusted only for this repository's `production` environment on `refs/heads/main` from `deploy.yml`: PowerUserAccess plus IAM limited to `invdash-*` roles carrying the boundary, a PassRole service allowlist, and denies on changing itself, the provider or boundary, assuming roles and Identity Center. Set `github_oidc_provider_arn` to reuse an existing provider | AC-2, AC-3, AC-6, IA-2, IA-5 |
| `services/data-jobs/src/app/observability.py` | Powertools helper: JSON logs with `run_id`/`source_id`, X-Ray subsegments and custom metrics. A public `health.json` publisher is not yet wired. | AU-3, AU-8, SI-4 |
| `services/data-jobs/src/app/api_keys.py` | Reads keys with a 5-minute cache, so rotations apply without a redeploy | IA-5 |
| `infra/terraform/scripts/rotate-key.sh` | Stores a regenerated key without it touching shell history or the process list | IA-5(h) |
| `services/data-jobs/src/app/http_client.py` | httpx client that refuses any host outside the provider allowlist (compensating control for SC-7(5)) | SC-7, AC-4 |
| `services/data-jobs/src/app/universe.py` | Ticker union from all users (capped at `max_user_tickers`), index ETF proxies, Alpha Vantage sentiment rotation | |
| `services/data-jobs/src/app/status_feed.py` | Builds `serving/status.json` (job, status, last/next run) with S3 conditional writes | SI-4 |
| `services/data-jobs/src/app/collectors/` | Alpaca bars and options, FINRA short interest, Kalshi odds, Atlanta/Cleveland Fed, Yahoo backfill (parsers tested with fixtures) | |
| `services/data-jobs/src/functions/` | Handlers for collectors, price backfill, dashboard snapshot, authenticated API, status feed and key rotation | |
| `infra/docker/Dockerfile`, `services/data-jobs/requirements-jobs.txt` | One arm64 job image; each function picks its handler | CM-2 |
| `services/data-jobs/tests/` | Pytest coverage for trend math, fundamentals, API, collectors, retries, status, backfill and dashboard snapshot | SA-11 |
| `services/data-jobs/scripts/add_fundamental.py` | Adds a reviewed deliveries / FSD-subscriber row to the manual fundamentals file | SI-10 |
| `infra/terraform/canary/index.js` | Checks `health.json` through CloudFront for HTTP 200, data age and stale P1 sources | SI-4, CP-2 |
| `.github/workflows/ci.yml`, `deploy.yml` | CI validation and protected production deployment via GitHub OIDC; requires repository/environment setup described in [the operations guide](../../docs/operations.md) | CM-3, RA-5, SA-11, IA-5 |

## Before you apply

1. **Use a Paid-plan AWS account.** Free-plan accounts close after 6 months.
2. **Sign in through IAM Identity Center with MFA** (IA-2(1), IA-2(2)); avoid IAM users. The audit
   bucket policy only lets an Identity Center `AdministratorAccess` role or a role named
   `<project>-terraform-deploy` delete audit objects or change its lifecycle. Deploy as one of those.
3. **Check for existing services.** One Config recorder, one GuardDuty detector and one Security Hub
   account are allowed per region. If any already exist, `terraform import` them first.
4. **Format and validate.** This code was written without access to the Terraform binary; it was
   parsed with an HCL parser and cross-checked, but run these before your first commit:

   ```sh
   terraform fmt -recursive
   terraform init -backend=false && terraform validate
   ```

## Apply

```sh
cp terraform.tfvars.example terraform.tfvars   # set alert_emails
cp backend.tf.example backend.tf               # optional: remote state in S3
terraform init
terraform plan -out tfplan
terraform apply tfplan
```

Then confirm the SNS subscription emails (one per topic per address).

## Application deployment and monitoring

The jobs module sets Lambda environments, X-Ray tracing, per-job IAM, retry/DLQ behavior and
configured schedules. The site/API/Cognito modules are part of the same Terraform root. Follow
the [Operations guide](../../docs/operations.md) to configure the protected GitHub deployment environment,
publish the UI and run the initial history load.

The CloudFront distribution is created with HTTPS redirection and security headers; CloudFront
5xx monitoring is connected to the distribution by default. The Synthetics data-freshness canary
is conditional on `health_url` and remains disabled until a non-sensitive public health document
is actually published. Do not expose `dashboard.json`, preferences, tokens or other user data to
make that check pass.

## API key rotation

| Key | Rotate every | Why |
| --- | --- | --- |
| `sam-gov` | 90 days | SAM.gov personal keys expire every 90 days |
| `alpaca-key-id`, `alpaca-secret-key` | 180 days | Account-level keys; use paper-trading keys only |
| `bls` | 365 days | BLS requires renewal at least once a year |
| `fred`, `finnhub`, `alpha-vantage`, `massive`, `bea`, `census`, `api-data-gov`, `acled-password`, `finra-client-id`, `finra-client-secret` | 365 days | Policy (keys never expire on their own) |

Change any period in `var.api_keys`. To rotate a key:

1. Regenerate it in the provider's dashboard (the reminder email has the link). If the provider allows two
   keys at once, create the new one first.
2. Run `scripts/rotate-key.sh <key>` and paste the value.
3. After the next job run succeeds, revoke the old key at the provider.
4. Rotate immediately, outside the schedule, if a key may have leaked.

Evidence for an assessor: the daily `key_rotation_status` log lines (kept 400 days), the CloudTrail
`PutParameter` events (30 months), and the `api-key-changed` security alert on every change.

## Users, preferences and data contracts (for the dashboard UI)

| Contract | Location | Fields |
| --- | --- | --- |
| Preferences API | `GET/PUT {site_api_endpoint}/prefs`, `Authorization: Bearer <Cognito access token>` | `tickers` (ordered, max 50), `pinned` (max 6, subset of tickers), `display.time_zone` (IANA), `display.updown_palette` (`green-red`, `red-green`, `blue-orange`), `version` (send back on PUT; 409 if stale), `updated_at` |
| Daily prices | `curated/prices_daily/ticker=<T>/year=<YYYY>/prices.parquet`; `serving/dashboard.json` `tickers.<T>.price_history` | `ticker, date, close` (split-adjusted), `close_raw` (traded), `adj_close` (split + dividend adjusted; charts and trends), `volume, source_id`. `close_raw`/`adj_close` may be null on rows written before price-reconcile ran |
| Trend metrics | `serving/trend_metrics/ticker=<T>/trend_metrics.parquet`, `serving/trend_metrics/latest/<T>.json` | `series_id, ticker, date, value, chg_1w, chg_1m, chg_3m, z_1w, z_1m, z_3m, range_pct_1y, trend_state, days_in_state, corr_30d, corr_90d, effect, net_pressure` |
| Fundamentals | `serving/fundamentals_quarterly.json` | `ticker, metric (gross_margin_gaap, revenue_gaap, gross_profit_gaap, deliveries, fsd_subscribers), fiscal_quarter, release_date, value, unit, source_id` |
| Refresh status | `serving/status.json` | `generated_at`, `jobs[]: job, name, status (ok, partial, failed, never_run), last_run, last_outcome, failed_sources, next_run` |

Sign-in is invite-only: create users with
`aws cognito-idp admin-create-user --user-pool-id <id> --username you@example.com`.

## Accepted risk: job Lambdas run outside a VPC

Outside a VPC the functions can reach any internet host, which falls short of SC-7(5)
(deny outbound by default) and AC-4. Compensating controls: least-privilege job roles, GuardDuty Lambda
Protection (it monitors network activity of non-VPC functions too), Inspector scanning, a host allowlist
in the HTTP client, and only public market data at stake. To close the gap, run the jobs in a VPC with
a NAT gateway and a Route 53 DNS Firewall allowlist (~$33/month) or AWS Network Firewall (~$290/month).

## Security checks

The CI workflow runs Checkov against Terraform, Dockerfiles and GitHub Actions. Review its
findings for the current branch; an earlier scan result is not evidence that later changes pass.
Accepted-risk comments in infrastructure code explain individual Checkov suppressions.

## Cost levers

| Lever | Saves | Trade-off |
| --- | --- | --- |
| `canary_rate_minutes = 60` | ~$2.50/month | Outages detected in up to 2 h instead of 30 min |
| `enable_malware_protection = false` | ~$1/month | No malware scan of downloaded files (SI-3 gap) |
| Drop `LAMBDA` from Inspector resource types | ~$0.30 per zip function | Only the container image is scanned |

## Scope

This aligns technical controls only. A FedRAMP authorization also needs a System Security Plan,
policies and procedures, a 3PAO assessment, POA&M tracking and monthly continuous-monitoring
reports. Those are out of scope for a personal system.
