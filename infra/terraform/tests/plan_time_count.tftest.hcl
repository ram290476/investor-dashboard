# Regression test for deploy #199: every count and for_each must be decidable at
# plan time on a fresh stack. Mock providers leave computed attributes (ARNs, IDs)
# unknown during plan, so a count that reads one fails here with
# "Invalid count argument" just as the production plan did.
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

mock_provider "archive" {}

variables {
  alert_emails   = ["ops@example.com"]
  alert_sms_e164 = "+14155552671"
  jobs_image_uri = "123456789012.dkr.ecr.us-west-1.amazonaws.com/invdash/jobs:test"
  health_url     = "https://example.com/health.json"
}

run "plan_with_every_alert_path_on" {
  command = plan

  variables {
    enable_cloudfront_alarms = true
  }
}

run "plan_with_optional_alert_paths_off" {
  command = plan

  variables {
    enable_cloudfront_alarms = false
    alert_notifier_enabled   = false
    alert_sms_e164           = ""
    jobs_image_uri           = ""
    health_url               = ""
  }
}

run "plan_with_cloudfront_alarm_but_notifier_off" {
  command = plan

  variables {
    enable_cloudfront_alarms = true
    alert_notifier_enabled   = false
  }
}
