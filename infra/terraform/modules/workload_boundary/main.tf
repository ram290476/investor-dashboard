# Permissions boundary for every IAM role this stack creates (AC-6, AC-6(10)). A boundary grants
# nothing by itself: a role's effective permissions are the overlap of its own policies and this
# boundary. Its job is to keep any role the deploy pipeline creates or edits below the deploy
# role's own reach:
#   - no IAM writes of any kind (only the reads AWS Config needs);
#   - no Organizations, Account, Identity Center or Identity Store writes;
#   - no STS role assumption except the prefs API's session-tagged hop into <name>-prefs-access.
# The explicit denies below repeat the most important of these, so widening the allow statements
# later can't quietly reopen them. The deploy role is required to set exactly this boundary on
# every <name>-* role it touches (modules/github_deploy).

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

variable "name" {
  type = string
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id    = data.aws_caller_identity.current.account_id
  partition     = data.aws_partition.current.partition
  policy_name   = "${var.name}-workload-boundary"
  boundary_arn  = "arn:${local.partition}:iam::${local.account_id}:policy/${local.policy_name}"
  deploy_role   = "arn:${local.partition}:iam::${local.account_id}:role/${var.name}-terraform-deploy"
  oidc_provider = "arn:${local.partition}:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"
  prefs_access  = "arn:${local.partition}:iam::${local.account_id}:role/${var.name}-prefs-access"
}

data "aws_iam_policy_document" "boundary" {
  #checkov:skip=CKV_AWS_107:Permissions boundary: a ceiling that grants nothing; each role's own policy scopes actions and resources
  #checkov:skip=CKV_AWS_108:Permissions boundary: a ceiling that grants nothing; each role's own policy scopes actions and resources
  #checkov:skip=CKV_AWS_109:Permissions boundary: a ceiling that grants nothing; each role's own policy scopes actions and resources
  #checkov:skip=CKV_AWS_110:Permissions boundary: a ceiling that grants nothing; each role's own policy scopes actions and resources
  #checkov:skip=CKV_AWS_111:Permissions boundary: a ceiling that grants nothing; each role's own policy scopes actions and resources
  #checkov:skip=CKV_AWS_356:Permissions boundary: a ceiling that grants nothing; each role's own policy scopes actions and resources

  # Service actions the workloads use (S3, KMS, Lambda, DynamoDB, SSM, SNS, SQS, EventBridge,
  # Scheduler, CloudWatch, Logs, X-Ray, Synthetics, GuardDuty, Config delivery...). Everything
  # identity-related is left out here and handled below.
  statement {
    sid    = "WorkloadServices"
    effect = "Allow"
    not_actions = [
      "iam:*", "sts:*", "organizations:*", "account:*",
      "sso:*", "sso-directory:*", "sso-oauth:*", "identitystore:*", "identity-sync:*",
    ]
    resources = ["*"]
  }

  # Read-only identity inventory used by the AWS Config recorder (AWS_ConfigRole).
  statement {
    sid    = "IdentityReadsForConfig"
    effect = "Allow"
    actions = [
      "iam:Get*", "iam:List*", "iam:GenerateCredentialReport",
      "organizations:Describe*", "organizations:List*",
      "account:GetAlternateContact",
      "sso:Describe*", "sso:Get*", "sso:List*",
      "identitystore:Describe*", "identitystore:List*",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  # The prefs API's per-user, session-tagged hop (user_prefs module). No other role assumption.
  statement {
    sid       = "PrefsApiRoleChain"
    effect    = "Allow"
    actions   = ["sts:AssumeRole", "sts:TagSession"]
    resources = [local.prefs_access]
  }

  statement {
    sid         = "DenyProtectedIdentityResources"
    effect      = "Deny"
    not_actions = ["iam:Get*", "iam:List*"]
    resources   = [local.deploy_role, local.oidc_provider, local.boundary_arn]
  }

  statement {
    sid    = "DenyIamUsersAndCredentials"
    effect = "Deny"
    actions = [
      "iam:CreateUser", "iam:CreateAccessKey", "iam:UpdateAccessKey", "iam:CreateLoginProfile",
      "iam:UpdateLoginProfile", "iam:CreateServiceSpecificCredential", "iam:UploadSSHPublicKey",
      "iam:UploadSigningCertificate", "iam:AddUserToGroup", "iam:AttachUserPolicy", "iam:PutUserPolicy",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "DenyBoundaryChanges"
    effect = "Deny"
    actions = [
      "iam:DeleteRolePermissionsBoundary", "iam:PutRolePermissionsBoundary",
      "iam:DeleteUserPermissionsBoundary", "iam:PutUserPermissionsBoundary",
    ]
    resources = ["*"]
  }

  statement {
    sid           = "DenyOtherRoleAssumption"
    effect        = "Deny"
    actions       = ["sts:AssumeRole"]
    not_resources = [local.prefs_access]
  }
}

resource "aws_iam_policy" "boundary" {
  name        = local.policy_name
  description = "Permissions boundary for every ${var.name}-* IAM role (no IAM writes, no role assumption except the prefs API hop)"
  policy      = data.aws_iam_policy_document.boundary.json
}

output "arn" {
  value = aws_iam_policy.boundary.arn
}
