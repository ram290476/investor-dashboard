"""#227: every HTTP API route must have a Lambda invoke permission, or API Gateway returns 500.

Parses infra/terraform/modules/user_prefs/main.tf and checks each aws_apigatewayv2_route.site route
key is matched by some aws_lambda_permission.api_route source_arn path pattern (`*` = one or more chars
within the execute-api ARN, as API Gateway/Lambda match it).
"""
from __future__ import annotations

import fnmatch
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


def _routes(text: str) -> list[str]:
    block = _block(text, 'resource "aws_apigatewayv2_route" "site"')
    toset = re.search(r"for_each\s*=\s*toset\(\[(.*?)\]\)", block, re.S)
    assert toset, "route for_each toset([...]) not found"
    return re.findall(r'"([^"]+)"', toset.group(1))


def _permission_patterns(text: str) -> dict[str, str]:
    block = _block(text, 'resource "aws_lambda_permission" "api_route"')
    mapping = re.search(r"for_each\s*=\s*\{(.*?)\}", block, re.S)
    assert mapping, "permission for_each map not found"
    assert "/*/*/${each.value}" in block, "source_arn shape changed; update this test"
    return dict(re.findall(r'^\s*(\w+)\s*=\s*"([^"]+)"', mapping.group(1), re.M))


def _path_matches(route_key: str, pattern: str) -> bool:
    _method, path = route_key.split(" ", 1)
    concrete = re.sub(r"\{[^}]+\}", "X", path).lstrip("/")
    return fnmatch.fnmatchcase(concrete, pattern)


def test_every_api_route_has_an_invoke_permission():
    text = MAIN_TF.read_text()
    routes = _routes(text)
    patterns = _permission_patterns(text)
    assert routes and patterns
    missing = [r for r in routes if not any(_path_matches(r, p) for p in patterns.values())]
    assert not missing, f"routes without aws_lambda_permission.api_route grant: {missing}"


def test_fund_routes_are_granted():
    patterns = _permission_patterns(MAIN_TF.read_text())
    assert patterns.get("fund") == "fund/*"
    assert patterns.get("fund_flag_reset") == "admin/fund-flag/*"


def test_matcher_rejects_an_ungranted_route():
    assert not _path_matches("GET /fund/{ticker}", "chart/*")
    assert _path_matches("PUT /stock/{ticker}/metrics/{metric_id}", "stock/*/metrics/*")
