override_data {
  target = module.us_east_1.data.aws_caller_identity.current
  values = {
    account_id = "123456789012"
    arn        = "arn:aws:iam::123456789012:root"
    id         = "123456789012"
    user_id    = "AIDA1234567890EXAMPLE"
  }
}

run "unknown_notifier_arn_still_plans" {
  command = plan
}
