variable "name" {
  type = string
}

variable "lake_bucket_name" {
  type = string
}

variable "data_key_arn" {
  type = string
}

variable "enable_malware_protection" {
  type = bool
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region
  lake_arn   = "arn:${local.partition}:s3:::${var.lake_bucket_name}"
}

# ---------------------------------------------------------------------------
# GuardDuty: threat detection on CloudTrail, S3 data events and Lambda network
# activity (SI-4 system monitoring, IR-4 incident handling).
# ---------------------------------------------------------------------------
resource "aws_guardduty_detector" "this" {
  #checkov:skip=CKV2_AWS_3:Single-account deployment; no AWS Organization to delegate GuardDuty to
  enable                       = true
  finding_publishing_frequency = "FIFTEEN_MINUTES"
}

resource "aws_guardduty_detector_feature" "s3" {
  detector_id = aws_guardduty_detector.this.id
  name        = "S3_DATA_EVENTS"
  status      = "ENABLED"
}

resource "aws_guardduty_detector_feature" "lambda" {
  detector_id = aws_guardduty_detector.this.id
  name        = "LAMBDA_NETWORK_LOGS"
  status      = "ENABLED"
}

# Malware scanning of files pulled from the internet into raw/ (SI-3).
# Permissions follow the AWS-documented policy for Malware Protection for S3.
data "aws_iam_policy_document" "malware_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["malware-protection-plan.guardduty.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "malware" {
  statement {
    sid       = "AllowManagedRuleToSendS3EventsToGuardDuty"
    actions   = ["events:PutRule", "events:DeleteRule", "events:PutTargets", "events:RemoveTargets"]
    resources = ["arn:${local.partition}:events:${local.region}:${local.account_id}:rule/DO-NOT-DELETE-AmazonGuardDutyMalwareProtectionS3*"]
    condition {
      test     = "StringLike"
      variable = "events:ManagedBy"
      values   = ["malware-protection-plan.guardduty.amazonaws.com"]
    }
  }
  statement {
    sid       = "AllowGuardDutyToMonitorEventBridgeManagedRule"
    actions   = ["events:DescribeRule", "events:ListTargetsByRule"]
    resources = ["arn:${local.partition}:events:${local.region}:${local.account_id}:rule/DO-NOT-DELETE-AmazonGuardDutyMalwareProtectionS3*"]
  }
  statement {
    sid       = "AllowPostScanTag"
    actions   = ["s3:PutObjectTagging", "s3:GetObjectTagging", "s3:PutObjectVersionTagging", "s3:GetObjectVersionTagging"]
    resources = ["${local.lake_arn}/*"]
  }
  statement {
    sid       = "AllowEnableS3EventBridgeEvents"
    actions   = ["s3:PutBucketNotification", "s3:GetBucketNotification"]
    resources = [local.lake_arn]
  }
  statement {
    sid       = "AllowPutValidationObject"
    actions   = ["s3:PutObject"]
    resources = ["${local.lake_arn}/malware-protection-resource-validation-object"]
  }
  statement {
    sid       = "AllowCheckBucketOwnership"
    actions   = ["s3:ListBucket"]
    resources = [local.lake_arn]
  }
  statement {
    sid       = "AllowMalwareScan"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${local.lake_arn}/*"]
  }
  statement {
    sid       = "AllowDecryptForMalwareScan"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [var.data_key_arn]
    condition {
      test     = "StringLike"
      variable = "kms:ViaService"
      values   = ["s3.${local.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "malware" {
  count              = var.enable_malware_protection ? 1 : 0
  name               = "${var.name}-guardduty-malware-s3"
  assume_role_policy = data.aws_iam_policy_document.malware_trust.json
}

resource "aws_iam_role_policy" "malware" {
  count  = var.enable_malware_protection ? 1 : 0
  name   = "malware-protection-s3"
  role   = aws_iam_role.malware[0].id
  policy = data.aws_iam_policy_document.malware.json
}

resource "aws_guardduty_malware_protection_plan" "raw" {
  count = var.enable_malware_protection ? 1 : 0
  role  = aws_iam_role.malware[0].arn

  protected_resource {
    s3_bucket {
      bucket_name     = var.lake_bucket_name
      object_prefixes = ["raw/"]
    }
  }

  actions {
    tagging {
      status = "ENABLED" # Tags each object NO_THREATS_FOUND / THREATS_FOUND; parsers skip anything not clean.
    }
  }

  depends_on = [aws_iam_role_policy.malware]
}

# ---------------------------------------------------------------------------
# Security Hub CSPM with the NIST SP 800-53 Rev 5 standard: continuous control
# checks plus one place for GuardDuty and Inspector findings (CA-7, RA-5).
# ---------------------------------------------------------------------------
resource "aws_securityhub_account" "this" {
  enable_default_standards  = false
  auto_enable_controls      = true
  control_finding_generator = "SECURITY_CONTROL"
}

resource "aws_securityhub_standards_subscription" "nist_800_53" {
  standards_arn = "arn:${local.partition}:securityhub:${local.region}::standards/nist-800-53/v/5.0.0"
  depends_on    = [aws_securityhub_account.this]
}

# ---------------------------------------------------------------------------
# Inspector: continuous vulnerability scanning of the container image and any
# zip-packaged Lambda functions (RA-5, SI-2).
# ---------------------------------------------------------------------------
resource "aws_inspector2_enabler" "this" {
  account_ids    = [local.account_id]
  resource_types = ["ECR", "LAMBDA"]
}

resource "aws_ecr_registry_scanning_configuration" "this" {
  scan_type = "ENHANCED"

  rule {
    scan_frequency = "CONTINUOUS_SCAN"
    repository_filter {
      filter      = "*"
      filter_type = "WILDCARD"
    }
  }

  depends_on = [aws_inspector2_enabler.this]
}

# ---------------------------------------------------------------------------
# Account guardrails (AC-3, AC-6, CM-6, CM-7, IA-5)
# ---------------------------------------------------------------------------
resource "aws_accessanalyzer_analyzer" "external" {
  analyzer_name = "${var.name}-external-access"
  type          = "ACCOUNT"
}

resource "aws_s3_account_public_access_block" "this" {
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_ebs_encryption_by_default" "this" {
  enabled = true
}

# Applies only to IAM users; people should sign in through IAM Identity Center with MFA.
resource "aws_iam_account_password_policy" "this" {
  #checkov:skip=CKV_AWS_9:NIST SP 800-63B and FedRAMP Rev 5 IA-5(1) drop forced periodic expiry; people sign in via Identity Center with MFA
  minimum_password_length        = 15
  password_reuse_prevention      = 24
  require_lowercase_characters   = true
  require_uppercase_characters   = true
  require_numbers                = true
  require_symbols                = true
  allow_users_to_change_password = true
}

output "guardduty_detector_id" {
  value = aws_guardduty_detector.this.id
}
