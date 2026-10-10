import base64
import gzip
import json

import boto3
import pytest
from botocore.exceptions import ClientError
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
        original_publish = prefs_api.publish_ticker_added

        def _record(tickers):
            sent.extend(tickers)

        _record.original = original_publish
        monkeypatch.setattr(prefs_api, "publish_ticker_added", _record)
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


def _stored(document: dict, etag: str = '"etag"') -> tuple[bytes, str, dict]:
    raw = json.dumps(document, separators=(",", ":"), default=str).encode()
    return raw, etag, document


def test_authenticated_dashboard_and_status_routes(api, monkeypatch):
    mod, _ = api
    documents = {
        "serving/dashboard.json": {"schema_version": 1, "tickers": {"TSLA": {"price_history": []}}},
        "serving/status.json": {"jobs": [{"job": "D4", "status": "ok"}]},
    }
    monkeypatch.setattr(
        mod, "_read_serving_document", lambda key: _stored(documents[key], f'"{key}"') if key in documents else None
    )

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

    def read(key):
        reads.append(key)
        return _stored(chart_data, '"chart"')

    monkeypatch.setattr(mod, "_read_serving_document", read)

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


def test_stock_route_hides_proposed_metrics_and_stays_on_the_watchlist(api, monkeypatch):
    mod, _ = api
    mod.handler(_event("PUT", body={"tickers": ["TSLA"], "pinned": [], "version": 0}), None)
    stored = {
        "ticker": "TSLA",
        "generated_at": "2026-10-08T12:00:00Z",
        "run_status": "ok",
        "metrics": [
            {"metric_id": "optimus", "approved": False, "approval_state": "proposed", "latest": {"value": 9}},
            {"metric_id": "tesla_semi", "approved": True, "approval_state": "approved", "latest": {"value": 4}},
        ],
    }
    monkeypatch.setattr(mod, "_read_serving_json", lambda key: stored if key == "serving/stock/TSLA.json" else None)

    event = _event("GET")
    event["rawPath"] = "/stock/TSLA"
    body = json.loads(mod.handler(event, None)["body"])
    assert [item["metric_id"] for item in body["metrics"]] == ["tesla_semi"]

    missing = _event("GET")
    missing["rawPath"] = "/stock/SPCX"
    denied = mod.handler(missing, None)
    assert denied["statusCode"] == 403

    mod.handler(_event("PUT", body={"tickers": ["TSLA", "NVDA"], "pinned": [], "version": 1}), None)
    empty = _event("GET")
    empty["rawPath"] = "/stock/NVDA"
    page = json.loads(mod.handler(empty, None)["body"])
    assert page["metrics"] == []
    assert page["freshness_label"] == "No company-specific metrics discovered"


def test_dashboard_route_reports_not_ready_instead_of_fake_data(api, monkeypatch):
    mod, _ = api
    monkeypatch.setattr(mod, "_read_serving_document", lambda _key: None)
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

def test_empty_chart_choices_round_trip_without_restoring_defaults(api):
    mod, _ = api
    body = {
        "tickers": ["TSLA", "SPCX"], "version": 0,
        "chart_settings": {
            "TSLA": {"overlays": [], "lanes": []},
            "SPCX": {"overlays": ["MA20"], "lanes": ["OPT", "PRESS", "VOL"]},
        },
    }
    response = mod.handler(_event("PUT", body=body), None)
    assert response["statusCode"] == 200
    saved = json.loads(mod.handler(_event("GET"), None)["body"])
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


def test_panel_drawers_round_trip_and_ignore_unknown_ids(api):
    mod, _ = api
    saved = json.loads(
        mod.handler(
            _event(
                "PUT",
                body={
                    "tickers": ["TSLA"],
                    "pinned": [],
                    "display": {"panels": {"rates": "closed", "made-up": "open", "company": "open"}},
                    "version": 0,
                },
            ),
            None,
        )["body"]
    )
    assert saved["display"]["panels"] == {"rates": "closed", "company": "open"}
    kept = json.loads(
        mod.handler(
            _event(
                "PUT",
                body={
                    "tickers": ["TSLA"],
                    "pinned": [],
                    "display": {"theme": "clean-light"},
                    "version": saved["version"],
                },
            ),
            None,
        )["body"]
    )
    assert kept["display"]["panels"] == {"rates": "closed", "company": "open"}
    assert kept["display"]["theme"] == "clean-light"
    again = json.loads(mod.handler(_event("GET"), None)["body"])
    assert again["display"]["panels"] == kept["display"]["panels"]


def test_dismissed_panels_and_panel_mode_round_trip(api):
    mod, _ = api
    saved = json.loads(
        mod.handler(
            _event(
                "PUT",
                body={
                    "tickers": ["TSLA"],
                    "pinned": [],
                    "display": {
                        "panels": {
                            "rates": "dismissed",
                            "news": "closed",
                            "filings-events": "open",
                            "made-up": "dismissed",
                        },
                        "panel_mode": "follow",
                    },
                    "version": 0,
                },
            ),
            None,
        )["body"]
    )
    assert saved["display"]["panels"] == {"rates": "dismissed", "news": "closed", "filings-events": "open"}
    assert saved["display"]["panel_mode"] == "follow"
    kept = json.loads(
        mod.handler(
            _event(
                "PUT",
                body={
                    "tickers": ["TSLA"],
                    "pinned": [],
                    "display": {"theme": "clean-light"},
                    "version": saved["version"],
                },
            ),
            None,
        )["body"]
    )
    assert kept["display"]["panels"] == {"rates": "dismissed", "news": "closed", "filings-events": "open"}
    assert kept["display"]["panel_mode"] == "follow"
    assert kept["display"]["theme"] == "clean-light"
    shown = json.loads(
        mod.handler(
            _event(
                "PUT",
                body={
                    "tickers": ["TSLA"],
                    "pinned": [],
                    "display": {"panel_mode": "all"},
                    "version": kept["version"],
                },
            ),
            None,
        )["body"]
    )
    assert shown["display"]["panel_mode"] == "all"
    assert shown["display"]["panels"] == {"rates": "dismissed", "news": "closed", "filings-events": "open"}
    rejected = mod.handler(
        _event(
            "PUT",
            body={"tickers": ["TSLA"], "pinned": [], "display": {"panel_mode": "pinned"}, "version": shown["version"]},
        ),
        None,
    )
    assert rejected["statusCode"] == 400
    assert "follow or all" in json.loads(rejected["body"])["error"]


@pytest.mark.parametrize(
    "panels,msg",
    [
        ("open", "must be an object"),
        ({"rates": "expanded"}, "open, closed, or dismissed"),
        ([], "must be an object"),
    ],
)
def test_panel_drawers_reject_invalid_values(api, panels, msg):
    mod, _ = api
    response = mod.handler(
        _event("PUT", body={"tickers": ["TSLA"], "pinned": [], "display": {"panels": panels}, "version": 0}),
        None,
    )
    assert response["statusCode"] == 400
    assert msg in json.loads(response["body"])["error"]
    assert json.loads(mod.handler(_event("GET"), None)["body"])["version"] == 0


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


SECURITY_HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


def _gzip_event(path: str, encoding: str | None = "gzip"):
    event = _event("GET")
    event["rawPath"] = path
    if encoding is not None:
        event["headers"] = {"accept-encoding": encoding}
    return event


def _decode(response: dict) -> dict:
    body = response["body"]
    if response.get("isBase64Encoded"):
        body = gzip.decompress(base64.b64decode(body)).decode()
    return json.loads(body)


def _assert_security(headers: dict) -> None:
    for key, value in SECURITY_HEADERS.items():
        assert headers[key] == value
    assert not any(str(key).lower().startswith("access-control") for key in headers)


def _history_document(bars: int = 400) -> dict:
    return {
        "schema_version": 3,
        "tickers": {
            f"T{ticker}": {
                "price_history": [
                    {
                        "date": f"2024-{(index % 12) + 1:02d}-{(index % 28) + 1:02d}",
                        "close": 100.0 + index,
                        "close_raw": 99.0 + index,
                        "adj_close": 100.0 + index,
                        "volume": 1_000_000 + index,
                        "headline": f"Session note {index} for ticker {ticker}",
                    }
                    for index in range(bars)
                ]
            }
            for ticker in range(7)
        },
    }


def test_dashboard_chart_and_status_gzip_when_the_client_accepts_it(api, monkeypatch):
    mod, _ = api
    dashboard = _history_document()
    status = {"jobs": [{"job": "D4", "status": "ok", "detail": "x" * 1200}]}
    chart = {"ticker": "TSLA", "points": [{"date": f"2024-01-{(i % 28) + 1:02d}", "value": i} for i in range(80)]}
    documents = {
        "serving/dashboard.json": dashboard,
        "serving/status.json": status,
        "serving/chart_data/TSLA.json": chart,
    }
    monkeypatch.setattr(
        mod, "_read_serving_document", lambda key: _stored(documents[key], f'"{key}"') if key in documents else None
    )
    mod.handler(_event("PUT", body={"tickers": ["TSLA"], "pinned": [], "version": 0}), None)

    plain = mod.handler(_gzip_event("/dashboard", encoding=None), None)
    assert plain.get("isBase64Encoded") is not True
    assert "Content-Encoding" not in plain["headers"]
    assert plain["headers"]["Vary"] == "Accept-Encoding"
    _assert_security(plain["headers"])
    plain_body = json.loads(plain["body"])
    assert plain_body["tickers"]["T0"]["price_history"]
    assert plain_body["status"]["jobs"][0]["job"] == "D4"

    compressed = mod.handler(_gzip_event("/dashboard"), None)
    assert compressed["headers"]["Content-Encoding"] == "gzip"
    assert compressed["headers"]["Vary"] == "Accept-Encoding"
    assert compressed["isBase64Encoded"] is True
    _assert_security(compressed["headers"])
    transfer = len(base64.b64decode(compressed["body"]))
    raw_size = len(plain["body"].encode())
    assert transfer <= raw_size * 0.25
    assert _decode(compressed) == plain_body

    for path in ("/status", "/chart/TSLA"):
        small_plain = mod.handler(_gzip_event(path, encoding=None), None)
        small_gzip = mod.handler(_gzip_event(path), None)
        assert len(small_plain["body"].encode()) > 1024
        assert small_gzip["headers"]["Content-Encoding"] == "gzip"
        assert small_gzip["headers"]["Vary"] == "Accept-Encoding"
        assert small_gzip["isBase64Encoded"] is True
        _assert_security(small_gzip["headers"])
        assert _decode(small_gzip) == json.loads(small_plain["body"])


def test_gzip_is_skipped_under_1kb_and_when_the_client_refuses_it(api, monkeypatch):
    mod, _ = api
    status = {"jobs": [{"job": "D4", "status": "ok"}]}
    def read_status(key):
        return _stored(status) if key == "serving/status.json" else None

    monkeypatch.setattr(mod, "_read_serving_document", read_status)
    assert len(json.dumps(status, separators=(",", ":")).encode()) <= 1024

    for encoding in ("gzip", "gzip;q=0", "br", "identity", None):
        response = mod.handler(_gzip_event("/status", encoding=encoding), None)
        assert response["statusCode"] == 200
        assert response.get("isBase64Encoded") is not True
        assert "Content-Encoding" not in response["headers"]
        assert "Vary" not in response["headers"]
        assert json.loads(response["body"]) == status
        _assert_security(response["headers"])


def test_warm_cache_and_precompressed_sibling_skip_recompression(api, monkeypatch):
    mod, _ = api
    chart = {"ticker": "TSLA", "points": [{"i": index, "note": "bar"} for index in range(80)]}
    stored = _stored(chart, '"chart-1"')
    calls = []
    real_compress = gzip.compress

    def spy(data, **kwargs):
        calls.append(len(data))
        return real_compress(data, **kwargs)

    monkeypatch.setattr(mod.gzip, "compress", spy)
    monkeypatch.setattr(mod, "_read_serving_document", lambda key: stored if key.endswith("TSLA.json") else None)
    monkeypatch.setattr(mod, "_read_gzip_sibling", lambda key: None)
    mod.handler(_event("PUT", body={"tickers": ["TSLA"], "pinned": [], "version": 0}), None)

    first = mod.handler(_gzip_event("/chart/TSLA"), None)
    second = mod.handler(_gzip_event("/chart/TSLA"), None)
    assert calls == [len(stored[0])]
    assert first["body"] == second["body"]
    assert _decode(first) == chart

    sibling = real_compress(stored[0], compresslevel=6, mtime=0)
    calls.clear()
    mod._GZIP_CACHE.clear()
    monkeypatch.setattr(mod, "_read_gzip_sibling", lambda key: sibling)
    served = mod.handler(_gzip_event("/chart/TSLA"), None)
    assert calls == []
    assert base64.b64decode(served["body"]) == sibling
    assert _decode(served) == chart


def test_response_over_the_lambda_limit_is_refused(api, monkeypatch):
    mod, _ = api
    document = _history_document(bars=40)
    def read_dashboard(key):
        return _stored(document) if key.endswith("dashboard.json") else None

    monkeypatch.setattr(mod, "_read_serving_document", read_dashboard)
    monkeypatch.setattr(mod, "LAMBDA_RESPONSE_LIMIT", 800)

    refused = mod.handler(_gzip_event("/dashboard", encoding=None), None)
    assert refused["statusCode"] == 503
    assert json.loads(refused["body"])["code"] == "RESPONSE_TOO_LARGE"
    assert len(refused["body"]) < 200
    _assert_security(refused["headers"])

    monkeypatch.setattr(mod, "LAMBDA_RESPONSE_LIMIT", 120)
    still_refused = mod.handler(_gzip_event("/dashboard"), None)
    assert still_refused["statusCode"] == 503
    assert json.loads(still_refused["body"])["code"] == "RESPONSE_TOO_LARGE"
    assert "isBase64Encoded" not in still_refused
    assert len(still_refused["body"]) < 200

    monkeypatch.setattr(mod, "LAMBDA_RESPONSE_LIMIT", 6 * 1024 * 1024)
    allowed = mod.handler(_gzip_event("/dashboard"), None)
    assert allowed["statusCode"] == 200
    assert allowed["headers"]["Content-Encoding"] == "gzip"
    assert len(base64.b64decode(allowed["body"])) < len(json.dumps(_decode(allowed)).encode())


def test_precompressed_dashboard_sibling_matches_the_build(api, monkeypatch):
    mod, _ = api
    import lake

    status = {"jobs": [{"job": "D4", "status": "ok"}]}
    snapshot = {"schema_version": 3, "pad": "a" * 2000, "status": status, "tickers": {"TSLA": {"price_history": []}}}
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="lake")
    monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
    monkeypatch.setattr(lake, "_s3", s3)
    monkeypatch.setattr(mod, "LAKE_BUCKET", "lake")
    monkeypatch.setattr(mod, "_s3", s3)
    lake.write_json(status, "serving/status.json")
    size = lake.write_json_and_gzip(snapshot, "serving/dashboard.json")
    calls = []
    monkeypatch.setattr(mod.gzip, "compress", lambda *args, **kwargs: calls.append(1))

    response = mod.handler(_gzip_event("/dashboard"), None)
    assert calls == []
    assert response["headers"]["Content-Encoding"] == "gzip"
    assert len(base64.b64decode(response["body"])) == size["gzip_bytes"]
    assert _decode(response)["status"] == status
    assert _decode(response)["schema_version"] == 3
SECRET = "super-secret-token"
_LOG_KEYS = {"event", "route", "method", "error_class", "code"}


def _client_error(code, operation, message="upstream failed"):
    return ClientError(
        {"Error": {"Code": code, "Message": message}, "ResponseMetadata": {"RequestId": SECRET}},
        operation,
    )


class _Body:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self):
        return self.payload


def _route_event(method, path, body=None, sub="user-a"):
    event = _event(method, sub, body)
    event["rawPath"] = path
    return event


def _log_lines(capsys):
    text = capsys.readouterr().out.strip()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _assert_log(entry, event_name, code, error_class, route):
    raw = json.dumps(entry)
    assert set(entry) == _LOG_KEYS
    assert entry == {
        "event": event_name,
        "route": route,
        "method": route.split()[0],
        "error_class": error_class,
        "code": code,
    }
    assert f'"event": "{event_name}"' in raw
    assert f'"code": "{code}"' in raw
    assert SECRET not in raw
    assert "SecretAccessKey" not in raw
    assert "SessionToken" not in raw


def _assert_error(response, status, code):
    assert response["statusCode"] == status
    assert response["headers"]["Content-Type"] == "application/json"
    body = json.loads(response["body"])
    assert body["code"] == code
    assert body["error"]
    assert SECRET not in response["body"]
    return body


def _patch_sts(monkeypatch, mod, aws_code):
    def assume_role(**_kwargs):
        raise _client_error(aws_code, "AssumeRole", f"SessionToken={SECRET}")

    monkeypatch.setattr(mod._sts, "assume_role", assume_role)


def _patch_get_object(monkeypatch, mod, get_object):
    monkeypatch.setattr(mod, "LAKE_BUCKET", "lake-bucket")
    monkeypatch.setattr(mod._s3, "get_object", get_object)


def _patch_table(monkeypatch, mod, **methods):
    original = mod._table_for

    def table_for(sub):
        table = original(sub)
        for name, fn in methods.items():
            setattr(table, name, fn)
        return table

    monkeypatch.setattr(mod, "_table_for", table_for)


@pytest.mark.parametrize(
    "method,path,route",
    [
        ("GET", "/prefs", "GET /prefs"),
        ("PUT", "/prefs", "PUT /prefs"),
        ("GET", "/chart/TSLA", "GET /chart/{ticker}"),
        ("GET", "/stock/TSLA", "GET /stock/{ticker}"),
    ],
)
@pytest.mark.parametrize("aws_code,status", [("Throttling", 503), ("AccessDenied", 502)])
def test_sts_failures_return_json_and_omit_secrets(api, monkeypatch, capsys, method, path, route, aws_code, status):
    mod, _ = api
    _patch_sts(monkeypatch, mod, aws_code)
    event = _route_event(method, path, {"tickers": ["TSLA"], "version": 0, "access_token": SECRET})
    body = _assert_error(mod.handler(event, None), status, "STS_UNAVAILABLE")
    assert body["error"] == mod._ERROR_MESSAGES["STS_UNAVAILABLE"]
    logs = _log_lines(capsys)
    assert len(logs) == 1
    _assert_log(logs[0], "prefs_api_error", "STS_UNAVAILABLE", "ClientError", route)


@pytest.mark.parametrize(
    "path,route,aws_code,status",
    [
        ("/dashboard", "GET /dashboard", "SlowDown", 503),
        ("/dashboard", "GET /dashboard", "AccessDenied", 502),
        ("/dashboard", "GET /dashboard", "KMS.AccessDeniedException", 502),
        ("/status", "GET /status", "KMS.DisabledException", 502),
        ("/chart/TSLA", "GET /chart/{ticker}", "SlowDown", 503),
        ("/stock/TSLA", "GET /stock/{ticker}", "KMS.AccessDeniedException", 502),
    ],
)
def test_s3_and_kms_read_failures_return_serving_read_failed(api, monkeypatch, capsys, path, route, aws_code, status):
    mod, _ = api

    def get_object(**_kwargs):
        raise _client_error(aws_code, "GetObject", f"SecretAccessKey={SECRET}")

    _patch_get_object(monkeypatch, mod, get_object)
    body = _assert_error(mod.handler(_route_event("GET", path), None), status, "SERVING_READ_FAILED")
    assert body["error"] == "Dashboard data is being rebuilt; try again in a minute."
    logs = _log_lines(capsys)
    assert len(logs) == 1
    _assert_log(logs[0], "prefs_api_error", "SERVING_READ_FAILED", "ClientError", route)


@pytest.mark.parametrize(
    "aws_code,status",
    [("ProvisionedThroughputExceededException", 503), ("AccessDeniedException", 502)],
)
def test_dynamodb_get_failures_return_prefs_store_unavailable(api, monkeypatch, capsys, aws_code, status):
    mod, _ = api

    def get_item(**_kwargs):
        raise _client_error(aws_code, "GetItem", f"SessionToken={SECRET}")

    _patch_table(monkeypatch, mod, get_item=get_item)
    body = _assert_error(mod.handler(_event("GET"), None), status, "PREFS_STORE_UNAVAILABLE")
    assert body["error"] == mod._ERROR_MESSAGES["PREFS_STORE_UNAVAILABLE"]
    _assert_log(_log_lines(capsys)[0], "prefs_api_error", "PREFS_STORE_UNAVAILABLE", "ClientError", "GET /prefs")


def test_dynamodb_put_failure_does_not_save(api, monkeypatch, capsys):
    mod, _ = api

    def put_item(**kwargs):
        raise _client_error("InternalServerError", "PutItem", json.dumps(kwargs))

    _patch_table(monkeypatch, mod, put_item=put_item)
    event = _event("PUT", body={"tickers": ["TSLA", "RIVN"], "pinned": [], "version": 0, "access_token": SECRET})
    _assert_error(mod.handler(event, None), 503, "PREFS_STORE_UNAVAILABLE")
    stored = json.loads(mod.handler(_event("GET"), None)["body"])
    assert stored["tickers"] == ["TSLA", "SPCX"]
    _assert_log(_log_lines(capsys)[0], "prefs_api_error", "PREFS_STORE_UNAVAILABLE", "ClientError", "PUT /prefs")


@pytest.mark.parametrize(
    "path,payload,error_class",
    [
        ("/dashboard", b'{"token":"super-secret-token"', "JSONDecodeError"),
        ("/status", b'"super-secret-token"', "ValueError"),
        ("/chart/TSLA", b"[]", "ValueError"),
        ("/stock/TSLA", b"not-json super-secret-token", "JSONDecodeError"),
    ],
)
def test_invalid_serving_json_returns_503(api, monkeypatch, capsys, path, payload, error_class):
    mod, _ = api

    def get_object(**_kwargs):
        return {"Body": _Body(payload)}

    _patch_get_object(monkeypatch, mod, get_object)
    body = _assert_error(mod.handler(_route_event("GET", path), None), 503, "SERVING_DOCUMENT_INVALID")
    assert body["error"] == "Dashboard data is being rebuilt; try again in a minute."
    route = {"/dashboard": "GET /dashboard", "/status": "GET /status", "/chart/TSLA": "GET /chart/{ticker}",
             "/stock/TSLA": "GET /stock/{ticker}"}[path]
    _assert_log(_log_lines(capsys)[0], "prefs_api_error", "SERVING_DOCUMENT_INVALID", error_class, route)


def test_missing_serving_object_stays_not_ready(api, monkeypatch, capsys):
    mod, _ = api

    def get_object(**_kwargs):
        raise _client_error("NoSuchKey", "GetObject", SECRET)

    _patch_get_object(monkeypatch, mod, get_object)
    body = _assert_error(mod.handler(_route_event("GET", "/dashboard"), None), 503, "DASHBOARD_NOT_READY")
    assert "published" in body["error"]
    assert _log_lines(capsys) == []


def test_unexpected_errors_return_internal_json(api, monkeypatch, capsys):
    mod, _ = api

    def table_for(_sub):
        raise RuntimeError(f"SessionToken={SECRET}")

    monkeypatch.setattr(mod, "_table_for", table_for)
    body = _assert_error(mod.handler(_event("GET"), None), 500, "INTERNAL")
    assert body["error"] == mod._ERROR_MESSAGES["INTERNAL"]
    _assert_log(_log_lines(capsys)[0], "prefs_api_error", "INTERNAL", "RuntimeError", "GET /prefs")


def test_unconfigured_lake_bucket_returns_internal_json(api, capsys):
    mod, _ = api
    body = _assert_error(mod.handler(_route_event("GET", "/dashboard"), None), 500, "INTERNAL")
    assert body["error"] == mod._ERROR_MESSAGES["INTERNAL"]
    entry = _log_lines(capsys)[0]
    _assert_log(entry, "prefs_api_error", "INTERNAL", "RuntimeError", "GET /dashboard")
    assert "LAKE_BUCKET" not in json.dumps(entry)


@pytest.mark.parametrize("event", [None, [], "nope", {"requestContext": ["not-a-map"]}])
def test_handler_does_not_raise_on_a_bad_event(api, event):
    mod, _ = api
    response = mod.handler(event, None)
    assert response["statusCode"] == 401
    assert json.loads(response["body"])["error"] == "Not signed in"


def test_invalid_prefs_body_stays_400_and_is_not_logged(api, capsys):
    mod, _ = api
    event = _event("PUT")
    event["body"] = '{"access_token": "super-secret-token"'
    response = mod.handler(event, None)
    assert response["statusCode"] == 400
    assert _log_lines(capsys) == []


def test_put_reports_backfill_queued_when_publish_succeeds(api):
    mod, sent = api
    response = mod.handler(_event("PUT", body={"tickers": ["TSLA", "RIVN"], "pinned": [], "version": 0}), None)
    saved = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert saved["backfill_queued"] is True
    assert saved["tickers"] == ["TSLA", "RIVN"]
    assert sent == ["RIVN"]


def test_put_keeps_saved_prefs_when_eventbridge_publish_fails(api, monkeypatch, capsys):
    mod, _ = api
    monkeypatch.setattr(mod, "publish_ticker_added", mod.publish_ticker_added.original)

    def put_events(**kwargs):
        detail = kwargs["Entries"][0]["Detail"]
        raise _client_error("InternalFailure", "PutEvents", f"{detail} SessionToken={SECRET}")

    monkeypatch.setattr(mod._events, "put_events", put_events)
    event = _event("PUT", body={"tickers": ["TSLA", "RIVN"], "pinned": [], "version": 0, "access_token": SECRET})
    response = mod.handler(event, None)
    assert response["statusCode"] == 200
    saved = json.loads(response["body"])
    assert saved["tickers"] == ["TSLA", "RIVN"]
    assert saved["version"] == 1
    assert saved["backfill_queued"] is False
    stored = json.loads(mod.handler(_event("GET"), None)["body"])
    assert stored["tickers"] == ["TSLA", "RIVN"]
    assert "backfill_queued" not in stored
    logs = _log_lines(capsys)
    assert len(logs) == 1
    _assert_log(logs[0], "ticker_added_publish_failed", "TICKER_ADDED_PUBLISH_FAILED", "ClientError", "PUT /prefs")
    assert "RIVN" not in json.dumps(logs[0])
