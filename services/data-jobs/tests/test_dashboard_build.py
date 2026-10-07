import dashboard_build
import pytest


def test_snapshot_includes_prices_trends_status_and_only_matching_fundamentals():
    snapshot = dashboard_build.build_snapshot(
        tickers=["TSLA", "SPCX"],
        prices={
            "TSLA": [
                {"date": "2026-10-02", "close": 440.0, "adj_close": 440.0, "volume": 100},
                {"date": "2026-10-02", "close": 441.0, "adj_close": 441.0, "volume": 101},
            ]
        },
        trends={"TSLA": {"ticker": "TSLA", "rows": [{"series_id": "DGS10", "net_pressure": 0.2}]}},
        fundamentals=[
            {"ticker": "TSLA", "metric": "deliveries", "value": 500000},
            {"ticker": "UNKNOWN", "metric": "deliveries", "value": 1},
        ],
        status={"jobs": [{"job": "D4", "status": "ok"}]},
        generated_at="2026-10-04T22:00:00+00:00",
    )

    assert snapshot["schema_version"] == 2
    assert snapshot["tickers"]["TSLA"]["news"] is None
    assert snapshot["tickers"]["TSLA"]["filings"] is None
    assert snapshot["tickers"]["TSLA"]["insider_30d"] is None
    assert snapshot["events"] is None
    assert snapshot["tickers"]["TSLA"]["contracts"] is None
    assert snapshot["tickers"]["SPCX"]["contracts"] is None
    assert snapshot["releases"] is None
    assert snapshot["tickers"]["TSLA"]["intraday"] is None
    assert snapshot["tickers"]["TSLA"]["short_interest"] is None
    assert snapshot["generated_at"] == "2026-10-04T22:00:00+00:00"
    assert snapshot["data_status"] == "available"
    assert snapshot["tickers"]["TSLA"]["price_history"] == [
        {"date": "2026-10-02", "close": 441.0, "close_raw": None, "adj_close": 441.0, "volume": 101}
    ]
    assert snapshot["tickers"]["TSLA"]["trend"]["ticker"] == "TSLA"
    assert snapshot["tickers"]["SPCX"]["price_status"] == "unavailable"
    assert snapshot["fundamentals"] == [{"ticker": "TSLA", "metric": "deliveries", "value": 500000}]
    assert snapshot["status"]["jobs"][0]["job"] == "D4"


def test_snapshot_exposes_empty_dataset_as_unavailable():
    snapshot = dashboard_build.build_snapshot(["TSLA"], {}, {}, [], None)
    assert snapshot["data_status"] == "unavailable"
    assert snapshot["tickers"]["TSLA"]["price_history"] == []
    assert snapshot["tickers"]["TSLA"]["price_as_of"] is None
    assert snapshot["status"] is None


def test_releases_keep_the_latest_print_and_the_next_five_dates():
    calendar = [{"series": "cpi", "release_ts": f"2026-11-{day:02d}T08:35:00-05:00"} for day in range(1, 7)]
    snapshot = dashboard_build.build_snapshot(
        tickers=["TSLA"],
        prices={},
        trends={},
        fundamentals=[],
        status=None,
        releases=[
            {"series_id": "CUSR0000SA0", "period": "2026-08", "actual": 320.0, "consensus": 319.5, "surprise": 0.5},
            {"series_id": "CUSR0000SA0", "period": "2026-09", "actual": 321.2, "consensus": 321.0, "surprise": 0.2},
            {"series_id": "PCE", "period": "2026-09", "actual": 124.0, "consensus": 123.8, "surprise": 0.2},
        ],
        release_calendar=calendar,
    )
    assert snapshot["releases"]["latest"] == [
        {"series": "CUSR0000SA0", "period": "2026-09", "actual": 321.2, "consensus": 321.0, "surprise": 0.2},
        {"series": "PCE", "period": "2026-09", "actual": 124.0, "consensus": 123.8, "surprise": 0.2},
    ]
    assert len(snapshot["releases"]["next"]) == 5
    assert snapshot["releases"]["next"][0]["release_ts"].startswith("2026-11-01")


def test_snapshot_carries_close_raw_and_tolerates_rows_without_it():
    snapshot = dashboard_build.build_snapshot(
        tickers=["TSLA"],
        prices={
            "TSLA": [
                {"date": "2026-10-01", "close": 440.0, "adj_close": None, "volume": 1},  # legacy row
                {"date": "2026-10-02", "close": 150.0, "close_raw": 300.0, "adj_close": 149.0, "volume": 2},
            ]
        },
        trends={},
        fundamentals=[],
        status=None,
    )
    history = snapshot["tickers"]["TSLA"]["price_history"]
    assert history[0]["close_raw"] is None and history[0]["adj_close"] is None
    assert history[1] == {"date": "2026-10-02", "close": 150.0, "close_raw": 300.0, "adj_close": 149.0, "volume": 2}


def test_news_keeps_the_last_48_hours_and_a_seven_day_mean():
    snapshot = dashboard_build.build_snapshot(
        tickers=["TSLA"],
        prices={},
        trends={},
        fundamentals=[],
        status=None,
        generated_at="2026-10-06T18:00:00+00:00",
        news={
            "TSLA": {
                "daily": [
                    {"ticker": "TSLA", "date": "2026-10-01", "mean_sentiment": 0.2},
                    {"ticker": "TSLA", "date": "2026-10-06", "mean_sentiment": 0.4},
                ],
                "articles": [
                    {
                        "url_hash": "old",
                        "url": "https://example.com/old",
                        "title": "Old",
                        "publisher": "Old",
                        "published_at": "2026-10-01T00:00:00+00:00",
                        "sentiment_label": "bullish",
                        "tickers": ["TSLA"],
                    },
                    {
                        "url_hash": "new",
                        "url": "https://example.com/new",
                        "title": "Tesla approval",
                        "publisher": "Wire",
                        "published_at": "2026-10-06T16:30:00+00:00",
                        "sentiment_label": "bullish",
                        "tickers": ["TSLA"],
                    },
                    {
                        "url_hash": "new",
                        "url": "https://example.com/new?utm_source=x",
                        "title": "Duplicate",
                        "publisher": "Wire",
                        "published_at": "2026-10-06T16:00:00+00:00",
                        "sentiment_label": "bearish",
                        "tickers": ["TSLA"],
                    },
                ],
            }
        },
    )
    news = snapshot["tickers"]["TSLA"]["news"]
    assert news["sentiment_7d"] == pytest.approx(0.3)
    assert [item["title"] for item in news["headlines"]] == ["Tesla approval"]
    assert news["headlines"][0]["label"] == "bullish"


def test_filings_events_and_insider_flow_are_served():
    snapshot = dashboard_build.build_snapshot(
        tickers=["TSLA"],
        prices={},
        trends={},
        fundamentals=[],
        status=None,
        generated_at="2026-10-06T18:00:00+00:00",
        filings={
            "TSLA": [
                {
                    "accession_no": "0001",
                    "form": "8-K",
                    "filed_at": "2026-10-01",
                    "title": "Results",
                    "primary_doc_url": "https://www.sec.gov/a",
                    "filing_class": "earnings",
                },
                {
                    "accession_no": "0001",
                    "form": "8-K",
                    "filed_at": "2026-10-02",
                    "title": "Duplicate accession",
                    "primary_doc_url": "https://www.sec.gov/b",
                    "filing_class": "other",
                },
                {
                    "accession_no": "0002",
                    "form": "4",
                    "filed_at": "2026-09-20",
                    "title": "Form 4",
                    "primary_doc_url": "https://www.sec.gov/c",
                    "filing_class": "buy",
                    "txn_code": "P",
                    "shares": 100,
                    "price": 10,
                },
            ]
        },
        events=[
            {
                "event_id": "e1",
                "event_ts": "2026-10-05T14:00:00+00:00",
                "type": "monetary",
                "title": "FOMC",
                "source_url": "https://www.federalreserve.gov/new",
                "tickers": [],
            },
            {
                "event_id": "old",
                "event_ts": "2026-08-01T00:00:00+00:00",
                "type": "tariff",
                "title": "Old tariff",
                "source_url": "https://www.federalregister.gov/old",
                "tickers": [],
            },
        ],
    )
    filings = snapshot["tickers"]["TSLA"]["filings"]
    assert [row["title"] for row in filings] == ["Duplicate accession", "Form 4"]
    assert snapshot["tickers"]["TSLA"]["insider_30d"] == {"net_shares": 100.0, "net_value": 1000.0}
    assert [event["title"] for event in snapshot["events"]] == ["FOMC"]


def test_contracts_are_served_only_for_mapped_tickers():
    award = {
        "ttm_obligated": 1200.0,
        "by_agency": [{"agency": "NASA", "quarter": "FY2026Q4", "obligated": 1200.0}],
        "recent": [
            {
                "award_id": "80NSSC",
                "agency": "NASA",
                "amount": 1200.0,
                "date": "2026-10-01",
                "url": "https://sam.gov/x",
            }
        ],
    }
    snapshot = dashboard_build.build_snapshot(
        tickers=["SPCX", "TSLA", "AAPL"],
        prices={},
        trends={},
        fundamentals=[],
        status=None,
        contracts={"SPCX": award, "AAPL": award},
    )
    assert snapshot["tickers"]["SPCX"]["contracts"]["ttm_obligated"] == 1200.0
    assert snapshot["tickers"]["TSLA"]["contracts"] is None
    assert snapshot["tickers"]["AAPL"]["contracts"] is None


def test_intraday_is_today_only_and_short_interest_is_the_latest_settlement():
    snapshot = dashboard_build.build_snapshot(
        tickers=["TSLA"],
        prices={
            "TSLA": [
                {"date": "2026-10-05", "close": 360.0, "volume": 1},
                {"date": "2026-10-06", "close": 370.0, "volume": 1},
            ]
        },
        trends={},
        fundamentals=[],
        status=None,
        hourly={
            "TSLA": [
                {"ts_utc": "2026-10-05T18:00:00+00:00", "close": 361.0, "volume": 10},
                {"ts_utc": "2026-10-06T14:00:00+00:00", "close": 368.0, "volume": 20},
                {"ts_utc": "2026-10-06T20:00:00+00:00", "close": 372.0, "volume": 30},
            ]
        },
        short_interest={
            "TSLA": [
                {
                    "ticker": "TSLA",
                    "settlement_date": "2026-09-15",
                    "short_interest": 100,
                    "previous_short_interest": 80,
                    "days_to_cover": 1.2,
                },
                {
                    "ticker": "TSLA",
                    "settlement_date": "2026-09-30",
                    "short_interest": 90,
                    "previous_short_interest": 100,
                    "days_to_cover": 1.1,
                },
            ]
        },
    )
    intraday = snapshot["tickers"]["TSLA"]["intraday"]
    assert intraday["last"] == 372.0
    assert [bar["ts"] for bar in intraday["bars"]] == ["2026-10-06T14:00:00+00:00", "2026-10-06T20:00:00+00:00"]
    assert intraday["change_pct"] == (372.0 / 360.0 - 1)
    assert snapshot["tickers"]["TSLA"]["short_interest"] == {
        "settlement_date": "2026-09-30",
        "shares_short": 90,
        "days_to_cover": 1.1,
        "pct_change": -0.1,
    }
