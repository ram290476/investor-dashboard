variable "name" {
  type = string
}

variable "image_uri" {
  description = "Job image in ECR (built from the repo Dockerfile). Empty = create nothing yet."
  type        = string
}

variable "jobs" {
  type = map(object({
    handler        = string
    schedule       = string       # EventBridge Scheduler expression in America/New_York; "" for event-only
    triggers       = list(string) # "job:<ID>" (that job succeeded), "job:*" (any job finished), "ticker-added"
    memory         = number
    timeout        = number
    read_prefixes  = list(string)
    write_prefixes = list(string)
    api_keys       = list(string)
    reads_prefs    = bool
  }))
}

variable "lake_bucket_name" {
  type = string
}

variable "lake_bucket_arn" {
  type = string
}

variable "data_key_arn" {
  type = string
}

variable "audit_key_arn" {
  type = string
}

variable "dlq_arn" {
  type = string
}

variable "prefs_table_name" {
  type = string
}

variable "prefs_collector_policy_arn" {
  type = string
}

variable "prefs_event_source" {
  type = string
}

variable "api_key_path" {
  type = string
}

variable "base_environment" {
  type = map(string)
}

variable "extra_environment" {
  type = map(string)
}

variable "log_retention_days" {
  type = number
}

variable "reserved_concurrency" {
  type    = number
  default = null
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  partition    = data.aws_partition.current.partition
  region       = data.aws_region.current.region
  enabled_jobs = var.image_uri == "" ? {} : var.jobs
  job_source   = "${var.name}.jobs"

  scheduled = { for k, j in local.enabled_jobs : k => j if j.schedule != "" }
  triggers = merge([
    for k, j in local.enabled_jobs : {
      for t in j.triggers : "${k}--${replace(replace(t, ":", "-"), "*", "any")}" => { job = k, trigger = t }
    }
  ]...)
}

# ---------------------------------------------------------------------------
# Per-job least-privilege role (AC-6): only its own prefixes, keys and log group.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_group" "job" {
  for_each          = local.enabled_jobs
  name              = "/aws/lambda/${var.name}-${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.audit_key_arn
}

resource "aws_iam_role" "job" {
  for_each           = local.enabled_jobs
  name               = "${var.name}-job-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "job" {
  for_each = local.enabled_jobs

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.job[each.key].arn}:*"]
  }

  dynamic "statement" {
    for_each = length(each.value.read_prefixes) > 0 ? [1] : []
    content {
      sid       = "ReadLake"
      actions   = ["s3:GetObject"]
      resources = [for p in each.value.read_prefixes : "${var.lake_bucket_arn}/${p}*"]
    }
  }

  dynamic "statement" {
    for_each = length(each.value.write_prefixes) > 0 ? [1] : []
    content {
      sid       = "WriteLake"
      actions   = ["s3:PutObject"]
      resources = [for p in each.value.write_prefixes : "${var.lake_bucket_arn}/${p}*"]
    }
  }

  dynamic "statement" {
    for_each = length(concat(each.value.read_prefixes, each.value.write_prefixes)) > 0 ? [1] : []
    content {
      sid       = "ListOwnPrefixes"
      actions   = ["s3:ListBucket"]
      resources = [var.lake_bucket_arn]
      condition {
        test     = "StringLike"
        variable = "s3:prefix"
        values   = [for p in distinct(concat(each.value.read_prefixes, each.value.write_prefixes)) : "${p}*"]
      }
    }
  }

  dynamic "statement" {
    for_each = length(each.value.api_keys) > 0 ? [1] : []
    content {
      sid       = "ReadOwnApiKeys"
      actions   = ["ssm:GetParameter"]
      resources = [for k in each.value.api_keys : "arn:${local.partition}:ssm:${local.region}:${local.account_id}:parameter${var.api_key_path}/${k}"]
    }
  }

  statement {
    sid       = "DataKeyViaServices"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.data_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = [for s in ["s3", "ssm", "sqs", "lambda", "dynamodb"] : "${s}.${local.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "PublishJobEvents"
    actions   = ["events:PutEvents"]
    resources = ["arn:${local.partition}:events:${local.region}:${local.account_id}:event-bus/default"]
    condition {
      test     = "StringEquals"
      variable = "events:source"
      values   = [local.job_source]
    }
  }

  statement {
    sid       = "FailedRunsToDlq"
    actions   = ["sqs:SendMessage"]
    resources = [var.dlq_arn]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "job" {
  for_each = local.enabled_jobs
  name     = "job-${each.key}"
  role     = aws_iam_role.job[each.key].id
  policy   = data.aws_iam_policy_document.job[each.key].json
}

resource "aws_iam_role_policy_attachment" "prefs_read" {
  for_each   = { for k, j in local.enabled_jobs : k => j if j.reads_prefs }
  role       = aws_iam_role.job[each.key].name
  policy_arn = var.prefs_collector_policy_arn
}

# ---------------------------------------------------------------------------
# Functions: one image, one handler per job.
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "job" {
  #checkov:skip=CKV_AWS_117:Accepted risk: job and API Lambdas run outside a VPC; see architecture doc, Accepted risk
  #checkov:skip=CKV_AWS_272:Code signing not used; images and zips are built by CI from the protected main branch
  for_each                       = local.enabled_jobs
  function_name                  = "${var.name}-${each.key}"
  role                           = aws_iam_role.job[each.key].arn
  package_type                   = "Image"
  image_uri                      = var.image_uri
  architectures                  = ["arm64"]
  memory_size                    = each.value.memory
  timeout                        = each.value.timeout
  kms_key_arn                    = var.data_key_arn
  reserved_concurrent_executions = var.reserved_concurrency

  image_config {
    command = [each.value.handler]
  }

  environment {
    variables = merge(var.base_environment, var.extra_environment, {
      JOB_NAME    = each.key
      PREFS_TABLE = var.prefs_table_name
    })
  }

  tracing_config {
    mode = "Active"
  }

  dead_letter_config {
    target_arn = var.dlq_arn
  }

  depends_on = [aws_cloudwatch_log_group.job, aws_iam_role_policy.job]
}

resource "aws_lambda_function_event_invoke_config" "job" {
  for_each               = local.enabled_jobs
  function_name          = aws_lambda_function.job[each.key].function_name
  maximum_retry_attempts = 2

  destination_config {
    on_failure {
      destination = var.dlq_arn
    }
  }
}

# ---------------------------------------------------------------------------
# Schedules (ET, so daylight-saving changes need no edits) and event triggers.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  count              = length(local.scheduled) > 0 ? 1 : 0
  name               = "${var.name}-jobs-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

resource "aws_iam_role_policy" "scheduler" {
  count = length(local.scheduled) > 0 ? 1 : 0
  name  = "invoke-scheduled-jobs"
  role  = aws_iam_role.scheduler[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = [for k, _ in local.scheduled : aws_lambda_function.job[k].arn]
    }]
  })
}

resource "aws_scheduler_schedule" "job" {
  for_each                     = local.scheduled
  name                         = "${var.name}-${each.key}"
  schedule_expression          = each.value.schedule
  schedule_expression_timezone = "America/New_York"
  kms_key_arn                  = var.data_key_arn

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.job[each.key].arn
    role_arn = aws_iam_role.scheduler[0].arn
    input    = jsonencode({ source = "schedule" })
    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}

resource "aws_cloudwatch_event_rule" "trigger" {
  for_each    = local.triggers
  name        = "${var.name}-${each.key}"
  description = "Start ${each.value.job} on ${each.value.trigger}"
  event_pattern = each.value.trigger == "ticker-added" ? jsonencode({
    source        = [var.prefs_event_source]
    "detail-type" = ["TickerAdded"]
    }) : each.value.trigger == "job:*" ? jsonencode({
    source        = [local.job_source]
    "detail-type" = ["Job Finished"]
    }) : jsonencode({
    source        = [local.job_source]
    "detail-type" = ["Job Finished"]
    detail        = { job = [trimprefix(each.value.trigger, "job:")], outcome = ["success"] }
  })
}

resource "aws_cloudwatch_event_target" "trigger" {
  for_each = local.triggers
  rule     = aws_cloudwatch_event_rule.trigger[each.key].name
  arn      = aws_lambda_function.job[each.value.job].arn

  retry_policy {
    maximum_retry_attempts       = 4
    maximum_event_age_in_seconds = 3600
  }

  dead_letter_config {
    arn = var.dlq_arn
  }
}

resource "aws_lambda_permission" "trigger" {
  for_each      = local.triggers
  statement_id  = "AllowEventBridge-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.job[each.value.job].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.trigger[each.key].arn
}

output "function_names" {
  value = [for f in aws_lambda_function.job : f.function_name]
}

output "log_group_names" {
  value = [for g in aws_cloudwatch_log_group.job : g.name]
}
