from datetime import UTC, datetime

from news_sentiment import (
    JOB_ID,
    apply_sentiment,
    canonical_url,
    catch_up_since,
    curated_row,
    dedupe_articles,
    headline_relevance,
    label_for,
    lexicon_score,
    next_av_ticker,
    parse_rss,
    rollup_daily,
    strip_publisher_suffix,
    url_hash,
)
from observability import emit_job_finished, job_handler
from universe import SENTIMENT_CALLS_PER_DAY, sentiment_plan


def test_canonical_url_strips_tracking_and_dedupes():
    left = "https://WWW.Example.com/story/?utm_source=wire&id=1"
    right = "https://example.com/story"
    assert canonical_url(left) == canonical_url(right)
    assert url_hash(left) == url_hash(right)
    rows = dedupe_articles(
        [
            {"url": left, "title": "first"},
            {"url": right, "title": "second"},
        ]
    )
    assert len(rows) == 1
    assert rows[0]["title"] == "first"
    assert rows[0]["url"] == "https://example.com/story"


def test_alpha_vantage_rotation_never_exceeds_16_calls_a_day():
    plan = sentiment_plan(["TSLA", "SPCX", "NVDA", "AMD"], datetime(2026, 10, 6, tzinfo=UTC).date())
    assert len(plan) == SENTIMENT_CALLS_PER_DAY == 16
    state: dict = {}
    chosen = []
    noon = datetime(2026, 10, 6, 16, 15, tzinfo=UTC)  # 12:15 EDT, inside the window
    for _ in range(20):
        ticker, state = next_av_ticker(state, "2026-10-06", plan, noon)
        if ticker:
            chosen.append(ticker)
    assert len(chosen) == 16
    assert state["av_index"] == 16
    assert next_av_ticker(state, "2026-10-06", plan, noon)[0] is None
    early = datetime(2026, 10, 7, 9, 15, tzinfo=UTC)  # 05:15 EDT, before 06:15
    fresh, reset = next_av_ticker({"av_day": "2026-10-06", "av_index": 16}, "2026-10-07", plan, early)
    assert fresh is None and reset["av_index"] == 0


def test_rss_strips_the_publisher_suffix_and_drops_the_description_snippet():
    xml = """<?xml version="1.0"?>
    <rss><channel><item>
      <title>Tesla approval beats estimates - Reuters</title>
      <link>https://example.com/story?utm_source=gn</link>
      <pubDate>Tue, 06 Oct 2026 16:30:00 GMT</pubDate>
      <source>Reuters</source>
      <description>&lt;p&gt;A long description&lt;/p&gt;</description>
    </item></channel></rss>"""
    rows = parse_rss(xml, "google-tesla", ["TSLA"], ["tesla"], "2026-10-06T18:00:00+00:00")
    assert rows[0]["title"] == "Tesla approval beats estimates"
    assert rows[0]["publisher"] == "Reuters"
    assert curated_row(rows[0])["snippet"] == ""
    assert strip_publisher_suffix("SpaceX launch - NASA", "nasa") == "SpaceX launch"
    kept = curated_row({"source": "finnhub", "summary": "<p>Robotaxi <b>approval</b></p>", "title": "Tesla"})
    assert kept["snippet"] == "Robotaxi approval"
    sector = {"title": "Uber stake in Verne", "topics": ["robotaxi"], "summary": "A European deal"}
    assert headline_relevance(sector, "TSLA") == "sector"
    named = {"title": "Tesla Cybercab", "topics": ["robotaxi"], "summary": ""}
    assert headline_relevance(named, "TSLA") == "ticker"
    assert headline_relevance({"title": "Quarterly update", "topics": ["company"]}, "TSLA") == "ticker"


def test_scoring_thresholds_prefer_the_provider_score():
    assert label_for(-0.15) == "neutral"
    assert label_for(-0.16) == "bearish"
    assert label_for(0.15) == "neutral"
    assert label_for(0.16) == "bullish"
    scored = apply_sentiment({"title": "shares plunge", "summary": "recall"}, 0.4)
    assert scored["provider_sentiment"] == 0.4
    assert scored["sentiment_label"] == "bullish"
    local = apply_sentiment({"title": "shares plunge after recall", "summary": ""}, None)
    assert local["provider_sentiment"] is None
    assert local["sentiment_score"] == lexicon_score("shares plunge after recall")
    assert local["sentiment_label"] == "bearish"


def test_daily_rollup_counts_and_weights_sentiment():
    articles = [
        {
            "published_at": "2026-10-06T15:00:00+00:00",
            "tickers": ["TSLA"],
            "sentiment_score": 0.4,
            "sentiment_label": "bullish",
            "relevance": 1.0,
        },
        {
            "published_at": "2026-10-06T16:00:00+00:00",
            "tickers": ["TSLA", "SPCX"],
            "sentiment_score": -0.2,
            "sentiment_label": "bearish",
            "relevance": 3.0,
        },
    ]
    rows = { (row["ticker"], row["date"]): row for row in rollup_daily(articles) }
    tesla = rows[("TSLA", "2026-10-06")]
    assert tesla["articles"] == 2
    assert tesla["bullish"] == 1 and tesla["bearish"] == 1
    assert tesla["mean_sentiment"] == 0.1
    assert tesla["weighted_sentiment"] == (0.4 * 1 + -0.2 * 3) / 4
    assert rows[("SPCX", "2026-10-06")]["articles"] == 1


def test_weekend_catch_up_resumes_at_the_watermark():
    monday = datetime(2026, 10, 5, 14, 15, tzinfo=UTC)
    assert catch_up_since(None, monday) == "2026-09-28"
    friday = "2026-10-02T21:15:00+00:00"
    assert catch_up_since(friday, monday) == friday


def test_handler_emits_job_h2(monkeypatch):
    emitted = {}

    def capture(job_id, run_id, outcome, detail=None):
        emitted["job"] = job_id

    monkeypatch.setattr("observability.emit_job_finished", capture)

    @job_handler(JOB_ID)
    def run(event, context):
        return {"status": "success"}

    class Context:
        function_name = "news-sentiment"
        memory_limit_in_mb = 512
        invoked_function_arn = "arn:aws:lambda:us-east-1:1:function:news-sentiment"
        aws_request_id = "req"

    assert run({}, Context())["status"] == "success"
    assert emitted["job"] == "H2"
    assert emit_job_finished.__name__ == "emit_job_finished"
