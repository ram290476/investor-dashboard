# Plans the CloudFront notifier subscription with an ARN that is unknown until
# apply. Deploy 38158533263 failed because that ARN was used in count.
# random_id.hex is unknown during plan, which is the same shape as a Lambda ARN
# that does not exist yet. This directory is not part of the production root module.

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.67.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.1"
    }
  }
}

provider "aws" {
  region                      = "us-east-1"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

resource "random_id" "notifier" {
  byte_length = 8
}

module "us_east_1" {
  source = "../../modules/us_east_1"

  permissions_boundary_arn   = "arn:aws:iam::123456789012:policy/invdash-workload-boundary"
  name                       = "invdash"
  enable_cloudfront_alarms   = true
  cloudfront_distribution_id = "E1234567890123"
  primary_region             = "us-west-1"
  forward_event_patterns     = { iam-changes = "{\"source\":[\"aws.iam\"]}" }
  alert_notifier_enabled     = true
  sms_notifier_arn           = random_id.notifier.hex
}
