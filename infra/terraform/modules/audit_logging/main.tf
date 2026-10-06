variable "name" {
  type = string
}

variable "permissions_boundary_arn" {
  description = "Permissions boundary set on every IAM role in this module (modules/workload_boundary)."
  type        = string
}

variable "trail_name" {
  type = string
}

variable "audit_key_arn" {
  type = string
}

variable "lake_bucket_arn" {
  type = string
}

variable "retention_days" {
  type = number
}

variable "object_lock_mode" {
  type = string
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region
  trail_arn  = "arn:${local.partition}:cloudtrail:${local.region}:${local.account_id}:trail/${var.trail_name}"
}

# ---------------------------------------------------------------------------
# Audit bucket (CloudTrail): Object Lock + KMS + 12 months Standard, then Glacier,
# kept for retention_days in total (AU-9 protection, AU-11 retention).
# AWS Config cannot deliver to a bucket with Object Lock default retention, so its
# records go to the separate config bucket below.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "audit" {
  #checkov:skip=CKV_AWS_18:This is the log destination; CloudTrail records here are integrity-validated and Object-Locked
  #checkov:skip=CKV2_AWS_62:No consumers of object events; tampering is caught by Object Lock and the audit-tampering rule
  #checkov:skip=CKV_AWS_144:AU-9(2) separate-system copy is a High-baseline control; Object Lock covers Moderate AU-9
  bucket              = "${var.name}-audit-${local.account_id}"
  object_lock_enabled = true
}

resource "aws_s3_bucket_versioning" "audit" {
  bucket = aws_s3_bucket.audit.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    default_retention {
      mode = var.object_lock_mode
      days = var.retention_days
    }
  }
  depends_on = [aws_s3_bucket_versioning.audit]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.audit_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "audit" {
  bucket                  = aws_s3_bucket.audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id

  rule {
    id     = "searchable-12-months-then-cold"
    status = "Enabled"
    filter {}
    transition {
      days          = 365
      storage_class = "GLACIER"
    }
    expiration {
      days = var.retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.audit]
}

data "aws_iam_policy_document" "audit_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.audit.arn, "${aws_s3_bucket.audit.arn}/*"]
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

  statement {
    sid       = "CloudTrailAclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.audit.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid       = "CloudTrailWrite"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.audit.arn}/AWSLogs/${local.account_id}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  # Nobody except the delivery services may delete audit objects (AU-9).
  statement {
    sid       = "DenyAuditDeletion"
    effect    = "Deny"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutLifecycleConfiguration"]
    resources = [aws_s3_bucket.audit.arn, "${aws_s3_bucket.audit.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "StringNotLike"
      variable = "aws:PrincipalArn"
      values = [
        "arn:${local.partition}:iam::${local.account_id}:role/aws-reserved/sso.amazonaws.com/*AdministratorAccess*",
        "arn:${local.partition}:iam::${local.account_id}:role/${var.name}-terraform-deploy",
      ]
    }
  }
}

resource "aws_s3_bucket_policy" "audit" {
  bucket     = aws_s3_bucket.audit.id
  policy     = data.aws_iam_policy_document.audit_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.audit]
}

# ---------------------------------------------------------------------------
# Config bucket: AWS Config history and snapshots (CM-2, CM-8, AU-11). Same KMS key,
# TLS-only access, delete-deny and retention as the audit bucket, but no Object Lock
# default retention, which AWS Config does not support. Versioning plus the deny
# below (including suspending versioning) protects the records instead (AU-9).
# ---------------------------------------------------------------------------
locals {
  config_bucket_name = "${var.name}-config-${local.account_id}"
  config_prefix_arn  = "arn:${local.partition}:s3:::${local.config_bucket_name}/AWSLogs/${local.account_id}/Config/*"
  # Principals allowed to delete records or change retention (same as the audit bucket).
  records_admins = [
    "arn:${local.partition}:iam::${local.account_id}:role/aws-reserved/sso.amazonaws.com/*AdministratorAccess*",
    "arn:${local.partition}:iam::${local.account_id}:role/${var.name}-terraform-deploy",
  ]
}

resource "aws_s3_bucket" "config" {
  #checkov:skip=CKV_AWS_18:This is a log destination; access to it is recorded by CloudTrail management events
  #checkov:skip=CKV2_AWS_62:No consumers of object events; tampering is caught by the delete-deny policy and the s3-exposure rule
  #checkov:skip=CKV_AWS_144:AU-9(2) separate-system copy is a High-baseline control
  bucket = local.config_bucket_name
}

resource "aws_s3_bucket_versioning" "config" {
  bucket = aws_s3_bucket.config.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "config" {
  bucket = aws_s3_bucket.config.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.audit_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "config" {
  bucket                  = aws_s3_bucket.config.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "config" {
  bucket = aws_s3_bucket.config.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "config" {
  bucket = aws_s3_bucket.config.id

  rule {
    id     = "searchable-12-months-then-cold"
    status = "Enabled"
    filter {}
    transition {
      days          = 365
      storage_class = "GLACIER"
    }
    expiration {
      days = var.retention_days
    }
    # Without Object Lock, keep overwritten versions long enough to notice and recover.
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.config]
}

data "aws_iam_policy_document" "config_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.config.arn, "${aws_s3_bucket.config.arn}/*"]
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

  # AWS Config service access, as documented for delivery buckets; scoped to this account.
  statement {
    sid       = "AWSConfigBucketPermissionsCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.config.arn]
    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  statement {
    sid       = "AWSConfigBucketExistenceCheck"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.config.arn]
    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  statement {
    sid       = "AWSConfigBucketDelivery"
    actions   = ["s3:PutObject"]
    resources = [local.config_prefix_arn]
    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  # Nobody except the admins may delete records, shorten retention or suspend versioning (AU-9).
  statement {
    sid       = "DenyConfigRecordDeletion"
    effect    = "Deny"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutLifecycleConfiguration", "s3:PutBucketVersioning"]
    resources = [aws_s3_bucket.config.arn, "${aws_s3_bucket.config.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "StringNotLike"
      variable = "aws:PrincipalArn"
      values   = local.records_admins
    }
  }
}

resource "aws_s3_bucket_policy" "config" {
  bucket     = aws_s3_bucket.config.id
  policy     = data.aws_iam_policy_document.config_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.config]
}

# ---------------------------------------------------------------------------
# CloudTrail: all management events, S3 data events on the lake, Lambda
# invocations, log-file integrity validation, Insights (AU-2, AU-3, AU-6, AU-12).
# ---------------------------------------------------------------------------
resource "aws_cloudtrail" "this" {
  #checkov:skip=CKV2_AWS_10:Real-time detection uses free EventBridge rules on CloudTrail events instead of CloudWatch Logs delivery
  #checkov:skip=CKV_AWS_252:Alerts go through EventBridge rules to the security SNS topic, not trail-level SNS
  name                          = var.trail_name
  s3_bucket_name                = aws_s3_bucket.audit.id
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true
  kms_key_id                    = var.audit_key_arn

  advanced_event_selector {
    name = "All management events"
    field_selector {
      field  = "eventCategory"
      equals = ["Management"]
    }
  }

  advanced_event_selector {
    name = "Data lake object access"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }
    field_selector {
      field       = "resources.ARN"
      starts_with = ["${var.lake_bucket_arn}/"]
    }
  }

  advanced_event_selector {
    name = "Lambda invocations"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::Lambda::Function"]
    }
  }

  insight_selector {
    insight_type = "ApiCallRateInsight"
  }

  insight_selector {
    insight_type = "ApiErrorRateInsight"
  }

  depends_on = [aws_s3_bucket_policy.audit]
}

# ---------------------------------------------------------------------------
# AWS Config: continuous inventory and configuration history (CM-2, CM-8, CA-7).
# Security Hub's NIST 800-53 controls evaluate these records.
# Only one recorder is allowed per region; import an existing one if present.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "config_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "config" {
  name                 = "${var.name}-config-recorder"
  assume_role_policy   = data.aws_iam_policy_document.config_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_iam_role_policy_attachment" "config" {
  role       = aws_iam_role.config.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWS_ConfigRole"
}

data "aws_iam_policy_document" "config_delivery" {
  statement {
    actions   = ["s3:GetBucketAcl", "s3:ListBucket"]
    resources = [aws_s3_bucket.config.arn]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = [local.config_prefix_arn]
  }
  statement {
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [var.audit_key_arn]
  }
}

# The name predates the separate config bucket; renaming would replace the policy for no benefit.
resource "aws_iam_role_policy" "config_delivery" {
  name   = "deliver-to-audit-bucket"
  role   = aws_iam_role.config.id
  policy = data.aws_iam_policy_document.config_delivery.json
}

resource "aws_config_configuration_recorder" "this" {
  name     = "${var.name}-recorder"
  role_arn = aws_iam_role.config.arn

  recording_group {
    all_supported                 = true
    include_global_resource_types = true
  }

  recording_mode {
    recording_frequency = "CONTINUOUS"
  }
}

resource "aws_config_delivery_channel" "this" {
  name           = "${var.name}-delivery"
  s3_bucket_name = aws_s3_bucket.config.id
  s3_kms_key_arn = var.audit_key_arn

  snapshot_delivery_properties {
    delivery_frequency = "TwentyFour_Hours"
  }

  depends_on = [
    aws_config_configuration_recorder.this,
    aws_iam_role_policy.config_delivery,
    aws_s3_bucket_policy.config,
    aws_s3_bucket_server_side_encryption_configuration.config,
  ]
}

resource "aws_config_configuration_recorder_status" "this" {
  name       = aws_config_configuration_recorder.this.name
  is_enabled = true
  depends_on = [aws_config_delivery_channel.this]
}

# Lets other modules wait for Config recording without a module-wide depends_on.
output "config_recorder_status_id" {
  value = aws_config_configuration_recorder_status.this.id
}

output "audit_bucket_name" {
  value = aws_s3_bucket.audit.id
}

output "config_bucket_name" {
  value = aws_s3_bucket.config.id
}

output "trail_arn" {
  value = aws_cloudtrail.this.arn
}
