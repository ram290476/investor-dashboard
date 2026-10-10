terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.dr]
    }
  }
}

variable "permissions_boundary_arn" {
  description = "Permissions boundary set on every IAM role in this module (modules/workload_boundary)."
  type        = string
}

variable "name" {
  type = string
}

variable "data_key_arn" {
  type = string
}

variable "replica_key_arn" {
  type = string
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

data "aws_region" "dr" {
  provider = aws.dr
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region
  dr_region  = data.aws_region.dr.region
}

# Deny any request that is not TLS 1.2+ (SC-8).
data "aws_iam_policy_document" "tls_only" {
  for_each = {
    lake    = aws_s3_bucket.lake.arn
    replica = aws_s3_bucket.replica.arn
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value, "${each.value}/*"]
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
    sid       = "DenyOldTls"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value, "${each.value}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "NumericLessThan"
      variable = "s3:TlsVersion"
      values   = ["1.2"]
    }
  }
}

# ---------------------------------------------------------------------------
# Primary data lake
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "lake" {
  #checkov:skip=CKV_AWS_18:Object-level access is logged by CloudTrail S3 data events (AU-2/AU-12)
  #checkov:skip=CKV2_AWS_62:Event notifications are set by GuardDuty Malware Protection for raw/; nothing else consumes them
  bucket = "${var.name}-lake-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.data_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_policy" "lake" {
  bucket     = aws_s3_bucket.lake.id
  policy     = data.aws_iam_policy_document.tls_only["lake"].json
  depends_on = [aws_s3_bucket_public_access_block.lake]
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "company-ir-intelligent-tiering"
    status = "Enabled"
    filter {
      prefix = "raw/company_ir/"
    }
    transition {
      days          = 0
      storage_class = "INTELLIGENT_TIERING"
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "raw-to-glacier-after-1-year"
    status = "Enabled"
    filter {
      prefix = "raw/"
    }
    transition {
      days          = 365
      storage_class = "GLACIER_IR"
    }
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "curated-old-versions"
    status = "Enabled"
    filter {
      prefix = "curated/"
    }
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "serving-old-versions"
    status = "Enabled"
    filter {
      prefix = "serving/"
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.lake]
}

# ---------------------------------------------------------------------------
# DR replica in the second region (CP-6 alternate storage, CP-9 backup)
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "replica" {
  #checkov:skip=CKV_AWS_18:DR replica written only by S3 replication; access is logged in CloudTrail management events
  #checkov:skip=CKV2_AWS_62:DR replica has no consumers
  provider = aws.dr
  bucket   = "${var.name}-lake-dr-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "replica" {
  provider = aws.dr
  bucket   = aws_s3_bucket.replica.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "replica" {
  provider = aws.dr
  bucket   = aws_s3_bucket.replica.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.replica_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "replica" {
  provider                = aws.dr
  bucket                  = aws_s3_bucket.replica.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "replica" {
  provider = aws.dr
  bucket   = aws_s3_bucket.replica.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_policy" "replica" {
  provider   = aws.dr
  bucket     = aws_s3_bucket.replica.id
  policy     = data.aws_iam_policy_document.tls_only["replica"].json
  depends_on = [aws_s3_bucket_public_access_block.replica]
}

resource "aws_s3_bucket_lifecycle_configuration" "replica" {
  provider = aws.dr
  bucket   = aws_s3_bucket.replica.id

  rule {
    id     = "replica-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.replica]
}

data "aws_iam_policy_document" "replication_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "replication" {
  name                 = "${var.name}-lake-replication"
  assume_role_policy   = data.aws_iam_policy_document.replication_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "replication" {
  statement {
    sid       = "ReadSourceConfig"
    actions   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]
  }

  statement {
    sid = "ReadSourceObjects"
    actions = [
      "s3:GetObjectVersionForReplication",
      "s3:GetObjectVersionAcl",
      "s3:GetObjectVersionTagging",
    ]
    resources = ["${aws_s3_bucket.lake.arn}/*"]
  }

  statement {
    sid       = "WriteReplica"
    actions   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
    resources = ["${aws_s3_bucket.replica.arn}/*"]
  }

  statement {
    sid       = "DecryptSource"
    actions   = ["kms:Decrypt"]
    resources = [var.data_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${local.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "EncryptReplica"
    actions   = ["kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.replica_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${local.dr_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "replication" {
  name   = "replicate-lake"
  role   = aws_iam_role.replication.id
  policy = data.aws_iam_policy_document.replication.json
}

resource "aws_s3_bucket_replication_configuration" "lake" {
  role   = aws_iam_role.replication.arn
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "replicate-everything"
    status = "Enabled"

    filter {}

    delete_marker_replication {
      status = "Enabled"
    }

    source_selection_criteria {
      sse_kms_encrypted_objects {
        status = "Enabled"
      }
    }

    destination {
      bucket        = aws_s3_bucket.replica.arn
      storage_class = "STANDARD"
      encryption_configuration {
        replica_kms_key_id = var.replica_key_arn
      }
    }
  }

  depends_on = [
    aws_s3_bucket_versioning.lake,
    aws_s3_bucket_versioning.replica,
  ]
}

# ---------------------------------------------------------------------------
# Dead-letter queue for failed job runs (SI-11 error handling, CP-10 recovery)
# ---------------------------------------------------------------------------
resource "aws_sqs_queue" "dlq" {
  name                              = "${var.name}-job-dlq"
  message_retention_seconds         = 1209600 # 14 days
  kms_master_key_id                 = var.data_key_arn
  kms_data_key_reuse_period_seconds = 300
}

data "aws_iam_policy_document" "dlq" {
  statement {
    sid       = "EventBridgeFailedDeliveries"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dlq.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
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
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.dlq.arn]
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

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.dlq.json
}

# ---------------------------------------------------------------------------
# Container registry for the job image (CM-2 baseline, SI-7 integrity)
# ---------------------------------------------------------------------------
resource "aws_ecr_repository" "jobs" {
  name                 = "${var.name}/jobs"
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.data_key_arn
  }

  image_scanning_configuration {
    scan_on_push = true # Superseded by Inspector enhanced scanning at the registry level.
  }
}

resource "aws_ecr_lifecycle_policy" "jobs" {
  repository = aws_ecr_repository.jobs.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 newest images for rollback"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

output "lake_bucket_name" {
  value = aws_s3_bucket.lake.id
}

output "lake_bucket_arn" {
  value = aws_s3_bucket.lake.arn
}

output "replica_bucket_arn" {
  value = aws_s3_bucket.replica.arn
}

output "dlq_name" {
  value = aws_sqs_queue.dlq.name
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "ecr_repository_url" {
  value = aws_ecr_repository.jobs.repository_url
}
