# Investor Dashboard

A low-cost dashboard that combines Tesla (TSLA) and SpaceX (SPCX) prices with bond yields, rate decisions, CPI,
tariffs, geopolitical events, robotaxi permits and SpaceX launches and contracts, to support trading decisions.

## Architecture

Serverless on AWS: EventBridge Scheduler and events invoke Python Lambda jobs, which write Parquet and
serving JSON to S3. A responsive static dashboard is hosted in a private S3 bucket behind CloudFront,
authenticates through Cognito PKCE, and reads authenticated API routes for user preferences and serving
data. Terraform also defines encryption, audit, monitoring and security services.

## Repository layout

| Path | What |
| --- | --- |
| `apps/web/` | Production responsive dashboard, Cognito PKCE sign-in, preferences, market charts and data states |
| `services/data-jobs/src/` | Shared collectors and Lambda handlers |
| `services/data-jobs/tests/` | Python unit tests for collectors, API, transformations, retries and historical batching |
| `services/data-jobs/scripts/` | Data-job maintenance commands |
| `infra/terraform/` | Terraform for AWS infrastructure, site hosting, API, Cognito, jobs, audit and observability |
| `infra/docker/` | Shared Lambda job image definition |
| `data/catalog/` | Data-source catalog source, documentation and workbook |
| `design/ai-generated/` | AI-generated UI canvas and reference assets; not production site content |
| `.github/workflows/` | Pull request validation and approval-gated production deployment |
| [`docs/operations.md`](docs/operations.md) | Environment setup, deploy, historical load, refresh operations and troubleshooting |
| [`infra/terraform/README.md`](infra/terraform/README.md) | Infrastructure/security baseline and Terraform setup |

## Scope and status

The wired data path includes daily prices, Yahoo five-year daily-price backfill, trends,
fundamentals, short interest, options, status and dashboard snapshot serving. The architecture's
full source catalog is not yet implemented; the Operations guide lists current schedules,
assumptions and known gaps. The dashboard currently uses zero-build JavaScript rather than the
[architecture document](docs/architecture-infrastructure.md)'s proposed TypeScript/Vite/Svelte stack.

Validate Terraform and run tests before deployment. AWS provisioning, provider access and
production browser behavior require the corresponding AWS account and credentials; successful
local validation alone does not establish production readiness.

## Next Steps: Start the AWS deployment

The deployment workflow is already defined. It is not live in AWS yet; the AWS state bucket and GitHub OIDC role/environment must be configured first. Follow the detailed `operations guide`.

1. **Prepare Terraform state.** Create a private, versioned, encrypted S3 bucket with public access blocked. Copy `infra/terraform/backend.tf.example` to `infra/terraform/backend.tf`; it is pre-filled for bucket `invdash-tfstate-308639168050` in `us-west-1`. The state bucket must exist before Terraform can initialize.

2. **Set up GitHub OIDC and production protections.** In the AWS account, configure GitHub's OIDC provider and a deployment role trusted only for `repo:<OWNER>/<REPO>:environment:production` with audience `sts.amazonaws.com`. Give that role the permissions needed for this Terraform stack, ECR image publication, site-bucket upload, and CloudFront invalidation. In GitHub, create the `production` environment and require reviewer approval; otherwise the environment is not an approval gate.

3. **Add GitHub environment values** under Settings → Environments → `production`:
   - Variable `AWS_REGION`, matching the Terraform `region` (default `us-west-1`).
   - Secret `AWS_DEPLOY_ROLE_ARN`.
   - Secret `TF_BACKEND_CONFIG`, containing the complete S3 backend block.
   - Secret `TERRAFORM_TFVARS`, containing the non-secret Terraform settings (for example project, region, and alert email). Do not put provider API credentials in it.

   See `terraform.tfvars.example` for the configuration shape. Provider credentials belong in SSM SecureString using `rotate-key.sh`, not in GitHub runtime config or the static site.

4. **Start deployment.** Open **Actions → Deploy production → Run workflow**, select `main`, and run it. Or merge a PR to `main`: `CI` must pass first, then `Deploy production` waits for the production environment approval. On the first run it provisions the base stack if needed, builds/pushes the arm64 job image, applies the job Lambdas, generates the ignored `Web/site/config.json`, publishes the site, invalidates CloudFront, and checks the public site/config URLs.

5. **Verify and invite yourself.** From `infra/`, run `terraform output -json site` for the CloudFront URL and `terraform output -json cognito` for the user-pool ID. Invite a user with `aws cognito-idp admin-create-user --user-pool-id <user-pool-id> --username <your-email>`, then open the CloudFront URL and complete sign-in/MFA.

6. **Start the initial price history load.** After the workflow has deployed the `backfill` Lambda, invoke it with the project prefix (default `invdash`). Include the index ETF proxies; `trend_metrics` uses them as drivers:
   ```sh
   aws lambda invoke \
     --function-name invdash-backfill \
     --cli-binary-format raw-in-base64-out \
     --payload '{"tickers":["TSLA","SPCX","SPY","DIA","QQQ","IWM","XLY","ITA","SMH"]}' \
     /tmp/backfill-result.json
   cat /tmp/backfill-result.json
   ```
   Monitor `/aws/lambda/invdash-backfill` and `curated/prices_daily/_backfill/state.json` in the lake bucket. The scheduled 15-minute run resumes incomplete batches.

**Scope note:** this deploys the implemented platform path, not every collector in the architecture catalog. Five-year history is currently for daily prices; M1 and additional macro/news/regulatory/annual collectors remain outstanding, and the freshness canary is disabled until a safe public health document is implemented. The operations guide records these limits and troubleshooting steps.