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
architecture document's proposed TypeScript/Vite/Svelte stack.

Validate Terraform and run tests before deployment. AWS provisioning, provider access and
production browser behavior require the corresponding AWS account and credentials; successful
local validation alone does not establish production readiness.
