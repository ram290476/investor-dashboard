terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.dr]
    }
  }
}

variable "name" {
  type = string
}

variable "trail_name" {
  type = string
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region
  root_arn   = "arn:${local.partition}:iam::${local.account_id}:root"
  trail_arn  = "arn:${local.partition}:cloudtrail:${local.region}:${local.account_id}:trail/${var.trail_name}"
}

# ---------------------------------------------------------------------------
# Data key: data lake, DLQ, SNS topics, ECR, canary artifacts.
# Multi-Region so the DR replica in the second region uses the same key material.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "data_key" {
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

  # CloudWatch alarms and EventBridge rules publish to KMS-encrypted SNS topics.
  statement {
    sid       = "AlarmsAndEventRulesPublishToEncryptedTopics"
    actions   = ["kms:GenerateDataKey*", "kms:Decrypt"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com", "events.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "data_key_full" {
  source_policy_documents = [data.aws_iam_policy_document.data_key.json]

  statement {
    sid       = "SchedulerEncryptedSchedules"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = ["*"]
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

resource "aws_kms_key" "data" {
  description             = "${var.name} application data (lake, queues, topics, images)"
  multi_region            = true
  enable_key_rotation     = true
  rotation_period_in_days = 365
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.data_key_full.json
}

resource "aws_kms_alias" "data" {
  name          = "alias/${var.name}-data"
  target_key_id = aws_kms_key.data.key_id
}

resource "aws_kms_replica_key" "data_dr" {
  provider = aws.dr

  description             = "${var.name} application data (DR replica)"
  primary_key_arn         = aws_kms_key.data.arn
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.data_key_full.json
}

resource "aws_kms_alias" "data_dr" {
  provider = aws.dr

  name          = "alias/${var.name}-data"
  target_key_id = aws_kms_replica_key.data_dr.key_id
}

# ---------------------------------------------------------------------------
# Audit key: CloudTrail, AWS Config, CloudWatch Logs. Kept separate from the
# data key so app roles never get decrypt rights on audit records (AU-9(4)).
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "audit_key" {
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

  statement {
    sid       = "CloudTrailEncryptLogs"
    actions   = ["kms:GenerateDataKey*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:aws:cloudtrail:arn"
      values   = ["arn:${local.partition}:cloudtrail:*:${local.account_id}:trail/*"]
    }
  }

  statement {
    sid       = "CloudTrailDescribeKey"
    actions   = ["kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
  }

  statement {
    sid     = "CloudWatchLogsEncryption"
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${local.region}.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:*"]
    }
  }
}

resource "aws_kms_key" "audit" {
  description             = "${var.name} audit records (CloudTrail, Config, logs)"
  enable_key_rotation     = true
  rotation_period_in_days = 365
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.audit_key.json
}

resource "aws_kms_alias" "audit" {
  name          = "alias/${var.name}-audit"
  target_key_id = aws_kms_key.audit.key_id
}

output "data_key_arn" {
  value = aws_kms_key.data.arn
}

output "replica_key_arn" {
  value = aws_kms_replica_key.data_dr.arn
}

output "audit_key_arn" {
  value = aws_kms_key.audit.arn
}
