"""#330: paid security add-ons are off or narrowed in place. Core controls stay."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "infra" / "terraform"


def _resource(text: str, address: str) -> str:
    start = text.index(address)
    depth = 0
    begun = False
    for index, char in enumerate(text[start:], start):
        if char == "{":
            depth += 1
            begun = True
        elif char == "}":
            depth -= 1
            if begun and depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unterminated {address}")


def _blocks(text: str, keyword: str) -> list[str]:
    """Inner text of each `keyword { ... }` block, including nested braces."""
    needle = f"{keyword} {{"
    found = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return found
        brace = text.index("{", index)
        depth = 0
        for end, char in enumerate(text[brace:], brace):
            depth += {"{": 1, "}": -1}.get(char, 0)
            if depth == 0:
                found.append(text[brace + 1 : end])
                start = end + 1
                break
        else:
            raise AssertionError(f"unterminated {keyword}")


def test_cloudtrail_insights_are_off_and_lake_events_are_write_only():
    audit = (ROOT / "modules" / "audit_logging" / "main.tf").read_text()
    assert "insight_selector" not in audit
    assert "ApiCallRateInsight" not in audit
    assert "ApiErrorRateInsight" not in audit
    trail = _resource(audit, 'resource "aws_cloudtrail" "this"')
    assert re.search(r"enable_log_file_validation\s*=\s*true", trail)
    assert "DenyAuditDeletion" in audit
    selector = trail.split("Data lake object writes", 1)[1].split("Lambda invocations", 1)[0]
    assert re.search(r'field\s*=\s*"readOnly"', selector)
    assert re.search(r'equals\s*=\s*\["false"\]', selector)
    assert re.search(r'field\s*=\s*"resources\.ARN"', selector)
    assert "var.lake_bucket_arn" in selector
    assert "not_starts_with" in selector
    assert "aws_s3_bucket.audit.arn" in selector
    assert "aws_s3_bucket.config.arn" in selector
    arn_blocks = [
        block for block in _blocks(selector, "field_selector") if re.search(r'field\s*=\s*"resources\.ARN"', block)
    ]
    assert len(arn_blocks) == 1
    assert "starts_with" in arn_blocks[0] and "not_starts_with" in arn_blocks[0]
    lifecycle = _resource(audit, 'resource "aws_s3_bucket_lifecycle_configuration" "audit"')
    assert re.search(r"days\s*=\s*90\b", lifecycle)
    assert "GLACIER" in lifecycle
    assert "var.retention_days" in lifecycle
    config = _resource(audit, 'resource "aws_s3_bucket_lifecycle_configuration" "config"')
    assert re.search(r"days\s*=\s*365\b", config)


def test_advanced_event_selectors_use_each_field_once():
    """CloudTrail rejects two field selectors for the same field (deploy 43372765461)."""
    audit = (ROOT / "modules" / "audit_logging" / "main.tf").read_text()
    trail = _resource(audit, 'resource "aws_cloudtrail" "this"')
    selectors = _blocks(trail, "advanced_event_selector")
    assert selectors
    for body in selectors:
        fields = []
        for block in _blocks(body, "field_selector"):
            match = re.search(r'field\s*=\s*"([^"]+)"', block)
            assert match, block
            fields.append(match.group(1))
        assert fields
        assert len(fields) == len(set(fields)), fields
    writes = next(body for body in selectors if "Data lake object writes" in body)
    write_fields = [
        re.search(r'field\s*=\s*"([^"]+)"', block).group(1) for block in _blocks(writes, "field_selector")
    ]
    assert write_fields == ["eventCategory", "resources.type", "readOnly", "resources.ARN"]


def test_guardduty_addons_are_disabled_in_place_and_the_dollar_claim_is_gone():
    security = (ROOT / "modules" / "security_services" / "main.tf").read_text()
    s3_feature = _resource(security, 'resource "aws_guardduty_detector_feature" "s3"')
    assert 'name        = "S3_DATA_EVENTS"' in s3_feature
    assert 'status      = "DISABLED"' in s3_feature
    lambda_feature = _resource(security, 'resource "aws_guardduty_detector_feature" "lambda"')
    assert 'name        = "LAMBDA_NETWORK_LOGS"' in lambda_feature
    assert 'status      = "ENABLED"' in lambda_feature
    detector = _resource(security, 'resource "aws_guardduty_detector" "this"')
    assert re.search(r"enable\s*=\s*true", detector)
    assert "raw/company_ir/" in security
    assert "raw/fund_filings/" in security
    plan = _resource(security, 'resource "aws_guardduty_malware_protection_plan" "raw"')
    assert "malware_document_prefixes" in plan
    assert 'object_prefixes = ["raw/"]' not in security
    assert 'resource "aws_securityhub_account" "this"' in security
    assert "nist-800-53/v/5.0.0" in security
    assert 'resource "aws_inspector2_enabler" "this"' in security
    kms = (ROOT / "modules" / "kms" / "main.tf").read_text()
    assert 'resource "aws_kms_key" "data"' in kms
    assert 'resource "aws_kms_key" "audit"' in kms

    variables = (ROOT / "variables.tf").read_text()
    readme = (ROOT / "README.md").read_text()
    assert "$0.09" in variables and "0.215" in variables
    assert "~$1" not in variables and "$1/month" not in variables
    assert "~$1" not in readme and "$1/month" not in readme
    assert "$0.09" in readme and "0.215" in readme
    jobs = variables.split("dashboard-build = {", 1)[1].split("q1-fundamentals = {", 1)[0]
    assert 'schedule = "cron(45 7,9,11,13,15,17,19,21 ? * MON-FRI *)"' in jobs
    assert '"job:H2"' in jobs
    assert '"job:SEC?partial"' in jobs
