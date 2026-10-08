"""Site API for preferences and signed-in dashboard/chart reads.

API Gateway (HTTP API) verifies the Cognito JWT before this runs; the caller's
identity is the token's `sub` claim. Isolation is enforced by IAM, not by this
code: for every request the function assumes PREFS_ACCESS_ROLE_ARN with a session
tag sub=<caller sub>, and that role may only touch DynamoDB items whose partition
key equals ${aws:PrincipalTag/sub} (dynamodb:LeadingKeys). A bug here cannot read
or write another user's item.

Item (table user_prefs, partition key user_sub):
    user_sub      string   Cognito sub
    tickers       list     "My tickers", in display order (max 50)
    pinned        list     pinned tickers, in order (max 6, each also in tickers)
    display       map      time_zone (IANA, e.g. America/Los_Angeles), updown_palette (see PALETTES),
                           chart_period (1D|1W|1M|3M|YTD|1Y|3Y|5Y; unknown values are stored as 1M)
    chart_settings map     per-ticker overlay IDs and under-chart lane IDs, limited to tickers and allowlists
    version       number   optimistic concurrency; PUT must send the version it read
    updated_at    string   ISO-8601 UTC

GET /chart/{ticker} checks that ticker is in this caller's preferences before reading its chart document.

Adding a ticker nobody else follows publishes a TickerAdded event; the backfill
job then loads 5 years of daily history for it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import boto3
from botocore.exceptions import ClientError

TABLE = os.environ.get("PREFS_TABLE", "invdash-user-prefs")
LAKE_BUCKET = os.environ.get("LAKE_BUCKET", "")
ACCESS_ROLE_ARN = os.environ.get("PREFS_ACCESS_ROLE_ARN", "")
EVENT_SOURCE = os.environ.get("EVENT_SOURCE", "invdash.prefs")
MAX_TICKERS = int(os.environ.get("MAX_TICKERS_PER_USER", "50"))
MAX_PINNED = 6
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
PALETTES = {"green-red", "red-green", "blue-orange"}  # up/down colours; blue-orange is colour-blind safe
CHART_PERIODS = {"1D", "1W", "1M", "3M", "YTD", "1Y", "3Y", "5Y"}
CHART_LANES = {"VOL", "SI", "OPT", "PRESS"}
CHART_OVERLAYS = {
    "SPY", "DIA", "QQQ", "IWM", "XLY", "ITA", "SMH",
    "MA10", "MA20", "MA50", "MA100", "MA200",
    "DGS2", "DGS10", "DGS30", "T10Y2Y", "DFII10", "T10YIE", "DFEDTARU", "EFFR", "SOFR",
    "CPI_YOY", "CORE_CPI_YOY", "PCE_YOY", "VIXCLS", "DTWEXBGS", "DCOILWTICO", "USEPUINDXD",
    "FUNDAMENTAL:revenue_gaap", "FUNDAMENTAL:gross_profit_gaap", "FUNDAMENTAL:gross_margin_gaap",
    "FUNDAMENTAL:deliveries", "FUNDAMENTAL:fsd_subscribers",
}
DEFAULTS = {
    "tickers": ["TSLA", "SPCX"],
    "pinned": ["TSLA", "SPCX"],
    "display": {"time_zone": "America/New_York", "updown_palette": "green-red", "chart_period": "1M"},
    "chart_settings": {},
    "version": 0,
}

_sts = boto3.client("sts")
_events = boto3.client("events")
_s3 = boto3.client("s3")
_session_cache: dict[str, tuple[float, object]] = {}
logger = logging.getLogger(__name__)


class ValidationError(ValueError):
    pass


def validate(body: dict) -> dict:
    """Return a clean preferences dict or raise ValidationError with a user-facing message."""
    if not isinstance(body, dict):
        raise ValidationError("Body must be a JSON object")
    tickers = body.get("tickers")
    pinned = body.get("pinned", [])
    display = body.get("display", {})
    if not isinstance(tickers, list) or not tickers:
        raise ValidationError("tickers must be a non-empty list")
    clean = [str(t).strip().upper() for t in tickers]
    bad = [t for t in clean if not SYMBOL_RE.match(t)]
    if bad:
        raise ValidationError(f"Not valid ticker symbols: {', '.join(bad[:5])}")
    if len(clean) != len(set(clean)):
        raise ValidationError("tickers contains duplicates")
    if len(clean) > MAX_TICKERS:
        raise ValidationError(f"At most {MAX_TICKERS} tickers")
    if not isinstance(pinned, list):
        raise ValidationError("pinned must be a list")
    pins = [str(t).strip().upper() for t in pinned]
    if len(pins) > MAX_PINNED:
        raise ValidationError(f"At most {MAX_PINNED} pinned tickers")
    if len(pins) != len(set(pins)) or not set(pins) <= set(clean):
        raise ValidationError("pinned tickers must be unique and also in tickers")
    if not isinstance(display, dict):
        raise ValidationError("display must be an object")
    tz = display.get("time_zone", DEFAULTS["display"]["time_zone"])
    try:
        ZoneInfo(str(tz))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(f"Unknown time zone: {tz}") from exc
    palette = display.get("updown_palette", DEFAULTS["display"]["updown_palette"])
    if palette not in PALETTES:
        raise ValidationError(f"updown_palette must be one of {sorted(PALETTES)}")
    chart_period = display.get("chart_period", DEFAULTS["display"]["chart_period"])
    if chart_period not in CHART_PERIODS:
        chart_period = DEFAULTS["display"]["chart_period"]
    raw_chart_settings = body.get("chart_settings", {})
    if not isinstance(raw_chart_settings, dict):
        raise ValidationError("chart_settings must be an object")
    chart_settings = {}
    for raw_ticker, settings in raw_chart_settings.items():
        ticker = str(raw_ticker).strip().upper()
        if ticker not in clean:
            continue
        if ticker in chart_settings or not isinstance(settings, dict):
            raise ValidationError("chart_settings must map each ticker to an object")
        overlays = settings.get("overlays", [])
        lanes = settings.get("lanes", ["VOL", "PRESS"])
        if not isinstance(overlays, list) or not isinstance(lanes, list):
            raise ValidationError("chart overlays and lanes must be lists")
        if any(not isinstance(item, str) for item in overlays):
            raise ValidationError("chart_settings contains an unsupported overlay")
        if any(not isinstance(item, str) for item in lanes):
            raise ValidationError("chart_settings contains an unsupported lane")
        if len(overlays) > 5 or len(overlays) != len(set(overlays)):
            raise ValidationError("Choose at most 5 unique chart overlays")
        if any(item not in CHART_OVERLAYS for item in overlays):
            raise ValidationError("chart_settings contains an unsupported overlay")
        if len(lanes) > len(CHART_LANES) or len(lanes) != len(set(lanes)):
            raise ValidationError("Choose unique chart lanes")
        if any(item not in CHART_LANES for item in lanes):
            raise ValidationError("chart_settings contains an unsupported lane")
        chart_settings[ticker] = {"overlays": list(overlays), "lanes": list(lanes)}
    version = body.get("version", 0)
    if not isinstance(version, int) or version < 0:
        raise ValidationError("version must be the non-negative integer returned by GET")
    return {
        "tickers": clean,
        "pinned": pins,
        "display": {"time_zone": str(tz), "updown_palette": palette, "chart_period": chart_period},
        "chart_settings": chart_settings,
        "version": version,
    }


def _table_for(sub: str):
    """DynamoDB Table bound to credentials that can only reach this sub's item (cached ~10 minutes)."""
    cached = _session_cache.get(sub)
    if cached and cached[0] > time.time():
        return cached[1]
    creds = _sts.assume_role(
        RoleArn=ACCESS_ROLE_ARN,
        RoleSessionName=f"prefs-{sub[:40]}",
        DurationSeconds=900,
        Tags=[{"Key": "sub", "Value": sub}],
    )["Credentials"]
    table = boto3.resource(
        "dynamodb",
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    ).Table(TABLE)
    if len(_session_cache) > 500:
        _session_cache.clear()
    _session_cache[sub] = (time.time() + 600, table)
    return table


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
        },
        "body": json.dumps(body, default=str),
    }


def _public(item: dict) -> dict:
    chart_settings = item.get("chart_settings", {})
    return {
        "tickers": list(item.get("tickers", [])),
        "pinned": list(item.get("pinned", [])),
        "display": dict(item.get("display", {})),
        "chart_settings": {
            ticker: {"overlays": list(settings.get("overlays", [])), "lanes": list(settings.get("lanes", []))}
            for ticker, settings in chart_settings.items()
            if isinstance(settings, dict)
        },
        "version": int(item.get("version", 0)),
        "updated_at": item.get("updated_at"),
    }


def get_prefs(table, sub: str) -> dict:
    item = table.get_item(Key={"user_sub": sub}, ConsistentRead=True).get("Item")
    return _public(item) if item else {**DEFAULTS, "updated_at": None}


def put_prefs(table, sub: str, prefs: dict) -> tuple[dict, list[str]]:
    """Write with optimistic concurrency. Returns (saved prefs, tickers new to this user)."""
    before = get_prefs(table, sub)
    new_version = prefs["version"] + 1
    item = {
        "user_sub": sub,
        "tickers": prefs["tickers"],
        "pinned": prefs["pinned"],
        "display": prefs["display"],
        "chart_settings": prefs["chart_settings"],
        "version": new_version,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    cond = "attribute_not_exists(user_sub)" if prefs["version"] == 0 else "version = :v"
    kwargs = {"Item": item, "ConditionExpression": cond}
    if prefs["version"]:
        kwargs["ExpressionAttributeValues"] = {":v": prefs["version"]}
    table.put_item(**kwargs)
    added = [t for t in prefs["tickers"] if t not in before["tickers"]]
    return _public(item), added


def publish_ticker_added(tickers: list[str]) -> None:
    if not tickers:
        return
    _events.put_events(
        Entries=[
            {"Source": EVENT_SOURCE, "DetailType": "TickerAdded", "Detail": json.dumps({"ticker": t})}
            for t in tickers[:10]
        ]
    )


def _read_serving_json(key: str) -> dict | None:
    if not LAKE_BUCKET:
        raise RuntimeError("LAKE_BUCKET is not configured")
    try:
        body = _s3.get_object(Bucket=LAKE_BUCKET, Key=key)["Body"].read()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
            return None
        raise
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        logger.exception("serving_document_invalid_json", extra={"key": key})
        raise
    if not isinstance(document, dict):
        raise ValueError(f"Serving document {key} must be a JSON object")
    return document


def handler(event, context):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    sub = claims.get("sub")
    if not sub:
        return _response(401, {"error": "Not signed in"})
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path", "/prefs")
    if method == "GET" and path == "/dashboard":
        dashboard = _read_serving_json("serving/dashboard.json")
        if dashboard is None:
            return _response(
                503,
                {"error": "Dashboard data has not been published yet", "code": "DASHBOARD_NOT_READY"},
            )
        dashboard["status"] = _read_serving_json("serving/status.json")
        return _response(200, dashboard)
    if method == "GET" and path == "/status":
        status = _read_serving_json("serving/status.json")
        if status is None:
            return _response(503, {"error": "Refresh status has not been published yet", "code": "STATUS_NOT_READY"})
        return _response(200, status)
    if method == "GET" and path.startswith("/chart/"):
        match = re.fullmatch(r"/chart/([A-Za-z][A-Za-z0-9.\-]{0,9})", path)
        if not match:
            return _response(404, {"error": "Chart data not found"})
        ticker = match.group(1).upper()
        try:
            prefs = get_prefs(_table_for(sub), sub)
            if ticker not in prefs["tickers"]:
                return _response(403, {"error": "Ticker is not in your watchlist"})
            chart = _read_serving_json(f"serving/chart_data/{ticker}.json")
            if chart is None:
                return _response(503, {"error": "Chart data has not been published yet", "code": "CHART_NOT_READY"})
            return _response(200, chart)
        except ClientError:
            print(json.dumps({"event": "chart_data_error", "ticker": ticker}))
            return _response(500, {"error": "Could not load chart data"})

    table = _table_for(sub)
    try:
        if method == "GET":
            return _response(200, get_prefs(table, sub))
        if method == "PUT":
            try:
                body = json.loads(event.get("body") or "{}")
                prefs = validate(body)
                if "chart_settings" not in body:
                    prefs["chart_settings"] = get_prefs(table, sub)["chart_settings"]
            except (ValidationError, json.JSONDecodeError) as exc:
                return _response(400, {"error": str(exc)})
            try:
                saved, added = put_prefs(table, sub, prefs)
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    return _response(409, {"error": "Preferences changed in another tab; reload and try again"})
                raise
            publish_ticker_added(added)
            return _response(200, saved)
        return _response(405, {"error": f"{method} not allowed"})
    except ClientError:
        print(json.dumps({"event": "prefs_error", "method": method}))
        return _response(500, {"error": "Could not reach preferences store"})
