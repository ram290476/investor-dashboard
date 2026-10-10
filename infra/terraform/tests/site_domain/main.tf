# Plan-only harness for the site module. Terraform test mocks AWS, so this does not
# create certificates, distributions or DNS records.

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0, < 7.0"
    }
  }
}

provider "aws" {
  region = "us-west-1"
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

variable "site_domain" {
  type    = string
  default = ""
}

module "site" {
  source = "../../modules/site"
  providers = {
    aws.us_east_1 = aws.us_east_1
  }

  name        = "invdash"
  region      = "us-west-1"
  site_domain = var.site_domain
}
