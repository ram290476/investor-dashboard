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

# FINRA has used two field-name sets over time; accept either.
FIELDS = {
    "settlement_date": ("settlementDate",),
    "ticker": ("symbolCode", "issueSymbolIdentifier"),
    "short_interest": ("currentShortPositionQuantity", "currentShortShareNumber"),
    "previous_short_interest": ("previousShortPositionQuantity", "previousShortShareNumber"),
    "avg_daily_volume": ("averageDailyVolumeQuantity", "averageShortShareNumber"),
    "days_to_cover": ("daysToCoverQuantity", "daysToCoverNumber"),
}


def query_body(tickers: list[str], after: str | None) -> dict:
    body: dict = {"limit": 5000, "domainFilters": [{"fieldName": "symbolCode", "values": tickers}]}
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
