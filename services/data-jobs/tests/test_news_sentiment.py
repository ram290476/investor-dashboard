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


# --- every watchlist ticker gets news -------------------------------------------------

from news_sentiment import (  # noqa: E402
    build_aliases,
    keep_company_rows,
    massive_rotation,
    mentions_company,
    rss_feeds,
    short_company_name,
    tag_mentions,
    ticker_rss_query,
    watchlist_names,
)
from universe import watchlist_universe  # noqa: E402

WATCHLIST = ["TSLA", "SPCX", "NVDA", "AMZN", "GOOG", "AAPL", "PLTR", "QS", "BYND", "META", "MRVL"]
NAMES = {
    "TSLA": "Tesla, Inc.",
    "SPCX": None,
    "NVDA": "NVIDIA CORP",
    "AMZN": "AMAZON COM INC",
    "GOOG": "Alphabet Inc.",
    "AAPL": "Apple Inc.",
    "PLTR": "Palantir Technologies Inc.",
    "QS": "QuantumScape Corp",
    "BYND": "Beyond Meat, Inc.",
    "META": "Meta Platforms, Inc.",
    "MRVL": "Marvell Technology, Inc.",
}


def test_short_company_name_drops_corporate_suffixes():
    assert short_company_name("NVIDIA CORP") == "Nvidia"
    assert short_company_name("AMAZON COM INC") == "Amazon"
    assert short_company_name("Alphabet Inc.") == "Alphabet"
    assert short_company_name("Beyond Meat, Inc.") == "Beyond Meat"
    assert short_company_name(None) == ""


def test_watchlist_comes_from_prefs_not_a_hard_coded_list():
    tickers = watchlist_universe(["NVDA", "AAPL", "TSLA", "MRVL"])["tickers"]
    assert tickers == ["TSLA", "SPCX", "NVDA", "AAPL", "MRVL"]
    assert len(watchlist_universe([f"T{i}" for i in range(40)])["tickers"]) == 27  # 2 base + cap 25


def test_rss_feed_per_watchlist_ticker_uses_company_name():
    feeds = rss_feeds(WATCHLIST, NAMES)
    names = [feed[0] for feed in feeds]
    for symbol in WATCHLIST:
        if symbol not in ("TSLA", "SPCX"):
            assert f"ticker-{symbol.lower()}" in names
    nvda = next(feed for feed in feeds if feed[0] == "ticker-nvda")
    assert nvda[1] == '"Nvidia" OR "NVDA stock"' and nvda[2] == ["NVDA"] and nvda[3] == ["company"]
    assert ticker_rss_query("XYZ", None) == '"XYZ stock"'


def test_name_matching_built_from_watchlist_names():
    aliases = build_aliases(NAMES)
    assert mentions_company("Nvidia earnings beat estimates", "NVDA", aliases)
    assert mentions_company("QuantumScape ships samples", "QS", aliases)
    assert not mentions_company("QS rankings released", "QS", aliases)  # two-letter ticker alone is not a match
    assert mentions_company("Palantir wins Army deal", "PLTR", aliases)
    assert mentions_company("SpaceX Starship flies", "SPCX", aliases)


def test_tag_mentions_and_company_filter():
    aliases = build_aliases(NAMES)
    rows = [
        {"title": "Apple and Nvidia lead a tech rally", "summary": "", "tickers": ["AAPL"]},
        {"title": "Weather update", "summary": "", "tickers": ["AAPL"]},
    ]
    tagged = tag_mentions(rows, aliases)
    assert tagged[0]["tickers"] == ["AAPL", "NVDA"]
    assert keep_company_rows(rows, "AAPL", aliases) == [rows[0]]


def test_massive_rotation_respects_rate_limit_and_covers_all():
    seen = set()
    for hour in range(len(WATCHLIST)):
        batch = massive_rotation(WATCHLIST, hour)
        assert len(batch) <= 4
        seen.update(batch)
    assert seen == set(WATCHLIST)
    assert massive_rotation([], 3) == []


def test_watchlist_names_reads_serving_filings():
    docs = {"serving/filings/NVDA.json": {"entity_name": "NVIDIA CORP"}}

    def fake_read(key):
        if key == "serving/filings/BAD.json":
            raise RuntimeError("denied")
        return docs.get(key)

    assert watchlist_names(["NVDA", "AAPL", "BAD"], fake_read) == {"NVDA": "NVIDIA CORP", "AAPL": None, "BAD": None}
