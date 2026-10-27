# Real AWS provider plan. Caller identity and partition are overridden so the
# policy document is known without STS. aws_iam_policy_document is not mocked.

run "config_service_linked_role_passrole_is_exact" {
  command = plan

  override_data {
    target = module.github_deploy.data.aws_caller_identity.current
    values = {
      account_id = "308639168050"
      arn        = "arn:aws:iam::308639168050:root"
      id         = "308639168050"
      user_id    = "AIDATEST"
    }
  }

  override_data {
    target = module.github_deploy.data.aws_partition.current
    values = {
      dns_suffix = "amazonaws.com"
      id         = "aws"
      partition  = "aws"
    }
  }

  override_data {
    target = module.workload_boundary.data.aws_caller_identity.current
    values = {
      account_id = "308639168050"
      arn        = "arn:aws:iam::308639168050:root"
      id         = "308639168050"
      user_id    = "AIDATEST"
    }
  }

  override_data {
    target = module.workload_boundary.data.aws_partition.current
    values = {
      dns_suffix = "amazonaws.com"
      id         = "aws"
      partition  = "aws"
    }
  }

  assert {
    condition     = output.config_passrole_exact
    error_message = "PassConfigServiceLinkedRole must be iam:PassRole on arn:aws:iam::308639168050:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig with iam:PassedToService config.amazonaws.com."
  }

  assert {
    condition     = output.passrole_resources_are_bounded
    error_message = "iam:PassRole resources must be only that Config role ARN and arn:aws:iam::308639168050:role/invdash-*, never *."
  }

  assert {
    condition     = output.deploy_role_has_no_boundary
    error_message = "invdash-terraform-deploy must not carry a permissions boundary."
  }

  assert {
    condition     = output.boundary_passrole_stays_jobs_scheduler
    error_message = "invdash-workload-boundary must still pass only invdash-jobs-scheduler to scheduler.amazonaws.com."
  }
}
