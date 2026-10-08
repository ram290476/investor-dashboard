variable "name" {
  type = string
}

variable "callback_urls" {
  type = list(string)
}

variable "logout_urls" {
  type = list(string)
}

variable "branding_version" {
  type    = number
  default = 1
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region
  brand  = jsondecode(file("${path.module}/../../../../docs/design-roadmap/themes/themes.json")).themes[0].tokens
  rgba   = { for key, value in local.brand : key => "${lower(trimprefix(value, "#"))}ff" }
}

# ---------------------------------------------------------------------------
# Cognito user pool for dashboard sign-in (IA-2, IA-5, AC-7, AC-12).
# Invite-only, Plus tier for threat protection (compromised-credential and
# adaptive-authentication checks). MFA is intentionally OFF: password-only
# sign-in for now (hosted UI MFA setup was blocking access). Cognito encrypts
# user data with an AWS-owned key; user pools cannot use a customer-managed
# key (documented SC-28 exception).
# ---------------------------------------------------------------------------
resource "aws_cognito_user_pool" "this" {
  #checkov:skip=CKV_AWS_260:MFA intentionally OFF for password-only dashboard sign-in (temporary; threat protection remains ENFORCED)
  name                     = "${var.name}-users"
  user_pool_tier           = "PLUS"
  deletion_protection      = "ACTIVE"
  mfa_configuration        = "OFF"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

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
  auth_session_validity                = 15

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
  domain                = "${var.name}-${data.aws_caller_identity.current.account_id}"
  user_pool_id          = aws_cognito_user_pool.this.id
  managed_login_version = var.branding_version
}

resource "aws_cognito_user_pool_ui_customization" "web" {
  count        = var.branding_version == 1 ? 1 : 0
  client_id    = aws_cognito_user_pool_client.web.id
  user_pool_id = aws_cognito_user_pool.this.id
  css = templatefile("${path.module}/classic-login.css.tftpl", {
    page    = local.brand.bg
    surface = local.brand.surface
    raised  = local.brand["surface-2"]
    text    = local.brand.text
    muted   = local.brand["text-muted"]
    accent  = local.brand["accent-text"]
    border  = "#728094"
    error   = "#f07a7a"
  })
  depends_on = [aws_cognito_user_pool_domain.this]
}

resource "aws_cognito_managed_login_branding" "web" {
  count        = var.branding_version == 2 ? 1 : 0
  client_id    = aws_cognito_user_pool_client.web.id
  user_pool_id = aws_cognito_user_pool.this.id
  settings = jsonencode({
    categories = {
      global = {
        colorSchemeMode = "DARK"
        spacingDensity  = "REGULAR"
      }
    }
    componentClasses = {
      buttons    = { borderRadius = 9 }
      divider    = { darkMode = { borderColor = local.rgba.border } }
      focusState = { darkMode = { borderColor = local.rgba["accent-text"] } }
      input = {
        borderRadius = 8
        darkMode = {
          defaults         = { backgroundColor = local.rgba["surface-2"], borderColor = "728094ff" }
          placeholderColor = local.rgba["text-muted"]
        }
      }
      inputLabel       = { darkMode = { textColor = local.rgba.text } }
      inputDescription = { darkMode = { textColor = local.rgba["text-muted"] } }
      link             = { darkMode = { defaults = { textColor = local.rgba["accent-text"] } } }
    }
    components = {
      pageBackground = {
        darkMode = { color = local.rgba.bg }
        image    = { enabled = false }
      }
      form = {
        borderRadius    = 16
        backgroundImage = { enabled = false }
        darkMode        = { backgroundColor = local.rgba.surface, borderColor = local.rgba.border }
      }
      pageText = {
        darkMode = {
          bodyColor        = local.rgba.text
          descriptionColor = local.rgba["text-muted"]
          headingColor     = local.rgba.text
        }
      }
      primaryButton = {
        darkMode = {
          defaults = { backgroundColor = "1b2637ff", textColor = local.rgba.text }
          active   = { backgroundColor = "1b2637ff", textColor = local.rgba.text }
          hover    = { backgroundColor = "202630ff", textColor = local.rgba.text }
        }
      }
      secondaryButton = {
        darkMode = {
          defaults = {
            backgroundColor = local.rgba.surface
            borderColor     = local.rgba["accent-text"]
            textColor       = local.rgba["accent-text"]
          }
        }
      }
      alert = {
        borderRadius = 8
        darkMode     = { error = { backgroundColor = local.rgba.surface, borderColor = "f07a7aff" } }
      }
    }
  })
  depends_on = [aws_cognito_user_pool_domain.this]
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
