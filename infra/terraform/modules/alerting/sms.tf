# Email and SMS both go through this Lambda. Alarm intake topics
# (${name}-ops-alarms, ${name}-security-alarms, and the us-east-1 CloudFront
# topic) invoke it and do not deliver to an inbox or a handset themselves.
# The Lambda is the only application publisher to the delivery topics
# (${name}-ops-alerts and ${name}-security-alerts, main.tf), which keep the
# inboxes' existing, confirmed email subscriptions, and to the SMS topic when a
# handset is set. A direct subscription on an intake topic would bypass the cap.
#
# Windows are fixed America/Los_Angeles clock buckets aligned to local midnight,
# not a rolling interval from the first send. Email and SMS keep separate
# window lengths, maxima, and dedupe items. Each item's TTL is the end of the
# following window for that channel.

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
  description = "E.164 handset. Empty leaves SMS off. Email stays capped either way."
  type        = string
  default     = ""
}

variable "email_window_minutes" {
  description = "Email cap window in minutes. Fixed America/Los_Angeles clock buckets aligned to local midnight. 60 is one clock hour."
  type        = number
  default     = 60
}

variable "email_max_per_window" {
  description = "Emails per issue per email clock window. Independent of the SMS maximum."
  type        = number
  default     = 1
}

variable "sms_window_minutes" {
  description = "SMS cap window in minutes. Fixed America/Los_Angeles clock buckets aligned to local midnight. 60 is one clock hour."
  type        = number
  default     = 60
}

variable "sms_max_per_window" {
  description = "Texts per issue per SMS clock window. Independent of the email maximum."
  type        = number
  default     = 1
}

variable "vuln_window_minutes" {
  description = "Cap window for vulnerability (CVE/GHSA) keys on both channels. 1440 is one Pacific calendar day."
  type        = number
  default     = 1440
}

variable "enable_cloudfront_alarms" {
  description = "Also let the us-east-1 CloudFront topic invoke the notifier."
  type        = bool
  default     = true
}

variable "alert_notifier_enabled" {
  description = "Plan-time switch for the us-east-1 CloudFront topic's notifier subscription (and this permission). Must match the us_east_1 module."
  type        = bool
  default     = true
}

data "aws_region" "current" {}

locals {
  sms_on = var.alert_sms_e164 != ""
  sms_fn = "${var.name}-alert-notifier"
  region = data.aws_region.current.region
}

data "archive_file" "notifier" {
  type        = "zip"
  source_dir  = "${path.root}/../../services/data-jobs/src/functions/sms_notifier"
  output_path = "${path.module}/.build/sms_notifier.zip"
  excludes    = ["__pycache__", "*.pyc"]
}

resource "aws_dynamodb_table" "cap" {
  name         = "${var.name}-alert-cap"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "issue_key"
  range_key    = "channel"

  # Counters are disposable. Each item's TTL is the end of the next clock window
  # for that channel, long enough for that window's first message to report
  # suppressed repeats.
  deletion_protection_enabled = true

  attribute {
    name = "issue_key"
    type = "S"
  }

  attribute {
    name = "channel"
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
  name              = "/aws/lambda/${local.sms_fn}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.audit_key_arn
}

data "aws_iam_policy_document" "notifier_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "notifier" {
  name                 = local.sms_fn
  assume_role_policy   = data.aws_iam_policy_document.notifier_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "notifier" {
  statement {
    sid       = "CapItem"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.cap.arn]
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
    sid     = "PublishOutbound"
    actions = ["sns:Publish"]
    resources = concat(
      [for topic in aws_sns_topic.this : topic.arn],
      local.sms_on ? [aws_sns_topic.sms[0].arn] : [],
    )
  }

  statement {
    sid       = "OutboundTopicKey"
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
    resources = ["${aws_cloudwatch_log_group.notifier.arn}:*"]
  }
}

resource "aws_iam_role_policy" "notifier" {
  name   = "alert-notifier"
  role   = aws_iam_role.notifier.id
  policy = data.aws_iam_policy_document.notifier.json
}

# Intake topics. CloudWatch alarms, EventBridge rules and the key-rotation check
# publish here; only the notifier subscribes. Delivery to inboxes stays on the
# pre-existing ${name}-<topic>-alerts topics so their confirmed email
# subscriptions are not replaced (deploy #199).
resource "aws_sns_topic" "alarm" {
  for_each = local.topics

  name              = "${var.name}-${each.key}-alarms"
  kms_master_key_id = var.data_key_arn
}

data "aws_iam_policy_document" "alarm" {
  for_each = local.topics

  statement {
    sid = "AccountOwnerManage"
    actions = [
      "SNS:GetTopicAttributes", "SNS:SetTopicAttributes", "SNS:Subscribe",
      "SNS:ListSubscriptionsByTopic", "SNS:Publish",
    ]
    resources = [aws_sns_topic.alarm[each.key].arn]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid       = "AlarmsAndEventRulesPublish"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.alarm[each.key].arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com", "events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.alarm[each.key].arn]
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

resource "aws_sns_topic_policy" "alarm" {
  for_each = local.topics

  arn    = aws_sns_topic.alarm[each.key].arn
  policy = data.aws_iam_policy_document.alarm[each.key].json
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
      identifiers = [aws_iam_role.notifier.arn]
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
  #checkov:skip=CKV_AWS_117:Accepted risk: job and API Lambdas run outside a VPC; see architecture doc, Accepted risk
  #checkov:skip=CKV_AWS_272:Code signing not used; images and zips are built by CI from the protected main branch
  #checkov:skip=CKV_AWS_116:SNS retries a failed invoke. A dedupe-store error still sends (fail open), so a DLQ would only hold alerts already decided
  function_name                  = local.sms_fn
  description                    = "Cap email and SMS independently to N messages per issue per Pacific clock window"
  role                           = aws_iam_role.notifier.arn
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
    # Lambda rejects an empty value, so SMS_TOPIC_ARN is omitted until a handset is set.
    variables = merge(
      {
        CAP_TABLE_NAME           = aws_dynamodb_table.cap.name
        OPS_EMAIL_TOPIC_ARN      = aws_sns_topic.this["ops"].arn
        SECURITY_EMAIL_TOPIC_ARN = aws_sns_topic.this["security"].arn
        EMAIL_WINDOW_MINUTES     = tostring(var.email_window_minutes)
        EMAIL_MAX_PER_WINDOW     = tostring(var.email_max_per_window)
        SMS_WINDOW_MINUTES       = tostring(var.sms_window_minutes)
        SMS_MAX_PER_WINDOW       = tostring(var.sms_max_per_window)
        VULN_WINDOW_MINUTES      = tostring(var.vuln_window_minutes)
        AWS_USE_FIPS_ENDPOINT    = "true"
      },
      local.sms_on ? { SMS_TOPIC_ARN = aws_sns_topic.sms[0].arn } : {},
    )
  }

  tracing_config {
    mode = "PassThrough"
  }

  depends_on = [aws_cloudwatch_log_group.notifier, aws_iam_role_policy.notifier]
}

resource "aws_lambda_permission" "topics" {
  for_each = local.topics

  statement_id  = "AllowSNS-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notifier.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alarm[each.key].arn
}

resource "aws_sns_topic_subscription" "notifier" {
  for_each = local.topics

  topic_arn = aws_sns_topic.alarm[each.key].arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.notifier.arn

  depends_on = [aws_lambda_permission.topics]
}

# The CloudFront alarm topic lives in us-east-1. The subscription is created there;
# this permission lets that topic invoke the notifier without a module cycle.
resource "aws_lambda_permission" "cloudfront_topic" {
  count = var.enable_cloudfront_alarms && var.alert_notifier_enabled ? 1 : 0

  statement_id  = "AllowCloudFrontTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notifier.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = "arn:${data.aws_partition.current.partition}:sns:us-east-1:${local.account_id}:${var.name}-ops-alerts-us-east-1"
}

output "sms_notifier_arn" {
  description = "Notifier Lambda. Caps email and, when alert_sms_e164 is set, SMS."
  value       = aws_lambda_function.notifier.arn
  depends_on = [
    aws_lambda_permission.topics,
    aws_lambda_permission.cloudfront_topic,
  ]
}

output "sms_topic_arn" {
  description = "Topic the handset subscribes to. Empty when SMS is off."
  value       = local.sms_on ? aws_sns_topic.sms[0].arn : ""
}
