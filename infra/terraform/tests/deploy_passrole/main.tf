# Plans the real deploy-role policy (no mock of aws_iam_policy_document) and
# checks the Config service-linked role PassRole grant. This stack is never applied.
#
# The deploy role has no permissions boundary. invdash-workload-boundary is
# attached only to invdash-* roles; its PassRole grant stays the jobs scheduler.

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
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

module "github_deploy" {
  source = "../../modules/github_deploy"

  name                  = "invdash"
  github_repository     = "ram290476/investor-dashboard"
  github_owner_id       = "48363891"
  github_repository_id  = "1403740156"
  github_environment    = "production"
  create_oidc_provider  = false
  oidc_provider_arn     = "arn:aws:iam::308639168050:oidc-provider/token.actions.githubusercontent.com"
  workload_boundary_arn = "arn:aws:iam::308639168050:policy/invdash-workload-boundary"
}

module "workload_boundary" {
  source = "../../modules/workload_boundary"

  name = "invdash"
}

locals {
  config_slr_arn = "arn:aws:iam::308639168050:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig"
  project_role   = "arn:aws:iam::308639168050:role/invdash-*"
  jobs_scheduler = "arn:aws:iam::308639168050:role/invdash-jobs-scheduler"

  deploy_statements   = jsondecode(module.github_deploy.iam_for_stack_policy).Statement
  boundary_statements = jsondecode(module.workload_boundary.policy_json).Statement
}

locals {
  deploy_passrole = [
    for statement in local.deploy_statements : {
      sid     = can(statement.Sid) ? statement.Sid : ""
      actions = sort(can(statement.Action) ? try(tolist(statement.Action), [statement.Action]) : [])
      resources = sort(
        can(statement.Resource) ? try(tolist(statement.Resource), [statement.Resource]) : []
      )
      services = sort(
        can(statement.Condition) ? try(
          tolist(statement.Condition.StringEquals["iam:PassedToService"]),
          [statement.Condition.StringEquals["iam:PassedToService"]],
        ) : []
      )
    }
    if can(statement.Action) && contains(try(tolist(statement.Action), [statement.Action]), "iam:PassRole")
  ]

  config_passrole = [
    for statement in local.deploy_passrole : statement
    if statement.sid == "PassConfigServiceLinkedRole"
  ]

  boundary_passrole = [
    for statement in local.boundary_statements : {
      resources = sort(
        can(statement.Resource) ? try(tolist(statement.Resource), [statement.Resource]) : []
      )
      services = sort(
        can(statement.Condition) ? try(
          tolist(statement.Condition.StringEquals["iam:PassedToService"]),
          [statement.Condition.StringEquals["iam:PassedToService"]],
        ) : []
      )
    }
    if can(statement.Action) && contains(try(tolist(statement.Action), [statement.Action]), "iam:PassRole")
  ]
}

output "config_passrole_exact" {
  value = (
    length(local.config_passrole) == 1 &&
    tolist(local.config_passrole[0].actions) == tolist(["iam:PassRole"]) &&
    tolist(local.config_passrole[0].resources) == tolist([local.config_slr_arn]) &&
    tolist(local.config_passrole[0].services) == tolist(["config.amazonaws.com"])
  )
}

output "passrole_resources_are_bounded" {
  value = (
    length(local.deploy_passrole) == 2 &&
    sort(flatten([for statement in local.deploy_passrole : statement.resources])) == sort([
      local.config_slr_arn,
      local.project_role,
    ]) &&
    length([
      for statement in local.deploy_passrole : statement
      if contains(statement.resources, "*") || contains(statement.resources, "arn:aws:iam::*:*")
    ]) == 0
  )
}

output "deploy_role_has_no_boundary" {
  value = module.github_deploy.deploy_role_permissions_boundary == null
}

output "boundary_passrole_stays_jobs_scheduler" {
  value = (
    length(local.boundary_passrole) == 1 &&
    tolist(local.boundary_passrole[0].resources) == tolist([local.jobs_scheduler]) &&
    tolist(local.boundary_passrole[0].services) == tolist(["scheduler.amazonaws.com"])
  )
}
