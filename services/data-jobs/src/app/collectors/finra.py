"""FINRA consolidated short interest (DS-91), job short_interest.

FINRA publishes short interest twice a month (mid-month and end-of-month settlement
dates, released several business days later). The job polls once each trading
evening and writes only when a settlement date it hasn't stored appears, so it
lands on each publication date without hard-coding FINRA's calendar.

The Query API needs OAuth client credentials even for public data (free "Public"
credential from the FINRA API Console). They live in SSM as finra-client-id and
finra-client-secret, with the usual rotation reminders.
"""

from __future__ import annotations

TOKEN_URL = "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token?grant_type=client_credentials"
DATA_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"

class FinraResponseError(RuntimeError):
    """FINRA answered 2xx with a body that is not JSON (maintenance page, truncated body)."""


def decode_records(status_code: int, content_type: str, body: bytes) -> list[dict]:
    """Records from one FINRA data page.

    FINRA answers a query with no matching rows (e.g. no settlement date newer than the
    watermark, the normal case on most evenings) with ``204 No Content`` and an empty body,
    and ``record-total: 0``. That is an empty page, not an error. Anything else that is not
    JSON raises FinraResponseError with the first 200 characters of the body.
    """
    if status_code == 204 or not body.strip():
        return []
    if "json" not in (content_type or "").lower():
        head = body[:200].decode("utf-8", "replace")
        raise FinraResponseError(f"FINRA returned {status_code} {content_type or 'no content-type'}: {head!r}")
    import json

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise FinraResponseError(
            f"FINRA returned invalid JSON ({status_code}): {body[:200].decode('utf-8', 'replace')!r}"
        ) from exc
    return payload if isinstance(payload, list) else []


# FINRA has used two field-name sets over time; accept either.
FIELDS = {
    "settlement_date": ("settlementDate",),
    "ticker": ("symbolCode", "issueSymbolIdentifier"),
    "short_interest": ("currentShortPositionQuantity", "currentShortShareNumber"),
    "previous_short_interest": ("previousShortPositionQuantity", "previousShortShareNumber"),
    "avg_daily_volume": ("averageDailyVolumeQuantity", "averageShortShareNumber"),
    "days_to_cover": ("daysToCoverQuantity", "daysToCoverNumber"),
}


def query_body(tickers: list[str], after: str | None, *, offset: int = 0, limit: int = 5000) -> dict:
    """One FINRA page. ``after`` is the incremental watermark; omit it for a full history pull."""
    body: dict = {"limit": limit, "domainFilters": [{"fieldName": "symbolCode", "values": tickers}]}
    if offset:
        body["offset"] = offset
    if after:
        body["compareFilters"] = [{"compareType": "GREATER", "fieldName": "settlementDate", "fieldValue": after}]
    return body


def _pick(rec: dict, names: tuple[str, ...]):
    for n in names:
        if n in rec and rec[n] not in (None, ""):
            return rec[n]
    return None


def parse(records: list[dict]) -> list[dict]:
    rows = []
    for rec in records:
        row = {k: _pick(rec, names) for k, names in FIELDS.items()}
        if not row["ticker"] or not row["settlement_date"]:
            continue
        for k in ("short_interest", "previous_short_interest", "avg_daily_volume"):
            row[k] = int(float(row[k])) if row[k] is not None else None
        row["days_to_cover"] = float(row["days_to_cover"]) if row["days_to_cover"] is not None else None
        row["ticker"] = str(row["ticker"]).upper()
        row["source_id"] = "DS-91"
        rows.append(row)
    return rows


def new_settlement_dates(rows: list[dict], known: set[str]) -> set[str]:
    return {r["settlement_date"] for r in rows} - known
