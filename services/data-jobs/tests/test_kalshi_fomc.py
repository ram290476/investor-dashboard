"""Kalshi FOMC parser, 4xx/429 stop, and the decision to leave live collection off."""

from datetime import date

import kalshi_fomc as kalshi

FIXTURE = {
    "markets": [
        {
            "ticker": "KXFEDDECISION-26OCT-C25",
            "yes_sub_title": "Cut 25bps",
            "title": "Fed funds decision",
            "close_time": "2026-10-28T18:00:00Z",
            "status": "active",
            "last_price": 40,
        },
        {
            "ticker": "KXFEDDECISION-26OCT-C50",
            "yes_sub_title": "Cut 50bps",
            "close_time": "2026-10-28T18:00:00Z",
            "status": "active",
            "last_price": 22,
        },
        {
            "ticker": "KXFEDDECISION-26OCT-H0",
            "yes_sub_title": "Hold",
            "close_time": "2026-10-28T18:00:00Z",
            "status": "active",
            "yes_bid": 30,
            "yes_ask": 32,
        },
        {
            "ticker": "KXFEDDECISION-26OCT-H25",
            "yes_sub_title": "Hike 25bps",
            "close_time": "2026-10-28T18:00:00Z",
            "status": "active",
        },
        {
            "ticker": "KXFEDDECISION-26DEC-C25",
            "yes_sub_title": "Cut 25bps",
            "close_time": "2026-12-16T18:00:00Z",
            "status": "active",
            "last_price": 48,
        },
    ],
    "history": [
        {"date": "2026-09-20", "meeting_date": "2026-12-16", "cut": 0.4},
        {"date": "2026-10-01", "meeting_date": "2026-10-28", "cut": 0.55, "hold": 0.4, "hike": 0.05},
        {"date": "2026-10-08", "meeting_date": "2026-10-28", "cut": 0.62, "hold": 0.31, "hike": 0.07},
    ],
}


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Http:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload
        self.calls = 0

    def get(self, url, params=None):
        self.calls += 1
        assert url == kalshi.MARKETS_URL
        return _Response(self.status_code, self.payload)


def test_parser_picks_the_next_meeting_and_drops_a_market_with_no_price():
    parsed = kalshi.parse_fomc_payload(FIXTURE, date(2026, 10, 8))
    assert parsed["meeting_date"] == "2026-10-28"
    assert parsed["source"] == "Kalshi"
    assert parsed["outcomes"] == [
        {"label": "Cut", "prob": 0.62},
        {"label": "Hold", "prob": 0.31},
    ]
    assert [row["date"] for row in parsed["history_14d"]] == ["2026-10-01", "2026-10-08"]
    assert parsed["history_14d"][-1]["cut"] == 0.62
    assert all(row["prob"] != 0 for row in parsed["outcomes"])


def test_four_xx_and_429_do_not_store_probabilities_or_retry():
    for status in (400, 403, 404, 429):
        decision = kalshi.fetch_decision(status)
        assert decision["store"] is False
        assert decision["retry"] is False
    assert kalshi.fetch_decision(503)["retry"] is True
    assert kalshi.fetch_decision(200)["store"] is True
    result = kalshi.interpret_response(429, FIXTURE, date(2026, 10, 8), "2026-10-08T11:00:00+00:00")
    assert result["store"] is False
    assert result["document"] is None
    assert result["attempt"]["state"] == "error"
    assert result["attempt"]["detail"] == "provider_policy"
    assert result["attempt"]["last_attempt"] == "2026-10-08T11:00:00+00:00"


def test_live_collection_stays_off_and_a_priced_book_can_be_stored_once_terms_are_accepted():
    assert kalshi.TERMS_ACCEPTED is False
    skipped = kalshi.collect(None, date(2026, 10, 8), "2026-10-08T11:00:00+00:00")
    assert skipped["status"] == "skipped"
    assert skipped["document"] is None
    assert skipped["reason"] == "kalshi_terms_not_accepted"
    assert "terms" in skipped["attempt"]["detail"]

    http = _Http(429, FIXTURE)
    blocked = kalshi.collect(http, date(2026, 10, 8), "2026-10-08T12:00:00+00:00", terms_accepted=True)
    assert http.calls == 1
    assert blocked["document"] is None
    assert blocked["retry"] is False

    http = _Http(200, FIXTURE)
    stored = kalshi.collect(http, date(2026, 10, 8), "2026-10-08T12:00:00+00:00", terms_accepted=True)
    assert stored["status"] == "success"
    assert stored["document"]["meeting_date"] == "2026-10-28"
    assert stored["document"]["outcomes"][0] == {"label": "Cut", "prob": 0.62}
