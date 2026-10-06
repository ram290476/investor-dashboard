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

variable "data_key_arn" {
  type = string
}

variable "audit_key_arn" {
  type = string
}

variable "security_topic_arn" {
  type = string
}

variable "log_retention_days" {
  type = number
}

variable "api_keys" {
  type = map(object({
    provider       = string
    sources        = list(string)
    rotation_days  = number
    regenerate_url = string
  }))
}

variable "remind_days" {
  type = list(number)
}

variable "check_schedule" {
  type = string
}

variable "check_timezone" {
  type = string
}

variable "reserved_concurrency" {
  description = "Null = unreserved. New accounts with a 10-execution limit cannot reserve any."
  type        = number
  default     = null
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region
  key_path   = "/${var.name}/api-keys"
  fn_name    = "${var.name}-key-rotation-check"
}

# ---------------------------------------------------------------------------
# One SecureString per provider credential, encrypted with the data key
# (IA-5(h) protect authenticators, SC-12, SC-28). Terraform creates each with a
# placeholder and never manages the real value: store keys with
# scripts/rotate-key.sh. The RotationDays tag drives the reminder schedule.
# ---------------------------------------------------------------------------
resource "aws_ssm_parameter" "key" {
  for_each = var.api_keys

  name        = "${local.key_path}/${each.key}"
  description = "${each.value.provider} credential for ${join(", ", each.value.sources)}. Rotate every ${each.value.rotation_days} days."
  type        = "SecureString"
  tier        = "Standard"
  key_id      = var.data_key_arn
  value       = "REPLACE_ME"

  tags = {
    Provider      = each.value.provider
    Sources       = join(" ", each.value.sources)
    RotationDays  = tostring(each.value.rotation_days)
    RegenerateUrl = each.value.regenerate_url
  }

  lifecycle {
    ignore_changes = [value]
  }
}

# ---------------------------------------------------------------------------
# Daily checker: computes each key's due date and emails reminders.
# ---------------------------------------------------------------------------
data "archive_file" "checker" {
  type        = "zip"
  source_file = "${path.root}/../../services/data-jobs/src/functions/key_rotation_check/key_rotation_check.py"
  output_path = "${path.module}/.build/key_rotation_check.zip"
}

resource "aws_cloudwatch_log_group" "checker" {
  name              = "/aws/lambda/${local.fn_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.audit_key_arn
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "checker" {
  name               = local.fn_name
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

# Least privilege: metadata and tags only, never ssm:GetParameter or kms:Decrypt on the keys.
data "aws_iam_policy_document" "checker" {
  statement {
    sid       = "ListKeyMetadata"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"] # DescribeParameters does not support resource-level permissions
  }
  statement {
    sid       = "ReadKeyTags"
    actions   = ["ssm:ListTagsForResource"]
    resources = ["arn:${local.partition}:ssm:${local.region}:${local.account_id}:parameter${local.key_path}/*"]
  }
  statement {
    sid       = "SendReminders"
    actions   = ["sns:Publish"]
    resources = [var.security_topic_arn]
  }
  statement {
    sid       = "EncryptedTopic"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [var.data_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["sns.${local.region}.amazonaws.com"]
    }
  }
  statement {
    sid       = "EnvDecrypt"
    actions   = ["kms:Decrypt"]
    resources = [var.data_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["lambda.${local.region}.amazonaws.com"]
    }
  }
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.checker.arn}:*"]
  }
}

resource "aws_iam_role_policy" "checker" {
  name   = "key-rotation-check"
  role   = aws_iam_role.checker.id
  policy = data.aws_iam_policy_document.checker.json
}

resource "aws_lambda_function" "checker" {
  #checkov:skip=CKV_AWS_117:Accepted risk: job and API Lambdas run outside a VPC; see architecture doc, Accepted risk
  #checkov:skip=CKV_AWS_272:Code signing not used; images and zips are built by CI from the protected main branch
  #checkov:skip=CKV_AWS_116:Scheduler retries twice and the account-level Lambda errors alarm covers failures
  function_name                  = local.fn_name
  description                    = "Daily API key rotation check and reminders (IA-5)"
  role                           = aws_iam_role.checker.arn
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  handler                        = "key_rotation_check.handler"
  filename                       = data.archive_file.checker.output_path
  source_code_hash               = data.archive_file.checker.output_base64sha256
  timeout                        = 60
  memory_size                    = 256
  reserved_concurrent_executions = var.reserved_concurrency
  kms_key_arn                    = var.data_key_arn

  environment {
    variables = {
      KEY_PATH              = local.key_path
      TOPIC_ARN             = var.security_topic_arn
      PROJECT               = var.name
      REMIND_DAYS           = join(",", [for d in var.remind_days : tostring(d)])
      AWS_USE_FIPS_ENDPOINT = "true"
    }
  }

  tracing_config {
    mode = "PassThrough"
  }

  depends_on = [aws_cloudwatch_log_group.checker, aws_iam_role_policy.checker]
}

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
  name               = "${local.fn_name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "invoke-key-rotation-check"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.checker.arn
    }]
  })
}

resource "aws_scheduler_schedule" "daily" {
  name                         = "${local.fn_name}-daily"
  description                  = "Check API key ages and send rotation reminders"
  schedule_expression          = var.check_schedule
  schedule_expression_timezone = var.check_timezone
  kms_key_arn                  = var.data_key_arn

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.checker.arn
    role_arn = aws_iam_role.scheduler.arn
    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}

output "key_path" {
  value = local.key_path
}

output "parameter_arns" {
  value = { for k, p in aws_ssm_parameter.key : k => p.arn }
}

output "checker_function_name" {
  value = aws_lambda_function.checker.function_name
}
