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


def test_ticker_cap_and_new_fundamental_ids(api):
    mod, _ = api
    tickers = [f"T{i}" for i in range(25)]
    body = {
        "tickers": tickers,
        "chart_settings": {"T0": {
            "overlays": ["FUNDAMENTAL:shares_outstanding", "FUNDAMENTAL:public_float_usd", "NEWS:SENTIMENT"],
            "lanes": [],
        }},
    }
    assert len(mod.validate(body)["tickers"]) == 25
    with pytest.raises(mod.ValidationError, match="At most 25"):
        mod.validate({**body, "tickers": [*tickers, "NEW"]})


@pytest.mark.parametrize("change,accepted", [
    ("display", True), ("remove", True), ("reorder", True), ("pins", True),
    ("swap", False), ("grow", False),
])
def test_legacy_over_limit_watchlists_remain_editable_without_growth(api, change, accepted):
    mod, _ = api
    stored = [f"T{i}" for i in range(30)]
    boto3.resource("dynamodb").Table("invdash-user-prefs").put_item(Item={
        "user_sub": "user-a", "tickers": stored, "pinned": [], "display": {},
        "chart_settings": {}, "version": 1,
    })
    assert json.loads(mod.handler(_event("GET"), None)["body"])["tickers"] == stored
    tickers = stored[:-1] if change == "remove" else stored[::-1] if change == "reorder" else list(stored)
    if change == "swap":
        tickers[-1] = "NEW"
    if change == "grow":
        tickers.append("NEW")
    body = {
        "tickers": tickers, "version": 1, "display": {"theme": "clean-light"},
        "pinned": ["T0"] if change == "pins" else [],
    }
    response = mod.handler(_event("PUT", body=body), None)
    assert response["statusCode"] == (200 if accepted else 400)
    result = json.loads(mod.handler(_event("GET"), None)["body"])
    assert result["tickers"] == (tickers if accepted else stored)
    if not accepted:
        assert "Remove 6" in json.loads(response["body"])["error"]


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

def test_chart_route_only_reads_a_ticker_in_the_callers_watchlist(api, monkeypatch):
    mod, _ = api
    mod.handler(_event("PUT", body={"tickers": ["TSLA"], "pinned": [], "version": 0}), None)
    reads = []
    chart_data = {"ticker": "TSLA", "macro_pressure": [{"date": "2026-10-01", "value": 0.2}]}
    monkeypatch.setattr(mod, "_read_serving_json", lambda key: reads.append(key) or chart_data)

    event = _event("GET")
    event["rawPath"] = "/chart/TSLA"
    response = mod.handler(event, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == chart_data
    assert reads == ["serving/chart_data/TSLA.json"]

    other_ticker = _event("GET")
    other_ticker["rawPath"] = "/chart/SPCX"
    denied = mod.handler(other_ticker, None)
    assert denied["statusCode"] == 403
    assert reads == ["serving/chart_data/TSLA.json"]


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


@pytest.mark.parametrize("theme", [
    "industrial-dark", "terminal-amber", "charting-navy",
    "clean-light", "colorblind-hc", "midnight-slate",
])
def test_themes_round_trip_with_independent_palette_and_chart_settings(api, theme):
    mod, _ = api
    assert json.loads(mod.handler(_event("GET"), None)["body"])["display"]["theme"] == "industrial-dark"
    body = {
        "tickers": ["TSLA"], "pinned": ["TSLA"], "version": 0,
        "display": {"theme": theme, "updown_palette": "theme", "chart_period": "1Y"},
        "chart_settings": {"TSLA": {"overlays": ["MA20"], "lanes": ["VOL"]}},
    }
    response = mod.handler(_event("PUT", body=body), None)
    assert response["statusCode"] == 200
    saved = json.loads(mod.handler(_event("GET"), None)["body"])
    assert saved["display"]["theme"] == theme
    assert saved["display"]["updown_palette"] == "theme"
    assert saved["display"]["chart_period"] == "1Y"
    assert saved["chart_settings"] == body["chart_settings"]


@pytest.mark.parametrize("theme", ["invalid", "", None, [], {}])
def test_invalid_theme_returns_validation_error_without_writing(api, theme):
    mod, _ = api
    response = mod.handler(_event("PUT", body={"tickers": ["TSLA"], "display": {"theme": theme}}), None)
    assert response["statusCode"] == 400
    assert "theme must be one of" in response["body"]
    assert json.loads(mod.handler(_event("GET"), None)["body"])["version"] == 0


def test_legacy_stored_preferences_get_the_default_theme_without_losing_settings(api):
    mod, _ = api
    boto3.resource("dynamodb").Table("invdash-user-prefs").put_item(Item={
        "user_sub": "user-a", "tickers": ["TSLA"], "pinned": [],
        "display": {"updown_palette": "blue-orange", "chart_period": "1Y"},
        "version": 4,
    })
    got = json.loads(mod.handler(_event("GET"), None)["body"])
    assert got["display"] == {
        "theme": "industrial-dark", "updown_palette": "blue-orange", "chart_period": "1Y",
    }
    assert got["version"] == 4


def test_users_do_not_see_each_other(api):
    mod, _ = api
    mod.handler(_event("PUT", "user-a", {"tickers": ["NVDA"], "pinned": [], "version": 0}), None)
    other = json.loads(mod.handler(_event("GET", "user-b"), None)["body"])
    assert other["tickers"] == ["TSLA", "SPCX"]


def test_chart_period_allow_list_and_unknown_fallback(api):
    mod, _ = api
    fresh = json.loads(mod.handler(_event("GET"), None)["body"])
    assert fresh["display"]["chart_period"] == "1M"

    saved = json.loads(
        mod.handler(
            _event(
                "PUT",
                body={
                    "tickers": ["TSLA"],
                    "pinned": [],
                    "display": {"time_zone": "America/New_York", "updown_palette": "green-red", "chart_period": "5Y"},
                    "version": 0,
                },
            ),
            None,
        )["body"]
    )
    assert saved["display"]["chart_period"] == "5Y"
    assert saved["display"]["updown_palette"] == "green-red"
    coerced = json.loads(
        mod.handler(
            _event(
                "PUT",
                body={
                    "tickers": ["TSLA"],
                    "pinned": [],
                    "display": {"chart_period": "YTD"},
                    "version": saved["version"],
                },
            ),
            None,
        )["body"]
    )
    assert coerced["display"]["chart_period"] == "YTD"
    assert coerced["display"]["time_zone"] == "America/New_York"

    unknown = json.loads(
        mod.handler(
            _event(
                "PUT",
                body={
                    "tickers": ["TSLA"],
                    "pinned": [],
                    "display": {"chart_period": "not-a-period"},
                    "version": coerced["version"],
                },
            ),
            None,
        )["body"]
    )
    assert unknown["display"]["chart_period"] == "1M"


def test_chart_settings_are_saved_per_watchlist_ticker_and_survive_legacy_put(api):
    mod, _ = api
    saved = json.loads(
        mod.handler(
            _event(
                "PUT",
                body={
                    "tickers": ["TSLA", "SPCX"],
                    "pinned": [],
                    "chart_settings": {
                        "TSLA": {"overlays": ["SPY", "MA20"], "lanes": ["SI", "PRESS"]},
                        "REMOVED": {"overlays": ["DGS10"], "lanes": ["OPT"]},
                    },
                    "version": 0,
                },
            ),
            None,
        )["body"]
    )
    assert saved["chart_settings"] == {"TSLA": {"overlays": ["SPY", "MA20"], "lanes": ["SI", "PRESS"]}}

    legacy = json.loads(
        mod.handler(
            _event(
                "PUT",
                body={"tickers": ["TSLA", "SPCX"], "pinned": [], "version": saved["version"]},
            ),
            None,
        )["body"]
    )
    assert legacy["chart_settings"] == saved["chart_settings"]


@pytest.mark.parametrize(
    "settings,msg",
    [
        ({"TSLA": {"overlays": ["UNKNOWN"], "lanes": ["VOL"]}}, "unsupported overlay"),
        ({"TSLA": {"overlays": [{}], "lanes": ["VOL"]}}, "unsupported overlay"),
        ({"TSLA": {"overlays": [], "lanes": ["UNKNOWN"]}}, "unsupported lane"),
        ({"TSLA": {"overlays": ["SPY"] * 6, "lanes": ["VOL"]}}, "at most 5"),
    ],
)
def test_chart_settings_reject_unknown_or_excess_values(api, settings, msg):
    mod, _ = api
    response = mod.handler(
        _event(
            "PUT",
            body={"tickers": ["TSLA"], "pinned": [], "chart_settings": settings, "version": 0},
        ),
        None,
    )
    assert response["statusCode"] == 400
    assert msg in json.loads(response["body"])["error"]


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
