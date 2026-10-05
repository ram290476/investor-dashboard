# Investor Dashboard

A low-cost dashboard that combines Tesla (TSLA) and SpaceX (SPCX) prices with bond yields, rate decisions, CPI,
tariffs, geopolitical events, robotaxi permits and SpaceX launches and contracts, to support trading decisions.

## Architecture

Serverless on AWS: EventBridge Scheduler starts one Python Lambda per batch job (hourly to annual, skipping
weekends and regional holidays), jobs write Parquet to S3 (raw / curated / serving), and a static dashboard on
CloudFront reads small prebuilt JSON files. Base stack ~$0.25/month; with FedRAMP Moderate (NIST SP 800-53 Rev 5)
technical controls ~$21-35/month.

## Repository layout

| Path | What |
| --- | --- |
| `infra/` | Terraform starter: KMS keys, data lake with DR replica, CloudTrail + Config audit logging, GuardDuty / Security Hub / Inspector, alerting, observability with SLOs, API key rotation reminders. Start with [`infra/README.md`](infra/README.md). |
| `infra/app/` | Python helpers for job Lambdas: structured logs, traces, metrics, API key reader |
| `infra/functions/` | Daily API key rotation check |
| `infra/canary/` | External health check (CloudWatch Synthetics) |
| `.github/workflows/ci.yml` | fmt, validate, Checkov, ruff, shellcheck, pip-audit; approval-gated apply via OIDC |

## Status

- The Terraform has not yet been run with the Terraform binary: run `terraform fmt -recursive` and
  `terraform validate` in `infra/` before the first apply.
- Not built yet: the collector and build Lambdas, the CloudFront site and Cognito sign-in.
- All data sources use free tiers; no API keys or secrets are stored in this repository.
