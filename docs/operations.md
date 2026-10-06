# Investor Dashboard: deployment and operations

This guide describes the deployable parts currently present in this repository and calls out
work that is not yet implemented. The documented target architecture is AWS Lambda/EventBridge,
an S3 data lake, Terraform, Cognito and a CloudFront-hosted site.

## Current architecture and scope

```text
Browser --HTTPS--> CloudFront --> private S3 static site
   |                                |
   +-- Cognito PKCE sign-in         +-- config.json (public identifiers only)
   |
   +-- Bearer access token --> HTTP API + Cognito JWT authorizer
                                  |-- /prefs --> per-user DynamoDB
                                  |-- /dashboard, /status --> read-only S3 serving JSON

EventBridge Scheduler / events --> container-image Lambda jobs --> S3 raw/curated/serving
                                              |                     |
                                              +--> job status events +--> dashboard snapshot
```

Terraform provisions the encrypted and versioned data lake, audit/security services, monitoring,
API, Cognito pool, private static-site bucket and CloudFront distribution. The browser site is
zero-build HTML/CSS/JavaScript. It implements the repository's dark desktop/mobile direction;
this deliberately avoids introducing a frontend build toolchain, but differs from the architecture
document's proposed TypeScript/Vite/Svelte implementation.

The currently wired data jobs are daily Alpaca prices (`daily-prices`, emitted job ID `D4`),
Yahoo historical prices (`backfill`), trend metrics, quarterly fundamentals, short interest,
options, status-feed updates and dashboard snapshot building. The key-rotation checker is a
separate Lambda. The job map and schedules live in `infra/terraform/variables.tf`.

This is not yet the complete source catalog implementation: the architecture lists additional
hourly, macro, release-day, regulatory, weekly and annual collectors that are not wired here.
In particular, M1 is not implemented; configured trend refreshes can still run on D4 events.
Do not treat missing metrics or a successful deployment as proof that every catalog source is
being refreshed. The external freshness canary remains disabled until a safe public health
document and publisher are configured.

## Prerequisites and configuration

### Local tools

Install Terraform `>=1.10`, AWS CLI v2, Docker with Buildx, Python 3.12 and Git. Authenticate
to the intended AWS account with MFA; use a dedicated deployment role or IAM Identity Center,
not long-lived IAM user keys.

### Terraform variables and secrets

1. Copy `infra/terraform/terraform.tfvars.example` to `infra/terraform/terraform.tfvars`. Set at least the alert
   recipient email addresses and a valid SEC contact User-Agent before enabling SEC collectors.
2. For remote state, create a versioned, encrypted, private S3 state bucket, then copy
   `infra/terraform/backend.tf.example` to `infra/terraform/backend.tf` and fill in the bucket and region. Enable S3
   native locking as shown. Do not commit either generated file.
3. API credentials are not Terraform variables and must not be put in the static site or Git.
   Provision them with `infra/terraform/scripts/rotate-key.sh <key-name>`; the script stores values in
   SSM Parameter Store as SecureStrings. Grant the job roles access only to their declared keys.
4. `api_keys` in Terraform is metadata (provider, source IDs and rotation interval), not secret
   values. The static site's `config.json` contains only the API URL and public Cognito client
   identifiers; never add credentials or tokens to it.

Use environment-specific tfvars and state for staging and production. Review the plan before
applying, particularly account-level services, deletion protection, audit retention and regional
resources.

## Provision and update infrastructure

From `infra/terraform/`:

```sh
terraform fmt -recursive
terraform init
terraform validate
terraform plan -out tfplan
terraform apply tfplan
```

The static-site module creates a private, versioned S3 bucket, CloudFront Origin Access Control,
HTTPS-only CloudFront distribution, SPA fallback routing and security response headers. The site
URL, bucket name and distribution ID are available through `terraform output -json site`.
CloudFront invalidations are required after publishing a new site version.

Terraform outputs may be inspected with:

```sh
terraform output
terraform output -json site_runtime_config
terraform output -json cognito
terraform output -raw ecr_repository_url
```

Confirm infrastructure health in the AWS Console or with the relevant AWS CLI services:
CloudFormation/Terraform state, CloudFront distribution status, S3 public-access blocks,
Lambda state, EventBridge schedules, Cognito pool status, CloudWatch alarms, and the SNS
subscription confirmation emails. Each alert address gets three: the ops and security topics in the
primary region and the CloudFront alarm topic in us-east-1 (`terraform output cloudfront_alarm_topic_arn`).
The CloudFront alarm and the IAM/sign-in forwarding rules are in us-east-1, so look there in the console.

## GitHub Actions CI/CD

`.github/workflows/ci.yml` runs Python lint/tests, dependency audit, browser-JavaScript syntax
validation, Terraform format/validation, an arm64 Lambda image build and Checkov. It runs on pull
requests and pushes to `main`.

`.github/workflows/deploy.yml` runs only after a successful `main` CI push, or by manual
dispatch from `main`. Protect the GitHub `production` environment with required reviewers and
branch restrictions; the environment gate is what prevents an unreviewed production apply.
Configure:

### One-time setup

1. **Create the deploy role (administrator, once).** Terraform's `github_deploy` module creates the
   account's GitHub OIDC provider (`token.actions.githubusercontent.com`, audience
   `sts.amazonaws.com`) and the `invdash-terraform-deploy` role. The role trusts only
   `repo:ram290476/investor-dashboard:environment:production`, so branches, tags, pull requests
   and other repositories can't assume it. Apply it from a workstation signed in with
   AdministratorAccess. If the account already has a GitHub OIDC provider, set
   `github_oidc_provider_arn` instead of creating a second one.
2. **Protect the environment.** In GitHub, open Settings → Environments → `production`. Add
   **required reviewers** (Ram) and limit deployment branches to `main`. This approval is the
   only gate before `terraform apply -auto-approve` runs in CI.
3. **Add these values to the `production` environment** (environment-level, not repository-level):

| Name | Kind | Value and where it comes from |
| --- | --- | --- |
| `AWS_REGION` | Variable | `us-west-1` (must match Terraform `region`). |
| `AWS_DEPLOY_ROLE_ARN` | Secret | `terraform output -raw github_deploy_role_arn`, i.e. `arn:aws:iam::308639168050:role/invdash-terraform-deploy`. |
| `TF_BACKEND_CONFIG` | Secret | The full contents of the local `infra/terraform/backend.tf` (the `backend "s3"` block for bucket `invdash-tfstate-308639168050`, key `investor-dashboard/infra.tfstate`, region `us-west-1`, `encrypt = true`, `use_lockfile = true`). |
| `TERRAFORM_TFVARS` | Secret | The full contents of the local `infra/terraform/terraform.tfvars`, **plus** `sec_user_agent = "investor-dashboard <contact email>"`. Leave out `jobs_image_uri`; the workflow sets it for each image. Never put provider API keys here; they live in SSM (`rotate-key.sh`). |

The workflow checks that all four exist and fails with a clear message if one is missing.

### What the deploy role may do

The workflow runs `terraform apply` for the whole stack, so the role needs broad rights. They are
limited where Terraform allows:

- **AWS managed `PowerUserAccess`:** every service except IAM, Organizations and Account. This
  covers S3, KMS, Lambda, ECR push, CloudFront invalidation, Config, GuardDuty, Security Hub and
  the rest of the stack.
- **IAM limited to this project (inline `iam-for-this-stack`):**
  - read IAM;
  - manage `invdash-*` roles and policies;
  - attach only `AWS_ConfigRole` or `invdash-*` policies;
  - pass `invdash-*` roles only to the services the stack uses (Lambda, Scheduler, Config, S3,
    EventBridge, GuardDuty malware protection, Synthetics);
  - manage the account password policy.
- **No self-modification:** an explicit deny blocks every change to the
  `invdash-terraform-deploy` role and the GitHub OIDC provider. Widening CI's own access always
  needs an administrator apply.
- **Remaining risk:**
  - Anyone who gets a commit through review to `main` *and* an approved `production` run can still
    put an inline policy on an `invdash-*` role. That is effectively administrator-level within the
    account.
  - Keep required reviewers on.
  - The stricter alternatives are an IAM permissions boundary on all `invdash-*` roles, or a
    narrow publish-only role with Terraform applied by an administrator (see the PR that added
    this).
- **The role is named `invdash-terraform-deploy` on purpose:** the audit and config bucket
  policies allow only this role and AdministratorAccess SSO roles to change their lifecycle rules.

The deployment performs a base Terraform apply only when the ECR repository is not yet in state,
builds/pushes an immutable arm64 image, applies the job Lambdas with that image, writes the
runtime configuration to an ignored `apps/web/config.json`, publishes the static site and
invalidates CloudFront. It then checks the site and generated configuration over HTTPS.
No application secrets are printed or shipped to the browser.

For infrastructure-only local changes, use the Terraform plan/apply flow above. Failed CI runs
stop deployment. A failed deployment reports a failed workflow; Terraform does not automatically
roll back. Fix the cause or revert the application commit and rerun the protected deployment.

## Initial five-year historical price load

The historical loader is independent of the regular daily refresh. It starts at the current
UTC date and requests half-open date ranges backwards in 90-calendar-day batches (defaults:
five years, five requests per invocation). Each ticker has a persisted cursor in
`curated/prices_daily/_backfill/state.json`. Each batch is upserted into the ticker's yearly
partitions (see [Price storage layout](#price-storage-layout)), so reruns replace the same rows
instead of duplicating records and never overwrite a `daily-prices` close for the same day. The
scheduled 15-minute invocation continues incomplete work; individual invocations are bounded to
avoid API timeouts.

After the image and `backfill` Lambda are deployed, start the initial load for the base tickers
and the index ETF proxies (`BASE_TICKERS` and `INDEX_PROXIES` in `services/data-jobs/src/app/universe.py`).
`trend_metrics` uses the ETF histories as drivers, so it needs them too:

```sh
aws lambda invoke \
  --function-name invdash-backfill \
  --cli-binary-format raw-in-base64-out \
  --payload '{"tickers":["TSLA","SPCX","SPY","DIA","QQQ","IWM","XLY","ITA","SMH"]}' \
  /tmp/backfill-result.json
cat /tmp/backfill-result.json
```

Replace `invdash` with the Terraform `project` value. The first run requests the newest available
window, then works backward. It is safe to invoke again: active tickers keep their current
cursor, and a completed ticker is not requeued by duplicate events. A TickerAdded event starts
history for a new user ticker automatically; ETF proxies are not user tickers, so if you change
`INDEX_PROXIES`, invoke the backfill with the new symbols. The weekday `daily-prices` (D4) run
collects the base tickers, user tickers and ETF proxies going forward.

Monitor progress and failures:

```sh
aws s3 cp s3://<lake-bucket>/curated/prices_daily/_backfill/state.json -
aws logs tail /aws/lambda/invdash-backfill --follow
```

The state reports each ticker's cursor, batch count, rows stored and completion status. Logs
include requested/received/processed/rejected/stored counts, no-data boundaries and failed
tickers. CloudWatch custom metrics include requested days and backfill row counts. A failed
request leaves its cursor unchanged; the scheduled run retries it. Do not delete the checkpoint
to resume. Changing `backfill_years` while a load is active is rejected; use an explicit
`{"tickers":["TSLA","SPCX","SPY","DIA","QQQ","IWM","XLY","ITA","SMH"],"force":true}` event only when a deliberate full restart is intended.

The loader reports complete after reaching the five-year cutoff or an explicit provider
no-data boundary. It cannot guarantee five years for a newly listed or unsupported symbol.
Verify the earliest and latest `date` values in each ticker's yearly parquet objects and inspect
`complete`, `earliest_date`, `no_data_before`, and `failed_tickers` in the checkpoint.

## Price storage layout

Daily prices are stored as one Parquet object per ticker per calendar year:

```text
curated/prices_daily/ticker=<TICKER>/year=<YYYY>/prices.parquet
```

`daily-prices` (D4) and `backfill` write through `lake.upsert_prices`, which reads the
partition, merges the new rows, keeps one row per `(ticker, date)` and writes it back with an S3
conditional write (`If-Match` / `If-None-Match`), retrying if another job changed it in between.
When both sources have a day, the `daily-prices` row (`source_id` `DS-02`, Alpaca) wins over the
backfill row (`DS-05`, Yahoo). `dashboard-build` and `trend-metrics` read with `lake.read_prices`,
so a five-year ticker costs about six `GetObject` calls instead of one per trading day.

### Price columns and corporate actions

| Column | Meaning | Written by |
| --- | --- | --- |
| `close_raw` | Actual traded close; never re-adjusted | `daily-prices` (Alpaca `adjustment=raw`); `price-reconcile` derives it for backfill rows |
| `close` | Split-adjusted as of the last write or rebuild (Yahoo's `close`) | `daily-prices` (`adjustment=split`), `backfill`, `price-reconcile` |
| `adj_close` | Split- and dividend-adjusted as of the last write or rebuild (Yahoo's `adjclose`) | `daily-prices` (`adjustment=all`), `backfill`, `price-reconcile` |

Charts, 1-day returns and `trend-metrics` use `adj_close` (falling back to `close` on rows that
lack it); the dashboard shows `close_raw` (falling back to `close`) as the price. `daily-prices`
only re-adjusts its own 7-day window, so `price-reconcile` runs every weekday at 19:15 ET. For each
collected ticker it reads the last 35 days of Yahoo split/dividend events and rebuilds the
ticker's full history when it finds an event it has not seen that falls after the first stored
day, or when stored rows lack `close_raw`/`adj_close` (backfill rows and rows written before these
columns existed). A rebuild fetches Yahoo daily history from the first stored day, keeps the
`daily-prices` `close_raw`, recomputes `close` with the split ratios and `adj_close` with Yahoo's
dividend factor, and upserts every yearly partition of that ticker. Seen events are kept in
`curated/prices_daily/_reconcile/state.json` (`seen_events`, `last_rebuild`, `last_reason` per
ticker). A Yahoo failure for one ticker marks the run partial and is retried the next evening.
To force a rebuild of one ticker, remove its entry from the state file and invoke the job:

```sh
aws lambda invoke --function-name invdash-price-reconcile --payload '{}' /tmp/price-reconcile.json
```

Deployments before this layout wrote one object per ticker-day
(`ticker=<T>/date=<D>/daily.parquet`) and one per backfill batch
(`ticker=<T>/batch_start=<D>/batch_end=<D>/prices_daily.parquet`). Readers still include and
dedupe those objects, so the new code is correct before and after compaction; compaction only
restores the read savings. To compact an existing lake once, after deploying this version and
with operator credentials (`s3:ListBucket`, `GetObject`, `PutObject`, `DeleteObject` on the lake
and the data KMS key):

```sh
cd services/data-jobs
pip install -r requirements-jobs.txt boto3
python scripts/compact_price_partitions.py --bucket <lake-bucket> --dry-run        # counts only
python scripts/compact_price_partitions.py --bucket <lake-bucket>                  # write yearly objects
python scripts/compact_price_partitions.py --bucket <lake-bucket> --delete-legacy  # verify, then delete legacy
```

The script is idempotent and deletes legacy objects only after confirming every legacy
`(ticker, date)` is present in the yearly objects. The lake bucket is versioned, so deleted
objects remain recoverable as noncurrent versions. Avoid running it at the same time as a
`backfill` or `daily-prices` run; conditional writes make that safe, but slower.

## Regular data refresh and manual runs

The default schedules are configured in `infra/terraform/variables.tf` and use
`America/New_York` in `infra/terraform/modules/jobs/main.tf`. Currently wired schedules include:

| Job | Schedule / trigger | Notes |
| --- | --- | --- |
| `daily-prices` | Weekdays 16:45 ET | Alpaca daily bars for base, user and index ETF proxy tickers (raw, split- and fully adjusted closes for the last 7 days); NYSE weekends/holidays are skipped. |
| `q1-fundamentals` | Mondays 08:30 ET | Weekly safety refresh. |
| `short-interest` | Weekdays 18:30 ET | FINRA only publishes on settlement cadence. |
| `options-daily` | Weekdays 16:50 ET | Disabled by default through `enable_options_daily`; validate the feed before enabling. |
| `backfill` | Every 15 minutes and on ticker-added events | Resumes only persisted incomplete work. |
| `price-reconcile` | Weekdays 19:15 ET | Checks Yahoo for new splits/dividends and rewrites a ticker's adjusted history when needed (job ID `RECONCILE`). |
| `trend-metrics` | Job events from D4, M1 or RECONCILE | M1's collector is not yet implemented. |
| `dashboard-build` | D4, trend, fundamentals, short-interest, options, backfill and reconcile events | Publishes `serving/dashboard.json`. |
| `status-feed` | Any job-finished event | Publishes `serving/status.json`. |

Use a one-off Lambda invocation for manual refreshes, for example:

```sh
aws lambda invoke --function-name invdash-daily-prices --payload '{}' /tmp/daily-prices.json
```

To change schedules, update the relevant job entry in `infra/terraform/variables.tf`, inspect
`terraform plan`, then apply. Do not create a second schedule manually for the same job.
Check `/status` in the signed-in dashboard or `serving/status.json` for the latest run and
failure state. The `/dashboard` and `/status` API routes require a Cognito access token.

## Build, run and verify the web application

For local UI work, create a local runtime config based on `apps/web/config.example.json` or
generate it from deployed Terraform outputs:

```sh
terraform -chdir=infra/terraform output -json site_runtime_config > apps/web/config.json
python3 -m http.server 8080 --directory apps/web
```

Ensure `http://localhost:8080/` is in the Cognito callback/logout and API CORS settings for local
development. The root callback is compatible with Python's basic static server. Production uses
the CloudFront `/auth/callback` path and its SPA fallback. Open `http://localhost:8080/`, sign in
with an invited Cognito user and complete MFA. The application keeps access tokens in memory,
uses PKCE, and sends them only as Bearer authorization headers. `config.json` is git-ignored.

For deployment, use the protected Actions workflow or publish the generated config and site
using the S3 bucket and CloudFront distribution in the `site` output. Verify the home page,
`/config.json`, sign-in, `/prefs`, `/dashboard` and `/status`.

## Users, monitoring and rollback

Create invite-only Cognito users with the pool ID from `terraform output -json cognito`:

```sh
aws cognito-idp admin-create-user --user-pool-id <user-pool-id> --username <email>
```

Require the invited user to complete the temporary-password and MFA setup. Never disable the
JWT authorizer to troubleshoot a browser sign-in issue.

Use CloudWatch dashboards, alarms and the SNS ops topic from Terraform outputs to review Lambda
errors/throttles, API 5xx responses, data freshness/job status, dead-letter messages, CloudFront
errors and deployment health. The audit bucket (CloudTrail) and the config bucket (AWS Config history) retain infrastructure events.
CloudFront hosting and the API are monitored; the Synthetics freshness canary remains off until
the pipeline exposes a deliberately public, non-sensitive health document.

For application rollback, revert to a known-good commit and rerun the protected deployment. For
a manual S3-site rollback, use S3 object versions to restore the previous `index.html`, CSS,
JavaScript and config, then invalidate CloudFront. For a Lambda image rollback, set
`jobs_image_uri` to a previously published immutable ECR tag and apply Terraform. Do not delete
Terraform state or the data-lake checkpoint during rollback.

## Troubleshooting

| Symptom | Checks and recovery |
| --- | --- |
| Backfill has not advanced | Inspect checkpoint and Lambda logs; check Yahoo status/rate limits and function timeout. Retry the same event; keep state. |
| API rate limiting / throttles | Review `429`, `Retry-After`, retry logs and per-provider schedules. Reduce batch/work-per-run settings or space runs; do not raise concurrency blindly. |
| Missing or stale prices | Check D4 Lambda state, Alpaca SSM credentials, schedule timezone, source failures and the S3 `ticker=<T>/year=<YYYY>` partitions; then run the collector manually. |
| Dashboard API returns 503 | The dashboard build has not published `serving/dashboard.json`, or status is missing. Check build permissions/logs, upstream events and S3 serving keys. |
| Preferences API returns 409 | Another tab updated the version; reload `/prefs` and retry. A 401 means sign-in expired; sign in again. |
| API returns 5xx | Review API Gateway access logs and `invdash-prefs-api` Lambda logs; verify scoped DynamoDB role, KMS decrypt and S3 read permissions. |
| Site does not load or sign-in loops | Check `config.json`, callback/logout URLs, CORS origin, Cognito domain/client, CloudFront status and browser network errors. Invalidate after config changes. |
| Collector fails or goes to DLQ | Inspect structured Lambda logs and `failed_sources`, verify SSM credentials and allowlisted host, fix the root cause, then manually replay an idempotent event. |
| Database/DynamoDB failure | Check table status, KMS key state, API Lambda role assumptions and CloudWatch API alarm. Preferences use point-in-time recovery. |
| Infrastructure apply fails | Read the first Terraform error, inspect state lock and AWS service quotas, then rerun `terraform plan`; do not force-unlock unless the lock owner is confirmed stopped. |
| CI/CD fails | Start with the failed job logs. A failed check never deploys. For an AWS failure verify the production environment approval, OIDC subject trust, role permissions, tfvars/backend config and ECR/site outputs. |

## Known gaps and assumptions

* The full source catalog and all documented collection frequencies have not been implemented.
  Existing job-map entries and status-feed job names are not evidence that collectors exist.
* The initial historical loader is for daily prices only; it does not backfill macro, news,
  fundamentals or other data sources.
* The UI is a zero-build vanilla JavaScript implementation rather than the architecture
  document's proposed TypeScript/Vite/Svelte stack.
* No public freshness document is currently published, so the external Synthetics canary is
  intentionally disabled. Do not point it at authenticated user data.
* The GitHub deployment role, OIDC provider, remote state bucket and protected `production`
  environment must be configured by the account/repository owner before the automated
  deployment workflow can run.
* Live AWS provisioning, external-provider behavior and browser-device testing cannot be
  confirmed without the corresponding credentials, AWS account and browser/runtime tooling.
