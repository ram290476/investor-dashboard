"""Site API for preferences and signed-in dashboard/chart reads.

API Gateway (HTTP API) verifies the Cognito JWT before this runs; the caller's
identity is the token's `sub` claim. Isolation is enforced by IAM, not by this
code: for every request the function assumes PREFS_ACCESS_ROLE_ARN with a session
tag sub=<caller sub>, and that role may only touch DynamoDB items whose partition
key equals ${aws:PrincipalTag/sub} (dynamodb:LeadingKeys). A bug here cannot read
or write another user's item.

Item (table user_prefs, partition key user_sub):
    user_sub      string   Cognito sub
    tickers       list     "My tickers", in display order (max 25; legacy lists may shrink, not grow)
    pinned        list     pinned tickers, in order (max 6, each also in tickers)
    display       map      time_zone (IANA, e.g. America/Los_Angeles), updown_palette (see PALETTES),
                           chart_period (1D|1W|1M|3M|YTD|1Y|3Y|5Y; unknown values are stored as 1M),
                           theme (see THEMES; missing legacy values default to industrial-dark),
                           panels (optional map of bottom-drawer id -> "open"|"closed"; unknown ids
                           are ignored, other values are rejected, omitted key keeps the stored map)
    chart_settings map     per-ticker overlay IDs and under-chart lane IDs, limited to tickers and allowlists
    version       number   optimistic concurrency; PUT must send the version it read
    updated_at    string   ISO-8601 UTC

GET /chart/{ticker} checks that ticker is in this caller's preferences before reading its chart document.
GET /dashboard, /chart/{ticker} and /status are gzip-encoded when the client accepts gzip and the
JSON body is over 1 KB. The Lambda response stays under the 6 MiB synchronous payload limit.

Adding a ticker nobody else follows publishes a TickerAdded event; the backfill
job then loads 5 years of daily history for it.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import os
import re
import time
import zlib
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import boto3
from botocore.exceptions import ClientError

TABLE = os.environ.get("PREFS_TABLE", "invdash-user-prefs")
LAKE_BUCKET = os.environ.get("LAKE_BUCKET", "")
ACCESS_ROLE_ARN = os.environ.get("PREFS_ACCESS_ROLE_ARN", "")
EVENT_SOURCE = os.environ.get("EVENT_SOURCE", "invdash.prefs")
MAX_TICKERS = int(os.environ.get("MAX_TICKERS_PER_USER", "25"))
MAX_PINNED = 6
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
PALETTES = {"theme", "green-red", "red-green", "blue-orange"}
THEMES = {
    "industrial-dark", "terminal-amber", "charting-navy",
    "clean-light", "colorblind-hc", "midnight-slate",
}
CHART_PERIODS = {"1D", "1W", "1M", "3M", "YTD", "1Y", "3Y", "5Y"}
PANEL_IDS = {
    "rates", "inflation", "market-comparison", "moving-averages", "volatility",
    "dollar-oil", "tariffs", "correlation", "catalyst-calendar", "company",
    "contracts", "about-data", "news",
}
PANEL_STATES = {"open", "closed", "dismissed"}
PANEL_MODES = {"follow", "all"}
CHART_LANES = {"VOL", "SI", "OPT", "PRESS"}
CHART_OVERLAYS = {
    "SPY", "DIA", "QQQ", "IWM", "XLY", "ITA", "SMH",
    "MA10", "MA20", "MA50", "MA100", "MA200",
    "DGS2", "DGS10", "DGS30", "T10Y2Y", "DFII10", "T10YIE", "DFEDTARU", "EFFR", "SOFR",
    "CPI_YOY", "CORE_CPI_YOY", "PCE_YOY", "VIXCLS", "DTWEXBGS", "DCOILWTICO", "USEPUINDXD",
    "FUNDAMENTAL:revenue_gaap", "FUNDAMENTAL:gross_profit_gaap", "FUNDAMENTAL:gross_margin_gaap",
    "FUNDAMENTAL:deliveries", "FUNDAMENTAL:fsd_subscribers",
    "FUNDAMENTAL:shares_outstanding", "FUNDAMENTAL:public_float_usd",
    "NEWS:SENTIMENT",
}
DEFAULTS = {
    "tickers": ["TSLA", "SPCX"],
    "pinned": ["TSLA", "SPCX"],
    "display": {
        "time_zone": "America/New_York", "updown_palette": "green-red",
        "chart_period": "1M", "theme": "industrial-dark",
    },
    "chart_settings": {},
    "version": 0,
}

_sts = boto3.client("sts")
_events = boto3.client("events")
_s3 = boto3.client("s3")
_session_cache: dict[str, tuple[float, object]] = {}

# Warm-container cache of gzip bytes, keyed by S3 ETag (dashboard also includes the status ETag).
_GZIP_CACHE: dict[str, bytes] = {}
_GZIP_CACHE_MAX = 32
# Keep level and mtime in sync with lake.write_json_and_gzip so a sibling .gz can be returned as stored.
GZIP_LEVEL = 6
GZIP_MIN_BYTES = 1024
# Synchronous Lambda response limit. The measured size is the proxy JSON, including base64 expansion.
LAMBDA_RESPONSE_LIMIT = 6 * 1024 * 1024
SECURITY_HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}
logger = logging.getLogger(__name__)

class ValidationError(ValueError):
    pass


class ServingDocumentError(Exception):
    """A serving object was not a JSON object. The message never includes the document."""


def normalize_panels(raw) -> dict:
    """Known drawer ids only. Unknown ids are ignored. Any other value is rejected."""
    if not isinstance(raw, dict):
        raise ValidationError("display.panels must be an object")
    panels = {}
    for key, value in raw.items():
        panel_id = str(key)
        if panel_id not in PANEL_IDS:
            continue
        if value not in PANEL_STATES:
            raise ValidationError("panel state must be open, closed, or dismissed")
        panels[panel_id] = value
    return panels


def normalize_panel_mode(raw) -> str:
    if raw not in PANEL_MODES:
        raise ValidationError("panel_mode must be follow or all")
    return raw


def validate(body: dict, stored_tickers: list[str] | None = None) -> dict:
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
    stored = stored_tickers or []
    if len(clean) > MAX_TICKERS and not (
        len(clean) <= len(stored) and set(clean) <= set(stored)
    ):
        if len(stored) > MAX_TICKERS:
            raise ValidationError(
                f"Your watchlist has {len(stored)} tickers; the limit is {MAX_TICKERS}. "
                f"Remove {len(stored) - MAX_TICKERS + 1} to add another."
            )
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
    panels = normalize_panels(display["panels"]) if "panels" in display else None
    panel_mode = normalize_panel_mode(display["panel_mode"]) if "panel_mode" in display else None
    theme = display.get("theme", DEFAULTS["display"]["theme"])
    if not isinstance(theme, str) or theme not in THEMES:
        raise ValidationError(f"theme must be one of {sorted(THEMES)}")
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
    clean_display = {"time_zone": str(tz), "updown_palette": palette, "chart_period": chart_period, "theme": theme}
    if panels is not None:
        clean_display["panels"] = panels
    if panel_mode is not None:
        clean_display["panel_mode"] = panel_mode
    return {
        "tickers": clean,
        "pinned": pins,
        "display": clean_display,
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


def _public_display(item: dict) -> dict:
    display = {"theme": DEFAULTS["display"]["theme"], **dict(item.get("display") or {})}
    raw_panels = display.get("panels")
    if isinstance(raw_panels, dict):
        display["panels"] = {
            str(key): value
            for key, value in raw_panels.items()
            if str(key) in PANEL_IDS and value in PANEL_STATES
        }
    else:
        display.pop("panels", None)
    mode = display.get("panel_mode")
    if mode in PANEL_MODES:
        display["panel_mode"] = mode
    else:
        display.pop("panel_mode", None)
    return display


def _public(item: dict) -> dict:
    chart_settings = item.get("chart_settings", {})
    return {
        "tickers": list(item.get("tickers", [])),
        "pinned": list(item.get("pinned", [])),
        "display": _public_display(item),
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
    prefs = validate(prefs, before["tickers"])
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


def _public_stock(document: dict | None, ticker: str) -> dict:
    """Serve approved company metrics only. A missing object is an empty page, not a fabricated one."""
    source = document if isinstance(document, dict) else {}
    metrics = []
    for metric in source.get("metrics") or []:
        if not isinstance(metric, dict) or metric.get("approved") is not True:
            continue
        if metric.get("approval_state") in {"proposed", "rejected"} or metric.get("status") in {"proposed", "rejected"}:
            continue
        metrics.append(metric)
    return {
        "ticker": ticker.upper(),
        "generated_at": source.get("generated_at"),
        "run_status": source.get("run_status") or "ok",
        "stale": bool(source.get("stale")),
        "freshness_label": source.get("freshness_label") or "No company-specific metrics discovered",
        "discovered": bool(metrics),
        "metrics": metrics,
        "unavailable": source.get("unavailable") or [],
        "mismatches": source.get("mismatches") or [],
    }


def _accepts_gzip(event: dict | None) -> bool:
    """True when Accept-Encoding lists gzip with q greater than zero. HTTP API lowercases names."""
    if not event:
        return False
    headers = event.get("headers") or {}
    value = ""
    for key, item in headers.items():
        if isinstance(key, str) and key.lower() == "accept-encoding" and isinstance(item, str):
            value = item
            break
    for part in value.split(","):
        token, _, params = part.strip().lower().partition(";")
        if token != "gzip":
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _, raw_q = param.strip().partition("=")
            if name != "q":
                continue
            try:
                quality = float(raw_q)
            except ValueError:
                quality = 0.0
        return quality > 0
    return False


def _proxy_response_bytes(body: str, headers: dict, base64_encoded: bool) -> int:
    """Bytes of the Lambda proxy JSON. That is the body the 6 MiB synchronous limit measures."""
    envelope = {"statusCode": 200, "headers": headers, "body": body, "isBase64Encoded": base64_encoded}
    return len(json.dumps(envelope).encode())


def _remember_gzip(cache_key: str, payload: bytes) -> None:
    _GZIP_CACHE.pop(cache_key, None)
    while len(_GZIP_CACHE) >= _GZIP_CACHE_MAX:
        _GZIP_CACHE.pop(next(iter(_GZIP_CACHE)))
    _GZIP_CACHE[cache_key] = payload


def _gzip_matches(payload: bytes, raw: bytes) -> bool:
    try:
        return gzip.decompress(payload) == raw
    except (EOFError, OSError, zlib.error):
        return False


def _gzip_payload(cache_key: str | None, raw: bytes, gzip_key: str | None) -> bytes:
    if cache_key:
        cached = _GZIP_CACHE.get(cache_key)
        if cached is not None:
            return cached
    sibling = _read_gzip_sibling(gzip_key) if gzip_key else None
    if sibling is not None and not _gzip_matches(sibling, raw):
        logger.warning("serving_gzip_mismatch", extra={"key": gzip_key})
        sibling = None
    payload = sibling if sibling is not None else gzip.compress(raw, compresslevel=GZIP_LEVEL, mtime=0)
    if cache_key:
        _remember_gzip(cache_key, payload)
    return payload


def _too_large(raw_bytes: int) -> dict:
    logger.error("response_too_large", extra={"raw_bytes": raw_bytes, "limit_bytes": LAMBDA_RESPONSE_LIMIT})
    return {
        "statusCode": 503,
        "headers": dict(SECURITY_HEADERS),
        "body": json.dumps({"error": "The response is too large to deliver.", "code": "RESPONSE_TOO_LARGE"}),
    }


def _response(
    status: int,
    body: dict,
    event: dict | None = None,
    *,
    raw: bytes | None = None,
    gzip_key: str | None = None,
    cache_key: str | None = None,
) -> dict:
    encoded = raw if raw is not None else json.dumps(body, default=str).encode()
    headers = dict(SECURITY_HEADERS)
    if len(encoded) > GZIP_MIN_BYTES:
        headers["Vary"] = "Accept-Encoding"
    if _accepts_gzip(event) and len(encoded) > GZIP_MIN_BYTES:
        payload = _gzip_payload(cache_key, encoded, gzip_key)
        body_text = base64.b64encode(payload).decode("ascii")
        headers["Content-Encoding"] = "gzip"
        if _proxy_response_bytes(body_text, headers, True) > LAMBDA_RESPONSE_LIMIT:
            return _too_large(len(encoded))
        return {"statusCode": status, "headers": headers, "isBase64Encoded": True, "body": body_text}
    body_text = encoded.decode()
    if _proxy_response_bytes(body_text, headers, False) > LAMBDA_RESPONSE_LIMIT:
        return _too_large(len(encoded))
    return {"statusCode": status, "headers": headers, "body": body_text}


def _etag_token(etag: str, raw: bytes | None = None) -> str:
    token = etag.strip().strip('"')
    if token:
        return token
    if raw is None:
        return "none"
    return hashlib.sha256(raw).hexdigest()


_ERROR_MESSAGES = {
    "STS_UNAVAILABLE": "The sign-in check is temporarily unavailable. Try again in a minute.",
    "SERVING_READ_FAILED": "Dashboard data is being rebuilt; try again in a minute.",
    "PREFS_STORE_UNAVAILABLE": "Preferences could not be reached. Try again in a minute.",
    "SERVING_DOCUMENT_INVALID": "Dashboard data is being rebuilt; try again in a minute.",
    "INTERNAL": "Something went wrong. Try again in a minute.",
}
_TRANSIENT_AWS_CODES = frozenset({
    "Throttling",
    "ThrottlingException",
    "TooManyRequestsException",
    "SlowDown",
    "ServiceUnavailable",
    "RequestTimeout",
    "RequestTimeoutException",
    "ProvisionedThroughputExceededException",
    "RequestLimitExceeded",
    "InternalServerError",
    "InternalError",
    "InternalFailure",
})
_STS_OPERATIONS = frozenset({"AssumeRole", "AssumeRoleWithWebIdentity"})
_SERVING_OPERATIONS = frozenset({"GetObject", "HeadObject"})
_PREFS_OPERATIONS = frozenset({"GetItem", "PutItem", "UpdateItem", "DeleteItem", "Query", "BatchGetItem"})
_HTTP_METHODS = frozenset({"GET", "PUT", "POST", "DELETE", "PATCH", "HEAD", "OPTIONS"})
_KNOWN_PATHS = frozenset({"/prefs", "/dashboard", "/status"})


def _error_class(exc: BaseException) -> str:
    cause = exc.__cause__
    return type(cause).__name__ if cause is not None else type(exc).__name__


def _log_event(event_name: str, route: str, method: str, exc: BaseException, code: str) -> None:
    """Structured CloudWatch line. Fixed keys only: no body, token, or exception text."""
    print(json.dumps({
        "event": event_name,
        "route": route,
        "method": method,
        "error_class": _error_class(exc),
        "code": code,
    }))


def _safe_method(method: object) -> str:
    if not isinstance(method, str):
        return "UNKNOWN"
    method = method.upper()
    return method if method in _HTTP_METHODS else "UNKNOWN"


def _route_label(method: str, path: object) -> str:
    if not isinstance(path, str):
        return f"{method} /unknown"
    if path.startswith("/chart/"):
        return f"{method} /chart/{{ticker}}"
    if path.startswith("/stock/"):
        return f"{method} /stock/{{ticker}}"
    if path in _KNOWN_PATHS:
        return f"{method} {path}"
    return f"{method} /unknown"


def _aws_error_code(exc: ClientError) -> str:
    error = exc.response.get("Error", {}) if isinstance(exc.response, dict) else {}
    return str(error.get("Code", "")) if isinstance(error, dict) else ""


def _client_failure(exc: ClientError) -> tuple[int, str]:
    operation = getattr(exc, "operation_name", "") or ""
    aws_code = _aws_error_code(exc)
    if operation in _STS_OPERATIONS:
        code = "STS_UNAVAILABLE"
    elif operation in _SERVING_OPERATIONS or aws_code.startswith("KMS"):
        code = "SERVING_READ_FAILED"
    elif operation in _PREFS_OPERATIONS:
        code = "PREFS_STORE_UNAVAILABLE"
    else:
        return 500, "INTERNAL"
    status = 503 if aws_code.split(".")[-1] in _TRANSIENT_AWS_CODES else 502
    return status, code


def _failure_response(status: int, code: str) -> dict:
    return _response(status, {"error": _ERROR_MESSAGES[code], "code": code})


def _queue_backfill(added: list[str], route: str, method: str) -> bool:
    """Publish TickerAdded after a successful save. A publish failure does not undo the save."""
    if not added:
        return True
    try:
        publish_ticker_added(added)
    except ClientError as exc:
        _log_event("ticker_added_publish_failed", route, method, exc, "TICKER_ADDED_PUBLISH_FAILED")
        return False
    return True


def _read_serving_document(key: str) -> tuple[bytes, str, dict] | None:
    """Return the stored bytes, S3 ETag and parsed object. Bytes stay intact so a .gz sibling can match."""
    if not LAKE_BUCKET:
        raise RuntimeError("LAKE_BUCKET is not configured")
    try:
        obj = _s3.get_object(Bucket=LAKE_BUCKET, Key=key)
    except ClientError as exc:
        if _aws_error_code(exc) in {"NoSuchKey", "404"}:
            return None
        raise
    body = obj["Body"].read()
    try:
        document = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ServingDocumentError("serving document is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ServingDocumentError("serving document must be a JSON object") from ValueError(
            "serving document must be a JSON object"
        )
    return body, str(obj.get("ETag") or ""), document


def _read_gzip_sibling(key: str) -> bytes | None:
    """Optional precompressed object. A miss falls back to compressing in this process."""
    if not LAKE_BUCKET:
        return None
    try:
        return _s3.get_object(Bucket=LAKE_BUCKET, Key=f"{key}.gz")["Body"].read()
    except ClientError as exc:
        code = _aws_error_code(exc)
        if code not in {"NoSuchKey", "404"}:
            logger.warning("serving_gzip_unavailable", extra={"key": key, "code": code})
        return None


def _read_serving_json(key: str) -> dict | None:
    loaded = _read_serving_document(key)
    return None if loaded is None else loaded[2]


def _request_parts(event: dict) -> tuple[str | None, str, str, str]:
    context = event.get("requestContext")
    context = context if isinstance(context, dict) else {}
    authorizer = context.get("authorizer")
    authorizer = authorizer if isinstance(authorizer, dict) else {}
    jwt = authorizer.get("jwt")
    jwt = jwt if isinstance(jwt, dict) else {}
    claims = jwt.get("claims")
    sub = claims.get("sub") if isinstance(claims, dict) else None
    if not isinstance(sub, str) or not sub:
        return None, "GET", "/prefs", "UNKNOWN"
    http = context.get("http")
    http = http if isinstance(http, dict) else {}
    raw_method = http.get("method", "GET")
    method = raw_method if isinstance(raw_method, str) else "GET"
    raw_path = event.get("rawPath") or http.get("path") or "/prefs"
    path = raw_path if isinstance(raw_path, str) else "/prefs"
    return sub, method, path, _route_label(_safe_method(method), path)


def _dispatch(event, sub: str, method: str, path: str, route: str) -> dict:
    def respond(status: int, body: dict, **kwargs) -> dict:
        return _response(status, body, event, **kwargs)

    if method == "GET" and path == "/dashboard":
        loaded = _read_serving_document("serving/dashboard.json")
        if loaded is None:
            return respond(
                503,
                {"error": "Dashboard data has not been published yet", "code": "DASHBOARD_NOT_READY"},
            )
        raw_bytes, etag, dashboard = loaded
        status_loaded = _read_serving_document("serving/status.json")
        if status_loaded is None:
            dashboard["status"] = None
            status_token = "none"
        else:
            status_raw, status_etag, status_doc = status_loaded
            dashboard["status"] = status_doc
            status_token = _etag_token(status_etag, status_raw)
        served = json.dumps(dashboard, separators=(",", ":"), default=str).encode()
        gzip_key = "serving/dashboard.json" if served == raw_bytes else None
        return respond(
            200,
            dashboard,
            raw=served,
            gzip_key=gzip_key,
            cache_key=f"dashboard:{_etag_token(etag, raw_bytes)}:{status_token}",
        )
    if method == "GET" and path == "/status":
        loaded = _read_serving_document("serving/status.json")
        if loaded is None:
            return respond(503, {"error": "Refresh status has not been published yet", "code": "STATUS_NOT_READY"})
        raw_bytes, etag, status_doc = loaded
        return respond(200, status_doc, raw=raw_bytes, cache_key=f"status:{_etag_token(etag, raw_bytes)}")
    if method == "GET" and path.startswith("/chart/"):
        match = re.fullmatch(r"/chart/([A-Za-z][A-Za-z0-9.\-]{0,9})", path)
        if not match:
            return respond(404, {"error": "Chart data not found"})
        ticker = match.group(1).upper()
        prefs = get_prefs(_table_for(sub), sub)
        if ticker not in prefs["tickers"]:
            return respond(403, {"error": "Ticker is not in your watchlist"})
        chart_key = f"serving/chart_data/{ticker}.json"
        loaded = _read_serving_document(chart_key)
        if loaded is None:
            return respond(503, {"error": "Chart data has not been published yet", "code": "CHART_NOT_READY"})
        raw_bytes, etag, chart = loaded
        return respond(
            200,
            chart,
            raw=raw_bytes,
            gzip_key=chart_key,
            cache_key=f"chart:{ticker}:{_etag_token(etag, raw_bytes)}",
        )
    if method == "GET" and path.startswith("/stock/"):
        match = re.fullmatch(r"/stock/([A-Za-z][A-Za-z0-9.\-]{0,9})", path)
        if not match:
            return respond(404, {"error": "Company page not found"})
        ticker = match.group(1).upper()
        prefs = get_prefs(_table_for(sub), sub)
        if ticker not in prefs["tickers"]:
            return respond(403, {"error": "Ticker is not in your watchlist"})
        document = _read_serving_json(f"serving/stock/{ticker}.json")
        return respond(200, _public_stock(document, ticker))

    table = _table_for(sub)
    if method == "GET":
        return respond(200, get_prefs(table, sub))
    if method == "PUT":
        try:
            body = json.loads(event.get("body") or "{}")
            stored = get_prefs(table, sub)
            prefs = validate(body, stored["tickers"])
            incoming = body.get("display") if isinstance(body.get("display"), dict) else {}
            if "panels" not in incoming:
                prefs["display"]["panels"] = dict(stored["display"].get("panels") or {})
            if "panel_mode" not in incoming and stored["display"].get("panel_mode") in PANEL_MODES:
                prefs["display"]["panel_mode"] = stored["display"]["panel_mode"]
            if "chart_settings" not in body:
                prefs["chart_settings"] = stored["chart_settings"]
        except (ValidationError, json.JSONDecodeError) as exc:
            return respond(400, {"error": str(exc)})
        try:
            saved, added = put_prefs(table, sub, prefs)
        except ValidationError as exc:
            return respond(400, {"error": str(exc)})
        except ClientError as exc:
            if _aws_error_code(exc) == "ConditionalCheckFailedException":
                return respond(409, {"error": "Preferences changed in another tab; reload and try again"})
            raise
        saved["backfill_queued"] = _queue_backfill(added, route, _safe_method(method))
        return respond(200, saved)
    return respond(405, {"error": f"{method} not allowed"})


def handler(event, context):
    method = "GET"
    route = "UNKNOWN"
    try:
        if not isinstance(event, dict):
            return _response(401, {"error": "Not signed in"})
        sub, method, path, route = _request_parts(event)
        if not sub:
            return _response(401, {"error": "Not signed in"})
        return _dispatch(event, sub, method, path, route)
    except ClientError as exc:
        status, code = _client_failure(exc)
        _log_event("prefs_api_error", route, _safe_method(method), exc, code)
        return _failure_response(status, code)
    except ServingDocumentError as exc:
        _log_event("prefs_api_error", route, _safe_method(method), exc, "SERVING_DOCUMENT_INVALID")
        return _failure_response(503, "SERVING_DOCUMENT_INVALID")
    except Exception as exc:  # noqa: BLE001 - API Gateway must receive JSON, never an uncaught error
        _log_event("prefs_api_error", route, _safe_method(method), exc, "INTERNAL")
        return _failure_response(500, "INTERNAL")
