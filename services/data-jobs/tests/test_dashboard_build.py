import pytest

import dashboard_build


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

    assert snapshot["schema_version"] == 3
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


def test_snapshot_keeps_each_ticker_when_price_rows_are_mixed():
    mixed = [
        {"ticker": "TSLA", "date": "2026-10-01", "close": 100.0, "adj_close": 100.0, "volume": 1},
        {"ticker": "SPCX", "date": "2026-10-01", "close": 200.0, "adj_close": 180.0, "volume": 2},
        {"ticker": "TSLA", "date": "2026-10-02", "close": 110.0, "adj_close": 110.0, "volume": 3},
        {"ticker": "SPCX", "date": "2026-10-02", "close": 190.0, "adj_close": 170.0, "volume": 4},
    ]
    snapshot = dashboard_build.build_snapshot(
        ["TSLA", "SPCX"],
        {"TSLA": mixed, "SPCX": mixed},
        {},
        [],
        None,
    )
    tsla = snapshot["tickers"]["TSLA"]["price_history"]
    spcx = snapshot["tickers"]["SPCX"]["price_history"]
    assert [row["close"] for row in tsla] == [100.0, 110.0]
    assert [row["adj_close"] for row in tsla] == [100.0, 110.0]
    assert [row["close"] for row in spcx] == [200.0, 190.0]
    assert [row["adj_close"] for row in spcx] == [180.0, 170.0]
    assert tsla != spcx


def test_snapshot_exposes_empty_dataset_as_unavailable():
    snapshot = dashboard_build.build_snapshot(["TSLA"], {}, {}, [], None)
    assert snapshot["data_status"] == "unavailable"
    assert snapshot["tickers"]["TSLA"]["price_history"] == []
    assert snapshot["tickers"]["TSLA"]["price_as_of"] is None
    assert snapshot["status"] is None


def test_release_links_are_scoped_to_ticker_and_missing_stats_stay_null():
    rows = [
        {"row_kind": "summary", "series_id": "CPI_YOY", "ticker": "TSLA", "window": "release_day",
         "n_releases": 3, "trend_direction": "decelerating", "consecutive_releases": 2,
         "correlation_surprise": None},
        {"row_kind": "summary", "series_id": "CPI_YOY", "ticker": "SPCX", "window": "release_day",
         "n_releases": 12, "correlation_surprise": 0.5},
        {"row_kind": "release", "series_id": "CPI_YOY", "ticker": "TSLA", "release_date": "2026-09-11",
         "yoy": 2.8, "surprise": -0.1},
    ]
    snapshot = dashboard_build.build_snapshot(
        ["TSLA", "SPCX"], {}, {}, [], None, release_links=rows,
    )
    tsla = snapshot["tickers"]["TSLA"]["release_links"]
    assert tsla["summaries"][0]["n_releases"] == 3
    assert tsla["summaries"][0]["correlation_surprise"] is None
    assert tsla["latest"][0]["yoy"] == 2.8
    assert all(row["ticker"] == "TSLA" for row in tsla["summaries"])
    assert snapshot["tickers"]["SPCX"]["release_links"]["summaries"][0]["correlation_surprise"] == 0.5


def test_chart_data_prefers_filed_public_float_estimate_and_falls_back_to_shares():
    chart = dashboard_build.build_chart_data(
        ticker="TSLA",
        trend_rows=[
            {"ticker": "TSLA", "date": "2026-08-15", "series_id": "DGS10", "value": 4.2, "net_pressure": 0.3},
            {"ticker": "TSLA", "date": "2026-08-15", "series_id": "VIXCLS", "value": 18.0, "net_pressure": 0.3},
            {"ticker": "SPCX", "date": "2026-08-15", "series_id": "DGS10", "value": 9.9, "net_pressure": -0.9},
        ],
        short_interest_rows=[
            {"ticker": "TSLA", "settlement_date": "2026-08-15", "short_interest": 30_000_000, "days_to_cover": 1.2},
            {"ticker": "TSLA", "settlement_date": "2026-09-30", "short_interest": 33_000_000, "days_to_cover": 1.3},
            {"ticker": "SPCX", "settlement_date": "2026-09-30", "short_interest": 5, "days_to_cover": 1},
        ],
        options_rows=[
            {"ticker": "TSLA", "date": "2026-09-30", "put_call_volume_ratio": 1.1, "iv30": None, "iv_available": False}
        ],
        fundamentals=[
            {"ticker": "TSLA", "metric": "shares_outstanding", "release_date": "2026-07-24", "value": 3_000_000_000},
            {"ticker": "TSLA", "metric": "shares_outstanding", "release_date": "2026-09-15", "value": 3_100_000_000},
            {
                "ticker": "TSLA",
                "metric": "public_float_usd",
                "release_date": "2026-09-15",
                "measurement_date": "2026-06-30",
                "value": 900_000_000_000,
            },
            {
                "ticker": "TSLA",
                "metric": "gross_margin_gaap",
                "release_date": "2026-09-15",
                "fiscal_quarter": "2026Q2",
                "value": 0.18,
                "unit": "ratio",
                "source_id": "DS-11",
            },
        ],
        price_rows=[{"date": "2026-06-30", "close": 100.0}],
        generated_at="2026-10-01T00:00:00+00:00",
    )

    assert chart["macro_series"]["DGS10"] == [{"date": "2026-08-15", "value": 4.2}]
    assert chart["macro_pressure"] == [{"date": "2026-08-15", "value": 0.3}]
    assert chart["short_interest"][0]["denominator_type"] == "shares_outstanding_proxy"
    assert chart["short_interest"][0]["short_pct_denominator"] == pytest.approx(1.0)
    assert chart["short_interest"][1]["denominator_type"] == "estimated_public_float"
    assert chart["short_interest"][1]["shares_denominator"] == pytest.approx(9_000_000_000)
    assert chart["short_interest"][1]["short_pct_denominator"] == pytest.approx(33_000_000 / 9_000_000_000 * 100)
    assert {"public_float_usd", "shares_outstanding"} <= {row["series_id"] for row in chart["fundamentals"]}
    assert next(row for row in chart["fundamentals"] if row["series_id"] == "gross_margin_gaap")["source_id"] == "DS-11"
    assert chart["options"][0]["iv30"] is None
    assert any(row["series_id"] == "gross_margin_gaap" for row in chart["fundamentals"])


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
    assert news["sentiment_history"] == [
        {"date": "2026-10-01", "value": 0.2},
        {"date": "2026-10-06", "value": pytest.approx(0.3)},
    ]


def test_sentiment_history_is_ticker_scoped_finite_and_only_uses_observed_days():
    from datetime import UTC, datetime

    rows = [
        {"ticker": "TSLA", "date": "2026-09-20", "mean_sentiment": -0.9},
        {"ticker": "TSLA", "date": "2026-10-01", "mean_sentiment": 0.2},
        {"ticker": "TSLA", "date": "2026-10-06", "mean_sentiment": 0.4},
        {"ticker": "TSLA", "date": "2026-10-06", "mean_sentiment": 0.6},
        {"ticker": "SPCX", "date": "2026-10-06", "mean_sentiment": -1},
        {"ticker": "TSLA", "date": "2026-10-07", "mean_sentiment": 1},
        {"ticker": "TSLA", "date": "2026-10-04", "mean_sentiment": float("nan")},
        {"ticker": "TSLA", "date": "2026-10-05", "mean_sentiment": float("inf")},
        {"ticker": "TSLA", "date": "2026-02-30", "mean_sentiment": 1},
    ]
    news = dashboard_build.build_news("TSLA", rows, [], datetime(2026, 10, 6, tzinfo=UTC))
    assert news["sentiment_7d"] == pytest.approx(0.4)
    assert news["sentiment_history"] == [
        {"date": "2026-09-20", "value": -0.9},
        {"date": "2026-10-01", "value": 0.2},
        {"date": "2026-10-06", "value": pytest.approx(0.4)},
    ]
    assert dashboard_build.build_news("NONE", rows, [], datetime(2026, 10, 6, tzinfo=UTC))["sentiment_history"] == []


def test_sentiment_history_is_bounded_to_last_90_observed_days():
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 10, 6, tzinfo=UTC)
    rows = [
        {"ticker": "TSLA", "date": (now.date() - timedelta(days=i)).isoformat(), "mean_sentiment": 0.2}
        for i in range(110)
    ]
    history = dashboard_build.build_news("TSLA", rows, [], now)["sentiment_history"]
    assert len(history) == 90
    assert history[0]["date"] == (now.date() - timedelta(days=89)).isoformat()
    assert history[-1]["date"] == now.date().isoformat()
    assert all(row["value"] == pytest.approx(0.2) for row in history)


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
                "source": "federal-register",
                "source_url": "https://www.federalreserve.gov/new",
                "ingested_at": "2026-10-05T15:00:00+00:00",
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
    assert snapshot["events"][0]["source"] == "federal-register"
    assert snapshot["events"][0]["observed_at"] == "2026-10-05T15:00:00+00:00"


def test_contracts_are_served_only_for_mapped_tickers():
    award = {
        "ttm_obligated": 1200.0,
        "ttm_awards_count": 1,
        "as_of": "2026-10-06",
        "source_ids": ["usaspending"],
        "observed_at": "2026-10-06T18:00:00+00:00",
        "by_agency": [{"agency": "NASA", "quarter": "FY2026Q4", "obligated": 1200.0}],
        "recent": [
            {
                "award_id": "80NSSC",
                "agency": "NASA",
                "amount": 1200.0,
                "date": "2026-10-01",
                "source_id": "usaspending",
                "observed_at": "2026-10-06T18:00:00+00:00",
                "url": "https://www.usaspending.gov/award/x",
            }
        ],
    }
    snapshot = dashboard_build.build_snapshot(
        tickers=["SPCX", "TSLA", "AAPL"],
        prices={},
        trends={},
        fundamentals=[],
        status=None,
        generated_at="2026-10-13T18:00:00+00:00",
        contracts={"SPCX": award, "AAPL": award},
    )
    assert snapshot["tickers"]["SPCX"]["contracts"]["ttm_obligated"] == 1200.0
    assert snapshot["tickers"]["SPCX"]["contracts"]["coverage"] == "available"
    assert snapshot["tickers"]["SPCX"]["contracts"]["source_ids"] == ["usaspending"]
    assert snapshot["tickers"]["SPCX"]["contracts"]["observed_at"] == "2026-10-06T18:00:00+00:00"
    assert snapshot["tickers"]["SPCX"]["contracts"]["freshness"] == "fresh"
    assert snapshot["tickers"]["TSLA"]["contracts"] is None
    assert snapshot["tickers"]["AAPL"]["contracts"] is None


def test_stale_and_undated_contract_rollups_are_distinguishable():
    base = {
        "ttm_obligated": None,
        "ttm_awards_count": 0,
        "by_agency": [],
        "recent": [],
    }
    stale = dashboard_build.build_snapshot(
        ["SPCX"], {}, {}, [], None, generated_at="2026-10-09T12:00:00+00:00",
        contracts={"SPCX": {**base, "as_of": "2026-10-01"}},
    )
    unknown = dashboard_build.build_snapshot(
        ["SPCX"], {}, {}, [], None, generated_at="2026-10-09T12:00:00+00:00",
        contracts={"SPCX": base},
    )
    assert stale["tickers"]["SPCX"]["contracts"]["ttm_obligated"] is None
    assert stale["tickers"]["SPCX"]["contracts"]["freshness"] == "stale"
    assert unknown["tickers"]["SPCX"]["contracts"]["freshness"] == "unknown"


def test_legacy_or_empty_award_rollup_does_not_serve_zero_without_source_rows():
    snapshot = dashboard_build.build_snapshot(
        ["SPCX"],
        {},
        {},
        [],
        None,
        generated_at="2026-10-09T12:00:00+00:00",
        contracts={"SPCX": {"as_of": "2026-10-09", "ttm_obligated": 0.0}},
    )

    contracts = snapshot["tickers"]["SPCX"]["contracts"]
    assert contracts["ttm_obligated"] is None
    assert contracts["coverage"] == "unavailable"


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


def test_snapshot_includes_the_nyse_session_for_its_clock():
    snapshot = dashboard_build.build_snapshot(
        ["TSLA"], {}, {}, [], None, generated_at="2026-10-10T16:00:00+00:00",
    )
    assert snapshot["market"]["status"] == "closed"
    assert snapshot["market"]["next_open"] == "2026-10-12T13:30:00+00:00"
    assert snapshot["market"]["next_close"] == "2026-10-12T20:00:00+00:00"
    assert snapshot["market"]["as_of"] == "2026-10-10T16:00:00+00:00"
