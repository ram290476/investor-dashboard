terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
    archive = {
      source = "hashicorp/archive"
    }
  }
}

variable "name" {
  type = string
}

variable "audit_key_arn" {
  type = string
}

variable "data_key_arn" {
  type = string
}

variable "function_names" {
  type = list(string)
}

variable "log_retention_days" {
  type = number
}

variable "extra_log_group_names" {
  description = "Log groups created by other modules (jobs, site API) to include in saved queries."
  type        = list(string)
  default     = []
}

variable "metrics_namespace" {
  type = string
}

variable "ops_topic_arn" {
  type = string
}

variable "dlq_name" {
  type = string
}

variable "health_url" {
  type = string
}

variable "canary_runtime" {
  type = string
}

variable "canary_rate_minutes" {
  type = number
}

variable "monthly_budget_usd" {
  type = number
}

variable "budget_emails" {
  type = list(string)
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  partition   = data.aws_partition.current.partition
  region      = data.aws_region.current.region
  ns          = var.metrics_namespace
  canary_on   = var.health_url != ""
  canary_name = substr("${var.name}-health", 0, 21)

  # Service-level objectives (see the architecture doc, Reliability section).
  slo_availability = 99.5 # % of canary checks that pass, per 30 days
  slo_freshness    = 0.99 # share of P1 sources within their max age, business hours
}

# ---------------------------------------------------------------------------
# Log groups: pre-created so retention and KMS apply from the first run
# (AU-4 storage capacity, AU-9 protection, AU-11 retention).
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = toset(var.function_names)
  name              = "/aws/lambda/${each.value}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.audit_key_arn
}

# Saved Logs Insights queries for audit review (AU-6) and troubleshooting.
resource "aws_cloudwatch_query_definition" "failed_runs" {
  name            = "${var.name}/failed-source-runs"
  log_group_names = concat([for lg in aws_cloudwatch_log_group.lambda : lg.name], var.extra_log_group_names)
  query_string    = <<-EOT
    fields @timestamp, job, source_id, run_id, error_type, error
    | filter event = "source_run" and outcome = "failure"
    | sort @timestamp desc
    | limit 200
  EOT
}

resource "aws_cloudwatch_query_definition" "slow_sources" {
  name            = "${var.name}/slowest-sources"
  log_group_names = concat([for lg in aws_cloudwatch_log_group.lambda : lg.name], var.extra_log_group_names)
  query_string    = <<-EOT
    filter event = "source_run"
    | stats count(*) as runs, avg(duration_ms) as avg_ms, max(duration_ms) as max_ms,
            sum(outcome = "failure") as failures by source_id
    | sort avg_ms desc
  EOT
}

resource "aws_cloudwatch_query_definition" "trace_a_run" {
  name            = "${var.name}/trace-one-run"
  log_group_names = concat([for lg in aws_cloudwatch_log_group.lambda : lg.name], var.extra_log_group_names)
  query_string    = <<-EOT
    fields @timestamp, level, event, job, source_id, outcome, rows, duration_ms, xray_trace_id
    | filter run_id = "PASTE_RUN_ID"
    | sort @timestamp asc
  EOT
}

# ---------------------------------------------------------------------------
# Alarms -> ops topic. Kept at 11 to stay near the 10-alarm free tier.
# ---------------------------------------------------------------------------
locals {
  app_dims = { service = var.name } # Powertools adds this dimension; keep it constant across functions.

  base_alarms = {
    lambda-errors = {
      description = "Any job Lambda errored in two consecutive 15-minute windows"
      namespace   = "AWS/Lambda", metric = "Errors", stat = "Sum", dims = jsonencode({})
      period      = 900, evals = 2, datapoints = 2
      op          = "GreaterThanOrEqualToThreshold", threshold = 1, missing = "notBreaching"
    }
    lambda-throttles = {
      description = "Lambda throttled (concurrency cap reached)"
      namespace   = "AWS/Lambda", metric = "Throttles", stat = "Sum", dims = jsonencode({})
      period      = 900, evals = 1, datapoints = 1
      op          = "GreaterThanOrEqualToThreshold", threshold = 1, missing = "notBreaching"
    }
    dlq-not-empty = {
      description = "A failed job run is waiting in the dead-letter queue"
      namespace   = "AWS/SQS", metric = "ApproximateNumberOfMessagesVisible", stat = "Maximum"
      dims        = jsonencode({ QueueName = var.dlq_name })
      period      = 300, evals = 1, datapoints = 1
      op          = "GreaterThanThreshold", threshold = 0, missing = "notBreaching"
    }
    failed-runs = {
      description = "Two or more source runs failed within an hour"
      namespace   = local.ns, metric = "FailedRuns", stat = "Sum", dims = jsonencode(local.app_dims)
      period      = 3600, evals = 1, datapoints = 1
      op          = "GreaterThanOrEqualToThreshold", threshold = 2, missing = "notBreaching"
    }
    stale-p1-source = {
      description = "A P1 source has been stale for two hours in a row"
      namespace   = local.ns, metric = "StaleP1Sources", stat = "Maximum", dims = jsonencode(local.app_dims)
      period      = 3600, evals = 2, datapoints = 2
      op          = "GreaterThanThreshold", threshold = 0, missing = "notBreaching"
    }
    rate-limit-headroom = {
      description = "A provider's free-tier quota is more than 80% used"
      namespace   = local.ns, metric = "RateLimitHeadroomPct", stat = "Minimum", dims = jsonencode(local.app_dims)
      period      = 3600, evals = 1, datapoints = 1
      op          = "LessThanThreshold", threshold = 20, missing = "notBreaching"
    }
    freshness-slo-burn = {
      description = "Freshness SLO (99%) burning 6x too fast over 6 hours"
      namespace   = local.ns, metric = "FreshP1Ratio", stat = "Average", dims = jsonencode(local.app_dims)
      period      = 21600, evals = 1, datapoints = 1
      op          = "LessThanThreshold", threshold = 1 - 6 * (1 - local.slo_freshness), missing = "notBreaching"
    }
  }

  canary_alarm_defs = {
    canary-failing = {
      description = "External health check failed twice in a row (site down or data stale)"
      namespace   = "CloudWatchSynthetics", metric = "SuccessPercent", stat = "Average"
      dims        = jsonencode({ CanaryName = local.canary_name })
      period      = var.canary_rate_minutes * 60, evals = 2, datapoints = 2
      op          = "LessThanThreshold", threshold = 100, missing = "breaching"
    }
    availability-fast-burn = {
      description = "Availability SLO (99.5%) burning 14.4x too fast over 1 hour"
      namespace   = "CloudWatchSynthetics", metric = "SuccessPercent", stat = "Average"
      dims        = jsonencode({ CanaryName = local.canary_name })
      period      = 3600, evals = 1, datapoints = 1
      op          = "LessThanThreshold", threshold = 100 - 14.4 * (100 - local.slo_availability), missing = "breaching"
    }
    availability-slow-burn = {
      description = "Availability SLO (99.5%) burning 6x too fast over 6 hours"
      namespace   = "CloudWatchSynthetics", metric = "SuccessPercent", stat = "Average"
      dims        = jsonencode({ CanaryName = local.canary_name })
      period      = 21600, evals = 1, datapoints = 1
      op          = "LessThanThreshold", threshold = 100 - 6 * (100 - local.slo_availability), missing = "breaching"
    }
  }

  # for-filters instead of conditionals keep the object types consistent.
  canary_alarms = { for k, v in local.canary_alarm_defs : k => v if local.canary_on }
  alarms        = merge(local.base_alarms, local.canary_alarms)
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = local.alarms

  alarm_name          = "${var.name}-${each.key}"
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.stat
  dimensions          = jsondecode(each.value.dims)
  period              = each.value.period
  evaluation_periods  = each.value.evals
  datapoints_to_alarm = each.value.datapoints
  comparison_operator = each.value.op
  threshold           = each.value.threshold
  treat_missing_data  = each.value.missing
  alarm_actions       = [var.ops_topic_arn]
  ok_actions          = [var.ops_topic_arn]
}

# ---------------------------------------------------------------------------
# External health canary: fetches health.json through CloudFront, checks the
# data age and P1 staleness (SI-4, CP-2 continuity monitoring).
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "canary" {
  #checkov:skip=CKV_AWS_18:Short-lived canary artifacts (31 days); no data of record
  #checkov:skip=CKV2_AWS_62:No consumers of canary artifact events
  #checkov:skip=CKV_AWS_144:Canary artifacts are disposable; no DR copy needed
  count  = local.canary_on ? 1 : 0
  bucket = "${var.name}-canary-${local.account_id}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "canary" {
  count  = local.canary_on ? 1 : 0
  bucket = aws_s3_bucket.canary[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.data_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "canary" {
  count                   = local.canary_on ? 1 : 0
  bucket                  = aws_s3_bucket.canary[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "canary" {
  count  = local.canary_on ? 1 : 0
  bucket = aws_s3_bucket.canary[0].id
  rule {
    id     = "expire-artifacts"
    status = "Enabled"
    filter {}
    expiration {
      days = 31
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.canary]
}

resource "aws_s3_bucket_versioning" "canary" {
  count  = local.canary_on ? 1 : 0
  bucket = aws_s3_bucket.canary[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "canary_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "canary" {
  count              = local.canary_on ? 1 : 0
  name               = "${var.name}-canary"
  assume_role_policy = data.aws_iam_policy_document.canary_trust.json
}

data "aws_iam_policy_document" "canary" {
  count = local.canary_on ? 1 : 0

  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.canary[0].arn}/*"]
  }
  statement {
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.canary[0].arn]
  }
  statement {
    actions   = ["s3:ListAllMyBuckets", "xray:PutTraceSegments"]
    resources = ["*"]
  }
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["CloudWatchSynthetics"]
    }
  }
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/cwsyn-*"]
  }
  statement {
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [var.data_key_arn]
  }
}

resource "aws_iam_role_policy" "canary" {
  count  = local.canary_on ? 1 : 0
  name   = "canary"
  role   = aws_iam_role.canary[0].id
  policy = data.aws_iam_policy_document.canary[0].json
}

data "archive_file" "canary" {
  type        = "zip"
  source_file = "${path.module}/../../canary/index.js"
  output_path = "${path.module}/.build/canary.zip"
}

resource "aws_synthetics_canary" "health" {
  count                    = local.canary_on ? 1 : 0
  name                     = local.canary_name
  artifact_s3_location     = "s3://${aws_s3_bucket.canary[0].id}/runs/"
  execution_role_arn       = aws_iam_role.canary[0].arn
  handler                  = "index.handler"
  zip_file                 = data.archive_file.canary.output_path
  runtime_version          = var.canary_runtime
  start_canary             = true
  delete_lambda            = true
  success_retention_period = 7
  failure_retention_period = 31

  schedule {
    expression = "rate(${var.canary_rate_minutes} minutes)"
  }

  run_config {
    timeout_in_seconds = 60
    active_tracing     = true
    environment_variables = {
      HEALTH_URL  = var.health_url
      MAX_AGE_MIN = "90"
    }
  }

  artifact_config {
    s3_encryption {
      encryption_mode = "SSE_KMS"
      kms_key_arn     = var.data_key_arn
    }
  }

  depends_on = [aws_iam_role_policy.canary]
}

# ---------------------------------------------------------------------------
# Dashboards: ops (how the system is running) and SLO (are we meeting targets).
# ---------------------------------------------------------------------------
locals {

  ops_widgets = concat([
    {
      type = "alarm", x = 0, y = 0, width = 24, height = 3
      properties = {
        title  = "Alarm status"
        alarms = [for a in aws_cloudwatch_metric_alarm.this : a.arn]
      }
    },
    {
      type = "metric", x = 0, y = 3, width = 8, height = 6
      properties = {
        title   = "Job runs (all functions)", region = local.region, stat = "Sum", period = 3600, view = "timeSeries"
        metrics = [["AWS/Lambda", "Invocations"], [".", "Errors"], [".", "Throttles"]]
      }
    },
    {
      type = "metric", x = 8, y = 3, width = 8, height = 6
      properties = {
        title   = "Job duration p95 (ms)", region = local.region, stat = "p95", period = 3600, view = "timeSeries"
        metrics = [["AWS/Lambda", "Duration"]]
      }
    },
    {
      type = "metric", x = 16, y = 3, width = 8, height = 6
      properties = {
        title   = "Failed runs and rows written", region = local.region, stat = "Sum", period = 3600, view = "timeSeries"
        metrics = [[local.ns, "FailedRuns", "service", var.name], [".", "RowsWritten", ".", ".", { yAxis = "right" }]]
      }
    },
    {
      type = "metric", x = 0, y = 9, width = 8, height = 6
      properties = {
        title   = "Stale sources", region = local.region, stat = "Maximum", period = 3600, view = "timeSeries"
        metrics = [[local.ns, "StaleP1Sources", "service", var.name], [".", "StaleSources", ".", "."]]
      }
    },
    {
      type = "metric", x = 8, y = 9, width = 8, height = 6
      properties = {
        title       = "Lowest free-tier headroom (%)", region = local.region, stat = "Minimum", period = 3600, view = "timeSeries"
        metrics     = [[local.ns, "RateLimitHeadroomPct", "service", var.name]]
        annotations = { horizontal = [{ value = 20, label = "alarm" }] }
      }
    },
    {
      type = "metric", x = 16, y = 9, width = 8, height = 6
      properties = {
        title   = "Dead-letter queue", region = local.region, stat = "Maximum", period = 300, view = "timeSeries"
        metrics = [["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.dlq_name]]
      }
    },
  ])

  slo_widgets = concat([
    {
      type = "text", x = 0, y = 0, width = 24, height = 3
      properties = {
        markdown = "## SLOs\n**Availability** ${local.slo_availability}% of health checks pass (30 days, ~3.6 h error budget). **Freshness** ${local.slo_freshness * 100}% of P1 sources within max age during business hours. **Release capture** every CPI, PCE and FOMC release stored within 15 minutes (each miss is reviewed)."
      }
    },
    {
      type = "metric", x = 0, y = 3, width = 12, height = 6
      properties = {
        title       = "Freshness: share of P1 sources current", region = local.region, stat = "Average", period = 3600, view = "timeSeries"
        metrics     = [[local.ns, "FreshP1Ratio", "service", var.name]]
        yAxis       = { left = { min = 0.8, max = 1 } }
        annotations = { horizontal = [{ value = local.slo_freshness, label = "SLO" }] }
      }
    },
    ], [for w in [
      {
        type = "metric", x = 12, y = 3, width = 12, height = 6
        properties = {
          title       = "Availability: health checks passing (%)", region = local.region, stat = "Average", period = 3600, view = "timeSeries"
          metrics     = [["CloudWatchSynthetics", "SuccessPercent", "CanaryName", local.canary_name]]
          yAxis       = { left = { min = 90, max = 100 } }
          annotations = { horizontal = [{ value = local.slo_availability, label = "SLO" }] }
        }
      },
  ] : w if local.canary_on])
}

resource "aws_cloudwatch_dashboard" "ops" {
  dashboard_name = "${var.name}-ops"
  dashboard_body = jsonencode({ widgets = local.ops_widgets })
}

resource "aws_cloudwatch_dashboard" "slo" {
  dashboard_name = "${var.name}-slo"
  dashboard_body = jsonencode({ widgets = local.slo_widgets })
}

# ---------------------------------------------------------------------------
# Cost guardrail (budget alerts are free).
# ---------------------------------------------------------------------------
resource "aws_budgets_budget" "monthly" {
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.budget_emails
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.budget_emails
  }
}
