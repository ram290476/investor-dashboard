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

# The job role policy is the overlap of these prefixes and the workload permissions
# boundary. Launch Library 2 and CelesTrak add no IAM action: the functions run
# outside a VPC and the HTTP client already allowlists those hosts.
run "deep_dive_lake_prefixes_stay_least_privilege" {
  command = plan

  assert {
    condition     = contains(var.jobs["news-sentiment"].write_prefixes, "curated/news_topics/")
    error_message = "news-sentiment must be allowed to write curated/news_topics/."
  }

  assert {
    condition     = contains(var.jobs["news-sentiment"].write_prefixes, "curated/") == false
    error_message = "news-sentiment must not be granted the whole curated/ tree."
  }

  assert {
    condition = (
      contains(var.jobs["deep-dive-collect"].read_prefixes, "curated/company_metrics/")
      && contains(var.jobs["deep-dive-collect"].read_prefixes, "curated/news_topics/")
    )
    error_message = "deep-dive-collect must read company metrics and the news rollup."
  }

  assert {
    condition     = join(",", var.jobs["deep-dive-collect"].write_prefixes) == "curated/deep_dive/"
    error_message = "deep-dive-collect must write only curated/deep_dive/."
  }

  assert {
    condition     = length(var.jobs["deep-dive-collect"].api_keys) == 0
    error_message = "Launch Library 2 and CelesTrak use no API key."
  }

  assert {
    condition = (
      contains(var.jobs["q2-company-ir-serve"].read_prefixes, "curated/news_topics/")
      && join(",", var.jobs["q2-company-ir-serve"].write_prefixes) == "serving/stock/"
    )
    error_message = "company-IR serve may read the news rollup and must still write only serving/stock/."
  }
}
