# GitHub Actions deploy access (IA-2, IA-5, AC-6, CM-3): an OIDC identity provider for
# token.actions.githubusercontent.com and the invdash-terraform-deploy role. No long-lived keys.
#
# Who can assume it (trust policy): jobs whose OIDC token has
#   - sub = this repository's <github_environment> environment, in GitHub's immutable-ID form
#     (repo:OWNER@OWNER_ID/REPO@REPO_ID:environment:ENV) or the older name-only form;
#   - repository_id and repository_owner_id = the repository's numeric IDs, so the name-only form
#     can't be satisfied by a different repository that later takes the same name;
#   - ref = <github_ref>, and job_workflow_ref = <github_workflow_path> at that ref.
# The ref, job_workflow_ref and ID keys are GitHub claims AWS STS has evaluated since Feb 2026.
#
# What it can do: the deploy workflow runs `terraform apply` for this whole stack, so the role is
# broad. It has PowerUserAccess (everything except IAM, Organizations and Account) plus IAM limited
# to this project's `<name>-*` roles and policies. Every role write is allowed only when the role
# carries the workload permissions boundary (modules/workload_boundary), and that boundary allows
# no IAM writes and no role assumption except the prefs API hop. So a role CI creates or edits
# can't be used to change IAM, including this role or the identity provider. Explicit denies stop
# it from changing itself, the provider or the boundary, removing boundaries, assuming any role,
# or touching Identity Center. Changing any of those needs an administrator apply.
# See docs/operations.md for what this does not cover.

variable "name" {
  type = string
}

variable "github_repository" {
  description = "owner/repo allowed to assume the role."
  type        = string
}

variable "github_owner_id" {
  description = "Numeric GitHub ID of the repository owner (immutable; appears in the sub claim and repository_owner_id)."
  type        = string
}

variable "github_repository_id" {
  description = "Numeric GitHub ID of the repository (immutable; appears in the sub claim and repository_id)."
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

variable "workload_boundary_arn" {
  description = "Permissions boundary every <name>-* role must carry for the deploy role to create or change it."
  type        = string
}

variable "github_ref" {
  description = "Only workflow runs for this git ref may assume the role."
  type        = string
  default     = "refs/heads/main"
}

variable "github_workflow_path" {
  description = "Only this workflow file (at github_ref) may assume the role."
  type        = string
  default     = ".github/workflows/deploy.yml"
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

  # GitHub sub claims for jobs in the deploy environment. Repositories created after
  # 2026-07-15 (this one) or opted in use the immutable-ID form; the name-only form is kept so a
  # GitHub-side rollback or format change doesn't lock deploys out. Both are exact strings.
  github_owner  = split("/", var.github_repository)[0]
  github_repo   = split("/", var.github_repository)[1]
  sub_immutable = "repo:${local.github_owner}@${var.github_owner_id}/${local.github_repo}@${var.github_repository_id}:environment:${var.github_environment}"
  sub_name_only = "repo:${var.github_repository}:environment:${var.github_environment}"
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
    # sub identifies the repository and environment only. When a job uses an environment,
    # GitHub's default sub has no branch, tag or PR in it, so the ref is checked separately below.
    # Either exact form is accepted (StringEquals with a list is an OR of exact matches).
    condition {
      test     = "StringEquals"
      variable = "${local.issuer}:sub"
      values   = [local.sub_immutable, local.sub_name_only]
    }
    # The repository's immutable IDs, required whichever sub form GitHub sends.
    condition {
      test     = "StringEquals"
      variable = "${local.issuer}:repository_id"
      values   = [var.github_repository_id]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.issuer}:repository_owner_id"
      values   = [var.github_owner_id]
    }
    # Branch restriction enforced by AWS: the run's ref must be main...
    condition {
      test     = "StringEquals"
      variable = "${local.issuer}:ref"
      values   = [var.github_ref]
    }
    # ...and the job must come from the deploy workflow file as it exists on main. For a job that
    # isn't in a reusable workflow, job_workflow_ref equals workflow_ref and is name-based
    # (owner/repo/.github/workflows/<file>@<ref>); the immutable IDs only appear in sub.
    condition {
      test     = "StringEquals"
      variable = "${local.issuer}:job_workflow_ref"
      values   = ["${var.github_repository}/${var.github_workflow_path}@${var.github_ref}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = local.role_name
  description          = "GitHub Actions deploy for ${var.github_repository} (${var.github_environment} environment only)"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

# Everything except IAM, Organizations and Account (Identity Center is denied below).
resource "aws_iam_role_policy_attachment" "power_user" {
  role       = aws_iam_role.deploy.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "iam_for_stack" {
  #checkov:skip=CKV_AWS_356:ReadIam is read-only IAM metadata; the List* actions Terraform calls have no resource-level permissions
  statement {
    sid       = "ReadIam"
    actions   = ["iam:Get*", "iam:List*"]
    resources = ["*"]
  }

  # Every role write the IAM condition key covers requires the role to carry the workload boundary
  # (for CreateRole and PutRolePermissionsBoundary: the boundary being set).
  statement {
    sid = "ManageBoundedProjectRoles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateRoleDescription",
      "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy", "iam:PutRolePermissionsBoundary",
    ]
    resources = [local.project_role]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [var.workload_boundary_arn]
    }
  }

  # Only the managed policies this stack attaches (AWS_ConfigRole and the project's own), and only
  # to bounded roles.
  statement {
    sid       = "AttachKnownPoliciesToBoundedRoles"
    actions   = ["iam:AttachRolePolicy"]
    resources = [local.project_role]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [var.workload_boundary_arn]
    }
    condition {
      test     = "ArnLike"
      variable = "iam:PolicyARN"
      values = [
        "arn:${local.partition}:iam::aws:policy/service-role/AWS_ConfigRole",
        local.project_pol,
      ]
    }
  }

  # IAM has no boundary condition for tagging; tags grant nothing in this stack.
  statement {
    sid       = "TagProjectRoles"
    actions   = ["iam:TagRole", "iam:UntagRole"]
    resources = [local.project_role]
  }

  statement {
    sid = "ManageProjectPolicies"
    actions = [
      "iam:CreatePolicy", "iam:DeletePolicy", "iam:CreatePolicyVersion", "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion", "iam:TagPolicy", "iam:UntagPolicy",
    ]
    resources = [local.project_pol]
  }

  # Hand project roles only to the services this stack configures. iam:PassRole has no boundary
  # condition key, but every role CI can create or edit carries the boundary (above).
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

  # First DynamoDB auto scaling registration creates this service-linked role.
  # PowerUserAccess does not include that IAM write.
  statement {
    sid     = "DynamoDbAutoscalingServiceRole"
    actions = ["iam:CreateServiceLinkedRole"]
    resources = [join("", [
      "arn:${local.partition}:iam::${local.account_id}:role/aws-service-role/",
      "dynamodb.application-autoscaling.amazonaws.com/",
      "AWSServiceRoleForApplicationAutoScaling_DynamoDBTable",
    ])]
    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["dynamodb.application-autoscaling.amazonaws.com"]
    }
  }

  statement {
    sid       = "AccountPasswordPolicy"
    actions   = ["iam:UpdateAccountPasswordPolicy", "iam:DeleteAccountPasswordPolicy"]
    resources = ["*"]
  }

  statement {
    sid       = "DenyBoundaryRemoval"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary", "iam:DeleteUserPermissionsBoundary"]
    resources = ["*"]
  }

  # The boundary policy matches <name>-*; CI must not be able to rewrite or delete it.
  statement {
    sid    = "ProtectBoundaryPolicy"
    effect = "Deny"
    actions = [
      "iam:CreatePolicyVersion", "iam:DeletePolicyVersion", "iam:SetDefaultPolicyVersion",
      "iam:DeletePolicy", "iam:TagPolicy", "iam:UntagPolicy",
    ]
    resources = [var.workload_boundary_arn]
  }

  # No changes to this role or the identity provider (also blocks passing or assuming this role).
  statement {
    sid         = "NoSelfModification"
    effect      = "Deny"
    not_actions = ["iam:Get*", "iam:List*"]
    resources   = [local.self_role, local.oidc_arn]
  }

  # Terraform here assumes no roles. Blocks hopping into project roles, and into
  # OrganizationAccountAccessRole in member accounts (this is the Organization management account).
  statement {
    sid       = "NoRoleAssumption"
    effect    = "Deny"
    actions   = ["sts:AssumeRole"]
    resources = ["*"]
  }

  # PowerUserAccess would otherwise allow Identity Center and Identity Store writes (users,
  # permission sets, account assignments). The stack manages none of them.
  statement {
    sid       = "NoIdentityCenter"
    effect    = "Deny"
    actions   = ["sso:*", "sso-directory:*", "sso-oauth:*", "identitystore:*", "identity-sync:*"]
    resources = ["*"]
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
