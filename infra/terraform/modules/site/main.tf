terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

variable "name" {
  type = string
}

variable "region" {
  type = string
}

data "aws_caller_identity" "current" {}

locals {
  bucket_name = "${var.name}-site-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "site" {
  #checkov:skip=CKV_AWS_18:Served only through CloudFront OAC; bucket changes are in CloudTrail management events
  #checkov:skip=CKV2_AWS_62:No consumers of site object events
  #checkov:skip=CKV_AWS_144:Static build output; the deploy workflow rebuilds it from git, so no DR copy is kept
  #checkov:skip=CKV_AWS_145:Public static assets with no data; SSE-S3 avoids a KMS grant to CloudFront
  bucket = local.bucket_name
}

resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    id     = "expire-noncurrent-site-builds"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.name}-site-oac"
  description                       = "Private S3 access for the Investor Dashboard static site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "site" {
  #checkov:skip=CKV_AWS_174:Default *.cloudfront.net certificate, for which CloudFront always allows TLSv1; TLS 1.2 minimum needs a custom domain and ACM certificate
  #checkov:skip=CKV2_AWS_42:No custom domain yet; uses the default CloudFront certificate
  #checkov:skip=CKV_AWS_68:Accepted risk: no WAF on the static site (cost); data is behind the Cognito-authorized API
  #checkov:skip=CKV2_AWS_47:No WAF attached (see CKV_AWS_68)
  #checkov:skip=CKV_AWS_86:Accepted gap: no CloudFront access logs for static assets; API access is logged by API Gateway
  #checkov:skip=CKV_AWS_310:Single S3 origin; the site is rebuilt by the deploy workflow
  #checkov:skip=CKV_AWS_374:Invited users may sign in from any country
  enabled             = true
  comment             = "${var.name} investor dashboard"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  http_version        = "http2and3"

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "private-dashboard-site"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id           = "private-dashboard-site"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = "658327ea-f89d-4fab-a63d-7e88639e58f6"
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site.id
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    # With the default certificate CloudFront always applies TLSv1 (it ignored the TLSv1.2_2021
    # previously set here, which showed as a change on every plan). TLS 1.2 minimum needs a custom
    # domain with an ACM certificate.
    minimum_protocol_version = "TLSv1"
  }

  tags = {
    Name      = "${var.name}-site"
    ManagedBy = "terraform"
  }
}

resource "aws_cloudfront_response_headers_policy" "site" {
  name = "${var.name}-site-security-headers"

  security_headers_config {
    content_security_policy {
      content_security_policy = "default-src 'self'; base-uri 'none'; form-action 'self' https://*.amazoncognito.com; frame-ancestors 'none'; object-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self' https://*.amazoncognito.com https://*.execute-api.${var.region}.amazonaws.com"
      override                = true
    }

    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    strict_transport_security {
      access_control_max_age_sec = 63072000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }

    xss_protection {
      mode_block = true
      protection = true
      override   = true
    }
  }
}

data "aws_iam_policy_document" "site_bucket" {
  statement {
    sid       = "AllowCloudFrontRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.site.arn, "${aws_s3_bucket.site.arn}/*"]

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

resource "aws_s3_bucket_policy" "site" {
  bucket     = aws_s3_bucket.site.id
  policy     = data.aws_iam_policy_document.site_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.site]
}

output "bucket_name" {
  value = aws_s3_bucket.site.bucket
}

output "distribution_id" {
  value = aws_cloudfront_distribution.site.id
}

output "domain_name" {
  value = aws_cloudfront_distribution.site.domain_name
}

output "url" {
  value = "https://${aws_cloudfront_distribution.site.domain_name}"
}
