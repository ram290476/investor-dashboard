# Mock apply makes tags_all a known empty map when the resource sets no tags.
# The plan script in this directory checks the real AWS provider: default_tags
# land on the control role and not on the untagged Config role.

mock_provider "aws" {}

mock_provider "aws" {
  alias = "untagged"
}

run "untagged_service_linked_role_has_no_tags" {
  command = apply

  assert {
    condition     = aws_iam_service_linked_role.config.aws_service_name == "config.amazonaws.com"
    error_message = "The Config service-linked role must use config.amazonaws.com."
  }

  assert {
    condition     = try(length(aws_iam_service_linked_role.config.tags), 0) == 0
    error_message = "The Config service-linked role must not set tags."
  }

  assert {
    condition     = try(length(aws_iam_service_linked_role.config.tags_all), 0) == 0
    error_message = "The Config service-linked role must not inherit default_tags (tags_all=${jsonencode(aws_iam_service_linked_role.config.tags_all)})."
  }
}
