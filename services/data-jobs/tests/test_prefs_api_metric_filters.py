"""Guard the CloudWatch metric filters for prefs_api structured errors.

Deploys 165/167 failed with `InvalidParameterException: Invalid metric filter pattern`
because the filter used quoted terms joined by bare colons (`"event": "x"`), which is
not valid filter syntax. The filters must be JSON filters on the keys _log_event emits.
"""

import json
import re
from pathlib import Path

MAIN_TF = Path(__file__).resolve().parents[3] / "infra" / "terraform" / "modules" / "user_prefs" / "main.tf"


def _block(text: str, header: str) -> str:
    start = text.index(header)
    depth = 0
    for i in range(text.index("{", start), len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start : i + 1]
    raise AssertionError(f"unterminated block {header}")


def _pattern_template() -> str:
    block = _block(MAIN_TF.read_text(), 'resource "aws_cloudwatch_log_metric_filter" "prefs_api_error"')
    match = re.search(r'^\s*pattern\s*=\s*"(.*)"\s*$', block, re.M)
    assert match, "pattern attribute not found"
    return match.group(1).replace('\\"', '"')


def test_prefs_api_metric_filter_is_a_json_filter():
    pattern = _pattern_template()
    assert pattern.startswith("{") and pattern.endswith("}"), pattern
    assert '($.event = "${each.value.event}")' in pattern
    assert '($.code = "${each.key}")' in pattern


def test_metric_review_route_cors_integration_and_iam_prefixes():
    main = MAIN_TF.read_text()
    start = main.index('sid     = "WriteMetricReview"')
    block = main[start:main.index("statement {", start)]
    assert 'actions = ["s3:PutObject"]' in block
    assert "${var.lake_bucket_arn}/catalog/*" in block
    assert "${var.lake_bucket_arn}/serving/stock/*" in block
    assert "s3:DeleteObject" not in block
    assert "s3:GetObject" not in block
    read_catalog = main[main.index('sid       = "ReadMetricCatalog"'):start]
    assert 'actions   = ["s3:GetObject"]' in read_catalog
    assert "${var.lake_bucket_arn}/catalog/*" in read_catalog
    read_serving = main[
        main.index('sid     = "ReadDashboardServingObjects"'):main.index('sid       = "ReadMetricCatalog"')
    ]
    assert "${var.lake_bucket_arn}/serving/stock/*" in read_serving
    route = _block(main, 'resource "aws_apigatewayv2_route" "site"')
    assert 'PUT /stock/{ticker}/metrics/{metric_id}' in route
    assert "route_key          = each.value" in route
    assert 'authorization_type = "JWT"' in route
    assert "authorizer_id      = aws_apigatewayv2_authorizer.jwt.id" in route
    assert 'target             = "integrations/${aws_apigatewayv2_integration.prefs.id}"' in route
    integration = _block(main, 'resource "aws_apigatewayv2_integration" "prefs"')
    assert 'integration_type       = "AWS_PROXY"' in integration
    assert 'payload_format_version = "2.0"' in integration
    assert "aws_lambda_function.api.invoke_arn" in integration
    cors = _block(main, 'resource "aws_apigatewayv2_api" "site"')
    assert 'allow_methods = ["GET", "PUT", "POST"]' in cors
    assert '"authorization"' in cors
    assert '"content-type"' in cors
    assert '"accept"' in cors
    permission = main[
        main.index('resource "aws_lambda_permission" "api_route"'):
        main.index('resource "aws_cloudwatch_metric_alarm" "api_5xx"')
    ]
    assert 'stock_metric = "stock/*/metrics/*"' in permission
    assert "source_arn    = \"${aws_apigatewayv2_api.site.execution_arn}/*/*/${each.value}\"" in permission
    assert "CATALOG_WRITE_FAILED" in main
    auth = (MAIN_TF.parents[1] / "site_auth" / "main.tf").read_text()
    assert 'resource "aws_cognito_user_group" "admin"' in auth
    assert "aws_cognito_user_in_group" not in auth
    assert "ram.vellamsetti@gmail.com" in auth
    assert "must not be added" in auth
    assert "admin-add-user-to-group" in auth
    jobs = (MAIN_TF.parents[2] / "variables.tf").read_text()
    serve = jobs[jobs.index("q2-company-ir-serve = {"):jobs.index("short-interest = {")]
    assert 'read_prefixes  = ["curated/company_metrics/", "serving/stock/", "catalog/"]' in serve
    assert 'write_prefixes = ["serving/stock/"]' in serve
    extract = jobs[jobs.index("q2-company-ir-extract = {"):jobs.index("q2-company-ir-serve = {")]
    assert '"catalog/"' in extract
    assert "catalog/" not in extract[extract.index("write_prefixes"):]


def test_prefs_api_metric_filter_matches_log_event_keys(capsys):
    import prefs_api

    prefs_api._log_event("prefs_api_error", "GET /prefs", "GET", RuntimeError("x"), "INTERNAL")
    entry = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    for key in re.findall(r"\$\.(\w+)", _pattern_template()):
        assert key in entry, key
