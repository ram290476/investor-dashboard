"""H2: company news and a bounded sentiment score for the watchlist.

Finnhub company-news covers TSLA and SPCX. Alpha Vantage NEWS_SENTIMENT is the only
provider score, one ticker per run, and never more than 16 calls in a day. Massive is
the backup when Finnhub fails. RSS fills the gaps without a key.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlunparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import polars as pl

JOB_ID = "H2"
ET = ZoneInfo("America/New_York")
BACKFILL_DAYS = 7
AV_CALLS_PER_DAY = 16
AV_OPEN = (6, 15)
AV_CLOSE = (21, 15)
BULLISH_ABOVE = 0.15
BEARISH_BELOW = -0.15
STATE_KEY = "curated/news_articles/_state.json"
COMPANY_SYMBOLS = ("TSLA", "SPCX")

_POSITIVE = frozenset(
    {
        "beat",
        "beats",
        "surge",
        "surged",
        "rally",
        "upgrade",
        "record",
        "growth",
        "win",
        "wins",
        "soar",
        "approval",
        "bullish",
    }
)
_NEGATIVE = frozenset(
    {
        "miss",
        "misses",
        "plunge",
        "plunged",
        "drop",
        "dropped",
        "downgrade",
        "lawsuit",
        "recall",
        "bearish",
        "crash",
        "probe",
        "decline",
        "sanctions",
        "tariff",
    }
)
_RSS = (
    ("tesla", "Tesla", ["TSLA"], ["tesla"]),
    ("spacex", "SpaceX OR Starlink OR Starship", ["SPCX"], ["launch"]),
    ("robotaxi", "Robotaxi OR Cybercab", ["TSLA"], ["robotaxi"]),
    ("geopolitics", "tariff OR sanctions OR Taiwan", [], ["geopolitics"]),
)
# RSS queries that tag a ticker even when the story is about the topic, not the company.
TOPIC_QUERY_TOPICS = frozenset({"robotaxi"})
COMPANY_TOPICS = frozenset({"tesla", "launch", "company"})
COMPANY_ALIASES = {
    "TSLA": ("tesla", "tsla"),
    "SPCX": ("spacex", "starlink", "starship", "spcx"),
}
_TAG = re.compile(r"<[^>]+>")


def is_market_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    import holidays

    return day not in holidays.financial_holidays("NYSE", years=[day.year])


def canonical_url(url: str) -> str:
    """Strip the query string, including utm parameters, so the same story hashes once."""
    parsed = urlparse((url or "").strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower() or "https", host, path, "", "", ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()


def dedupe_articles(rows: list[dict]) -> list[dict]:
    """One row per canonical URL. The first copy wins so a later source cannot overwrite it."""
    seen: set[str] = set()
    kept = []
    for row in rows:
        digest = row.get("url_hash") or url_hash(row.get("url") or "")
        if not digest or digest in seen:
            continue
        seen.add(digest)
        kept.append({**row, "url_hash": digest, "url": canonical_url(row.get("url") or "")})
    return kept


def plain_text(value: object) -> str:
    return " ".join(_TAG.sub(" ", str(value or "")).split())


def strip_publisher_suffix(title: str, publisher: str) -> str:
    """Google News RSS titles end in ' - {publisher}'. The publisher is stored on its own."""
    text = plain_text(title)
    source = plain_text(publisher)
    suffix = f" - {source}"
    if source and text.lower().endswith(suffix.lower()):
        return text[: -len(suffix)].rstrip()
    return text


def mentions_company(text: str, ticker: str) -> bool:
    haystack = f" {re.sub(r'[^a-z0-9]+', ' ', plain_text(text).lower())} "
    aliases = COMPANY_ALIASES.get(str(ticker or "").upper(), (str(ticker or "").lower(),))
    return any(alias and f" {alias} " in haystack for alias in aliases)


def headline_relevance(row: dict, ticker: str) -> str:
    """Topic-only queries keep the ticker tag only when the company is actually mentioned."""
    topics = {str(topic).lower() for topic in (row.get("topics") or [])}
    topic_only = bool(topics & TOPIC_QUERY_TOPICS) and not bool(topics & COMPANY_TOPICS)
    if not topic_only:
        return "ticker"
    blob = " ".join(str(row.get(key) or "") for key in ("title", "snippet", "summary"))
    return "ticker" if mentions_company(blob, ticker) else "sector"


def label_for(score: float | None) -> str:
    if score is None:
        return "neutral"
    if score < BEARISH_BELOW:
        return "bearish"
    if score > BULLISH_ABOVE:
        return "bullish"
    return "neutral"


def lexicon_score(text: str) -> float:
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words:
        return 0.0
    positive = sum(word in _POSITIVE for word in words)
    negative = sum(word in _NEGATIVE for word in words)
    if positive + negative == 0:
        return 0.0
    return max(-1.0, min(1.0, (positive - negative) / (positive + negative)))


def apply_sentiment(row: dict, provider_score: float | None) -> dict:
    """Prefer Alpha Vantage's ticker score. Otherwise score the title and summary locally."""
    if isinstance(provider_score, (int, float)):
        score = max(-1.0, min(1.0, float(provider_score)))
        provider = score
    else:
        provider = None
        score = lexicon_score(f"{row.get('title') or ''} {row.get('summary') or ''}")
    return {**row, "provider_sentiment": provider, "sentiment_score": score, "sentiment_label": label_for(score)}


def in_av_window(now: datetime) -> bool:
    local = now.astimezone(ET)
    stamp = (local.hour, local.minute)
    return AV_OPEN <= stamp <= AV_CLOSE


def next_av_ticker(state: dict, day: str, plan: list[str], now: datetime) -> tuple[str | None, dict]:
    """One ticker from today's plan. The index persists so 16 calls a day holds across runs."""
    updated = {**state, "av_index": int(state.get("av_index") or 0)}
    if updated.get("av_day") != day:
        updated["av_day"] = day
        updated["av_index"] = 0
    index = updated["av_index"]
    if not in_av_window(now) or index >= AV_CALLS_PER_DAY or index >= len(plan):
        return None, updated
    updated["av_index"] = index + 1
    return plan[index], updated


def catch_up_since(watermark: str | None, now: datetime) -> str:
    """First deploy looks back 7 days. Later runs, including Monday after a gap, resume at the watermark."""
    if not watermark:
        return (now.astimezone(ET).date() - timedelta(days=BACKFILL_DAYS)).isoformat()
    return watermark


def _iso(stamp: datetime) -> str:
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).isoformat(timespec="seconds")


def _blank(
    url: str,
    title: str,
    source: str,
    publisher: str,
    published_at: str,
    tickers: list[str],
    topics: list[str],
) -> dict:
    return {
        "url_hash": url_hash(url),
        "url": canonical_url(url),
        "title": title or "",
        "source": source,
        "publisher": publisher or source,
        "published_at": published_at,
        "tickers": [ticker.upper() for ticker in tickers if ticker],
        "topics": topics,
        "summary": "",
        "relevance": 1.0,
        "provider_sentiment": None,
        "sentiment_score": 0.0,
        "sentiment_label": "neutral",
    }


def parse_finnhub(payload: list | dict, symbol: str, ingested_at: str) -> list[dict]:
    rows = payload if isinstance(payload, list) else payload.get("news") or []
    articles = []
    for item in rows:
        url = item.get("url") or ""
        if not url:
            continue
        published = datetime.fromtimestamp(int(item.get("datetime") or 0), UTC)
        row = _blank(
            url,
            item.get("headline") or "",
            "finnhub",
            item.get("source") or "finnhub",
            _iso(published),
            [symbol],
            ["company"],
        )
        row["summary"] = item.get("summary") or ""
        row["ingested_at"] = ingested_at
        articles.append(apply_sentiment(row, None))
    return articles


def parse_alpha_vantage(payload: dict, ingested_at: str) -> list[dict]:
    articles = []
    for item in payload.get("feed") or []:
        url = item.get("url") or ""
        if not url:
            continue
        raw_time = str(item.get("time_published") or "")
        try:
            published = datetime.strptime(raw_time, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            published = datetime.now(UTC)
        sentiments = item.get("ticker_sentiment") or []
        tickers = [row.get("ticker") for row in sentiments if row.get("ticker")]
        score = None
        relevance = None
        if sentiments:
            raw_score = sentiments[0].get("ticker_sentiment_score")
            raw_relevance = sentiments[0].get("relevance_score")
            if raw_score not in (None, ""):
                score = float(raw_score)
            if raw_relevance not in (None, ""):
                relevance = float(raw_relevance)
        topics = [topic.get("topic") for topic in item.get("topics") or [] if topic.get("topic")]
        row = _blank(
            url,
            item.get("title") or "",
            "alpha-vantage",
            item.get("source") or "alpha-vantage",
            _iso(published),
            tickers,
            topics,
        )
        row["summary"] = item.get("summary") or ""
        row["relevance"] = 1.0 if relevance is None else relevance
        row["ingested_at"] = ingested_at
        articles.append(apply_sentiment(row, score))
    return articles


def parse_massive(payload: dict, ingested_at: str) -> list[dict]:
    articles = []
    for item in payload.get("results") or []:
        url = item.get("article_url") or item.get("url") or ""
        if not url:
            continue
        published = datetime.fromisoformat(str(item.get("published_utc") or ingested_at).replace("Z", "+00:00"))
        publisher = (item.get("publisher") or {}).get("name") or "massive"
        row = _blank(
            url,
            item.get("title") or "",
            "massive",
            publisher,
            _iso(published),
            item.get("tickers") or [],
            item.get("keywords") or [],
        )
        row["summary"] = item.get("description") or ""
        row["ingested_at"] = ingested_at
        articles.append(apply_sentiment(row, None))
    return articles


def parse_rss(xml_text: str, source: str, tickers: list[str], topics: list[str], ingested_at: str) -> list[dict]:
    if not xml_text:
        return []
    root = ElementTree.fromstring(xml_text)
    articles = []
    for item in root.iter("item"):
        link = item.find("link")
        href = link.get("href") if link is not None else None
        text = link.text if link is not None else ""
        url = (href or text or "").strip()
        if not url:
            continue
        pub = item.findtext("pubDate") or ""
        try:
            published = parsedate_to_datetime(pub)
        except (TypeError, ValueError):
            published = datetime.now(UTC)
        source_name = item.findtext("source") or source
        title = strip_publisher_suffix(item.findtext("title") or "", source_name)
        row = _blank(url, title, source, source_name, _iso(published), tickers, topics)
        row["summary"] = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        row["ingested_at"] = ingested_at
        articles.append(apply_sentiment(row, None))
    return articles


def rollup_daily(articles: list[dict]) -> list[dict]:
    """Per ticker and day: counts, mean sentiment, and a relevance-weighted sentiment."""
    buckets: dict[tuple[str, str], list[dict]] = {}
    for article in articles:
        day = str(article.get("published_at") or "")[:10]
        if len(day) != 10:
            continue
        for ticker in article.get("tickers") or []:
            buckets.setdefault((str(ticker).upper(), day), []).append(article)
    rows = []
    for (ticker, day), group in sorted(buckets.items()):
        scores = [
            float(row["sentiment_score"])
            for row in group
            if isinstance(row.get("sentiment_score"), (int, float))
        ]
        weighted_pairs = []
        for row in group:
            if not isinstance(row.get("sentiment_score"), (int, float)):
                continue
            relevance = row.get("relevance")
            weight = float(relevance) if isinstance(relevance, (int, float)) and relevance else 1.0
            weighted_pairs.append((float(row["sentiment_score"]), weight))
        weighted = None
        if weighted_pairs:
            denom = sum(weight for _, weight in weighted_pairs)
            weighted = sum(score * weight for score, weight in weighted_pairs) / denom if denom else None
        rows.append(
            {
                "ticker": ticker,
                "date": day,
                "articles": len(group),
                "mean_sentiment": (sum(scores) / len(scores)) if scores else None,
                "weighted_sentiment": weighted,
                "bullish": sum(row.get("sentiment_label") == "bullish" for row in group),
                "bearish": sum(row.get("sentiment_label") == "bearish" for row in group),
            }
        )
    return rows


def rolling_mean(daily_rows: list[dict], as_of: date, days: int = 7) -> float | None:
    start = (as_of - timedelta(days=days - 1)).isoformat()
    end = as_of.isoformat()
    window = [
        float(row["mean_sentiment"])
        for row in daily_rows
        if start <= str(row.get("date")) <= end and isinstance(row.get("mean_sentiment"), (int, float))
    ]
    if not window:
        return None
    return sum(window) / len(window)


def curated_row(row: dict) -> dict:
    keys = (
        "url_hash",
        "url",
        "title",
        "source",
        "publisher",
        "published_at",
        "tickers",
        "topics",
        "provider_sentiment",
        "sentiment_score",
        "sentiment_label",
        "relevance",
        "ingested_at",
    )
    curated = {key: row.get(key) for key in keys}
    source = str(row.get("source") or "")
    # RSS descriptions repeat the headline. Provider summaries are the tooltip snippet.
    curated["snippet"] = "" if source.startswith("google-") else plain_text(row.get("summary") or "")
    return curated


def partition_articles(rows: list[dict], run_id: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        day = str(row.get("published_at") or "")[:10]
        if len(day) == 10:
            grouped.setdefault(day, []).append(row)
    return {day: rows_for_day for day, rows_for_day in grouped.items() if rows_for_day and run_id}


def article_key(day: str, run_id: str) -> str:
    return f"curated/news_articles/date={day}/{run_id}.parquet"


def raw_key(source: str, day: date, run_id: str) -> str:
    return f"raw/news/{source}/date={day.isoformat()}/{run_id}.json"


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from api_keys import api_key
    from http_client import get_client, request_with_retry
    from lake import read_json, read_parquet_prefix, upsert_parquet, write_json, write_parquet
    from observability import job_handler, logger, source_run
    from universe import collection_universe, sentiment_plan, user_ticker_union

    @job_handler(JOB_ID)
    def run(event, context):
        now = datetime.now(ET)
        if not is_market_day(now.date()):
            logger.info("market_calendar_skip", extra={"job": JOB_ID, "reason": "NYSE holiday or weekend"})
            return {"status": "skipped", "reason": "NYSE holiday or weekend", "date": now.date().isoformat()}

        ingested_at = datetime.now(UTC).isoformat(timespec="seconds")
        run_id = ingested_at.replace(":", "")
        state = read_json(STATE_KEY) or {}
        since = catch_up_since(state.get("watermark"), now)
        since_day = since[:10]
        today = now.date().isoformat()
        equities = collection_universe(user_ticker_union())["equities"]
        plan = sentiment_plan(equities, now.date())
        ticker, state = next_av_ticker(state, today, plan, now)
        collected: list[dict] = []
        raw: dict[str, object] = {}

        with get_client() as http:
            finnhub_failed = False
            for symbol in COMPANY_SYMBOLS:
                with source_run("DS-41") as record:
                    payload = request_with_retry(
                        http,
                        "GET",
                        "https://finnhub.io/api/v1/company-news",
                        params={"symbol": symbol, "from": since_day, "to": today, "token": api_key("finnhub")},
                    ).json()
                    rows = parse_finnhub(payload, symbol, ingested_at)
                    collected.extend(rows)
                    raw[f"finnhub-{symbol}"] = payload
                    record["rows"] = len(rows)
                if record["outcome"] == "failure":
                    finnhub_failed = True
            if finnhub_failed:
                for symbol in COMPANY_SYMBOLS:
                    with source_run("DS-42") as record:
                        payload = request_with_retry(
                            http,
                            "GET",
                            "https://api.massive.com/v2/reference/news",
                            params={"ticker": symbol, "limit": 20, "apiKey": api_key("massive")},
                        ).json()
                        rows = parse_massive(payload, ingested_at)
                        collected.extend(rows)
                        raw[f"massive-{symbol}"] = payload
                        record["rows"] = len(rows)
            if ticker:
                with source_run("DS-40") as record:
                    payload = request_with_retry(
                        http,
                        "GET",
                        "https://www.alphavantage.co/query",
                        params={"function": "NEWS_SENTIMENT", "tickers": ticker, "apikey": api_key("alpha-vantage")},
                    ).json()
                    rows = parse_alpha_vantage(payload, ingested_at)
                    collected.extend(rows)
                    raw["alpha-vantage"] = payload
                    record["rows"] = len(rows)
            else:
                state["av_index"] = int(state.get("av_index") or 0)
            for name, query, symbols, topics in _RSS:
                with source_run(f"rss-{name}") as record:
                    payload = request_with_retry(
                        http,
                        "GET",
                        "https://news.google.com/rss/search",
                        params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                    ).text
                    rows = parse_rss(payload, f"google-{name}", symbols, topics, ingested_at)
                    collected.extend(rows)
                    raw[f"rss-{name}"] = payload[:2000]
                    record["rows"] = len(rows)
            with source_run("spaceflight") as record:
                payload = request_with_retry(
                    http,
                    "GET",
                    "https://api.spaceflightnewsapi.net/v4/articles/",
                    params={"limit": 10, "ordering": "-published_at"},
                ).json()
                rows = []
                for item in payload.get("results") or []:
                    parsed = parse_massive(
                        {
                            "results": [
                                {
                                    "title": item.get("title"),
                                    "article_url": item.get("url"),
                                    "published_utc": item.get("published_at"),
                                    "publisher": {"name": item.get("news_site") or "spaceflight"},
                                    "description": item.get("summary"),
                                    "tickers": ["SPCX"],
                                    "keywords": ["launch"],
                                }
                            ]
                        },
                        ingested_at,
                    )
                    for row in parsed:
                        row["source"] = "spaceflight"
                    rows.extend(parsed)
                collected.extend(rows)
                raw["spaceflight"] = payload
                record["rows"] = len(rows)

        fresh = dedupe_articles(collected)
        existing = read_parquet_prefix("curated/news_articles/")
        seen: set[str] = set()
        if not existing.is_empty() and "url_hash" in existing.columns:
            seen = set(existing["url_hash"].to_list())
        novel = [curated_row(row) for row in fresh if row["url_hash"] not in seen]
        for source, payload in raw.items():
            write_json({"payload": payload}, raw_key(source, now.date(), run_id))
        stored = 0
        for day, rows_for_day in partition_articles(novel, run_id).items():
            frame = pl.DataFrame(rows_for_day)
            write_parquet(frame, article_key(day, run_id))
            stored += frame.height
        combined = existing.to_dicts() if not existing.is_empty() else []
        rollup = pl.DataFrame(rollup_daily([*combined, *novel]))
        if not rollup.is_empty():
            for (ticker_name, year), part in (
                rollup.with_columns(pl.col("date").str.slice(0, 4).alias("_year"))
                .partition_by(["ticker", "_year"], as_dict=True)
                .items()
            ):
                key = f"curated/news_daily/ticker={ticker_name}/year={int(year)}/news.parquet"
                upsert_parquet(part.drop("_year"), key)
        state["watermark"] = ingested_at
        write_json(state, STATE_KEY)
        return {"status": "success", "rows_stored": stored, "av_index": state.get("av_index"), "date": today}

    return run(event, context)
