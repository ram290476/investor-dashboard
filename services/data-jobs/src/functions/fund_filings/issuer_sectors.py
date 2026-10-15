"""Sector and industry for 13F issuers (#223), derived from SEC data only.

13F rows carry a CUSIP and an issuer name, no sector. Each CUSIP is mapped to an SEC filer:
first through a ticker the same CUSIP has in N-PORT holdings (then company_tickers.json), else by
an exact normalized issuer-name match against company_tickers.json titles. The filer's submissions
give `ownerOrg` (the SEC office that reviews it, e.g. "06 Technology") as the sector and
`sicDescription` as the industry. Anything unmatched is "Other". Results are cached in the lake.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from manager_holdings import norm_name

ISSUER_SECTORS_KEY = "curated/fund_universe/issuer_sectors.json"
OTHER = "Other"
RESOLVED_MAX_AGE_DAYS = 90
UNRESOLVED_MAX_AGE_DAYS = 7
MAX_SUBMISSIONS_PER_RUN = 300


def sector_from_submissions(subs: dict) -> dict:
    org = str((subs or {}).get("ownerOrg") or "").strip()
    sector = org.split(" ", 1)[1].strip() if org[:2].isdigit() and " " in org else org
    return {
        "sector": sector or OTHER,
        "industry": (subs or {}).get("sicDescription") or None,
        "sic": str((subs or {}).get("sic") or "") or None,
        "issuer_name": (subs or {}).get("name"),
    }


def _fresh(entry: dict | None, now: datetime) -> bool:
    try:
        stamp = datetime.fromisoformat(str((entry or {}).get("resolved_at")))
    except ValueError:
        return False
    days = RESOLVED_MAX_AGE_DAYS if (entry or {}).get("cik") else UNRESOLVED_MAX_AGE_DAYS
    return now - stamp < timedelta(days=days)


def name_index(operating_map: dict) -> dict[str, dict]:
    """normalized company title -> {cik, ticker}; names shared by several CIKs are dropped."""
    index: dict[str, dict] = {}
    clashes: set[str] = set()
    for ticker, rec in operating_map.items():
        key = norm_name(rec.get("title") or rec.get("name"))
        if not key:
            continue
        cik = str(int(rec["cik"]))
        if key in index and index[key]["cik"] != cik:
            clashes.add(key)
        index.setdefault(key, {"cik": cik, "ticker": ticker})
    for key in clashes:
        index.pop(key, None)
    return index


def resolve_issuers(rows: list[dict], *, cusip_tickers: dict[str, str], operating_map: dict, cache: dict,
                    get_json, submissions_url: str, now: datetime) -> dict:
    """Return the updated {cusip: {cik, ticker, sector, industry, sic, method, resolved_at}} cache."""
    out = dict(cache or {})
    stamp = now.isoformat(timespec="seconds")
    names = None
    pending: dict[str, dict] = {}
    for row in rows:
        cusip = row.get("cusip")
        if not cusip or cusip in pending or _fresh(out.get(cusip), now):
            continue
        ticker = cusip_tickers.get(cusip)
        rec = operating_map.get(ticker) if ticker else None
        if rec:
            pending[cusip] = {"cik": str(int(rec["cik"])), "ticker": ticker, "method": "nport_cusip_ticker"}
            continue
        if names is None:
            names = name_index(operating_map)
        hit = names.get(norm_name(row.get("name")))
        if hit:
            pending[cusip] = {**hit, "method": "issuer_name"}
        else:
            out[cusip] = {"cik": None, "ticker": None, "sector": OTHER, "industry": None, "method": "unmatched",
                          "resolved_at": stamp}
    sectors_by_cik: dict[str, dict] = {}
    for cusip, match in pending.items():
        cik = match["cik"]
        if cik not in sectors_by_cik:
            if len(sectors_by_cik) >= MAX_SUBMISSIONS_PER_RUN:
                break  # the rest is picked up next run
            subs = get_json(submissions_url.format(cik=cik.zfill(10)))
            sectors_by_cik[cik] = sector_from_submissions(subs) if isinstance(subs, dict) else None
        info = sectors_by_cik[cik]
        if info is None:
            continue  # SEC did not answer; retry next run
        out[cusip] = {**match, **info, "resolved_at": stamp}
    return out
