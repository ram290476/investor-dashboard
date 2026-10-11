import gzip
import json

import boto3
import polars as pl
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

import dashboard_build
import lake
import observability
import universe


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
        {"date": "2026-10-02", "close": 441.0, "volume": 101}
    ]
    assert snapshot["tickers"]["TSLA"]["trend"]["ticker"] == "TSLA"
    assert snapshot["tickers"]["SPCX"]["price_status"] == "unavailable"
    assert "fundamentals" not in snapshot
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
    assert [row.get("adj_close", row["close"]) for row in tsla] == [100.0, 110.0]
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
    assert chart["price_history"] == [{"date": "2026-06-30", "close": 100.0}]
    assert chart["volume_source_changes"] == []


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
    assert snapshot["releases"]["inflation"] == {"prints": [], "headline": None, "core": None}


def test_inflation_block_keeps_six_cpi_yoy_prints_and_the_previous_print_change():
    rows = []
    for index, yoy in enumerate((2.7, 2.8, 2.9, 3.0, 2.9, 3.0, 2.8)):
        month = index + 1
        rows.append({"series_id": "CUSR0000SA0", "period": f"2026-{month:02d}", "yoy": yoy, "actual": 300 + yoy})
    rows.append({"series_id": "CUSR0000SA0L1E", "period": "2026-06", "yoy": 3.1})
    rows.append({"series_id": "CUSR0000SA0L1E", "period": "2026-07", "yoy": 3.0})
    rows.append({"series_id": "CUSR0000SA0", "period": "2026-08", "actual": 321.0})
    block = dashboard_build.build_inflation_block(rows)
    assert [row["month"] for row in block["prints"]] == ["Feb", "Mar", "Apr", "May", "Jun", "Jul"]
    assert block["prints"][0]["yoy"] == 2.8
    assert block["prints"][0]["surprise_pp"] == pytest.approx(0.1)
    last = block["prints"][-1]
    assert last["period"] == "2026-07"
    assert last["month"] == "Jul"
    assert last["yoy"] == 2.8
    assert last["surprise_pp"] == pytest.approx(-0.2)
    assert block["headline"] == "easing"
    assert block["core"] == "easing"


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
    assert "close_raw" not in history[0] and "adj_close" not in history[0]
    assert history[1] == {
        "date": "2026-10-02",
        "close": 150.0,
        "close_raw": 300.0,
        "adj_close": 149.0,
        "volume": 2,
    }


def test_snapshot_serves_volume_source_and_an_explicit_change_point():
    """A mixed series stays intact and names the session where the source changes."""
    snapshot = dashboard_build.build_snapshot(
        tickers=["TSLA"],
        prices={
            "TSLA": [
                {
                    "date": "2026-09-28",
                    "close": 357.0,
                    "volume": 39452900,
                    "volume_source": "DS-05",
                    "source_id": "DS-05",
                },
                {
                    "date": "2026-09-29",
                    "close": 352.0,
                    "volume": 524882,
                    "volume_iex": 524882,
                    "volume_source": "DS-02",
                    "source_id": "DS-02",
                },
                {
                    "date": "2026-09-30",
                    "close": 354.0,
                    "volume": 39235800,
                    "volume_iex": 779713,
                    "volume_source": "DS-05",
                    "source_id": "DS-02",
                },
            ]
        },
        trends={},
        fundamentals=[],
        status=None,
    )
    history = snapshot["tickers"]["TSLA"]["price_history"]
    assert [row["volume_source"] for row in history] == ["DS-05", "DS-02", "DS-05"]
    assert history[1]["volume"] == 524882 and "volume_iex" not in history[1]
    assert history[2]["volume"] == 39235800 and history[2]["volume_iex"] == 779713
    assert snapshot["tickers"]["TSLA"]["volume_source_changes"] == [
        {"date": "2026-09-29", "from": "DS-05", "to": "DS-02"},
        {"date": "2026-09-30", "from": "DS-02", "to": "DS-05"},
    ]

    uniform = dashboard_build.build_snapshot(
        tickers=["TSLA"],
        prices={
            "TSLA": [
                {"date": "2026-09-29", "close": 1.0, "volume": 100, "source_id": "DS-05"},
                {"date": "2026-09-30", "close": 1.0, "volume": 110, "volume_source": "DS-05", "source_id": "DS-02"},
            ]
        },
        trends={},
        fundamentals=[],
        status=None,
    )
    assert snapshot_sources(uniform) == ["DS-05", "DS-05"]
    assert uniform["tickers"]["TSLA"]["volume_source_changes"] == []


def snapshot_sources(snapshot):
    return [row["volume_source"] for row in snapshot["tickers"]["TSLA"]["price_history"]]


def test_news_keeps_seven_days_and_a_seven_day_mean():
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
    assert news["sentiment_7d_prior"] is None
    assert news["count_7d"] == 2
    assert [item["title"] for item in news["headlines"] if item["relevance"] != "sector"] == [
        "Tesla approval",
        "Old",
    ]
    assert news["headlines"][0]["label"] == "bullish"
    assert news["headlines"][0]["date_precision"] == "minute"
    assert news["headlines"][0]["score"] is None
    assert news["sentiment_history"] == [
        {"date": "2026-10-01", "value": 0.2},
        {"date": "2026-10-06", "value": pytest.approx(0.3)},
    ]


def test_news_serves_scores_clean_titles_and_hides_topic_only_stories():
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 10, 8, 18, tzinfo=UTC)
    articles = [
        {
            "url_hash": "old",
            "title": "Ancient Tesla note",
            "publisher": "Wire",
            "published_at": "2026-09-30T18:00:00+00:00",
            "tickers": ["TSLA"],
            "topics": ["tesla"],
            "sentiment_score": 0.4,
            "sentiment_label": "bullish",
        },
        {
            "url_hash": "lex",
            "title": "Tesla approval - BASENOR",
            "publisher": "BASENOR",
            "published_at": "2026-10-08T02:06:20+00:00",
            "tickers": ["TSLA"],
            "topics": ["tesla"],
            "sentiment_score": 0.22,
            "provider_sentiment": None,
            "sentiment_label": "bullish",
            "snippet": "<b>Approval</b> coverage",
        },
        {
            "url_hash": "provider",
            "title": "Shares plunge - Wire",
            "publisher": "Wire",
            "published_at": "2026-10-07T15:00:00+00:00",
            "tickers": ["TSLA"],
            "topics": ["company"],
            "sentiment_score": -0.31,
            "provider_sentiment": -0.31,
            "sentiment_label": "bearish",
        },
        {
            "url_hash": "uber",
            "title": "Uber takes a stake in Croatian robotaxi startup Verne - Wire",
            "publisher": "Wire",
            "published_at": "2026-10-07T12:00:00+00:00",
            "tickers": ["TSLA"],
            "topics": ["robotaxi"],
            "summary": "A European autonomy deal.",
            "sentiment_score": 0.05,
            "sentiment_label": "neutral",
        },
        {
            "url_hash": "mentioned",
            "title": "Tesla robotaxi miles - Wire",
            "publisher": "Wire",
            "published_at": "2026-10-06T12:00:00+00:00",
            "tickers": ["TSLA"],
            "topics": ["robotaxi"],
            "sentiment_score": 0.4,
            "provider_sentiment": 0.4,
            "sentiment_label": "bullish",
        },
        {
            "url_hash": "other",
            "title": "Apple event",
            "publisher": "Wire",
            "published_at": "2026-10-08T12:00:00+00:00",
            "tickers": ["AAPL"],
            "sentiment_score": 0.9,
            "sentiment_label": "bullish",
        },
    ]
    news = dashboard_build.build_news(
        "TSLA",
        [
            {"ticker": "TSLA", "date": "2026-10-08", "mean_sentiment": 0.2},
            {"ticker": "TSLA", "date": "2026-10-02", "mean_sentiment": 0.1},
            {"ticker": "TSLA", "date": "2026-09-28", "mean_sentiment": -0.2},
            {"ticker": "TSLA", "date": "2026-09-25", "mean_sentiment": 0.4},
        ],
        articles,
        now,
    )
    lex = next(item for item in news["headlines"] if item["title"] == "Tesla approval")
    assert lex["publisher"] == "BASENOR"
    assert lex["score"] == pytest.approx(0.22)
    assert lex["score_source"] == "lexicon"
    assert lex["date_precision"] == "minute"
    assert lex["relevance"] == "ticker"
    assert lex["snippet"] == "Approval coverage"
    provider = next(item for item in news["headlines"] if item["title"] == "Shares plunge")
    assert provider["score"] == pytest.approx(-0.31)
    assert provider["score_source"] == "provider"
    uber = next(item for item in news["headlines"] if item["title"].startswith("Uber"))
    assert uber["relevance"] == "sector"
    assert uber["title"] == "Uber takes a stake in Croatian robotaxi startup Verne"
    mentioned = next(item for item in news["headlines"] if item["title"] == "Tesla robotaxi miles")
    assert mentioned["relevance"] == "ticker"
    assert mentioned["score_source"] == "provider"
    assert all(item["title"] != "Apple event" for item in news["headlines"])
    assert all(item["title"] != "Ancient Tesla note" for item in news["headlines"])
    assert news["count_7d"] == 3
    assert news["sentiment_7d"] == pytest.approx(0.15)
    assert news["sentiment_7d_prior"] == pytest.approx(0.1)
    limited = dashboard_build.build_news(
        "TSLA",
        [],
        [
            {
                "url_hash": f"n-{index}",
                "title": f"Tesla headline {index}",
                "publisher": "Wire",
                "published_at": (now - timedelta(minutes=index)).isoformat(),
                "tickers": ["TSLA"],
                "topics": ["tesla"],
                "sentiment_score": 0.0,
                "sentiment_label": "neutral",
            }
            for index in range(35)
        ],
        now,
    )
    assert limited["count_7d"] == 35
    assert sum(item["relevance"] != "sector" for item in limited["headlines"]) == 30
    assert limited["headlines"][0]["title"] == "Tesla headline 0"
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
    assert intraday["change_pct"] == pytest.approx(372.0 / 360.0 - 1, abs=1e-6)
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


def test_publish_writes_gzip_siblings_and_size_metrics(monkeypatch):
    import lake
    import observability

    recorded = []
    monkeypatch.setattr(observability.metrics, "add_metric", lambda **kwargs: recorded.append(kwargs))
    snapshot = {"schema_version": 3, "tickers": {"TSLA": {"price_history": [{"date": "2026-10-01", "close": 1.0}]}}}
    chart = {"ticker": "TSLA", "macro_pressure": [{"date": "2026-10-01", "value": 0.2}]}
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", s3)
        size = dashboard_build.publish_serving_documents(snapshot, {"TSLA": chart})
        raw = s3.get_object(Bucket="lake", Key="serving/dashboard.json")["Body"].read()
        compressed = s3.get_object(Bucket="lake", Key="serving/dashboard.json.gz")["Body"].read()
        chart_gz = s3.get_object(Bucket="lake", Key="serving/chart_data/TSLA.json.gz")["Body"].read()
    assert gzip.decompress(compressed) == raw
    assert json.loads(gzip.decompress(chart_gz))["ticker"] == "TSLA"
    assert size == {"raw_bytes": len(raw), "gzip_bytes": len(compressed)}
    assert [item["name"] for item in recorded] == ["DashboardRawBytes", "DashboardGzipBytes"]
    assert recorded[0]["value"] == len(raw)
    assert recorded[1]["value"] == len(compressed)
    assert compressed == gzip.compress(raw, compresslevel=6, mtime=0)


def _weekday_session(count: int) -> list[str]:
    from datetime import date, timedelta

    days = []
    cursor = date(2021, 10, 4)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def test_synthetic_25_ticker_dashboard_stays_under_two_megabytes():
    """A 25-ticker build with five-year inputs must keep dashboard.json under 2 MB raw.

    The same prices, short-interest settlements and correlation rows are heavy enough
    that leaving the five-year closes on the snapshot exceeds that budget.
    """
    days = _weekday_session(dashboard_build.MAX_PRICE_ROWS)
    tickers = [f"T{index:02d}" for index in range(25)]
    series_ids = ["DGS10", "DGS2", "VIXCLS", "T10Y2Y", "DCOILWTICO"]
    prices: dict[str, list[dict]] = {}
    shorts: dict[str, list[dict]] = {}
    trends: dict[str, dict] = {}
    trend_rows: dict[str, list[dict]] = {}
    for index, ticker in enumerate(tickers):
        prices[ticker] = [
            {
                "ticker": ticker,
                "date": day,
                "close": 100 + index + offset * 0.137,
                "adj_close": 99 + index + offset * 0.131,
                "close_raw": 101 + index + offset * 0.139,
                "volume": 1_000_000 + offset,
                "volume_source": "DS-02" if offset % 50 else "DS-05",
                "volume_iex": 800_000 + offset,
            }
            for offset, day in enumerate(days)
        ]
        shorts[ticker] = [
            {
                "ticker": ticker,
                "settlement_date": day,
                "short_interest": 1_000_000 + offset,
                "previous_short_interest": 900_000 + offset,
                "days_to_cover": 1.25,
            }
            for offset, day in enumerate(days[::10])
        ]
        trends[ticker] = {
            "ticker": ticker,
            "date": days[-1],
            "rows": [
                {
                    "series_id": series,
                    "value": 1.23456789 + slot,
                    "corr_30d": 0.123456789,
                    "corr_90d": -0.234567891,
                    "effect": 0.034567891,
                    "net_pressure": 0.111111111,
                }
                for slot, series in enumerate(series_ids)
            ],
        }
        trend_rows[ticker] = [
            {
                "ticker": ticker,
                "date": day,
                "series_id": series,
                "value": 4.2 + slot,
                "net_pressure": 0.2,
                "corr_30d": 0.111111111,
                "corr_90d": -0.222222222,
            }
            for day in days[-90:]
            for slot, series in enumerate(series_ids[:3])
        ]
    snapshot = dashboard_build.build_snapshot(
        tickers=tickers,
        prices=prices,
        trends=trends,
        fundamentals=[{"ticker": tickers[0], "metric": "deliveries", "release_date": days[-1], "value": 500000}],
        status={"jobs": [{"job": "D4", "status": "ok"}]},
        generated_at="2026-10-06T18:00:00+00:00",
        short_interest=shorts,
        rates_observations={
            "DGS2": [{"obs_date": "2026-10-01", "value": 4.11}, {"obs_date": "2026-09-01", "value": 4.01}],
            "DGS10": [{"obs_date": "2026-10-01", "value": 4.32}, {"obs_date": "2026-09-01", "value": 4.21}],
        },
    )
    raw = json.dumps(snapshot, separators=(",", ":"), default=str).encode()
    assert len(raw) < dashboard_build.DASHBOARD_RAW_BUDGET_BYTES, len(raw)
    assert b"correlation_history" not in raw
    assert b"market_comparison" not in raw
    assert b'"fundamentals"' not in raw
    for ticker in tickers:
        record = snapshot["tickers"][ticker]
        assert len(record["price_history"]) == dashboard_build.SUMMARY_PRICE_ROWS
        assert isinstance(record["short_interest"], dict)
        assert record["price_history"][-1]["date"] == days[-1]
    chart = dashboard_build.build_chart_data(
        ticker=tickers[0],
        trend_rows=trend_rows[tickers[0]],
        short_interest_rows=shorts[tickers[0]],
        options_rows=[],
        fundamentals=[],
        price_rows=prices[tickers[0]],
        generated_at=snapshot["generated_at"],
    )
    assert len(chart["price_history"]) == dashboard_build.MAX_PRICE_ROWS
    assert len(chart["short_interest"]) > 50
    assert len(chart["correlation_history"]["DGS10"]) == 90
    bloated = json.loads(raw)
    bloated["tickers"][tickers[0]]["price_history"] = chart["price_history"]
    for ticker in tickers[1:]:
        bloated["tickers"][ticker]["price_history"] = dashboard_build.price_history_for(ticker, prices[ticker])
    bloated_raw = json.dumps(bloated, separators=(",", ":"), default=str).encode()
    assert len(bloated_raw) > dashboard_build.DASHBOARD_RAW_BUDGET_BYTES, len(bloated_raw)


def test_charts_are_written_one_ticker_at_a_time(monkeypatch):
    held: list[str] = []
    written: list[str] = []

    def load(ticker):
        assert held == []
        held.append(ticker)
        return {
            "trend_rows": [{"date": "2026-10-01", "series_id": "DGS10", "value": 4.0, "ticker": ticker}],
            "short_interest_rows": [],
            "options_rows": [],
            "fundamentals": [],
            "price_rows": [{"date": "2026-10-01", "close": 1.0, "ticker": ticker}],
        }

    def write(obj, key, bucket=None, cache_seconds=60):
        ticker = key.removeprefix("serving/chart_data/").removesuffix(".json")
        assert held == [ticker]
        assert obj["ticker"] == ticker
        assert obj["price_history"] == [{"date": "2026-10-01", "close": 1}]
        held.clear()
        written.append(ticker)
        return {"raw_bytes": 1, "gzip_bytes": 1}

    monkeypatch.setattr(lake, "write_json_and_gzip", write)
    dashboard_build.publish_charts_sequentially(["META", "EVGO"], load, "2026-10-11T00:00:00+00:00")
    assert written == ["META", "EVGO"]
    assert held == []


def test_access_denied_or_missing_rates_input_serves_null(monkeypatch):
    warnings = []
    monkeypatch.setattr(observability.logger, "warning", lambda *args, **kwargs: warnings.append(args[0]))

    def denied(prefix, bucket=None, columns=None):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "ListObjectsV2")

    monkeypatch.setattr(lake, "read_parquet_prefix", denied)
    assert dashboard_build.load_rates_block() is None

    def empty_prefix(prefix, bucket=None, columns=None):
        if "DGS10" in prefix:
            return pl.DataFrame({"obs_date": ["2026-10-08"], "value": [4.2], "series_id": ["DGS10"]})
        return pl.DataFrame()

    def missing(key, bucket=None):
        raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")

    monkeypatch.setattr(lake, "read_parquet_prefix", empty_prefix)
    monkeypatch.setattr(lake, "read_json", missing)
    assert dashboard_build.load_rates_block() is None
    assert "rates_input_unavailable" in warnings


def test_absent_fomc_file_still_serves_the_curve_when_macro_is_readable(monkeypatch):
    def read_prefix(prefix, bucket=None, columns=None):
        if prefix.endswith("series_id=DGS10/"):
            return pl.DataFrame({"obs_date": ["2026-09-08", "2026-10-08"], "value": [4.1, 4.2]})
        return pl.DataFrame()

    monkeypatch.setattr(lake, "read_parquet_prefix", read_prefix)
    monkeypatch.setattr(lake, "read_json", lambda key, bucket=None: None)
    rates = dashboard_build.load_rates_block()
    assert rates["fomc"] is None
    assert rates["curve"]["tenors"]


class _Context:
    function_name = "test"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test"
    aws_request_id = "test"


def test_handler_publishes_charts_before_the_next_ticker_when_rates_are_denied(monkeypatch):
    events = []

    def read_prefix(prefix, bucket=None, columns=None):
        if "macro_daily" in str(prefix):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "ListObjectsV2")
        if "serving/trend_metrics/ticker=" in str(prefix):
            events.append(("trend", prefix))
        return pl.DataFrame()

    def read_json(key, bucket=None):
        if str(key).startswith("serving/rates/"):
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return None

    real_write = lake.write_json_and_gzip

    def write(obj, key, bucket=None, cache_seconds=60):
        events.append(("write", key))
        return real_write(obj, key, bucket=bucket, cache_seconds=cache_seconds)

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr(universe, "user_ticker_union", lambda: ["META", "EVGO"])
    monkeypatch.setattr(lake, "read_parquet_prefix", read_prefix)
    monkeypatch.setattr(lake, "read_json", read_json)
    monkeypatch.setattr(lake, "write_json_and_gzip", write)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", client)
        result = dashboard_build.handler({}, _Context())
        dashboard = json.loads(client.get_object(Bucket="lake", Key="serving/dashboard.json")["Body"].read())
    trend_writes = [item for item in events if item[0] == "trend" or str(item[1]).startswith("serving/chart_data/")]
    open_trend = False
    for kind, _key in trend_writes:
        if kind == "trend":
            assert not open_trend
            open_trend = True
        else:
            open_trend = False
    assert result["rates"] is False
    assert dashboard["rates"] is None
    assert "META" in dashboard["tickers"]
    assert "EVGO" in dashboard["tickers"]
    assert any(key == "serving/chart_data/META.json" for kind, key in events if kind == "write")
    assert any(key == "serving/chart_data/EVGO.json" for kind, key in events if kind == "write")


def test_index_proxy_chart_files_carry_full_price_history(monkeypatch):
    """SPY, QQQ, and the other overlay ETFs are collected with the watchlist.

    Their chart documents used to be empty stubs, so the comparison lines had nothing
    to draw. The one-ticker loop must write the same daily history it writes for equities.
    """
    sessions = _weekday_session(80)

    def read_prices(ticker, bucket=None):
        return pl.DataFrame(
            {
                "ticker": [ticker] * len(sessions),
                "date": sessions,
                "close": [100 + offset for offset in range(len(sessions))],
                "volume": [1_000 + offset for offset in range(len(sessions))],
            }
        )

    def read_prefix(prefix, bucket=None, columns=None):
        if "macro_daily" in str(prefix):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "ListObjectsV2")
        return pl.DataFrame()

    def read_json(key, bucket=None):
        if str(key).startswith("serving/rates/"):
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return None

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr(universe, "user_ticker_union", lambda: [])
    monkeypatch.setattr(lake, "read_prices", read_prices)
    monkeypatch.setattr(lake, "read_parquet_prefix", read_prefix)
    monkeypatch.setattr(lake, "read_json", read_json)
    covered = universe.collection_universe([])
    symbols = covered["equities"] + covered["etfs"]
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", client)
        dashboard_build.handler({}, _Context())
        dashboard = json.loads(client.get_object(Bucket="lake", Key="serving/dashboard.json")["Body"].read())
        for symbol in universe.INDEX_PROXIES:
            raw = client.get_object(Bucket="lake", Key=f"serving/chart_data/{symbol}.json")["Body"].read()
            chart = json.loads(raw)
            assert len(raw) > 192
            assert chart["ticker"] == symbol
            assert len(chart["price_history"]) == len(sessions)
            assert chart["price_history"][0]["date"] == sessions[0]
            assert chart["price_history"][-1]["close"] == 100 + len(sessions) - 1
            assert len(dashboard["tickers"][symbol]["price_history"]) == dashboard_build.SUMMARY_PRICE_ROWS
            assert dashboard["tickers"][symbol]["price_history"][-1]["date"] == sessions[-1]
        for symbol in symbols:
            chart = json.loads(
                client.get_object(Bucket="lake", Key=f"serving/chart_data/{symbol}.json")["Body"].read()
            )
            assert len(chart["price_history"]) == len(sessions)
            assert "market_comparison" in chart
            assert symbol not in chart["market_comparison"]["indexes"]
            assert chart["market_comparison"]["window_sessions"] == 90
        tsla = json.loads(client.get_object(Bucket="lake", Key="serving/chart_data/TSLA.json")["Body"].read())
        spy = tsla["market_comparison"]["indexes"]["SPY"]
        assert spy["n"] == len(sessions) - 1
        assert spy["rho_90d"] == pytest.approx(1)
        assert spy["beta_90d"] == pytest.approx(1)
        assert tsla["market_comparison"]["returns"]["5Y"] is None
        assert tsla["market_comparison"]["returns"]["1M"] is not None
        raw_dashboard = client.get_object(Bucket="lake", Key="serving/dashboard.json")["Body"].read()
        assert b"market_comparison" not in raw_dashboard


def _walk(start: float, returns: list[float]) -> list[float]:
    prices = [start]
    for value in returns:
        prices.append(prices[-1] * (1 + value))
    return prices


def _dated(values: list[float], start: str = "2026-01-05") -> list[dict]:
    from datetime import date, timedelta

    cursor = date.fromisoformat(start)
    rows = []
    for value in values:
        while cursor.weekday() >= 5:
            cursor += timedelta(days=1)
        rows.append({"date": cursor.isoformat(), "close": value, "adj_close": value, "ticker": "X"})
        cursor += timedelta(days=1)
    return rows


def test_beta_is_stock_on_index_and_period_returns_follow_the_chart_windows():
    assert dashboard_build.INDEX_PROXIES == universe.INDEX_PROXIES
    moves = [0.01, -0.02, 0.015, 0.005] * 8
    index = _dated(_walk(100, moves))
    stock = _dated(_walk(200, [value * 1.5 for value in moves]))
    link = dashboard_build.beta_rho(stock, index)
    assert link["n"] == 32
    assert link["rho_90d"] == pytest.approx(1)
    assert link["beta_90d"] == pytest.approx(1.5)
    short = dashboard_build.beta_rho(stock[:10], index[:10])
    assert short["rho_90d"] is None and short["n"] == 9
    flat = _dated([50] * 30)
    assert dashboard_build.beta_rho(flat, index[:30])["rho_90d"] is None
    # Dropping the index's first session removes one return date. The rest stay paired.
    gapped = dashboard_build.beta_rho(stock, index[1:])
    assert gapped["n"] == 31
    assert gapped["beta_90d"] == pytest.approx(1.5)
    returns = dashboard_build.period_returns(stock)
    assert returns["1D"] == pytest.approx(stock[-1]["close"] / stock[-2]["close"] - 1)
    assert returns["1M"] == pytest.approx(stock[-1]["close"] / stock[-22]["close"] - 1)
    assert returns["1Y"] is None
    year = [
        {"date": "2025-12-31", "close": 10, "adj_close": 10},
        {"date": "2026-01-02", "close": 12, "adj_close": 12},
        {"date": "2026-01-05", "close": 13, "adj_close": 13},
    ]
    assert dashboard_build.period_returns(year)["YTD"] == pytest.approx(13 / 12 - 1)


def test_market_comparison_stays_on_the_chart_file_and_correlation_history_keeps_six_months():
    from datetime import date, timedelta

    cursor = date(2025, 6, 2)
    days = []
    while len(days) < 200:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    trend_rows = [
        {"ticker": "TSLA", "date": day, "series_id": "DGS10", "value": 4.0, "corr_30d": 0.2, "corr_90d": 0.1}
        for day in days[:200]
    ]
    prices = _dated(_walk(100, [0.001] * 40), start="2026-01-05")
    for row in prices:
        row["ticker"] = "TSLA"
    index = [{**row, "ticker": "SPY"} for row in prices]
    chart = dashboard_build.build_chart_data(
        ticker="TSLA",
        trend_rows=trend_rows,
        short_interest_rows=[],
        options_rows=[],
        fundamentals=[],
        price_rows=prices,
        index_prices={"SPY": index, "TSLA": prices},
        generated_at="2026-10-11T00:00:00+00:00",
    )
    assert len(chart["correlation_history"]["DGS10"]) == dashboard_build.CORRELATION_HISTORY_ROWS
    assert chart["correlation_history"]["DGS10"][-1]["date"] == trend_rows[199]["date"]
    assert chart["correlation_history"]["DGS10"][0]["date"] == trend_rows[200 - 126]["date"]
    assert "SPY" in chart["market_comparison"]["indexes"]
    assert "TSLA" not in chart["market_comparison"]["indexes"]
    bare = dashboard_build.build_chart_data(
        ticker="TSLA", trend_rows=[], short_interest_rows=[], options_rows=[], fundamentals=[], price_rows=prices,
    )
    assert "market_comparison" not in bare
    snapshot = dashboard_build.build_snapshot(
        tickers=["TSLA"], prices={"TSLA": prices}, trends={}, fundamentals=[], status=None,
    )
    assert "market_comparison" not in json.dumps(snapshot)
