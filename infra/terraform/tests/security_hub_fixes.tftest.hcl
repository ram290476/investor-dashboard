# Security Hub fixes (#320): Config recorder service-linked role, ECR lifecycle
# that keeps the live digest. The default VPC and its default security group
# were deleted on 2026-10-28, and the legacy invdash-config-recorder role is
# removed (#327).
#
# Terraform test can read module outputs, not resources nested in a child
# module. The outputs below are those resource attributes:
#   module.audit_logging.aws_config_configuration_recorder.this.role_arn
#   module.audit_logging.aws_config_configuration_recorder.this.name
#   module.audit_logging.aws_iam_service_linked_role.config
#   module.audit_logging.config_service_linked_role_arn
#   module.audit_logging.config_service_linked_role_tags
#   module.data_lake.jobs_lifecycle_policy
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

# No default_tags. The Config service-linked role is the only resource on this alias.
mock_provider "aws" {
  alias = "untagged"

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
}

# Mock providers cannot import. This stands in for the import of
# AWSServiceRoleForConfig and pins the computed ARN to the recorder literal
# (mock account 123456789012). Tags are not overridden: an empty tags value
# has to come from the configuration.
override_resource {
  target = module.audit_logging.aws_iam_service_linked_role.config
  values = {
    arn = "arn:aws:iam::123456789012:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig"
  }
  override_during = plan
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
    condition = (
      module.audit_logging.config_recorder_role_arn
      == module.audit_logging.config_service_linked_role_arn
    )
    error_message = "Config recorder role_arn must equal the service-linked role ARN."
  }

  assert {
    condition     = !strcontains(module.audit_logging.config_recorder_role_arn, "${var.project}-config-recorder")
    error_message = "Config recorder must not use the custom ${var.project}-config-recorder role."
  }

  assert {
    condition = (
      module.audit_logging.config_service_linked_role_tags == null
      || module.audit_logging.config_service_linked_role_tags == {}
    )
    error_message = "The Config service-linked role must not set tags (provider default_tags would call iam:TagRole)."
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

  # #327: the legacy custom recorder role and the default VPC/SG adoption are
  # gone from configuration.
  assert {
    condition = (
      !strcontains(file("${path.module}/modules/audit_logging/main.tf"), "config-recorder\"")
      && !strcontains(file("${path.module}/modules/audit_logging/main.tf"), "resource \"aws_iam_role\" \"config\"")
    )
    error_message = "The legacy ${var.project}-config-recorder IAM role must not be declared."
  }

  assert {
    condition = (
      !strcontains(file("${path.module}/modules/security_services/main.tf"), "data \"aws_vpc\" \"default\"")
      && !strcontains(file("${path.module}/modules/security_services/main.tf"), "resource \"aws_default_security_group\"")
    )
    error_message = "The default VPC lookup and aws_default_security_group must not be declared; the default VPC was deleted."
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
    condition = (
      module.audit_logging.config_recorder_role_arn
      == module.audit_logging.config_service_linked_role_arn
    )
    error_message = "Empty jobs_image_uri must still point the recorder at the service-linked role."
  }

  assert {
    condition     = strcontains(module.audit_logging.config_recorder_role_arn, "${var.project}-config-recorder") == false
    error_message = "Empty jobs_image_uri must not use the custom ${var.project}-config-recorder role."
  }

  assert {
    condition = (
      module.audit_logging.config_service_linked_role_tags == null
      || module.audit_logging.config_service_linked_role_tags == {}
    )
    error_message = "Empty jobs_image_uri must leave the service-linked role untagged."
  }

  assert {
    condition = anytrue([
      for rule in jsondecode(module.data_lake.jobs_lifecycle_policy).rules :
      contains(try(rule.selection.tagPrefixList, []), "deployed-")
      && try(rule.selection.countType, "") != "sinceImagePushed"
    ])
    error_message = "The deployed- lifecycle rule must still exist when jobs_image_uri is empty."
  }
}
