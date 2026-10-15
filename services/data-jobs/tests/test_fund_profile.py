"""Yahoo fund summaries (#223): best-effort, weekly, never fails the job.

The quoteSummary payload is a minimal hand-built sample in Yahoo's documented module shape
({"raw", "fmt"} numbers); the live endpoint returned 429 from the box during development."""

from __future__ import annotations

from datetime import UTC, datetime

import fund_profile as fp
import http_client

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
PAYLOAD = {"quoteSummary": {"error": None, "result": [{
    "fundProfile": {"categoryName": "Mid-Cap Growth", "family": "ARK ETF Trust", "legalType": "Exchange Traded Fund",
                    "feesExpensesInvestment": {"annualReportExpenseRatio": {"raw": 0.0075, "fmt": "0.75%"},
                                               "annualHoldingsTurnover": {"raw": 0.54, "fmt": "54%"}}},
    "summaryDetail": {"totalAssets": {"raw": 7.92e9, "fmt": "7.92B"}, "yield": {"raw": 0.0, "fmt": "0.00%"}},
    "defaultKeyStatistics": {"fundInceptionDate": {"raw": 1414713600, "fmt": "2014-10-31"}},
    "topHoldings": {"holdings": [{"symbol": "TSLA", "holdingName": "Tesla Inc", "holdingPercent": {"raw": 0.098}}],
                    "sectorWeightings": [{"technology": {"raw": 0.31}}, {"healthcare": {"raw": 0.22}}]},
    "fundPerformance": {"trailingReturns": {"ytd": {"raw": 0.1333}}},
    "assetProfile": {"longBusinessSummary": "The fund is an actively managed ETF."},
}]}}


class _R:
    def __init__(self, status=200, text="", payload=None):
        self.status_code, self.text, self._p = status, text, payload

    def json(self):
        return self._p


def _fetch(crumb_status=200, summary_status=200):
    calls = []

    def fetch(url):
        calls.append(url)
        if url == fp.COOKIE_URL:
            return _R(404)
        if url == fp.CRUMB_URL:
            return _R(crumb_status, "abcDEF123" if crumb_status == 200 else "Too Many Requests")
        return _R(summary_status, payload=PAYLOAD if summary_status == 200 else None)

    return fetch, calls


def test_fund_tickers_come_from_resolved_fund_map_only():
    fund_map = {"funds": {"ARKK": {"kind": "fund", "status": "resolved"}, "TSLA": {"kind": "operating",
                "status": "resolved"}, "AKF": {"kind": "unknown", "status": "unresolved"}}}
    assert fp.fund_tickers(fund_map) == ["ARKK"]
    assert fp.fund_tickers(None) == []


def test_parse_profile_fields():
    p = fp.parse_profile(PAYLOAD)
    assert p["category"] == "Mid-Cap Growth" and p["expense_ratio"] == 0.0075 and p["turnover"] == 0.54
    assert p["total_assets"] == 7.92e9 and p["ytd_return"] == 0.1333
    assert p["sector_weights"] == {"technology": 0.31, "healthcare": 0.22}
    assert p["top_holdings"][0] == {"symbol": "TSLA", "name": "Tesla Inc", "pct": 0.098}
    assert fp.parse_profile({"quoteSummary": {"result": []}}) is None


def test_run_profiles_ok_and_one_call_per_fund():
    fetch, calls = _fetch()
    rows = fp.run_profiles(["ARKK", "XOVR"], fetch=fetch, existing_for=lambda t: None, now=NOW)
    assert [r["status"] for r in rows] == ["ok", "ok"]
    assert rows[0]["profile"]["source"] == "yahoo" and rows[0]["profile"]["fetched_at"].startswith("2026-10-19")
    assert sum("quoteSummary" in u for u in calls) == 2 and calls.count(fp.CRUMB_URL) == 1


def test_throttled_crumb_is_unavailable_not_an_error():
    fetch, calls = _fetch(crumb_status=429)
    rows = fp.run_profiles(["ARKK"], fetch=fetch, existing_for=lambda t: None, now=NOW)
    assert rows == [{"ticker": "ARKK", "status": "unavailable", "reason": "no_crumb"}]
    assert not any("quoteSummary" in u for u in calls)


def test_summary_429_keeps_going():
    fetch, _ = _fetch(summary_status=429)
    rows = fp.run_profiles(["ARKK"], fetch=fetch, existing_for=lambda t: None, now=NOW)
    assert rows[0]["status"] == "unavailable" and rows[0]["reason"] == "http_429"


def test_fresh_profile_is_not_refetched():
    fetch, calls = _fetch()
    recent = {"fetched_at": "2026-10-15T12:00:00+00:00"}
    rows = fp.run_profiles(["ARKK"], fetch=fetch, existing_for=lambda t: recent, now=NOW)
    assert rows == [{"ticker": "ARKK", "status": "fresh"}] and calls == []


def test_keys_and_hosts():
    assert fp.curated_profile_key("arkk") == "curated/fund_profile/ARKK.json"
    assert fp.raw_profile_key("ARKK", "2026-10-19") == "raw/fund_profile/yahoo/date=2026-10-19/ARKK.json"
    assert "fc.yahoo.com" in http_client.ALLOWED_HOSTS
