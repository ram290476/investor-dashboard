variable "name" {
  type = string
}

variable "data_key_arn" {
  type = string
}

variable "alert_emails" {
  type = list(string)
}

variable "security_alert_emails" {
  type = list(string)
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  topics = {
    ops      = var.alert_emails
    security = var.security_alert_emails
  }
  subscriptions = merge([
    for topic, emails in local.topics : {
      for email in emails : "${topic}:${email}" => { topic = topic, email = email }
    }
  ]...)
}

# ---------------------------------------------------------------------------
# Two encrypted topics: ops (reliability) and security (incident response).
# ---------------------------------------------------------------------------
resource "aws_sns_topic" "this" {
  for_each          = local.topics
  name              = "${var.name}-${each.key}-alerts"
  kms_master_key_id = var.data_key_arn
}

data "aws_iam_policy_document" "topic" {
  for_each = local.topics

  statement {
    sid       = "AccountOwnerManage"
    actions   = ["SNS:GetTopicAttributes", "SNS:SetTopicAttributes", "SNS:Subscribe", "SNS:ListSubscriptionsByTopic", "SNS:Publish"]
    resources = [aws_sns_topic.this[each.key].arn]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid       = "AlarmsAndEventRulesPublish"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.this[each.key].arn]
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
    resources = [aws_sns_topic.this[each.key].arn]
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

resource "aws_sns_topic_policy" "this" {
  for_each = local.topics
  arn      = aws_sns_topic.this[each.key].arn
  policy   = data.aws_iam_policy_document.topic[each.key].json
}

resource "aws_sns_topic_subscription" "email" {
  for_each  = local.subscriptions
  topic_arn = aws_sns_topic.this[each.value.topic].arn
  protocol  = "email"
  endpoint  = each.value.email
}

# ---------------------------------------------------------------------------
# EventBridge rules. AWS management and service events on the default bus are
# free, so these replace CloudWatch metric-filter alarms at no cost.
# ---------------------------------------------------------------------------
locals {
  # Events from global services. IAM API calls and root/global-endpoint console sign-ins are
  # delivered only to the us-east-1 default bus; sign-ins through a regional endpoint land in that
  # region. The us_east_1 module forwards matching us-east-1 events to this region's default bus, so
  # these rules alert on both. Each event reaches exactly one region's bus, so nothing is doubled.
  global_rules = {
    iam-changes = {
      topic       = "security"
      description = "IAM identity and permission changes (AC-2(4) automated audit actions)"
      pattern = jsonencode({
        "detail-type" = ["AWS API Call via CloudTrail"]
        detail = {
          eventSource = ["iam.amazonaws.com"]
          eventName = [
            "CreateUser", "DeleteUser", "CreateAccessKey", "CreateLoginProfile", "UpdateLoginProfile",
            "AttachUserPolicy", "AttachRolePolicy", "PutUserPolicy", "PutRolePolicy",
            "CreatePolicyVersion", "SetDefaultPolicyVersion", "UpdateAssumeRolePolicy",
            "DeactivateMFADevice", "DeleteVirtualMFADevice",
          ]
        }
      })
    }
    risky-sign-in = {
      topic       = "security"
      description = "Root sign-in or console sign-in without MFA (IA-2(1), AC-6(9))"
      pattern = jsonencode({
        "detail-type" = ["AWS Console Sign In via CloudTrail"]
        detail = {
          "$or" = [
            { userIdentity = { type = ["Root"] } },
            { additionalEventData = { MFAUsed = ["No"] } },
          ]
        }
      })
    }
  }

  rules = merge(local.global_rules, {
    guardduty-findings = {
      topic       = "security"
      description = "GuardDuty findings, medium severity and above"
      pattern = jsonencode({
        source        = ["aws.guardduty"]
        "detail-type" = ["GuardDuty Finding"]
        detail        = { severity = [{ numeric = [">=", 4] }] }
      })
    }
    securityhub-high = {
      topic       = "security"
      description = "New, active Security Hub findings rated HIGH or CRITICAL"
      pattern = jsonencode({
        source        = ["aws.securityhub"]
        "detail-type" = ["Security Hub Findings - Imported"]
        detail = {
          findings = {
            Severity    = { Label = ["HIGH", "CRITICAL"] }
            Workflow    = { Status = ["NEW"] }
            RecordState = ["ACTIVE"]
          }
        }
      })
    }
    inspector-high = {
      topic       = "security"
      description = "Inspector vulnerabilities rated HIGH or CRITICAL"
      pattern = jsonencode({
        source        = ["aws.inspector2"]
        "detail-type" = ["Inspector2 Finding"]
        detail        = { severity = ["HIGH", "CRITICAL"], status = ["ACTIVE"] }
      })
    }
    audit-tampering = {
      topic       = "security"
      description = "Attempts to weaken logging, monitoring or encryption (AU-5, AU-9, SI-4)"
      pattern = jsonencode({
        "detail-type" = ["AWS API Call via CloudTrail"]
        detail = {
          eventSource = [
            "cloudtrail.amazonaws.com", "config.amazonaws.com", "guardduty.amazonaws.com",
            "securityhub.amazonaws.com", "kms.amazonaws.com", "inspector2.amazonaws.com",
          ]
          eventName = [
            "StopLogging", "DeleteTrail", "UpdateTrail", "PutEventSelectors", "PutInsightSelectors",
            "StopConfigurationRecorder", "DeleteConfigurationRecorder", "DeleteDeliveryChannel",
            "DeleteDetector", "UpdateDetector", "DeleteMalwareProtectionPlan",
            "DisableSecurityHub", "BatchDisableStandards", "UpdateStandardsControl",
            "DisableKey", "ScheduleKeyDeletion", "PutKeyPolicy",
            "Disable",
          ]
        }
      })
    }
    s3-exposure = {
      topic       = "security"
      description = "Bucket policy, ACL, public access or Object Lock changes (AC-3, SC-7)"
      pattern = jsonencode({
        "detail-type" = ["AWS API Call via CloudTrail"]
        detail = {
          eventSource = ["s3.amazonaws.com"]
          eventName = [
            "PutBucketPolicy", "DeleteBucketPolicy", "PutBucketAcl", "PutBucketPublicAccessBlock",
            "DeleteBucketPublicAccessBlock", "PutAccountPublicAccessBlock", "DeleteAccountPublicAccessBlock",
            "PutObjectLockConfiguration", "PutBucketReplication", "DeleteBucketReplication",
          ]
        }
      })
    }
    aws-health = {
      topic       = "ops"
      description = "AWS Health events for services this account uses (CP-2, SA-9)"
      pattern = jsonencode({
        source = ["aws.health"]
      })
    }
    api-key-changed = {
      topic       = "security"
      description = "A provider API key was stored, changed or deleted (IA-5 rotation evidence, AC-2(4))"
      pattern = jsonencode({
        "detail-type" = ["AWS API Call via CloudTrail"]
        detail = {
          eventSource       = ["ssm.amazonaws.com"]
          eventName         = ["PutParameter", "DeleteParameter", "LabelParameterVersion"]
          requestParameters = { name = [{ prefix = "/${var.name}/api-keys/" }] }
        }
      })
    }
  })
}

resource "aws_cloudwatch_event_rule" "this" {
  for_each      = local.rules
  name          = "${var.name}-${each.key}"
  description   = each.value.description
  event_pattern = each.value.pattern
}

resource "aws_cloudwatch_event_target" "this" {
  for_each  = local.rules
  rule      = aws_cloudwatch_event_rule.this[each.key].name
  target_id = "sns-${each.value.topic}"
  arn       = aws_sns_topic.this[each.value.topic].arn
}

output "ops_topic_arn" {
  value = aws_sns_topic.this["ops"].arn
}

output "security_topic_arn" {
  value = aws_sns_topic.this["security"].arn
}

output "global_event_patterns" {
  description = "Patterns for global-service events, forwarded from us-east-1 to this region's default bus."
  value       = { for k, v in local.global_rules : k => v.pattern }
}
