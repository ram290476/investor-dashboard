import json

import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName="invdash-user-prefs",
            KeySchema=[{"AttributeName": "user_sub", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "user_sub", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        role = boto3.client("iam").create_role(RoleName="prefs-access", AssumeRolePolicyDocument="{}")
        monkeypatch.setenv("PREFS_ACCESS_ROLE_ARN", role["Role"]["Arn"])
        import importlib

        import prefs_api

        importlib.reload(prefs_api)
        sent = []
        monkeypatch.setattr(prefs_api, "publish_ticker_added", lambda t: sent.extend(t))
        yield prefs_api, sent


def _event(method, sub="user-a", body=None):
    return {
        "requestContext": {"http": {"method": method}, "authorizer": {"jwt": {"claims": {"sub": sub}}}},
        "body": json.dumps(body) if body is not None else None,
    }


def test_authenticated_dashboard_and_status_routes(api, monkeypatch):
    mod, _ = api
    documents = {
        "serving/dashboard.json": {"schema_version": 1, "tickers": {"TSLA": {"price_history": []}}},
        "serving/status.json": {"jobs": [{"job": "D4", "status": "ok"}]},
    }
    monkeypatch.setattr(mod, "_read_serving_json", lambda key: documents.get(key))

    dashboard_event = _event("GET")
    dashboard_event["rawPath"] = "/dashboard"
    dashboard = json.loads(mod.handler(dashboard_event, None)["body"])
    assert dashboard["schema_version"] == 1
    assert dashboard["status"]["jobs"][0]["job"] == "D4"

    status_event = _event("GET")
    status_event["rawPath"] = "/status"
    status = json.loads(mod.handler(status_event, None)["body"])
    assert status["jobs"][0]["status"] == "ok"


def test_dashboard_route_reports_not_ready_instead_of_fake_data(api, monkeypatch):
    mod, _ = api
    monkeypatch.setattr(mod, "_read_serving_json", lambda _key: None)
    event = _event("GET")
    event["rawPath"] = "/dashboard"

    response = mod.handler(event, None)
    assert response["statusCode"] == 503
    assert json.loads(response["body"])["code"] == "DASHBOARD_NOT_READY"


def test_get_defaults_then_put_and_read_back(api):
    mod, sent = api
    r = mod.handler(_event("GET"), None)
    assert r["statusCode"] == 200 and json.loads(r["body"])["tickers"] == ["TSLA", "SPCX"]
    body = {
        "tickers": ["tsla", "SPCX", "RIVN"],
        "pinned": ["RIVN"],
        "display": {"time_zone": "America/Los_Angeles", "updown_palette": "blue-orange"},
        "version": 0,
    }
    r = mod.handler(_event("PUT", body=body), None)
    saved = json.loads(r["body"])
    assert r["statusCode"] == 200 and saved["version"] == 1 and saved["tickers"][0] == "TSLA"
    assert sent == ["RIVN"]
    got = json.loads(mod.handler(_event("GET"), None)["body"])
    assert got["pinned"] == ["RIVN"] and got["display"]["updown_palette"] == "blue-orange"


def test_stale_version_is_rejected(api):
    mod, _ = api
    body = {"tickers": ["TSLA"], "pinned": [], "version": 0}
    assert mod.handler(_event("PUT", body=body), None)["statusCode"] == 200
    assert mod.handler(_event("PUT", body=body), None)["statusCode"] == 409


def test_users_do_not_see_each_other(api):
    mod, _ = api
    mod.handler(_event("PUT", "user-a", {"tickers": ["NVDA"], "pinned": [], "version": 0}), None)
    other = json.loads(mod.handler(_event("GET", "user-b"), None)["body"])
    assert other["tickers"] == ["TSLA", "SPCX"]


def test_unauthenticated(api):
    mod, _ = api
    assert mod.handler({"requestContext": {"http": {"method": "GET"}}}, None)["statusCode"] == 401


@pytest.mark.parametrize(
    "body,msg",
    [
        ({"tickers": []}, "non-empty"),
        ({"tickers": ["TSLA", "TSLA"]}, "duplicates"),
        ({"tickers": ["$$$"]}, "Not valid"),
        ({"tickers": ["A", "B", "C", "D", "E", "F", "G"], "pinned": ["A", "B", "C", "D", "E", "F", "G"]}, "At most 6"),
        ({"tickers": ["TSLA"], "pinned": ["NVDA"]}, "also in tickers"),
        ({"tickers": ["TSLA"], "display": {"time_zone": "Mars/Olympus"}}, "Unknown time zone"),
        ({"tickers": ["TSLA"], "display": {"updown_palette": "pink"}}, "updown_palette"),
    ],
)
def test_validation(api, body, msg):
    mod, _ = api
    r = mod.handler(_event("PUT", body={"version": 0, **body}), None)
    assert r["statusCode"] == 400 and msg in json.loads(r["body"])["error"]
