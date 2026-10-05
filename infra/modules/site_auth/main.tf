variable "name" {
  type = string
}

variable "callback_urls" {
  type = list(string)
}

variable "logout_urls" {
  type = list(string)
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region
}

# ---------------------------------------------------------------------------
# Cognito user pool for dashboard sign-in (IA-2, IA-2(1), IA-2(2), IA-5, AC-7, AC-12).
# Invite-only, MFA required, Plus tier for threat protection (compromised-credential
# and adaptive-authentication checks). Cognito encrypts user data with an AWS-owned
# key; user pools cannot use a customer-managed key (documented SC-28 exception).
# ---------------------------------------------------------------------------
resource "aws_cognito_user_pool" "this" {
  name                     = "${var.name}-users"
  user_pool_tier           = "PLUS"
  deletion_protection      = "ACTIVE"
  mfa_configuration        = "ON"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  software_token_mfa_configuration {
    enabled = true
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
  auth_session_validity                = 3

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
