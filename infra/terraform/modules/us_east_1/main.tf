terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

# Resources that must live in us-east-1 whatever the primary region is (pass aws.us_east_1 as aws):
# - CloudFront publishes its metrics only in us-east-1, and an alarm can only notify an SNS topic
#   in its own region, so the 5xx alarm gets its own encrypted topic and key here (SI-4, IR-6).
# - IAM API calls and root/global-endpoint console sign-ins are delivered only to the us-east-1
#   default event bus. Rules here forward them to the primary region's default bus, where the
#   alerting module's rules publish them to the security topic (AC-2(4), IA-2(1)).

variable "permissions_boundary_arn" {
  description = "Permissions boundary set on every IAM role in this module (modules/workload_boundary)."
  type        = string
}

variable "name" {
  type = string
}

variable "alert_emails" {
  description = "Recipients of the CloudFront alarm (the ops alert list). Each must confirm this topic's subscription too."
  type        = list(string)
}

variable "enable_cloudfront_alarms" {
  type = bool
}

variable "cloudfront_distribution_id" {
  description = "Distribution to watch. May be unknown until apply; only the static flag above controls creation."
  type        = string
}

variable "primary_region" {
  description = "Region whose default event bus receives the forwarded events."
  type        = string
}

variable "forward_event_patterns" {
  description = "Rule name => event pattern, forwarded from us-east-1 to the primary region's default bus."
  type        = map(string)
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  root_arn   = "arn:${local.partition}:iam::${local.account_id}:root"
  alarm_on   = var.enable_cloudfront_alarms

  # With the primary region in us-east-1 the regional rules already see these events; forwarding
  # to the same bus is not allowed and would double the alerts.
  forward    = var.primary_region == "us-east-1" ? {} : var.forward_event_patterns
  target_bus = "arn:${local.partition}:events:${var.primary_region}:${local.account_id}:event-bus/default"
}

# ---------------------------------------------------------------------------
# Key and topic for the CloudFront alarm. Keys are regional, so the primary region's data key
# cannot encrypt this topic; a small dedicated key keeps data-key material out of us-east-1.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "alerts_key" {
  #checkov:skip=CKV_AWS_109:Key policy: root-account administration statement is the standard KMS pattern; IAM policies then scope access
  #checkov:skip=CKV_AWS_111:Key policy: resources '*' means this key only
  #checkov:skip=CKV_AWS_356:Key policy: resources '*' means this key only
  statement {
    sid       = "AccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [local.root_arn]
    }
  }

  # CloudWatch alarms publish to the KMS-encrypted topic.
  statement {
    sid       = "AlarmsPublishToEncryptedTopic"
    actions   = ["kms:GenerateDataKey*", "kms:Decrypt"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "alerts" {
  count = local.alarm_on ? 1 : 0

  description             = "${var.name} us-east-1 alert topic (CloudFront alarm)"
  enable_key_rotation     = true
  rotation_period_in_days = 365
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.alerts_key.json
}

resource "aws_kms_alias" "alerts" {
  count = local.alarm_on ? 1 : 0

  name          = "alias/${var.name}-alerts"
  target_key_id = aws_kms_key.alerts[0].key_id
}

resource "aws_sns_topic" "ops" {
  count = local.alarm_on ? 1 : 0

  name              = "${var.name}-ops-alerts-us-east-1"
  kms_master_key_id = aws_kms_key.alerts[0].arn
}

data "aws_iam_policy_document" "ops_topic" {
  count = local.alarm_on ? 1 : 0

  statement {
    sid       = "AccountOwnerManage"
    actions   = ["SNS:GetTopicAttributes", "SNS:SetTopicAttributes", "SNS:Subscribe", "SNS:ListSubscriptionsByTopic", "SNS:Publish"]
    resources = [aws_sns_topic.ops[0].arn]
    principals {
      type        = "AWS"
      identifiers = [local.root_arn]
    }
  }

  statement {
    sid       = "AlarmsPublish"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.ops[0].arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
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
    resources = [aws_sns_topic.ops[0].arn]
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

resource "aws_sns_topic_policy" "ops" {
  count = local.alarm_on ? 1 : 0

  arn    = aws_sns_topic.ops[0].arn
  policy = data.aws_iam_policy_document.ops_topic[0].json
}

resource "aws_sns_topic_subscription" "ops_email" {
  for_each = local.alarm_on ? toset(var.alert_emails) : toset([])

  topic_arn = aws_sns_topic.ops[0].arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_cloudwatch_metric_alarm" "cloudfront_5xx" {
  count = local.alarm_on ? 1 : 0

  alarm_name          = "${var.name}-cloudfront-5xx"
  alarm_description   = "CloudFront 5xx error rate above 5% for 15 minutes"
  namespace           = "AWS/CloudFront"
  metric_name         = "5xxErrorRate"
  statistic           = "Average"
  dimensions          = { DistributionId = var.cloudfront_distribution_id, Region = "Global" }
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops[0].arn]
  ok_actions          = [aws_sns_topic.ops[0].arn]
}

# ---------------------------------------------------------------------------
# Forward global-service events to the primary region's default bus (cross-Region delivery
# needs a role that EventBridge assumes to call PutEvents on the target bus).
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "forward_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:events:us-east-1:${local.account_id}:rule/${var.name}-forward-*"]
    }
  }
}

resource "aws_iam_role" "forward" {
  count = length(local.forward) > 0 ? 1 : 0

  name                 = "${var.name}-events-forward-us-east-1"
  assume_role_policy   = data.aws_iam_policy_document.forward_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_iam_role_policy" "forward" {
  count = length(local.forward) > 0 ? 1 : 0

  name = "put-events-primary-bus"
  role = aws_iam_role.forward[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "events:PutEvents"
      Resource = local.target_bus
    }]
  })
}

resource "aws_cloudwatch_event_rule" "forward" {
  for_each = local.forward

  name          = "${var.name}-forward-${each.key}"
  description   = "Forward ${each.key} events to ${var.primary_region}, where the ${var.name}-${each.key} rule alerts"
  event_pattern = each.value
}

resource "aws_cloudwatch_event_target" "forward" {
  for_each = local.forward

  rule      = aws_cloudwatch_event_rule.forward[each.key].name
  target_id = "bus-${var.primary_region}"
  arn       = local.target_bus
  role_arn  = aws_iam_role.forward[0].arn
}

output "cloudfront_topic_arn" {
  value = local.alarm_on ? aws_sns_topic.ops[0].arn : null
}

output "forward_rule_names" {
  value = [for r in aws_cloudwatch_event_rule.forward : r.name]
}
