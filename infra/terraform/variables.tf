variable "project" {
  description = "Short name used as a prefix for every resource. Lowercase letters, digits and hyphens."
  type        = string
  default     = "invdash"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,12}$", var.project))
    error_message = "project must be 3-13 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "region" {
  description = "Primary AWS region. CloudFront metrics and global-service events (IAM, root sign-in) always land in us-east-1; the us_east_1 module covers those."
  type        = string
  default     = "us-west-1"
}

variable "cognito_login_branding_version" {
  description = "Cognito branding version: 1 keeps classic login with matching CSS; 2 enables Managed Login after the preflight/import steps in docs/operations.md."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2], var.cognito_login_branding_version)
    error_message = "cognito_login_branding_version must be 1 or 2."
  }
}

variable "cognito_allow_branding_migration" {
  description = "Allow the deployment preflight to change an existing Cognito domain's branding version only after reviewing the migration and rollback procedure."
  type        = bool
  default     = false
}

variable "dr_region" {
  description = "Region for the replicated data lake copy (contingency planning). Must differ from region."
  type        = string
  default     = "us-west-2"

  validation {
    condition     = var.dr_region != var.region
    error_message = "dr_region must be a different region from region."
  }
}

variable "use_fips_endpoint" {
  description = "Send Terraform API calls to FIPS endpoints (SC-13). See providers.tf before enabling."
  type        = bool
  default     = false
}

variable "alert_emails" {
  description = "Email addresses that receive ops and security alerts. Each must confirm the SNS subscription."
  type        = list(string)
}

variable "security_alert_emails" {
  description = "Optional separate recipients for security alerts. Defaults to alert_emails when empty."
  type        = list(string)
  default     = []
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget. Emails at 80% actual and 100% forecast."
  type        = number
  default     = 40
}

variable "audit_retention_days" {
  description = "Days to keep audit records (AU-11). 913 days = 12 months searchable + 18 months cold, which meets OMB M-21-31 and its 2026 successor M-26-14."
  type        = number
  default     = 913
}

variable "audit_object_lock_mode" {
  description = "S3 Object Lock mode for the audit bucket (AU-9). GOVERNANCE lets a privileged admin remove locks; COMPLIANCE cannot be undone by anyone, including root."
  type        = string
  default     = "GOVERNANCE"

  validation {
    condition     = contains(["GOVERNANCE", "COMPLIANCE"], var.audit_object_lock_mode)
    error_message = "audit_object_lock_mode must be GOVERNANCE or COMPLIANCE."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for application logs. 400 days keeps 12+ months searchable."
  type        = number
  default     = 400
}

variable "function_names" {
  description = "Lambda function names the app will deploy. Log groups are pre-created with KMS encryption and retention."
  type        = list(string)
  default = [
    "invdash-h1-prices", "invdash-h2-news", "invdash-h3-regulatory",
    "invdash-d1-macro", "invdash-d2-sweep", "invdash-d3-rates", "invdash-d4-close", "invdash-d5-contracts",
    "invdash-w1-weekly", "invdash-m1-release", "invdash-c1-calendar", "invdash-a1-annual",
    "invdash-build-serving", "invdash-build-analytics", "invdash-compaction",
  ]
}

variable "max_tickers_per_user" {
  description = "Per-user watchlist limit, mirrored by the frontend. Legacy lists may shrink but not grow."
  type        = number
  default     = 25
  validation {
    condition     = var.max_tickers_per_user == 25
    error_message = "The frontend watchlist limit is 25; update both surfaces before changing this limit."
  }
}

variable "metrics_namespace" {
  description = "CloudWatch namespace for the app's custom metrics. Keep the metric set at 10 or fewer to stay in the free tier."
  type        = string
  default     = "InvestorDashboard"
}

variable "health_url" {
  description = "Public HTTPS URL of the non-sensitive freshness health document consumed by the canary. Leave empty until a publisher and public endpoint are configured."
  type        = string
  default     = ""
}

variable "canary_runtime" {
  description = "CloudWatch Synthetics runtime. syn-nodejs-puppeteer-17.0 is the newest as of Oct 2026."
  type        = string
  default     = "syn-nodejs-puppeteer-17.0"
}

variable "canary_rate_minutes" {
  description = "How often the external health canary runs. 15 min costs about $3.40/month."
  type        = number
  default     = 15
}

variable "cloudfront_distribution_id" {
  description = "Override the CloudFront distribution watched by the 5xx alarm. Leave empty to use the distribution this stack creates."
  type        = string
  default     = ""
}

variable "enable_cloudfront_alarms" {
  description = "Create the CloudFront 5xx alarm (and its us-east-1 topic and key). A static flag, so the first plan works before the distribution exists."
  type        = bool
  default     = true
}

variable "enable_malware_protection" {
  description = "GuardDuty Malware Protection for S3 on the raw/ prefix (SI-3). About $1/month."
  type        = bool
  default     = true
}

variable "api_keys" {
  description = "Provider credentials kept in SSM, with how often each must be rotated (IA-5). Sources are catalog IDs."
  type = map(object({
    provider       = string
    sources        = list(string)
    rotation_days  = number
    regenerate_url = string
  }))
  default = {
    # Provider-enforced: SAM.gov personal keys expire every 90 days.
    sam-gov = { provider = "SAM.gov", sources = ["DS-68", "DS-69"], rotation_days = 90, regenerate_url = "https://sam.gov/profile/details" }
    # Account-level keys: use paper-trading keys only (they can place paper orders); 180 days.
    alpaca-key-id     = { provider = "Alpaca", sources = ["DS-02", "DS-82"], rotation_days = 180, regenerate_url = "https://app.alpaca.markets/" }
    alpaca-secret-key = { provider = "Alpaca", sources = ["DS-02", "DS-82"], rotation_days = 180, regenerate_url = "https://app.alpaca.markets/" }
    # Provider-enforced: BLS registration must be renewed at least once a year.
    bls = { provider = "BLS", sources = ["DS-21"], rotation_days = 365, regenerate_url = "https://data.bls.gov/registrationEngine/" }
    # Policy: yearly rotation for keys that never expire.
    fred           = { provider = "FRED", sources = ["DS-15", "DS-16", "DS-26", "DS-39", "DS-45", "DS-23"], rotation_days = 365, regenerate_url = "https://fredaccount.stlouisfed.org/apikeys" }
    finnhub        = { provider = "Finnhub", sources = ["DS-41", "DS-13", "DS-04"], rotation_days = 365, regenerate_url = "https://finnhub.io/dashboard" }
    alpha-vantage  = { provider = "Alpha Vantage", sources = ["DS-40", "DS-03"], rotation_days = 365, regenerate_url = "https://www.alphavantage.co/support/" }
    massive        = { provider = "Massive Polygon", sources = ["DS-01", "DS-42"], rotation_days = 365, regenerate_url = "https://massive.com/dashboard" }
    bea            = { provider = "BEA", sources = ["DS-24"], rotation_days = 365, regenerate_url = "https://apps.bea.gov/API/signup/" }
    census         = { provider = "Census", sources = ["DS-34"], rotation_days = 365, regenerate_url = "https://api.census.gov/data/key_signup.html" }
    api-data-gov   = { provider = "api.data.gov FCC", sources = ["DS-74"], rotation_days = 365, regenerate_url = "https://api.data.gov/signup/" }
    acled-password = { provider = "ACLED myACLED login", sources = ["DS-36"], rotation_days = 365, regenerate_url = "https://acleddata.com/" }
    # FINRA Query API needs OAuth client credentials even for public data (free Public credential).
    finra-client-id     = { provider = "FINRA", sources = ["DS-91"], rotation_days = 365, regenerate_url = "https://gateway.finra.org/" }
    finra-client-secret = { provider = "FINRA", sources = ["DS-91"], rotation_days = 365, regenerate_url = "https://gateway.finra.org/" }
  }
}

variable "key_reminder_days" {
  description = "Days before a key's due date when a reminder email is sent. Overdue keys get one email a day."
  type        = list(number)
  default     = [14, 7, 3, 1, 0]
}

variable "key_check_schedule" {
  description = "When the rotation check runs (EventBridge Scheduler expression)."
  type        = string
  default     = "cron(0 8 * * ? *)"
}

variable "key_check_timezone" {
  description = "Time zone for key_check_schedule."
  type        = string
  default     = "America/Los_Angeles"
}

# ---------------------------------------------------------------------------
# Site sign-in, preferences and jobs (2026-10 UI brief)
# ---------------------------------------------------------------------------
variable "site_origins" {
  description = "Origins allowed to call the site API (CORS), e.g. the CloudFront URL. Local dev origin is for testing only."
  type        = list(string)
  default     = ["http://localhost:8080"]
}

variable "site_callback_urls" {
  description = "Additional local Cognito sign-in redirect URLs; the CloudFront callback URL is added automatically."
  type        = list(string)
  default     = ["http://localhost:8080/"]
}

variable "site_logout_urls" {
  description = "Cognito redirect URLs after sign-out."
  type        = list(string)
  default     = ["http://localhost:8080/"]
}

variable "jobs_image_uri" {
  description = "ECR image URI for the job Lambdas (built from Dockerfile). Leave empty until the first image is pushed; the jobs module creates nothing until then."
  type        = string
  default     = ""
}

variable "lambda_reserved_concurrency" {
  description = "Reserved concurrency per function. Null = unreserved (required on new accounts limited to 10 concurrent executions)."
  type        = number
  default     = null
}

variable "max_user_tickers" {
  description = "Cap on the union of users' tickers that collectors cover (beyond TSLA, SPCX). Keeps Finnhub and Massive inside free limits."
  type        = number
  default     = 25
}

variable "enable_options_daily" {
  description = "Enable daily options snapshots from Alpaca's free indicative feed."
  type        = bool
  default     = true
}

variable "backfill_years" {
  description = "Historical price lookback. The initial backfill and new-ticker loads use this range."
  type        = number
  default     = 5

  validation {
    condition     = var.backfill_years >= 1 && var.backfill_years <= 20
    error_message = "backfill_years must be between 1 and 20."
  }
}

variable "backfill_batch_days" {
  description = "Calendar days per Yahoo historical request; smaller batches make recovery more granular."
  type        = number
  default     = 90

  validation {
    condition     = var.backfill_batch_days >= 1 && var.backfill_batch_days <= 365
    error_message = "backfill_batch_days must be between 1 and 365."
  }
}

variable "backfill_max_batches" {
  description = "Maximum historical requests per backfill invocation; remaining batches resume on the schedule."
  type        = number
  default     = 5

  validation {
    condition     = var.backfill_max_batches >= 1 && var.backfill_max_batches <= 100
    error_message = "backfill_max_batches must be between 1 and 100."
  }
}

variable "backfill_resume_schedule" {
  description = "EventBridge Scheduler expression for backfill, overriding jobs.backfill.schedule."
  type        = string
  default     = "rate(15 minutes)"
}

variable "sec_user_agent" {
  description = "User-Agent for SEC EDGAR requests, e.g. 'investor-dashboard you@example.com' (SEC requires a contact)."
  type        = string
  default     = ""
}

variable "atlanta_mpt_url" {
  description = "Direct URL of the Atlanta Fed Market Probability Tracker historical-data workbook, once confirmed."
  type        = string
  default     = ""
}

variable "kalshi_series" {
  description = "Kalshi series tickers collected for rate and CPI odds (comma-separated)."
  type        = string
  default     = "KXFEDDECISION,KXCPI"
}

variable "jobs" {
  description = "Container-image jobs: handler, schedule (ET), event triggers, size and least-privilege data access."
  type = map(object({
    handler        = string
    schedule       = string
    triggers       = list(string)
    memory         = number
    timeout        = number
    read_prefixes  = list(string)
    write_prefixes = list(string)
    api_keys       = list(string)
    reads_prefs    = bool
    # Only the release-day job creates one-off schedules. Default false so existing jobs are unchanged.
    manages_schedules = optional(bool, false)
  }))
  default = {
    # After D4 (daily close), D1 (morning macro), M1 (release days), or a price reconcile
    trend-metrics = {
      handler        = "trend_metrics.handler"
      schedule       = ""
      triggers       = ["job:D4", "job:M1", "job:RECONCILE", "job:D1", "job:BACKFILL?batches>0"]
      memory         = 2048
      timeout        = 600
      read_prefixes  = ["curated/prices_daily/", "curated/macro_daily/", "serving/trend_metrics/_lease.json"]
      write_prefixes = ["serving/trend_metrics/"]
      api_keys       = []
      reads_prefs    = true
    }
    # D1: 07:00 ET weekdays. The handler no-ops on federal holidays. First run backfills five years.
    macro-daily = {
      handler        = "macro_daily.handler"
      schedule       = "cron(0 7 ? * MON-FRI *)"
      triggers       = []
      memory         = 512
      timeout        = 300
      read_prefixes  = ["curated/macro_daily/source=fred/"]
      write_prefixes = ["raw/fred/", "curated/macro_daily/source=fred/"]
      api_keys       = ["fred"]
      reads_prefs    = false
    }
    daily-prices = {
      handler        = "daily_prices.handler"
      schedule       = "cron(45 16 ? * MON-FRI *)"
      triggers       = []
      memory         = 1024
      timeout        = 300
      read_prefixes  = ["curated/prices_daily/"]
      write_prefixes = ["curated/prices_daily/"]
      api_keys       = ["alpaca-key-id", "alpaca-secret-key"]
      reads_prefs    = true
    }
    # H2: hourly on NYSE days. Alpha Vantage stays inside 06:15–21:15 and 16 calls/day.
    news-sentiment = {
      handler        = "news_sentiment.handler"
      schedule       = "cron(15 * ? * MON-FRI *)"
      triggers       = []
      memory         = 512
      timeout        = 180
      read_prefixes  = ["curated/news_articles/", "curated/news_daily/"]
      write_prefixes = ["raw/news/", "curated/news_articles/", "curated/news_daily/"]
      api_keys       = ["finnhub", "alpha-vantage", "massive"]
      reads_prefs    = true
    }
    # H1: hourly bars during the NYSE session. Early-close days stop after 13:05 in the handler.
    hourly-prices = {
      handler        = "hourly_prices.handler"
      schedule       = "cron(5 10-16 ? * MON-FRI *)"
      triggers       = []
      memory         = 512
      timeout        = 120
      read_prefixes  = ["curated/prices_hourly/"]
      write_prefixes = ["raw/alpaca_bars_1h/", "curated/prices_hourly/"]
      api_keys       = ["alpaca-key-id", "alpaca-secret-key", "finnhub", "alpha-vantage"]
      reads_prefs    = true
    }
    # H3: federal business days. The 14:05 FOMC one-off waits for D1; this schedule still covers that hour.
    regulatory-feeds = {
      handler        = "regulatory_feeds.handler"
      schedule       = "cron(25 6-22 ? * MON-FRI *)"
      triggers       = []
      memory         = 512
      timeout        = 300
      read_prefixes  = ["curated/filings/", "curated/events/"]
      write_prefixes = ["raw/edgar/", "raw/regfeeds/", "curated/filings/", "curated/events/", "curated/events_daily/"]
      api_keys       = ["api-data-gov"]
      reads_prefs    = true
    }
    # D5: after the DoD daily contract post. SAM stays inside a 10-call day.
    gov-contracts = {
      handler       = "gov_contracts.handler"
      schedule      = "cron(45 17 ? * MON-FRI *)"
      triggers      = []
      memory        = 512
      timeout       = 300
      read_prefixes = ["curated/contracts/", "curated/contracts_rollup/"]
      write_prefixes = [
        "raw/contracts/",
        "curated/contracts/",
        "curated/contracts_rollup/",
        "curated/contract_opportunities/",
      ]
      api_keys    = ["sam-gov"]
      reads_prefs = true
    }
    dashboard-build = {
      handler  = "dashboard_build.handler"
      schedule = ""
      triggers = ["job:D4", "job:TREND", "job:Q1", "job:SHORT", "job:OPTIONS", "job:BACKFILL?batches>0", "job:RECONCILE", "job:H1", "job:H2", "job:H3", "job:D5", "job:M1"]
      memory   = 1024
      timeout  = 300
      read_prefixes = [
        "curated/prices_daily/",
        "curated/news_daily/",
        "curated/news_articles/",
        "curated/prices_hourly/",
        "curated/short_interest/",
        "curated/filings/",
        "curated/events/",
        "curated/contracts_rollup/",
        "curated/contracts/",
        "serving/trend_metrics/latest/",
        "serving/trend_metrics/ticker=",
        "curated/options_daily/",
        "serving/fundamentals_quarterly.json",
        "serving/status.json",
        "serving/dashboard-build/",
        "curated/releases/",
        "curated/release_calendar/",
        "curated/release_links/",
      ]
      write_prefixes = ["serving/dashboard.json", "serving/chart_data/", "serving/dashboard-build/"]
      api_keys       = []
      reads_prefs    = true
    }
    # Weekly safety net; D1 also schedules a one-off run the day after earnings
    q1-fundamentals = {
      handler        = "fundamentals.handler"
      schedule       = "cron(30 8 ? * MON *)"
      triggers       = ["ticker-added"]
      memory         = 1024
      timeout        = 300
      read_prefixes  = ["manual/fundamentals/", "curated/fundamentals_quarterly/"]
      write_prefixes = ["curated/fundamentals_quarterly/", "serving/fundamentals_quarterly.json"]
      api_keys       = []
      reads_prefs    = true
    }
    # Daily poll; writes only when FINRA publishes a new settlement date (twice a month)
    short-interest = {
      handler        = "short_interest.handler"
      schedule       = "cron(30 18 ? * MON-FRI *)"
      triggers       = []
      memory         = 512
      timeout        = 300
      read_prefixes  = ["curated/short_interest/"]
      write_prefixes = ["curated/short_interest/"]
      api_keys       = ["finra-client-id", "finra-client-secret"]
      reads_prefs    = true
    }
    # Runs five minutes after daily-prices; set enable_options_daily false to pause collection.
    options-daily = {
      handler        = "options_daily.handler"
      schedule       = "cron(50 16 ? * MON-FRI *)"
      triggers       = []
      memory         = 1024
      timeout        = 600
      read_prefixes  = []
      write_prefixes = ["curated/options_daily/"]
      api_keys       = ["alpaca-key-id", "alpaca-secret-key"]
      reads_prefs    = true
    }
    # 5-year history when a user adds a ticker nobody followed; also run once at setup (O1)
    backfill = {
      handler        = "backfill.handler"
      schedule       = "rate(15 minutes)"
      triggers       = ["ticker-added"]
      memory         = 1024
      timeout        = 900
      read_prefixes  = ["curated/prices_daily/"]
      write_prefixes = ["curated/prices_daily/"]
      api_keys       = []
      reads_prefs    = false
    }
    # Weekday evenings: Yahoo split/dividend check; rewrites a ticker's adjusted history on a new event
    price-reconcile = {
      handler        = "price_reconcile.handler"
      schedule       = "cron(15 19 ? * MON-FRI *)"
      triggers       = []
      memory         = 1024
      timeout        = 900
      read_prefixes  = ["curated/prices_daily/"]
      write_prefixes = ["curated/prices_daily/"]
      api_keys       = []
      reads_prefs    = true
    }
    # Morning calendar bootstrap plus release-morning one-off at() schedules.
    release-day = {
      handler  = "release_day.handler"
      schedule = "cron(35 6 ? * MON-FRI *)"
      # A backfill that wrote rows rebuilds links. Idle runs (batches = 0) do not start M1.
      triggers = ["job:BACKFILL?batches>0", "job:D4", "job:RECONCILE"]
      memory   = 512
      timeout  = 300
      read_prefixes = [
        "curated/releases/",
        "curated/release_calendar/",
        "curated/macro_daily/",
        "curated/prices_daily/",
      ]
      write_prefixes = [
        "raw/releases/",
        "curated/releases/",
        "curated/release_calendar/",
        "curated/macro_daily/source=releases/",
        "curated/release_links/",
      ]
      api_keys          = ["bls", "bea", "census", "fred"]
      reads_prefs       = true
      manages_schedules = true
    }
    # Rebuilds serving/status.json after every job
    status-feed = {
      handler        = "status_feed_job.handler"
      schedule       = ""
      triggers       = ["job:*"]
      memory         = 512
      timeout        = 60
      read_prefixes  = ["serving/status.json"]
      write_prefixes = ["serving/status.json"]
      api_keys       = []
      reads_prefs    = false
    }
  }
}

variable "enable_github_deploy" {
  description = "Create the GitHub OIDC provider and the invdash-terraform-deploy role used by .github/workflows/deploy.yml."
  type        = bool
  default     = true
}

variable "github_repository" {
  description = "GitHub owner/repo allowed to assume the deploy role."
  type        = string
  default     = "ram290476/investor-dashboard"
}

variable "github_repository_owner_id" {
  description = "Numeric ID of the GitHub repository owner, used in the deploy role's trust policy. Find it with `gh api repos/OWNER/REPO --jq .owner.id`, or in the sub_claim_prefix from `gh api repos/OWNER/REPO/actions/oidc/customization/sub`."
  type        = string
  default     = "48363891" # ram290476

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_owner_id))
    error_message = "github_repository_owner_id must be the numeric GitHub owner ID."
  }
}

variable "github_repository_id" {
  description = "Numeric ID of the GitHub repository, used in the deploy role's trust policy. Find it with `gh api repos/OWNER/REPO --jq .id`."
  type        = string
  default     = "1403740156" # ram290476/investor-dashboard

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "github_repository_id must be the numeric GitHub repository ID."
  }
}

variable "github_environment" {
  description = "GitHub environment allowed to assume the deploy role. Protect it with required reviewers."
  type        = string
  default     = "production"
}

variable "github_oidc_provider_arn" {
  description = "Existing token.actions.githubusercontent.com provider ARN. Leave empty to create one (only one per account is allowed)."
  type        = string
  default     = ""
}
