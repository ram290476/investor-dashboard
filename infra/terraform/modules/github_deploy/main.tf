# GitHub Actions deploy access (IA-2, IA-5, AC-6, CM-3): an OIDC identity provider for
# token.actions.githubusercontent.com and the invdash-terraform-deploy role, assumable only by
# workflow jobs running in this repository's protected GitHub environment. No long-lived keys.
#
# The deploy workflow runs `terraform apply` for this whole stack, so the role needs broad rights:
# PowerUserAccess (everything except IAM, Organizations and Account) plus IAM limited to this
# project's `<name>-*` roles and policies. It cannot change its own role or the OIDC provider, so
# widening CI's access always needs an administrator apply. See docs/operations.md.

variable "name" {
  type = string
}

variable "github_repository" {
  description = "owner/repo allowed to assume the role."
  type        = string
}

variable "github_environment" {
  description = "GitHub environment whose jobs may assume the role (protect it with required reviewers)."
  type        = string
}

variable "create_oidc_provider" {
  description = "Create the account's GitHub OIDC provider. Set false if one already exists and pass oidc_provider_arn."
  type        = bool
}

variable "oidc_provider_arn" {
  description = "Existing provider ARN when create_oidc_provider is false."
  type        = string
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  partition    = data.aws_partition.current.partition
  issuer       = "token.actions.githubusercontent.com"
  role_name    = "${var.name}-terraform-deploy" # also the name the audit and config bucket policies allow
  provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.oidc_provider_arn
  project_role = "arn:${local.partition}:iam::${local.account_id}:role/${var.name}-*"
  project_pol  = "arn:${local.partition}:iam::${local.account_id}:policy/${var.name}-*"
  self_role    = "arn:${local.partition}:iam::${local.account_id}:role/${local.role_name}"
  oidc_arn     = "arn:${local.partition}:iam::${local.account_id}:oidc-provider/${local.issuer}"
}

# AWS validates GitHub's OIDC certificates itself, so no thumbprint is pinned.
resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url            = "https://${local.issuer}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.issuer}:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only jobs that run in the protected environment of this repository; no branches, tags or PRs.
    condition {
      test     = "StringEquals"
      variable = "${local.issuer}:sub"
      values   = ["repo:${var.github_repository}:environment:${var.github_environment}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = local.role_name
  description          = "GitHub Actions deploy for ${var.github_repository} (${var.github_environment} environment only)"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

# Everything except IAM, Organizations and Account.
resource "aws_iam_role_policy_attachment" "power_user" {
  role       = aws_iam_role.deploy.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "iam_for_stack" {
  statement {
    sid       = "ReadIam"
    actions   = ["iam:Get*", "iam:List*"]
    resources = ["*"]
  }

  statement {
    sid = "ManageProjectRoles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateRoleDescription",
      "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole",
      "iam:PutRolePolicy", "iam:DeleteRolePolicy",
    ]
    resources = [local.project_role]
  }

  # Only the managed policies this stack attaches: AWS_ConfigRole and the project's own policies.
  statement {
    sid       = "AttachKnownPolicies"
    actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = [local.project_role]
    condition {
      test     = "ArnLike"
      variable = "iam:PolicyARN"
      values = [
        "arn:${local.partition}:iam::aws:policy/service-role/AWS_ConfigRole",
        local.project_pol,
      ]
    }
  }

  statement {
    sid = "ManageProjectPolicies"
    actions = [
      "iam:CreatePolicy", "iam:DeletePolicy", "iam:CreatePolicyVersion", "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion", "iam:TagPolicy", "iam:UntagPolicy",
    ]
    resources = [local.project_pol]
  }

  # Hand project roles only to the services this stack configures.
  statement {
    sid       = "PassProjectRoles"
    actions   = ["iam:PassRole"]
    resources = [local.project_role]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values = [
        "lambda.amazonaws.com", "scheduler.amazonaws.com", "config.amazonaws.com", "s3.amazonaws.com",
        "events.amazonaws.com", "malware-protection-plan.guardduty.amazonaws.com", "synthetics.amazonaws.com",
      ]
    }
  }

  statement {
    sid       = "AccountPasswordPolicy"
    actions   = ["iam:UpdateAccountPasswordPolicy", "iam:DeleteAccountPasswordPolicy"]
    resources = ["*"]
  }

  # CI can never widen its own access or repoint the identity provider.
  statement {
    sid         = "NoSelfModification"
    effect      = "Deny"
    not_actions = ["iam:Get*", "iam:List*"]
    resources   = [local.self_role, local.oidc_arn]
  }
}

resource "aws_iam_role_policy" "iam_for_stack" {
  name   = "iam-for-this-stack"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.iam_for_stack.json
}

output "role_arn" {
  value = aws_iam_role.deploy.arn
}

output "oidc_provider_arn" {
  value = local.provider_arn
}
