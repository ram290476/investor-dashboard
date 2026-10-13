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
    chart_settings map     per-ticker overlay IDs and under-chart lane IDs, limited to tickers and allowlists.
                           FUNDAMENTAL:<id>_pending overlays are dropped on read and write.
    version       number   optimistic concurrency; PUT must send the version it read
    updated_at    string   ISO-8601 UTC

GET /chart/{ticker} reads a chart document when the ticker is in this caller's preferences
or is an index proxy (SPY, DIA, QQQ, IWM, XLY, ITA, SMH). Other symbols return 403.
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

# This file is the entire user_prefs Lambda zip (infra/terraform/modules/user_prefs archive_file).
# Do not import src/app. These are the same symbols as universe.INDEX_PROXIES; GET /chart allows
# them even when they are not on the caller's watchlist, because the market overlays read them.
INDEX_PROXIES = ("SPY", "DIA", "QQQ", "IWM", "XLY", "ITA", "SMH")

TABLE = os.environ.get("PREFS_TABLE", "invdash-user-prefs")
LAKE_BUCKET = os.environ.get("LAKE_BUCKET", "")
ACCESS_ROLE_ARN = os.environ.get("PREFS_ACCESS_ROLE_ARN", "")
EVENT_SOURCE = os.environ.get("EVENT_SOURCE", "invdash.prefs")
MAX_TICKERS = int(os.environ.get("MAX_TICKERS_PER_USER", "25"))
MAX_PINNED = 6
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
METRIC_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,64}$")
ADMIN_GROUP = os.environ.get("METRIC_ADMIN_GROUP", "admin")
# The public demo account is never an admin, even if it is added to the group by mistake.
DEMO_ACCOUNT = "ram.vellamsetti@gmail.com"
PALETTES = {"theme", "green-red", "red-green", "blue-orange"}
THEMES = {
    "industrial-dark", "terminal-amber", "charting-navy",
    "clean-light", "colorblind-hc", "midnight-slate",
}
CHART_PERIODS = {"1D", "1W", "1M", "3M", "YTD", "1Y", "3Y", "5Y"}
PANEL_IDS = {
    "rates", "inflation", "market-comparison", "moving-averages", "volatility",
    "dollar-oil", "tariffs", "correlation", "catalyst-calendar", "company",
    "contracts", "about-data", "news", "filings-events",
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
# Company metrics beyond the static ids. Pending-review aliases (`*_pending`) are not selectable.
# \Z (not $) so a trailing newline is not a second, distinct overlay.
FUNDAMENTAL_OVERLAY_RE = re.compile(r"\AFUNDAMENTAL:[A-Za-z][A-Za-z0-9_]{0,64}\Z")
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


def overlay_allowed(item: str) -> bool:
    """Static overlays, plus any FUNDAMENTAL:<id> with a safe metric id."""
    if item.endswith("_pending"):
        return False
    return item in CHART_OVERLAYS or FUNDAMENTAL_OVERLAY_RE.fullmatch(item) is not None


def visible_overlays(overlays: list) -> list:
    """Drop saved pending-review series. Those ids are no longer chart overlays."""
    return [item for item in overlays if isinstance(item, str) and not item.endswith("_pending")]


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
        overlays = visible_overlays(overlays)
        if len(overlays) > 5 or len(overlays) != len(set(overlays)):
            raise ValidationError("Choose at most 5 unique chart overlays")
        if any(not overlay_allowed(item) for item in overlays):
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
            ticker: {
                "overlays": visible_overlays(list(settings.get("overlays", []))),
                "lanes": list(settings.get("lanes", [])),
            }
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


def _metric_visible(metric: dict) -> bool:
    state = metric.get("approval_state") or metric.get("status")
    if state == "rejected":
        return False
    if metric.get("approved") is True and state != "proposed":
        return True
    return state == "proposed"


def _public_stock(document: dict | None, ticker: str) -> dict:
    """Serve approved metrics first, and proposed metrics with their status. Rejected metrics stay out."""
    source = document if isinstance(document, dict) else {}
    metrics = []
    for metric in source.get("metrics") or []:
        if not isinstance(metric, dict) or not _metric_visible(metric):
            continue
        metrics.append(metric)
    metrics.sort(key=lambda metric: (
        0 if metric.get("approved") is True and metric.get("approval_state") != "proposed" else 1
    ))
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
        "company_name": source.get("company_name") or None,
        "company_description": source.get("company_description") or None,
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
    "CATALOG_WRITE_FAILED": "The metric review could not be saved. Try again in a minute.",
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
    if path.startswith("/stock/") and "/metrics/" in path:
        return f"{method} /stock/{{ticker}}/metrics/{{metric_id}}"
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
    if operation == "PutObject":
        code = "CATALOG_WRITE_FAILED"
    elif operation in _STS_OPERATIONS:
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


def _jwt_claims(event: dict) -> dict:
    context = event.get("requestContext")
    context = context if isinstance(context, dict) else {}
    authorizer = context.get("authorizer")
    authorizer = authorizer if isinstance(authorizer, dict) else {}
    jwt = authorizer.get("jwt")
    jwt = jwt if isinstance(jwt, dict) else {}
    claims = jwt.get("claims")
    return claims if isinstance(claims, dict) else {}


def _authorization_header(event: dict) -> str:
    headers = event.get("headers")
    if not isinstance(headers, dict):
        return ""
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == "authorization" and isinstance(value, str):
            return value.strip()
    return ""


def _unsigned_jwt_payload(token: str) -> dict:
    """Decode a JWT payload. The HTTP API authorizer already checked the signature."""
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        return {}
    segment = parts[1]
    padded = segment + ("=" * (-len(segment) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        claims = json.loads(decoded)
    except (ValueError, json.JSONDecodeError, UnicodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _bearer_claims(event: dict) -> dict:
    header = _authorization_header(event)
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token or " " in token:
        return {}
    return _unsigned_jwt_payload(token)


def _claims_for_review(event: dict) -> dict:
    """Authorizer claims, plus groups from the same token when the array claim was dropped.

    Approve is shown from the access token's ``cognito:groups`` array. The HTTP API JWT
    authorizer copies claims into ``authorizer.jwt.claims`` as strings and omits array
    claims, so a live admin can be missing ``cognito:groups`` here after the authorizer
    has already accepted the token. The Authorization bearer is that token. Its groups
    count only when ``sub`` matches the verified sub and ``token_use`` is ``access``
    (what the page sends) or ``id`` (accepted when ``aud`` is the app client).
    """
    claims = dict(_jwt_claims(event))
    if ADMIN_GROUP in _claim_groups(claims):
        return claims
    bearer = _bearer_claims(event)
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or bearer.get("sub") != sub:
        return claims
    if bearer.get("token_use") not in {"access", "id"}:
        return claims
    if "cognito:groups" not in claims and bearer.get("cognito:groups") is not None:
        claims["cognito:groups"] = bearer["cognito:groups"]
    for key in ("email", "username", "cognito:username"):
        if key not in claims and isinstance(bearer.get(key), str) and bearer[key].strip():
            claims[key] = bearer[key].strip()
    return claims


def _claim_groups(claims: dict) -> set[str]:
    raw = claims.get("cognito:groups")
    if isinstance(raw, list):
        parts = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        parts = re.split(r"[\s,]+", text)
    else:
        parts = []
    return {str(part).strip().strip("\"'") for part in parts if str(part).strip()}


def _identity_values(claims: dict) -> list[str]:
    values = []
    for key in ("email", "username", "cognito:username"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values


def _is_demo_account(claims: dict) -> bool:
    return any(value.lower() == DEMO_ACCOUNT for value in _identity_values(claims))


def _is_metric_admin(claims: dict) -> bool:
    """Cognito group membership. The published demo account is refused."""
    if _is_demo_account(claims):
        return False
    return ADMIN_GROUP in _claim_groups(claims)


def _reviewer(claims: dict) -> str:
    for value in _identity_values(claims):
        if value.lower() != DEMO_ACCOUNT:
            return value[:256]
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else "admin"


def _catalog_key(ticker: str) -> str:
    return f"catalog/{ticker}.json"


def _read_json_object(key: str) -> dict | None:
    if not LAKE_BUCKET:
        raise RuntimeError("LAKE_BUCKET is not configured")
    try:
        obj = _s3.get_object(Bucket=LAKE_BUCKET, Key=key)
    except ClientError as exc:
        if _aws_error_code(exc) in {"NoSuchKey", "404"}:
            return None
        raise
    document = json.loads(obj["Body"].read())
    if not isinstance(document, dict):
        raise ServingDocumentError("catalog document must be a JSON object")
    return document


def _write_json_object(key: str, document: dict, *, gzip_sibling: bool = False) -> None:
    """Write the JSON object GET /stock reads. The serving file also gets a matching .gz sibling.

    Cache-Control is no-store on both objects. The API response already sends no-store, and
    CloudFront does not proxy this API. A later collect rewrites the JSON and the sibling together.
    """
    if not LAKE_BUCKET:
        raise RuntimeError("LAKE_BUCKET is not configured")
    body = json.dumps(document, separators=(",", ":"), default=str).encode()
    _s3.put_object(
        Bucket=LAKE_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/json",
        CacheControl="no-store",
    )
    if not gzip_sibling:
        return
    _s3.put_object(
        Bucket=LAKE_BUCKET,
        Key=f"{key}.gz",
        Body=gzip.compress(body, compresslevel=GZIP_LEVEL, mtime=0),
        ContentType="application/gzip",
        CacheControl="no-store",
    )


def _set_catalog_metric(
    catalog: dict, ticker: str, metric_id: str, status: str, reviewer: str, approved_at: str,
) -> dict:
    updated = dict(catalog)
    updated["ticker"] = str(updated.get("ticker") or ticker).upper()
    metrics = [item for item in updated.get("metrics") or [] if isinstance(item, dict)]
    entry = next((item for item in metrics if item.get("metric_id") == metric_id), None)
    if entry is None:
        entry = {"metric_id": metric_id}
        metrics.append(entry)
    entry["status"] = status
    if status == "approved":
        entry["approved_by"] = reviewer
        entry["approved_at"] = approved_at
    else:
        entry["approved_by"] = None
        entry["approved_at"] = None
    updated["metrics"] = metrics
    return updated


def _freshness_label(metrics: list[dict]) -> str:
    approved = 0
    proposed = 0
    for metric in metrics:
        state = metric.get("approval_state") or metric.get("status")
        if metric.get("approved") is True and state != "proposed":
            approved += 1
        elif state == "proposed":
            proposed += 1
    if approved and proposed:
        return "IR data approved · pending review included"
    if approved:
        return "IR data approved"
    if proposed:
        return "Pending review"
    return "No approved company metrics"


def _patch_serving_metric(document: dict, metric_id: str, status: str, reviewer: str, approved_at: str) -> dict:
    """Rewrite one metric in the serving file so the page updates before the next collect."""
    updated = json.loads(json.dumps(document))
    metrics = [item for item in updated.get("metrics") or [] if isinstance(item, dict)]
    for metric in metrics:
        if metric.get("metric_id") != metric_id:
            continue
        approved = status == "approved"
        metric["approved"] = approved
        metric["approval_state"] = status
        metric["status"] = status
        if approved:
            metric["approved_by"] = reviewer
            metric["approved_at"] = approved_at
        else:
            metric["approved_by"] = None
            metric["approved_at"] = None
        provenance = metric.get("provenance")
        if isinstance(provenance, dict):
            provenance["approval_state"] = status
    metrics.sort(key=lambda metric: (
        0 if metric.get("approved") is True and metric.get("approval_state") != "proposed" else 1
    ))
    updated["metrics"] = metrics
    if updated.get("run_status") in {None, "ok"}:
        updated["freshness_label"] = _freshness_label(metrics)
    return updated


def _review_metric(event, sub: str, ticker: str, metric_id: str, respond) -> dict:
    claims = _claims_for_review(event)
    if not _is_metric_admin(claims):
        return respond(403, {"error": "Only an admin can change metric review status"})
    if ticker not in get_prefs(_table_for(sub), sub)["tickers"]:
        return respond(403, {"error": "Ticker is not in your watchlist"})
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return respond(400, {"error": "Body must be a JSON object"})
    if not isinstance(body, dict):
        return respond(400, {"error": "Body must be a JSON object"})
    status = body.get("status")
    if status not in {"approved", "proposed"}:
        return respond(400, {"error": "status must be approved or proposed"})
    document = _read_serving_json(f"serving/stock/{ticker}.json")
    metrics = document.get("metrics") if isinstance(document, dict) else None
    if not any(isinstance(metric, dict) and metric.get("metric_id") == metric_id for metric in metrics or []):
        return respond(404, {"error": "Metric is not on this company page"})
    reviewer = _reviewer(claims)
    approved_at = datetime.now(UTC).isoformat(timespec="seconds")
    catalog = _read_json_object(_catalog_key(ticker)) or {"ticker": ticker, "catalog_version": 1, "metrics": []}
    catalog = _set_catalog_metric(catalog, ticker, metric_id, status, reviewer, approved_at)
    patched = _patch_serving_metric(document, metric_id, status, reviewer, approved_at)
    _write_json_object(_catalog_key(ticker), catalog)
    _write_json_object(f"serving/stock/{ticker}.json", patched, gzip_sibling=True)
    return respond(200, _public_stock(patched, ticker))


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
        if ticker not in prefs["tickers"] and ticker not in INDEX_PROXIES:
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
    review = re.fullmatch(r"/stock/([A-Za-z][A-Za-z0-9.\-]{0,9})/metrics/([A-Za-z][A-Za-z0-9_]{0,64})", path)
    if review and method == "PUT":
        ticker = review.group(1).upper()
        metric_id = review.group(2)
        if not METRIC_ID_RE.fullmatch(metric_id):
            return respond(400, {"error": "Metric id is not valid"})
        return _review_metric(event, sub, ticker, metric_id, respond)
    if review:
        return respond(405, {"error": f"{method} not allowed"})
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
