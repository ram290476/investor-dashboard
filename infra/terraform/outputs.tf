output "lake_bucket_name" {
  description = "Data lake bucket for raw/, curated/ and serving/ prefixes."
  value       = module.data_lake.lake_bucket_name
}

output "replica_bucket_arn" {
  description = "Cross-region replica of the data lake (DR)."
  value       = module.data_lake.replica_bucket_arn
}

output "audit_bucket_name" {
  description = "Object-locked bucket holding CloudTrail and Config records."
  value       = module.audit_logging.audit_bucket_name
}

output "ecr_repository_url" {
  description = "Push the job container image here (immutable tags, continuous scanning)."
  value       = module.data_lake.ecr_repository_url
}

output "job_dlq_arn" {
  description = "Use as each Lambda's on-failure destination."
  value       = module.data_lake.dlq_arn
}

output "data_key_arn" {
  description = "KMS key for app data, queues, topics and the container image."
  value       = module.kms.data_key_arn
}

output "ops_topic_arn" {
  description = "SNS topic for operational alerts."
  value       = module.alerting.ops_topic_arn
}

output "security_topic_arn" {
  description = "SNS topic for security alerts."
  value       = module.alerting.security_topic_arn
}

output "lambda_environment" {
  description = "Environment variables every job Lambda should set so logs, traces and metrics line up."
  value = {
    POWERTOOLS_SERVICE_NAME      = var.project
    POWERTOOLS_METRICS_NAMESPACE = var.metrics_namespace
    POWERTOOLS_LOG_LEVEL         = "INFO"
    AWS_USE_FIPS_ENDPOINT        = "true"
    LAKE_BUCKET                  = module.data_lake.lake_bucket_name
  }
}

output "api_key_path" {
  description = "SSM path holding provider API keys. Job roles need ssm:GetParameter on <path>/* and kms:Decrypt on the data key."
  value       = module.api_keys.key_path
}

output "key_rotation_check_function" {
  description = "Run it on demand: aws lambda invoke --function-name <this> /dev/stdout"
  value       = module.api_keys.checker_function_name
}

output "cognito" {
  description = "Values the site needs for sign-in (PKCE authorization-code flow)."
  value = {
    user_pool_id  = module.site_auth.user_pool_id
    client_id     = module.site_auth.client_id
    issuer_url    = module.site_auth.issuer_url
    hosted_ui_url = module.site_auth.hosted_ui_url
  }
}

output "site_api_endpoint" {
  description = "Base URL for the authenticated /prefs, /dashboard and /status routes."
  value       = module.user_prefs.api_endpoint
}

output "site" {
  description = "Private S3/CloudFront static dashboard hosting."
  value = {
    url             = module.site_hosting.url
    bucket_name     = module.site_hosting.bucket_name
    distribution_id = module.site_hosting.distribution_id
  }
}

output "site_runtime_config" {
  description = "Write this JSON object to apps/web/config.json before publishing the static site."
  value = {
    apiBaseUrl    = module.user_prefs.api_endpoint
    cognitoDomain = module.site_auth.hosted_ui_url
    clientId      = module.site_auth.client_id
    callbackUrl   = "${module.site_hosting.url}/auth/callback"
    logoutUrl     = "${module.site_hosting.url}/"
  }
}

output "user_prefs_table" {
  value = module.user_prefs.table_name
}

output "job_functions" {
  description = "Job Lambdas created from jobs_image_uri (empty until the image is set)."
  value       = module.jobs.function_names
}

output "cloudfront_alarm_topic_arn" {
  description = "us-east-1 topic for the CloudFront 5xx alarm (null when enable_cloudfront_alarms is false). Recipients confirm it separately."
  value       = module.us_east_1.cloudfront_topic_arn
}
