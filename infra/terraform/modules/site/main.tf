terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.us_east_1]
    }
  }
}

variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "site_domain" {
  description = "Hostname this distribution serves, such as investor.vellamsetti.com. Empty keeps the default CloudFront certificate and adds no alias. This module does not create DNS records."
  type        = string
  default     = ""
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

# CloudFront only accepts an ACM certificate from us-east-1. DNS validation records are
# outputs for the registrar (GoDaddy); this module does not create them.
resource "aws_acm_certificate" "site" {
  #checkov:skip=CKV2_AWS_71:Single hostname from site_domain; no wildcard
  count    = var.site_domain == "" ? 0 : 1
  provider = aws.us_east_1

  domain_name       = var.site_domain
  validation_method = "DNS"
  key_algorithm     = "RSA_2048"

  options {
    certificate_transparency_logging_preference = "ENABLED"
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name      = "${var.name}-site"
    ManagedBy = "terraform"
  }
}

resource "aws_acm_certificate_validation" "site" {
  count    = var.site_domain == "" ? 0 : 1
  provider = aws.us_east_1

  certificate_arn = one(aws_acm_certificate.site[*].arn)
  validation_record_fqdns = flatten([
    for cert in aws_acm_certificate.site : [
      for dvo in cert.domain_validation_options : dvo.resource_record_name
    ]
  ])

  timeouts {
    create = "45m"
  }
}

resource "aws_cloudfront_distribution" "site" {
  #checkov:skip=CKV_AWS_174:With site_domain empty, the default certificate only allows TLSv1. A set site_domain uses the us-east-1 ACM certificate at TLSv1.2_2021.
  #checkov:skip=CKV2_AWS_42:ACM certificate is attached only when site_domain is set; empty keeps the default CloudFront certificate.
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

  # Empty site_domain matches the previous distribution: no alias, default certificate, TLSv1.
  # CloudFront ignores TLSv1.2_2021 on its default certificate. A set site_domain is the alias.
  aliases = var.site_domain == "" ? null : [var.site_domain]

  # No viewer-request function. Redirecting the *.cloudfront.net hostname to site_domain is
  # issue #103 and stays off (root variable redirect_cloudfront_to_custom_domain).
  dynamic "viewer_certificate" {
    for_each = var.site_domain == "" ? [1] : []
    content {
      cloudfront_default_certificate = true
      minimum_protocol_version       = "TLSv1"
    }
  }

  dynamic "viewer_certificate" {
    for_each = var.site_domain == "" ? [] : [1]
    content {
      acm_certificate_arn      = one(aws_acm_certificate_validation.site[*].certificate_arn)
      ssl_support_method       = "sni-only"
      minimum_protocol_version = "TLSv1.2_2021"
    }
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
      # 'self' follows the viewer host, so investor.vellamsetti.com needs no extra source.
      # connect-src and form-action already allow the Cognito hosted UI and the regional API.
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
  description = "CloudFront origin. Stays allowed for Cognito and CORS when a custom domain is primary."
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "public_url" {
  description = "Origin written into config.json. The custom domain when site_domain is set; otherwise the CloudFront URL."
  value       = var.site_domain == "" ? "https://${aws_cloudfront_distribution.site.domain_name}" : "https://${var.site_domain}"
}

output "domain_attachment" {
  description = "Alias and TLS mode actually set on the distribution, for plan tests and for checking an empty site_domain still matches today's certificate."
  value = {
    aliases                        = aws_cloudfront_distribution.site.aliases
    minimum_protocol_version       = one(aws_cloudfront_distribution.site.viewer_certificate[*].minimum_protocol_version)
    cloudfront_default_certificate = one(aws_cloudfront_distribution.site.viewer_certificate[*].cloudfront_default_certificate)
    ssl_support_method             = one(aws_cloudfront_distribution.site.viewer_certificate[*].ssl_support_method)
    acm_domain_names               = aws_acm_certificate.site[*].domain_name
    acm_validation_methods         = aws_acm_certificate.site[*].validation_method
    acm_validation_count           = length(aws_acm_certificate_validation.site)
  }
}

output "acm_dns_validation_records" {
  description = "CNAME records the DNS host must add before ACM will issue the certificate. Empty when site_domain is empty."
  value = [
    for dvo in flatten([
      for cert in aws_acm_certificate.site : tolist(cert.domain_validation_options)
      ]) : {
      name  = dvo.resource_record_name
      type  = dvo.resource_record_type
      value = dvo.resource_record_value
    }
  ]
}
