# Security Hub fixes (#320): Config recorder service-linked role, ECR lifecycle
# that keeps the live digest, and a default security group with no rules.
#
# Terraform test can read module outputs, not resources nested in a child
# module. The outputs below are those resource attributes:
#   module.audit_logging.aws_config_configuration_recorder.this.role_arn
#   module.audit_logging.aws_config_configuration_recorder.this.name
#   module.audit_logging.aws_iam_service_linked_role.config
#   module.data_lake.jobs_lifecycle_policy
#   module.security_services.aws_default_security_group.default.ingress
#   module.security_services.aws_default_security_group.default.egress
#
# Run: terraform init -backend=false && terraform test

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-west-1", name = "us-west-1" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_vpc" {
    defaults = { id = "vpc-0123456789abcdef0" }
  }

  # Computed ingress/egress are otherwise filled with generated values. An
  # empty default matches a configuration that sets no rules. Blocks in the
  # configuration still override this.
  mock_resource "aws_default_security_group" {
    defaults = {
      ingress = []
      egress  = []
    }
  }
}

mock_provider "aws" {
  alias = "dr"

  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-west-2", name = "us-west-2" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

mock_provider "aws" {
  alias = "us_east_1"

  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-east-1", name = "us-east-1" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

mock_provider "archive" {}

variables {
  alert_emails   = ["ops@example.com"]
  alert_sms_e164 = "+14155552671"
  jobs_image_uri = "123456789012.dkr.ecr.us-west-1.amazonaws.com/invdash/jobs:test"
  health_url     = "https://example.com/health.json"
}

run "security_hub_fixes" {
  command = plan

  assert {
    condition = endswith(
      module.audit_logging.config_recorder_role_arn,
      "role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig",
    )
    error_message = "Config recorder must use AWSServiceRoleForConfig."
  }

  assert {
    condition     = module.audit_logging.config_recorder_name == "${var.project}-recorder"
    error_message = "Recorder name must stay ${var.project}-recorder; a name change forces replacement."
  }

  assert {
    condition     = module.audit_logging.config_service_linked_role_service == "config.amazonaws.com"
    error_message = "The Config service-linked role must be created for config.amazonaws.com."
  }

  assert {
    condition = anytrue([
      for rule in jsondecode(module.data_lake.jobs_lifecycle_policy).rules :
      try(rule.selection.tagStatus, "") == "untagged"
      && try(rule.selection.countType, "") == "sinceImagePushed"
      && try(rule.selection.countUnit, "") == "days"
      && try(rule.selection.countNumber, 99) <= 7
      && try(rule.action.type, "") == "expire"
    ])
    error_message = "Lifecycle policy must expire untagged images within 7 days of push."
  }

  assert {
    condition = anytrue([
      for rule in jsondecode(module.data_lake.jobs_lifecycle_policy).rules :
      try(rule.selection.tagStatus, "") == "any"
      && try(rule.selection.countType, "") == "imageCountMoreThan"
      && try(rule.selection.countNumber, 99) <= 3
      && try(rule.action.type, "") == "expire"
    ])
    error_message = "The catch-all lifecycle rule must keep at most 3 images."
  }

  assert {
    condition = length([
      for rule in jsondecode(module.data_lake.jobs_lifecycle_policy).rules : rule
      if contains(try(rule.selection.tagPrefixList, []), "deployed-")
      ]) > 0 && alltrue([
      for deployed_priority in [
        for rule in jsondecode(module.data_lake.jobs_lifecycle_policy).rules :
        rule.rulePriority
        if contains(try(rule.selection.tagPrefixList, []), "deployed-")
        ] : alltrue([
          for any_priority in [
            for rule in jsondecode(module.data_lake.jobs_lifecycle_policy).rules :
            rule.rulePriority
            if try(rule.selection.tagStatus, "") == "any" && try(rule.action.type, "") == "expire"
          ] : deployed_priority < any_priority
      ])
    ])
    error_message = "A deployed- rule must exist and outrank every tagStatus=any expire rule."
  }

  assert {
    condition = alltrue([
      for rule in jsondecode(module.data_lake.jobs_lifecycle_policy).rules :
      !(
        contains(try(rule.selection.tagPrefixList, []), "deployed-")
        && try(rule.selection.countType, "") == "sinceImagePushed"
      )
    ])
    error_message = "No deployed- rule may age images out with sinceImagePushed."
  }

  assert {
    condition = (
      length(module.security_services.default_security_group_ingress) == 0
      && length(module.security_services.default_security_group_egress) == 0
    )
    error_message = "The default security group must not allow ingress or egress."
  }
}

# A fresh stack plans with an empty jobs image. The lifecycle policy and these
# assertions must not read that variable.
run "security_hub_fixes_without_jobs_image" {
  command = plan

  variables {
    jobs_image_uri = ""
  }

  assert {
    condition = endswith(
      module.audit_logging.config_recorder_role_arn,
      "role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig",
    )
    error_message = "Config recorder must use AWSServiceRoleForConfig when jobs_image_uri is empty."
  }

  assert {
    condition = anytrue([
      for rule in jsondecode(module.data_lake.jobs_lifecycle_policy).rules :
      contains(try(rule.selection.tagPrefixList, []), "deployed-")
      && try(rule.selection.countType, "") != "sinceImagePushed"
    ])
    error_message = "The deployed- lifecycle rule must still exist when jobs_image_uri is empty."
  }

  assert {
    condition = (
      length(module.security_services.default_security_group_ingress) == 0
      && length(module.security_services.default_security_group_egress) == 0
    )
    error_message = "The default security group must stay closed when jobs_image_uri is empty."
  }
}
