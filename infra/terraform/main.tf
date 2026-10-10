locals {
  name                  = var.project
  trail_name            = "${var.project}-audit-trail"
  security_alert_emails = length(var.security_alert_emails) > 0 ? var.security_alert_emails : var.alert_emails
}

# AC-6: ceiling for every IAM role below; the deploy role may only create or edit roles that carry it.
module "workload_boundary" {
  source = "./modules/workload_boundary"

  name = local.name
}

# SC-12, SC-13, SC-28: customer-managed keys with yearly rotation.
module "kms" {
  source = "./modules/kms"
  providers = {
    aws    = aws
    aws.dr = aws.dr
  }

  name       = local.name
  trail_name = local.trail_name
}

# Data lake with versioning, encryption, and cross-region replication (CP-6, CP-9, SC-28).
module "data_lake" {
  source = "./modules/data_lake"

  permissions_boundary_arn = module.workload_boundary.arn
  providers = {
    aws    = aws
    aws.dr = aws.dr
  }

  name            = local.name
  data_key_arn    = module.kms.data_key_arn
  replica_key_arn = module.kms.replica_key_arn
}

# CloudTrail + AWS Config into a locked audit bucket (AU-2, AU-3, AU-9, AU-11, AU-12, CM-8).
module "audit_logging" {
  source = "./modules/audit_logging"

  permissions_boundary_arn = module.workload_boundary.arn

  name             = local.name
  trail_name       = local.trail_name
  audit_key_arn    = module.kms.audit_key_arn
  lake_bucket_arn  = module.data_lake.lake_bucket_arn
  retention_days   = var.audit_retention_days
  object_lock_mode = var.audit_object_lock_mode
}

# GuardDuty, Security Hub (NIST 800-53 Rev 5), Inspector, Access Analyzer, account guardrails
# (CA-7, RA-5, SI-3, SI-4, AC-6, CM-6).
module "security_services" {
  source = "./modules/security_services"

  permissions_boundary_arn = module.workload_boundary.arn

  name                      = local.name
  lake_bucket_name          = module.data_lake.lake_bucket_name
  data_key_arn              = module.kms.data_key_arn
  enable_malware_protection = var.enable_malware_protection
  config_recorder_status_id = module.audit_logging.config_recorder_status_id # Security Hub after Config
}

# SNS topics and EventBridge rules for security and ops events (IR-4, IR-5, IR-6, SI-4(5)).
module "alerting" {
  source = "./modules/alerting"

  name                  = local.name
  data_key_arn          = module.kms.data_key_arn
  alert_emails          = var.alert_emails
  security_alert_emails = local.security_alert_emails
}

# Logs, metrics, alarms, SLOs, dashboards, external canary, budget (AU-6, SI-4, CP-2, SA-9).
module "observability" {
  source = "./modules/observability"

  permissions_boundary_arn = module.workload_boundary.arn

  name                  = local.name
  audit_key_arn         = module.kms.audit_key_arn
  data_key_arn          = module.kms.data_key_arn
  function_names        = var.function_names
  log_retention_days    = var.log_retention_days
  metrics_namespace     = var.metrics_namespace
  ops_topic_arn         = module.alerting.ops_topic_arn
  dlq_name              = module.data_lake.dlq_name
  health_url            = var.health_url
  canary_runtime        = var.canary_runtime
  canary_rate_minutes   = var.canary_rate_minutes
  monthly_budget_usd    = var.monthly_budget_usd
  budget_emails         = var.alert_emails
  extra_log_group_names = concat(module.jobs.log_group_names, module.user_prefs.log_group_names)
}

# What must live in us-east-1: the CloudFront 5xx alarm (with its own topic and key) and
# forwarding of IAM and root/global sign-in events to this region's alerting rules (SI-4, AC-2(4)).
module "us_east_1" {
  source = "./modules/us_east_1"

  permissions_boundary_arn = module.workload_boundary.arn
  providers = {
    aws = aws.us_east_1
  }

  name                       = local.name
  alert_emails               = var.alert_emails
  enable_cloudfront_alarms   = var.enable_cloudfront_alarms
  cloudfront_distribution_id = var.cloudfront_distribution_id != "" ? var.cloudfront_distribution_id : module.site_hosting.distribution_id
  primary_region             = var.region
  forward_event_patterns     = module.alerting.global_event_patterns
}

# Provider API keys in SSM (encrypted with the data key) plus a daily rotation
# check that emails reminders before each key is due (IA-5, IA-5(h), SC-28).
module "api_keys" {
  source = "./modules/api_keys"

  permissions_boundary_arn = module.workload_boundary.arn

  name               = local.name
  data_key_arn       = module.kms.data_key_arn
  audit_key_arn      = module.kms.audit_key_arn
  security_topic_arn = module.alerting.security_topic_arn
  log_retention_days = var.log_retention_days
  api_keys           = var.api_keys
  remind_days        = var.key_reminder_days
  check_schedule     = var.key_check_schedule
  check_timezone     = var.key_check_timezone

  reserved_concurrency = var.lambda_reserved_concurrency
}

# Dashboard sign-in: invite-only Cognito pool, MFA required, PKCE web client (IA-2, AC-7, AC-12).
module "site_auth" {
  source = "./modules/site_auth"

  name = local.name
  callback_urls = distinct(concat(
    var.site_callback_urls,
    compact([
      "${module.site_hosting.url}/auth/callback",
      var.site_domain == "" ? "" : "https://${var.site_domain}/auth/callback",
    ]),
  ))
  logout_urls = distinct(concat(
    var.site_logout_urls,
    compact([
      "${module.site_hosting.url}/",
      var.site_domain == "" ? "" : "https://${var.site_domain}/",
    ]),
  ))
  branding_version = var.cognito_login_branding_version
}

module "site_hosting" {
  source = "./modules/site"
  providers = {
    aws.us_east_1 = aws.us_east_1
  }

  name        = local.name
  region      = var.region
  site_domain = var.site_domain
}

# Per-user preferences (DynamoDB) and the site API (HTTP API + JWT authorizer), with
# IAM-enforced per-user isolation (AC-3, AC-6, SC-28, CP-9).
module "user_prefs" {
  source               = "./modules/user_prefs"
  max_tickers_per_user = var.max_tickers_per_user

  permissions_boundary_arn = module.workload_boundary.arn

  name               = local.name
  data_key_arn       = module.kms.data_key_arn
  audit_key_arn      = module.kms.audit_key_arn
  log_retention_days = var.log_retention_days
  cognito_issuer_url = module.site_auth.issuer_url
  cognito_client_id  = module.site_auth.client_id
  site_origins = distinct(concat(
    var.site_origins,
    compact([
      module.site_hosting.url,
      var.site_domain == "" ? "" : "https://${var.site_domain}",
    ]),
  ))
  lake_bucket_name     = module.data_lake.lake_bucket_name
  lake_bucket_arn      = module.data_lake.lake_bucket_arn
  ops_topic_arn        = module.alerting.ops_topic_arn
  reserved_concurrency = var.lambda_reserved_concurrency
}

# Container-image jobs: trend metrics, quarterly fundamentals, short interest, options,
# ticker backfill, status feed. Created once jobs_image_uri is set.
module "jobs" {
  source = "./modules/jobs"

  permissions_boundary_arn = module.workload_boundary.arn

  name      = local.name
  image_uri = var.jobs_image_uri
  jobs = {
    for job_name, job in var.jobs : job_name => job_name == "backfill" ? merge(job, {
      schedule = var.backfill_resume_schedule
    }) : job
  }
  lake_bucket_name           = module.data_lake.lake_bucket_name
  lake_bucket_arn            = module.data_lake.lake_bucket_arn
  data_key_arn               = module.kms.data_key_arn
  audit_key_arn              = module.kms.audit_key_arn
  dlq_arn                    = module.data_lake.dlq_arn
  prefs_table_name           = module.user_prefs.table_name
  prefs_collector_policy_arn = module.user_prefs.collector_read_policy_arn
  prefs_event_source         = module.user_prefs.event_source
  api_key_path               = module.api_keys.key_path
  log_retention_days         = var.log_retention_days
  reserved_concurrency       = var.lambda_reserved_concurrency

  base_environment = {
    POWERTOOLS_SERVICE_NAME      = var.project
    POWERTOOLS_METRICS_NAMESPACE = var.metrics_namespace
    POWERTOOLS_LOG_LEVEL         = "INFO"
    AWS_USE_FIPS_ENDPOINT        = "true"
    LAKE_BUCKET                  = module.data_lake.lake_bucket_name
  }

  extra_environment = {
    MAX_USER_TICKERS     = tostring(var.max_user_tickers)
    OPTIONS_ENABLED      = tostring(var.enable_options_daily)
    SEC_USER_AGENT       = var.sec_user_agent
    ATLANTA_MPT_URL      = var.atlanta_mpt_url
    KALSHI_SERIES        = var.kalshi_series
    BACKFILL_YEARS       = tostring(var.backfill_years)
    BACKFILL_BATCH_DAYS  = tostring(var.backfill_batch_days)
    BACKFILL_MAX_BATCHES = tostring(var.backfill_max_batches)
  }
}

# GitHub Actions deploy role via OIDC, limited to this repository's production environment
# (IA-2, AC-6, CM-3). Changes to this module must be applied by an administrator.
module "github_deploy" {
  source = "./modules/github_deploy"
  count  = var.enable_github_deploy ? 1 : 0

  name                  = local.name
  github_repository     = var.github_repository
  github_owner_id       = var.github_repository_owner_id
  github_repository_id  = var.github_repository_id
  github_environment    = var.github_environment
  create_oidc_provider  = var.github_oidc_provider_arn == ""
  oidc_provider_arn     = var.github_oidc_provider_arn
  workload_boundary_arn = module.workload_boundary.arn
}
