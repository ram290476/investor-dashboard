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


def test_prefs_api_metric_filter_matches_log_event_keys(capsys):
    import prefs_api

    prefs_api._log_event("prefs_api_error", "GET /prefs", "GET", RuntimeError("x"), "INTERNAL")
    entry = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    for key in re.findall(r"\$\.(\w+)", _pattern_template()):
        assert key in entry, key
