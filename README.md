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
| `Web/site/` | Responsive dashboard, Cognito PKCE sign-in, preferences, market charts and data states |
| `infra/` | Terraform for AWS infrastructure, site hosting, API, Cognito, jobs, audit and observability |
| `infra/app/` | Shared collectors, S3 helpers, structured logging/tracing/metrics, API key reader |
| `infra/functions/` | Collector, backfill, dashboard build, API and key-rotation handlers |
| `infra/tests/` | Python unit tests for collectors, API, transformations, retries and historical batching |
| `.github/workflows/` | Pull request validation and approval-gated production deployment |
| [`OPERATIONS.md`](OPERATIONS.md) | Environment setup, deploy, historical load, refresh operations and troubleshooting |
| [`infra/README.md`](infra/README.md) | Infrastructure/security baseline and Terraform setup |

## Scope and status

The wired data path includes daily prices, Yahoo five-year daily-price backfill, trends,
fundamentals, short interest, options, status and dashboard snapshot serving. The architecture's
full source catalog is not yet implemented; the Operations guide lists current schedules,
assumptions and known gaps. The dashboard currently uses zero-build JavaScript rather than the
architecture document's proposed TypeScript/Vite/Svelte stack.

Validate Terraform and run tests before deployment. AWS provisioning, provider access and
production browser behavior require the corresponding AWS account and credentials; successful
local validation alone does not establish production readiness.
