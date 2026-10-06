variable "name" {
  type = string
}

variable "callback_urls" {
  type = list(string)
}

variable "logout_urls" {
  type = list(string)
}

variable "permissions_boundary_arn" {
  description = "Permissions boundary set on every IAM role in this module (modules/workload_boundary)."
  type        = string
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  region     = data.aws_region.current.region
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  # Matched by the SMS role's trust policy. Not a secret; it ties the role to this pool's config.
  sms_external_id = "${var.name}-users-sms-${local.account_id}"
}

# ---------------------------------------------------------------------------
# SMS for MFA codes (IA-2(1)). Cognito assumes this role and calls sns:Publish, which passes
# through to AWS End User Messaging SMS in the same Region. US numbers additionally need a
# registered origination identity (toll-free number) in that Region, and while the account is in
# the SMS sandbox only verified destination numbers receive messages. Neither can be done in
# Terraform; see docs/operations.md.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "cognito_sms_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["cognito-idp.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [local.sms_external_id]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    # The pool needs this role's ARN, so the role can't reference the pool's ID without a cycle;
    # user pools in this account and Region only.
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:cognito-idp:${local.region}:${local.account_id}:userpool/*"]
    }
  }
}

resource "aws_iam_role" "cognito_sms" {
  name                 = "${var.name}-cognito-sms"
  description          = "Cognito user pool SMS (MFA codes) via sns:Publish"
  assume_role_policy   = data.aws_iam_policy_document.cognito_sms_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

# Same shape as the role the Cognito console creates: SMS to phone numbers has no resource ARN,
# so Publish is allowed on "*" and denied for every SNS topic, leaving only direct SMS.
data "aws_iam_policy_document" "cognito_sms" {
  #checkov:skip=CKV_AWS_356:SMS publish to a phone number has no resource ARN; topics are denied below
  #checkov:skip=CKV_AWS_111:SMS publish to a phone number has no resource ARN; topics are denied below
  statement {
    sid       = "PublishSms"
    actions   = ["sns:Publish"]
    resources = ["*"]
  }
  statement {
    sid       = "NoTopicPublish"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = ["arn:${local.partition}:sns:*:*:*"]
  }
}

resource "aws_iam_role_policy" "cognito_sms" {
  name   = "sns-publish-sms"
  role   = aws_iam_role.cognito_sms.id
  policy = data.aws_iam_policy_document.cognito_sms.json
}

# ---------------------------------------------------------------------------
# Cognito user pool for dashboard sign-in (IA-2, IA-2(1), IA-2(2), IA-5, AC-7, AC-12).
# Invite-only, MFA required (SMS text message or authenticator app), Plus tier for threat
# protection (compromised-credential and adaptive-authentication checks). Cognito encrypts user data with an AWS-owned
# key; user pools cannot use a customer-managed key (documented SC-28 exception).
# ---------------------------------------------------------------------------
resource "aws_cognito_user_pool" "this" {
  name                     = "${var.name}-users"
  user_pool_tier           = "PLUS"
  deletion_protection      = "ACTIVE"
  mfa_configuration        = "ON"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # Both factors stay enabled so nobody is locked out: users with a phone_number can use SMS,
  # everyone else (and anyone whose SMS can't be delivered) keeps the authenticator app.
  # With sms_configuration set and MFA on, the provider enables SMS MFA.
  software_token_mfa_configuration {
    enabled = true
  }

  sms_authentication_message = "Your Investor Dashboard sign-in code is {####}"

  sms_configuration {
    external_id    = local.sms_external_id
    sns_caller_arn = aws_iam_role.cognito_sms.arn
    sns_region     = local.region
  }

  admin_create_user_config {
    allow_admin_create_user_only = true # invite-only: no self sign-up
  }

  password_policy {
    minimum_length                   = 15
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
    password_history_size            = 24
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  user_pool_add_ons {
    advanced_security_mode = "ENFORCED"
  }

  # Cognito checks the role can publish when SMS is configured.
  depends_on = [aws_iam_role_policy.cognito_sms]
}

# Public SPA client: authorization-code flow with PKCE, no client secret, no Amplify.
resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${var.name}-web"
  user_pool_id                         = aws_cognito_user_pool.this.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.logout_urls
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  auth_session_validity                = 15 # minutes to finish MFA setup/entry (maximum)

  # AC-12 session termination: 1-hour tokens, sign-in again after 12 hours.
  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 12

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = "${var.name}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.this.id
}

output "user_pool_id" {
  value = aws_cognito_user_pool.this.id
}

output "user_pool_arn" {
  value = aws_cognito_user_pool.this.arn
}

output "client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "issuer_url" {
  value = "https://cognito-idp.${local.region}.amazonaws.com/${aws_cognito_user_pool.this.id}"
}

output "hosted_ui_url" {
  value = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${local.region}.amazoncognito.com"
}
