# SMS is off until alert_sms_e164 is set. Email subscriptions on the ops and
# security topics stay as they are and are not capped. This Lambda is the only
# application publisher to the SMS topic, so a phone subscribed directly to an
# alarm topic would bypass the daily cap.

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

variable "permissions_boundary_arn" {
  description = "Permissions boundary set on every IAM role in this module (modules/workload_boundary)."
  type        = string
}

variable "audit_key_arn" {
  description = "KMS key for the notifier log group."
  type        = string
}

variable "log_retention_days" {
  type = number
}

variable "reserved_concurrency" {
  description = "Null = unreserved. New accounts with a 10-execution limit cannot reserve any."
  type        = number
  default     = null
}

variable "alert_sms_e164" {
  description = "E.164 handset. Empty leaves the cap resources uncreated."
  type        = string
  default     = ""
}

variable "sms_daily_cap" {
  description = "Texts per issue per America/Los_Angeles calendar day."
  type        = number
  default     = 1
}

variable "enable_cloudfront_alarms" {
  description = "Also let the us-east-1 CloudFront topic invoke the notifier."
  type        = bool
  default     = true
}

data "aws_region" "current" {}

locals {
  sms_on = var.alert_sms_e164 != ""
  sms_fn = "${var.name}-sms-notifier"
  region = data.aws_region.current.region
}

data "archive_file" "notifier" {
  type        = "zip"
  source_dir  = "${path.root}/../../services/data-jobs/src/functions/sms_notifier"
  output_path = "${path.module}/.build/sms_notifier.zip"
  excludes    = ["__pycache__", "*.pyc"]
}

resource "aws_dynamodb_table" "cap" {
  count = local.sms_on ? 1 : 0

  name         = "${var.name}-sms-cap"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "issue_key"

  # Counters are disposable, but point-in-time recovery is cheap at this size and
  # keeps a bad deploy from wiping the day's cap.
  deletion_protection_enabled = true

  attribute {
    name = "issue_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.data_key_arn
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_cloudwatch_log_group" "notifier" {
  count = local.sms_on ? 1 : 0

  name              = "/aws/lambda/${local.sms_fn}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.audit_key_arn
}

data "aws_iam_policy_document" "notifier_trust" {
  count = local.sms_on ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "notifier" {
  count = local.sms_on ? 1 : 0

  name                 = local.sms_fn
  assume_role_policy   = data.aws_iam_policy_document.notifier_trust[0].json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "notifier" {
  count = local.sms_on ? 1 : 0

  statement {
    sid       = "CapItem"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.cap[0].arn]
  }

  statement {
    sid       = "CapTableKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.data_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["dynamodb.${local.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "PublishSmsTopic"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.sms[0].arn]
  }

  statement {
    sid       = "SmsTopicKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
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
    resources = ["${aws_cloudwatch_log_group.notifier[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "notifier" {
  count = local.sms_on ? 1 : 0

  name   = "sms-notifier"
  role   = aws_iam_role.notifier[0].id
  policy = data.aws_iam_policy_document.notifier[0].json
}

resource "aws_sns_topic" "sms" {
  count = local.sms_on ? 1 : 0

  name              = "${var.name}-sms"
  kms_master_key_id = var.data_key_arn
}

data "aws_iam_policy_document" "sms_topic" {
  count = local.sms_on ? 1 : 0

  statement {
    sid       = "NotifierPublish"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.sms[0].arn]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.notifier[0].arn]
    }
  }

  statement {
    sid = "AccountOwnerManage"
    actions = [
      "SNS:GetTopicAttributes", "SNS:SetTopicAttributes", "SNS:Subscribe",
      "SNS:ListSubscriptionsByTopic", "SNS:Publish",
    ]
    resources = [aws_sns_topic.sms[0].arn]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.sms[0].arn]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_sns_topic_policy" "sms" {
  count = local.sms_on ? 1 : 0

  arn    = aws_sns_topic.sms[0].arn
  policy = data.aws_iam_policy_document.sms_topic[0].json
}

resource "aws_sns_topic_subscription" "handset" {
  count = local.sms_on ? 1 : 0

  topic_arn = aws_sns_topic.sms[0].arn
  protocol  = "sms"
  endpoint  = var.alert_sms_e164
}

resource "aws_lambda_function" "notifier" {
  count = local.sms_on ? 1 : 0

  #checkov:skip=CKV_AWS_117:Accepted risk: job and API Lambdas run outside a VPC; see architecture doc, Accepted risk
  #checkov:skip=CKV_AWS_272:Code signing not used; images and zips are built by CI from the protected main branch
  #checkov:skip=CKV_AWS_116:SNS retries a failed invoke. A dedupe-store error still sends the text (fail open), so a DLQ would only hold texts already decided
  function_name                  = local.sms_fn
  description                    = "Cap SMS to N texts per issue per Pacific day; email is not capped"
  role                           = aws_iam_role.notifier[0].arn
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  handler                        = "sms_notifier.handler"
  filename                       = data.archive_file.notifier.output_path
  source_code_hash               = data.archive_file.notifier.output_base64sha256
  timeout                        = 15
  memory_size                    = 128
  reserved_concurrent_executions = var.reserved_concurrency
  kms_key_arn                    = var.data_key_arn

  environment {
    variables = {
      CAP_TABLE_NAME        = aws_dynamodb_table.cap[0].name
      SMS_TOPIC_ARN         = aws_sns_topic.sms[0].arn
      SMS_DAILY_CAP         = tostring(var.sms_daily_cap)
      AWS_USE_FIPS_ENDPOINT = "true"
    }
  }

  tracing_config {
    mode = "PassThrough"
  }

  depends_on = [aws_cloudwatch_log_group.notifier, aws_iam_role_policy.notifier]
}

resource "aws_lambda_permission" "topics" {
  for_each = local.sms_on ? local.topics : {}

  statement_id  = "AllowSNS-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notifier[0].function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.this[each.key].arn
}

resource "aws_sns_topic_subscription" "notifier" {
  for_each = local.sms_on ? local.topics : {}

  topic_arn = aws_sns_topic.this[each.key].arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.notifier[0].arn

  depends_on = [aws_lambda_permission.topics]
}

# The CloudFront alarm topic lives in us-east-1. The subscription is created there;
# this permission lets that topic invoke the notifier without a module cycle.
resource "aws_lambda_permission" "cloudfront_topic" {
  count = local.sms_on && var.enable_cloudfront_alarms ? 1 : 0

  statement_id  = "AllowCloudFrontTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notifier[0].function_name
  principal     = "sns.amazonaws.com"
  source_arn    = "arn:${data.aws_partition.current.partition}:sns:us-east-1:${local.account_id}:${var.name}-ops-alerts-us-east-1"
}

output "sms_notifier_arn" {
  description = "Notifier Lambda. Empty when alert_sms_e164 is empty."
  value       = local.sms_on ? aws_lambda_function.notifier[0].arn : ""
  depends_on = [
    aws_lambda_permission.topics,
    aws_lambda_permission.cloudfront_topic,
  ]
}

output "sms_topic_arn" {
  description = "Topic the handset subscribes to. Empty when SMS is off."
  value       = local.sms_on ? aws_sns_topic.sms[0].arn : ""
}
