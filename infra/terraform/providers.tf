locals {
  default_tags = {
    Project            = "investor-dashboard"
    ManagedBy          = "terraform"
    ComplianceBaseline = "fedramp-moderate-aligned"
    DataClassification = "internal"
  }
}

# Primary region (default us-west-1). The US East/West regions (us-east-1, us-east-2, us-west-1,
# us-west-2) are FedRAMP Moderate authorized for every service used here except Security Hub and
# Budgets, which AWS lists as "FedRAMP not required" management tools.
provider "aws" {
  region = var.region

  # SC-13: set to true to send Terraform's own API calls to FIPS 140-validated endpoints.
  # Leave false until you confirm every service below has a FIPS endpoint in your region
  # (Synthetics and Security Hub do not in all regions).
  use_fips_endpoint = var.use_fips_endpoint

  default_tags {
    tags = local.default_tags
  }
}

# Disaster-recovery region for the replicated data lake and replica KMS key (CP-6, CP-9).
provider "aws" {
  alias  = "dr"
  region = var.dr_region

  use_fips_endpoint = var.use_fips_endpoint

  default_tags {
    tags = local.default_tags
  }
}

# us-east-1, for what AWS only offers there: CloudFront metrics (and so the CloudFront alarm) and
# the event bus that receives IAM and root/global-endpoint sign-in events. See modules/us_east_1.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  use_fips_endpoint = var.use_fips_endpoint

  default_tags {
    tags = local.default_tags
  }
}
