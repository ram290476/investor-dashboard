mock_provider "aws" {}

mock_provider "aws" {
  alias = "us_east_1"
}

run "empty_domain_keeps_today" {
  command = plan

  variables {
    site_domain = ""
  }

  assert {
    condition     = length(module.site.domain_attachment.acm_domain_names) == 0
    error_message = "Empty site_domain must not create an ACM certificate."
  }

  assert {
    condition     = module.site.domain_attachment.acm_validation_count == 0
    error_message = "Empty site_domain must not create an ACM validation waiter."
  }

  assert {
    condition     = module.site.domain_attachment.aliases == null || length(module.site.domain_attachment.aliases) == 0
    error_message = "Empty site_domain must not set a CloudFront alias."
  }

  assert {
    condition     = module.site.domain_attachment.cloudfront_default_certificate == true
    error_message = "Empty site_domain must keep the default CloudFront certificate."
  }

  assert {
    condition     = module.site.domain_attachment.minimum_protocol_version == "TLSv1"
    error_message = "Empty site_domain must keep TLSv1, which CloudFront forces on its default certificate."
  }
}

run "custom_domain_uses_acm_and_tls_12" {
  command = plan

  variables {
    site_domain = "investor.vellamsetti.com"
  }

  assert {
    condition     = module.site.domain_attachment.acm_domain_names == toset(["investor.vellamsetti.com"]) || module.site.domain_attachment.acm_domain_names == ["investor.vellamsetti.com"]
    error_message = "The ACM certificate must be for site_domain."
  }

  assert {
    condition     = module.site.domain_attachment.acm_validation_methods == toset(["DNS"]) || module.site.domain_attachment.acm_validation_methods == ["DNS"]
    error_message = "The ACM certificate must use DNS validation so the CNAME can be printed."
  }

  assert {
    condition     = module.site.domain_attachment.acm_validation_count == 1
    error_message = "site_domain must wait for the ACM certificate to be issued."
  }

  assert {
    condition     = module.site.domain_attachment.aliases == toset(["investor.vellamsetti.com"])
    error_message = "CloudFront must alias site_domain."
  }

  assert {
    condition     = module.site.domain_attachment.ssl_support_method == "sni-only"
    error_message = "The custom certificate must use SNI."
  }

  assert {
    condition     = module.site.domain_attachment.minimum_protocol_version == "TLSv1.2_2021"
    error_message = "The custom certificate must require TLS 1.2 (TLSv1.2_2021)."
  }

  assert {
    condition     = module.site.domain_attachment.cloudfront_default_certificate != true
    error_message = "The custom domain must not use the default CloudFront certificate."
  }

  assert {
    condition     = module.site.public_url == "https://investor.vellamsetti.com"
    error_message = "config.json's origin must be the custom domain when site_domain is set."
  }
}
