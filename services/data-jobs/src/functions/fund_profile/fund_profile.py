"""Yahoo Finance fund summaries (#223), best-effort weekly enrichment.

Funds come from the resolved fund map that fund_filings caches (curated/fund_universe/fund_map.json),
which is itself built only from the watchlist union. quoteSummary is unofficial and needs a cookie and
crumb; it throttles cloud IPs. So this job never fails on Yahoo: each fund is tried at most once per
run, a profile younger than PROFILE_MAX_AGE_DAYS is not refetched, and a failed fetch keeps the last
good profile and records why.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

JOB_ID = "FUNDPROFILE"
COOKIE_URL = "https://fc.yahoo.com"
CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules={modules}&crumb={crumb}"
MODULES = ("fundProfile", "topHoldings", "summaryDetail", "defaultKeyStatistics", "assetProfile", "fundPerformance")
FUND_MAP_KEY = "curated/fund_universe/fund_map.json"
LEASE_KEY = "curated/fund_profile/_lease.json"
PROFILE_MAX_AGE_DAYS = 7
BROWSER_UA = "Mozilla/5.0 (compatible; investor-dashboard fund profile)"


def raw_profile_key(ticker: str, day: str) -> str:
    return f"raw/fund_profile/yahoo/date={day}/{ticker.upper()}.json"


def curated_profile_key(ticker: str) -> str:
    return f"curated/fund_profile/{ticker.upper()}.json"


def fund_tickers(fund_map: dict | None) -> list[str]:
    funds = (fund_map or {}).get("funds") or {}
    return sorted(t for t, e in funds.items() if e.get("kind") == "fund" and e.get("status") == "resolved")


def _raw(value):
    """Yahoo wraps numbers as {"raw": 0.0075, "fmt": "0.75%"}."""
    if isinstance(value, dict):
        return value.get("raw", value.get("fmt"))
    return value


def parse_profile(payload: dict) -> dict | None:
    result = ((payload or {}).get("quoteSummary") or {}).get("result") or []
    if not result:
        return None
    mods = result[0]
    fp = mods.get("fundProfile") or {}
    fees = fp.get("feesExpensesInvestment") or {}
    sd = mods.get("summaryDetail") or {}
    ks = mods.get("defaultKeyStatistics") or {}
    ap = mods.get("assetProfile") or {}
    th = mods.get("topHoldings") or {}
    perf = (mods.get("fundPerformance") or {}).get("trailingReturns") or {}
    sectors = {}
    for item in th.get("sectorWeightings") or []:
        for name, weight in (item or {}).items():
            sectors[name] = _raw(weight)
    return {
        "category": fp.get("categoryName"),
        "family": fp.get("family"),
        "legal_type": fp.get("legalType"),
        "expense_ratio": _raw(fees.get("annualReportExpenseRatio")) or _raw(ks.get("annualReportExpenseRatio")),
        "turnover": _raw(fees.get("annualHoldingsTurnover")),
        "total_assets": _raw(sd.get("totalAssets")) or _raw(ks.get("totalAssets")),
        "yield": _raw(sd.get("yield")) or _raw(ks.get("yield")),
        "ytd_return": _raw(perf.get("ytd")) or _raw(ks.get("ytdReturn")),
        "inception": _raw(ks.get("fundInceptionDate")),
        "summary": ap.get("longBusinessSummary") or ks.get("longBusinessSummary"),
        "sector_weights": sectors,
        "top_holdings": [
            {"symbol": h.get("symbol"), "name": h.get("holdingName"), "pct": _raw(h.get("holdingPercent"))}
            for h in th.get("holdings") or []
        ],
    }


def profile_fresh(existing: dict | None, now: datetime, max_age_days: int = PROFILE_MAX_AGE_DAYS) -> bool:
    try:
        stamp = datetime.fromisoformat(str((existing or {}).get("fetched_at")))
    except ValueError:
        return False
    return now - stamp < timedelta(days=max_age_days)


def run_profiles(tickers: list[str], *, fetch, existing_for, now: datetime) -> list[dict]:
    """One crumb per run, one quoteSummary call per stale fund. Returns per-fund outcomes; never raises."""
    out: list[dict] = []
    stale = [t for t in tickers if not profile_fresh(existing_for(t), now)]
    for t in tickers:
        if t not in stale:
            out.append({"ticker": t, "status": "fresh"})
    if not stale:
        return out
    crumb = None
    try:
        fetch(COOKIE_URL)  # sets the session cookie; the 404 body is expected
        response = fetch(CRUMB_URL)
        text = (getattr(response, "text", "") or "").strip()
        if getattr(response, "status_code", 0) == 200 and text and " " not in text and len(text) < 64:
            crumb = text
    except Exception:  # noqa: BLE001 - best-effort source
        crumb = None
    if not crumb:
        return out + [{"ticker": t, "status": "unavailable", "reason": "no_crumb"} for t in stale]
    for t in stale:
        url = SUMMARY_URL.format(ticker=quote(t), modules=",".join(MODULES), crumb=quote(crumb))
        try:
            response = fetch(url)
            status = getattr(response, "status_code", 0)
            payload = response.json() if status == 200 else None
        except Exception:  # noqa: BLE001 - best-effort source
            status, payload = 0, None
        profile = parse_profile(payload) if payload else None
        if profile is None:
            out.append({"ticker": t, "status": "unavailable", "reason": f"http_{status}" if status != 200 else "empty"})
            continue
        stamp = now.isoformat(timespec="seconds")
        out.append({"ticker": t, "status": "ok", "raw": payload,
                    "profile": {"ticker": t, **profile, "source": "yahoo", "fetched_at": stamp}})
    return out


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from http_client import get_client
    from lake import read_json, write_json
    from observability import job_handler

    @job_handler(JOB_ID, lease_key=LEASE_KEY, skip_when_leased=True)
    def run(event, context):
        now = datetime.now(UTC)
        tickers = fund_tickers(read_json(FUND_MAP_KEY))
        with get_client(headers={"User-Agent": BROWSER_UA}) as http:
            results = run_profiles(tickers, fetch=lambda url: http.get(url),
                                   existing_for=lambda t: read_json(curated_profile_key(t)), now=now)
        for row in results:
            if row["status"] == "ok":
                write_json(row["raw"], raw_profile_key(row["ticker"], now.date().isoformat()))
                write_json(row["profile"], curated_profile_key(row["ticker"]))
        return {"status": "ok", "funds": [{k: r.get(k) for k in ("ticker", "status", "reason")} for r in results]}

    return run(event, context)
