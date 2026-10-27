# Plan-only check that the Config service-linked role does not inherit provider
# default_tags. The root module uses the same alias for
# aws_iam_service_linked_role.config. This stack is never applied.
#
# A provider with default_tags plans tags_all to that map (and the AWS provider
# then calls iam:TagRole). The untagged alias leaves tags null and tags_all
# unknown, which is an empty tag set: no TagRole call.

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.67.0"
    }
  }
}

provider "aws" {
  region                      = "us-west-1"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true

  default_tags {
    tags = {
      Project            = "investor-dashboard"
      ManagedBy          = "terraform"
      ComplianceBaseline = "fedramp-moderate-aligned"
      DataClassification = "internal"
    }
  }
}

provider "aws" {
  alias                       = "untagged"
  region                      = "us-west-1"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

resource "aws_iam_service_linked_role" "config" {
  provider         = aws.untagged
  aws_service_name = "config.amazonaws.com"

  lifecycle {
    ignore_changes = [description, tags, tags_all]
  }
}

resource "aws_iam_service_linked_role" "control" {
  aws_service_name = "elasticloadbalancing.amazonaws.com"
}
