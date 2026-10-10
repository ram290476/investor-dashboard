terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
    archive = {
      source = "hashicorp/archive"
    }
  }
}

variable "max_tickers_per_user" {
  type    = number
  default = 25
}

variable "permissions_boundary_arn" {
  description = "Permissions boundary set on every IAM role in this module (modules/workload_boundary)."
  type        = string
}

variable "name" {
  type = string
}

variable "data_key_arn" {
  type = string
}

variable "audit_key_arn" {
  type = string
}

variable "log_retention_days" {
  type = number
}

variable "cognito_issuer_url" {
  type = string
}

variable "cognito_client_id" {
  type = string
}

variable "site_origins" {
  type = list(string)
}

variable "lake_bucket_name" {
  type = string
}

variable "lake_bucket_arn" {
  type = string
}

variable "ops_topic_arn" {
  type = string
}

variable "reserved_concurrency" {
  type    = number
  default = null
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  partition    = data.aws_partition.current.partition
  region       = data.aws_region.current.region
  fn_name      = "${var.name}-prefs-api"
  event_source = "${var.name}.prefs"
  # Always-free provisioned DynamoDB is 25 RCU and 25 WCU per account per Region.
  # This is the only DynamoDB table in the stack, so that ceiling belongs here.
  prefs_capacity_floor   = 5
  prefs_capacity_ceiling = 25
  prefs_scaling_target   = 70
}

# ---------------------------------------------------------------------------
# Per-user preferences, one item per Cognito user (partition key user_sub).
# Provisioned 5/5 stays inside the always-free 25 RCU / 25 WCU allowance.
# Auto scaling may move either dimension from 5 to 25 at 70% utilization.
# ignore_changes is not set on capacity: on an on-demand to provisioned
# switch the AWS provider would seed 1 instead of these values
# (hashicorp/terraform-provider-aws#38100). Apply in a quiet window so a
# later plan does not pull a scaled-up table back to 5.
# Encrypted with the data key, point-in-time recovery, deletion protection
# (SC-28, CP-9, CP-10).
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "prefs" {
  name                        = "${var.name}-user-prefs"
  billing_mode                = "PROVISIONED"
  read_capacity               = local.prefs_capacity_floor
  write_capacity              = local.prefs_capacity_floor
  hash_key                    = "user_sub"
  deletion_protection_enabled = true

  attribute {
    name = "user_sub"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.data_key_arn
  }

  point_in_time_recovery {
    enabled = true
  }
}

# RegisterScalableTarget must follow the billing-mode update. On-demand tables
# reject a scalable target.
resource "aws_appautoscaling_target" "prefs_read" {
  max_capacity       = local.prefs_capacity_ceiling
  min_capacity       = local.prefs_capacity_floor
  resource_id        = "table/${aws_dynamodb_table.prefs.name}"
  scalable_dimension = "dynamodb:table:ReadCapacityUnits"
  service_namespace  = "dynamodb"

  depends_on = [aws_dynamodb_table.prefs]
}

resource "aws_appautoscaling_policy" "prefs_read" {
  name               = "${var.name}-user-prefs-read"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.prefs_read.resource_id
  scalable_dimension = aws_appautoscaling_target.prefs_read.scalable_dimension
  service_namespace  = aws_appautoscaling_target.prefs_read.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "DynamoDBReadCapacityUtilization"
    }
    target_value       = local.prefs_scaling_target
    scale_in_cooldown  = 60
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_target" "prefs_write" {
  max_capacity       = local.prefs_capacity_ceiling
  min_capacity       = local.prefs_capacity_floor
  resource_id        = "table/${aws_dynamodb_table.prefs.name}"
  scalable_dimension = "dynamodb:table:WriteCapacityUnits"
  service_namespace  = "dynamodb"

  depends_on = [aws_dynamodb_table.prefs]
}

resource "aws_appautoscaling_policy" "prefs_write" {
  name               = "${var.name}-user-prefs-write"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.prefs_write.resource_id
  scalable_dimension = aws_appautoscaling_target.prefs_write.scalable_dimension
  service_namespace  = aws_appautoscaling_target.prefs_write.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "DynamoDBWriteCapacityUtilization"
    }
    target_value       = local.prefs_scaling_target
    scale_in_cooldown  = 60
    scale_out_cooldown = 60
  }
}

# A throttle means a request arrived before auto scaling raised capacity.
# Ops email, same topic as the API 5xx alarm. Missing data is idle, not an outage.
resource "aws_cloudwatch_metric_alarm" "prefs_throttle" {
  for_each = {
    read  = "ReadThrottleEvents"
    write = "WriteThrottleEvents"
  }

  alarm_name          = "${var.name}-user-prefs-${each.key}-throttle"
  alarm_description   = "User prefs table saw ${each.key} throttle events"
  namespace           = "AWS/DynamoDB"
  metric_name         = each.value
  statistic           = "Sum"
  dimensions          = { TableName = aws_dynamodb_table.prefs.name }
  period              = 300
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.ops_topic_arn]
  ok_actions          = [var.ops_topic_arn]
}

# ---------------------------------------------------------------------------
# Tenant isolation (AC-3, AC-6): the API function cannot touch the table itself.
# For each request it assumes prefs_access with session tag sub=<JWT sub>; that
# role may only use items whose partition key equals ${aws:PrincipalTag/sub}.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name                 = local.fn_name
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "access_trust" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.api.arn]
    }
    condition {
      test     = "Null"
      variable = "aws:RequestTag/sub"
      values   = ["false"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["sub"]
    }
  }
}

resource "aws_iam_role" "access" {
  name                 = "${var.name}-prefs-access"
  assume_role_policy   = data.aws_iam_policy_document.access_trust.json
  permissions_boundary = var.permissions_boundary_arn
  max_session_duration = 3600
}

data "aws_iam_policy_document" "access" {
  statement {
    sid       = "OwnItemOnly"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.prefs.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = ["$${aws:PrincipalTag/sub}"]
    }
  }
  statement {
    sid       = "TableKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.data_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["dynamodb.${local.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "access" {
  name   = "own-prefs-item"
  role   = aws_iam_role.access.id
  policy = data.aws_iam_policy_document.access.json
}

resource "aws_cloudwatch_log_group" "api_fn" {
  name              = "/aws/lambda/${local.fn_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.audit_key_arn
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${var.name}-site-api"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.audit_key_arn
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "AssumeScopedRole"
    actions   = ["sts:AssumeRole", "sts:TagSession"]
    resources = [aws_iam_role.access.arn]
  }
  statement {
    sid       = "PublishTickerAdded"
    actions   = ["events:PutEvents"]
    resources = ["arn:${local.partition}:events:${local.region}:${local.account_id}:event-bus/default"]
    condition {
      test     = "StringEquals"
      variable = "events:source"
      values   = [local.event_source]
    }
  }
  statement {
    sid     = "ReadDashboardServingObjects"
    actions = ["s3:GetObject"]
    resources = [
      "${var.lake_bucket_arn}/serving/dashboard.json",
      "${var.lake_bucket_arn}/serving/status.json",
      "${var.lake_bucket_arn}/serving/chart_data/*",
      "${var.lake_bucket_arn}/serving/stock/*",
    ]
  }
  statement {
    sid       = "DecryptDashboardServingObjects"
    actions   = ["kms:Decrypt"]
    resources = [var.data_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${local.region}.amazonaws.com"]
    }
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api_fn.arn}:*"]
  }
  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
  statement {
    sid       = "EnvDecrypt"
    actions   = ["kms:Decrypt"]
    resources = [var.data_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["lambda.${local.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "prefs-api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

data "archive_file" "api" {
  type        = "zip"
  source_file = "${path.root}/../../services/data-jobs/src/functions/prefs_api/prefs_api.py"
  output_path = "${path.module}/.build/prefs_api.zip"
}

resource "aws_lambda_function" "api" {
  #checkov:skip=CKV_AWS_117:Accepted risk: job and API Lambdas run outside a VPC; see architecture doc, Accepted risk
  #checkov:skip=CKV_AWS_272:Code signing not used; images and zips are built by CI from the protected main branch
  #checkov:skip=CKV_AWS_116:Synchronous API function; errors return HTTP status codes, a DLQ would never receive them
  function_name                  = local.fn_name
  description                    = "GET/PUT /prefs for the signed-in user"
  role                           = aws_iam_role.api.arn
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  handler                        = "prefs_api.handler"
  filename                       = data.archive_file.api.output_path
  source_code_hash               = data.archive_file.api.output_base64sha256
  timeout                        = 10
  memory_size                    = 256
  kms_key_arn                    = var.data_key_arn
  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = {
      PREFS_TABLE           = aws_dynamodb_table.prefs.name
      MAX_TICKERS_PER_USER  = tostring(var.max_tickers_per_user)
      PREFS_ACCESS_ROLE_ARN = aws_iam_role.access.arn
      EVENT_SOURCE          = local.event_source
      LAKE_BUCKET           = var.lake_bucket_name
      AWS_USE_FIPS_ENDPOINT = "true"
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.api_fn, aws_iam_role_policy.api]
}

# ---------------------------------------------------------------------------
# HTTP API with a Cognito JWT authorizer. CloudFront will front it at /api/*
# (with WAF) when the site is built; until then the execute-api URL is used.
# ---------------------------------------------------------------------------
resource "aws_apigatewayv2_api" "site" {
  name          = "${var.name}-site-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = var.site_origins
    allow_methods = ["GET", "PUT"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.site.id
  name             = "cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    audience = [var.cognito_client_id]
    issuer   = var.cognito_issuer_url
  }
}

resource "aws_apigatewayv2_integration" "prefs" {
  api_id                 = aws_apigatewayv2_api.site.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "site" {
  for_each           = toset(["GET /prefs", "PUT /prefs", "GET /dashboard", "GET /status", "GET /chart/{ticker}", "GET /stock/{ticker}"])
  api_id             = aws_apigatewayv2_api.site.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.prefs.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.site.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId", ip = "$context.identity.sourceIp", sub = "$context.authorizer.claims.sub",
      route     = "$context.routeKey", status = "$context.status", latencyMs = "$context.responseLatency",
      error     = "$context.authorizer.error", time = "$context.requestTime"
    })
  }
}

resource "aws_lambda_permission" "api_route" {
  for_each = {
    prefs     = "prefs"
    dashboard = "dashboard"
    status    = "status"
    chart     = "chart/*"
    stock     = "stock/*"
  }
  statement_id  = "AllowApiGateway-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.site.execution_arn}/*/*/${each.value}"
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.name}-site-api-5xx"
  alarm_description   = "Site API returned 5xx errors in two consecutive 5-minute windows"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  statistic           = "Sum"
  dimensions          = { ApiId = aws_apigatewayv2_api.site.id, Stage = "$default" }
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.ops_topic_arn]
  ok_actions          = [var.ops_topic_arn]
}

# Per-code visibility for prefs_api structured errors. The filter phrases match
# _log_event() in prefs_api.py (json.dumps default spacing).
locals {
  prefs_api_error_alarms = {
    STS_UNAVAILABLE = {
      event       = "prefs_api_error"
      description = "Site API could not assume the preferences role (STS)"
    }
    SERVING_READ_FAILED = {
      event       = "prefs_api_error"
      description = "Site API could not read a serving object (S3 or KMS)"
    }
    PREFS_STORE_UNAVAILABLE = {
      event       = "prefs_api_error"
      description = "Site API could not reach the preferences table"
    }
    SERVING_DOCUMENT_INVALID = {
      event       = "prefs_api_error"
      description = "Site API read a serving document that was not valid JSON"
    }
    INTERNAL = {
      event       = "prefs_api_error"
      description = "Site API hit an unexpected error"
    }
    TICKER_ADDED_PUBLISH_FAILED = {
      event       = "ticker_added_publish_failed"
      description = "Site API saved preferences but could not publish TickerAdded"
    }
  }
}

resource "aws_cloudwatch_log_metric_filter" "prefs_api_error" {
  for_each       = local.prefs_api_error_alarms
  name           = "${var.name}-prefs-api-${lower(replace(each.key, "_", "-"))}"
  log_group_name = aws_cloudwatch_log_group.api_fn.name
  pattern        = "\"event\": \"${each.value.event}\" \"code\": \"${each.key}\""

  metric_transformation {
    name      = each.key
    namespace = "${var.name}/prefs-api"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "prefs_api_error" {
  for_each            = local.prefs_api_error_alarms
  alarm_name          = "${var.name}-prefs-api-${lower(replace(each.key, "_", "-"))}"
  alarm_description   = each.value.description
  namespace           = "${var.name}/prefs-api"
  metric_name         = each.key
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.ops_topic_arn]
  ok_actions          = [var.ops_topic_arn]
}

# ---------------------------------------------------------------------------
# Collectors' read access: Scan returning only user_sub and tickers, so jobs can
# build the ticker union without seeing anyone's display settings.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "collector_read" {
  statement {
    sid       = "ScanTickersOnly"
    actions   = ["dynamodb:Scan"]
    resources = [aws_dynamodb_table.prefs.arn]
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["user_sub", "tickers"]
    }
  }
  statement {
    sid       = "TableKey"
    actions   = ["kms:Decrypt"]
    resources = [var.data_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["dynamodb.${local.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "collector_read" {
  name        = "${var.name}-prefs-collector-read"
  description = "Read-only scan of user_sub and tickers in the user prefs table"
  policy      = data.aws_iam_policy_document.collector_read.json
}

output "table_name" {
  value = aws_dynamodb_table.prefs.name
}

output "table_arn" {
  value = aws_dynamodb_table.prefs.arn
}

output "collector_read_policy_arn" {
  value = aws_iam_policy.collector_read.arn
}

output "api_endpoint" {
  value = aws_apigatewayv2_api.site.api_endpoint
}

output "log_group_names" {
  value = [aws_cloudwatch_log_group.api_fn.name]
}

output "event_source" {
  value = local.event_source
}
