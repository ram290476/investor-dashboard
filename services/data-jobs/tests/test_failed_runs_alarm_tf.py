"""#233: CloudWatch rejects SEARCH on metric alarms. Keep invdash-failed-runs a plain metric alarm."""

import re
from pathlib import Path

TF = Path(__file__).resolve().parents[3] / "infra" / "terraform" / "modules" / "observability" / "main.tf"


def _failed_runs_block() -> str:
    text = TF.read_text()
    start = text.index('resource "aws_cloudwatch_metric_alarm" "failed_runs" {')
    depth, i = 0, text.index("{", start)
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[start : j + 1]
    raise AssertionError("unterminated block")


def test_failed_runs_alarm_is_plain_metric_alarm_on_failed_sources():
    block = _failed_runs_block()
    assert "SEARCH" not in block and "metric_query" not in block and "expression" not in block
    assert re.search(r'metric_name\s*=\s*"FailedSources"', block)
    assert re.search(r'statistic\s*=\s*"Sum"', block)
    assert re.search(r"period\s*=\s*3600", block)
    assert re.search(r"threshold\s*=\s*2\b", block)
    assert re.search(r"dimensions\s*=\s*local\.app_dims", block)


def test_moved_block_keeps_in_place_update():
    assert 'to   = aws_cloudwatch_metric_alarm.failed_runs' in TF.read_text()
