"""Build the compact, read-only serving document consumed by the dashboard API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

MAX_PRICE_ROWS = 1260
SCHEMA_VERSION = 2
HEADLINE_LIMIT = 10
ET = ZoneInfo("America/New_York")


def build_news(ticker: str, daily_rows: list[dict], articles: list[dict], now: datetime) -> dict:
    """Last 48 hours of headlines and the 7-day mean of daily sentiment."""
    from news_sentiment import rolling_mean

    cutoff = now.astimezone(UTC) - timedelta(hours=48)
    headlines = []
    seen: set[str] = set()
    ordered = sorted(articles, key=lambda row: str(row.get("published_at") or ""), reverse=True)
    for row in ordered:
        symbols = [str(symbol).upper() for symbol in row.get("tickers") or []]
        if ticker not in symbols:
            continue
        published = str(row.get("published_at") or "")
        try:
            stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if stamp.astimezone(UTC) < cutoff:
            continue
        digest = str(row.get("url_hash") or row.get("url") or "")
        if not digest or digest in seen:
            continue
        seen.add(digest)
        headlines.append(
            {
                "title": row.get("title") or "",
                "url": row.get("url") or "",
                "publisher": row.get("publisher") or "",
                "published_at": published,
                "label": row.get("sentiment_label") or "neutral",
            }
        )
        if len(headlines) == HEADLINE_LIMIT:
            break
    return {
        "as_of": headlines[0]["published_at"] if headlines else None,
        "sentiment_7d": rolling_mean(daily_rows, now.astimezone(UTC).date()),
        "headlines": headlines,
    }


def _as_of(generated_at: str | None) -> datetime:
    if not generated_at:
        return datetime.now(UTC)
    stamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def build_filings(rows: list[dict]) -> list[dict]:
    ordered = sorted(rows, key=lambda row: str(row.get("filed_at") or ""), reverse=True)
    latest = []
    seen: set[str] = set()
    for row in ordered:
        accession = str(row.get("accession_no") or "")
        if accession and accession in seen:
            continue
        if accession:
            seen.add(accession)
        latest.append(
            {
                "form": row.get("form"),
                "filed_at": row.get("filed_at"),
                "title": row.get("title") or row.get("form"),
                "url": row.get("primary_doc_url"),
                "class": row.get("filing_class") or row.get("class"),
            }
        )
        if len(latest) == 10:
            break
    return latest


def build_insider(rows: list[dict], as_of: date) -> dict:
    from regulatory_feeds import insider_flows

    return insider_flows(rows, as_of)


def build_events(rows: list[dict], as_of: date) -> list[dict]:
    cutoff = (as_of - timedelta(days=14)).isoformat()
    chosen = []
    seen: set[str] = set()
    ordered = sorted(rows, key=lambda row: str(row.get("event_ts") or ""), reverse=True)
    for row in ordered:
        day = str(row.get("event_ts") or "")[:10]
        if day and day < cutoff:
            continue
        key = str(row.get("event_id") or row.get("source_url") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        chosen.append(
            {
                "event_ts": row.get("event_ts"),
                "type": row.get("type"),
                "title": row.get("title"),
                "source_url": row.get("source_url"),
                "tickers": row.get("tickers") or [],
            }
        )
        if len(chosen) == 20:
            break
    return chosen


def _session_date(ts: str) -> str:
    stamp = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(ET).date().isoformat()


def build_intraday(rows: list[dict], daily_history: list[dict]) -> dict | None:
    """Today's hourly bars plus the change versus the prior daily close. Does not touch prices_daily."""
    parsed = []
    for row in rows:
        ts = row.get("ts_utc") or row.get("ts")
        close = row.get("close")
        if not ts or not isinstance(close, (int, float)):
            continue
        parsed.append({"ts": str(ts), "close": float(close), "volume": row.get("volume")})
    if not parsed:
        return None
    parsed.sort(key=lambda row: row["ts"])
    session = _session_date(parsed[-1]["ts"])
    today = [row for row in parsed if _session_date(row["ts"]) == session]
    prior = None
    for day in daily_history:
        if str(day.get("date", "")) < session and isinstance(day.get("close"), (int, float)):
            prior = float(day["close"])
    last = today[-1]["close"]
    return {
        "as_of": today[-1]["ts"],
        "last": last,
        "change_pct": None if not prior else last / prior - 1,
        "bars": today,
    }


def latest_short_interest(rows: list[dict]) -> dict[str, dict]:
    """Latest FINRA settlement per ticker. Missing rows stay absent."""
    latest: dict[str, dict] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        day = str(row.get("settlement_date") or "")
        if not ticker or not day:
            continue
        if ticker in latest and day < latest[ticker]["settlement_date"]:
            continue
        shares = row.get("short_interest", row.get("shares_short"))
        previous = row.get("previous_short_interest")
        pct = None
        if isinstance(shares, (int, float)) and isinstance(previous, (int, float)) and previous:
            pct = (float(shares) - float(previous)) / float(previous)
        latest[ticker] = {
            "settlement_date": day,
            "shares_short": shares,
            "days_to_cover": row.get("days_to_cover"),
            "pct_change": pct,
        }
    return latest


def build_snapshot(
    tickers: list[str],
    prices: dict[str, list[dict]],
    trends: dict[str, dict | None],
    fundamentals: list[dict],
    status: dict | None,
    generated_at: str | None = None,
    news: dict[str, dict] | None = None,
    hourly: dict[str, list[dict]] | None = None,
    short_interest: dict[str, list[dict]] | None = None,
    filings: dict[str, list[dict]] | None = None,
    events: list[dict] | None = None,
) -> dict:
    """Create a deterministic API document; absent source data remains explicitly unavailable."""
    clock = _as_of(generated_at)
    instruments = {}
    for ticker in tickers:
        unique: dict[str, dict] = {}
        for row in prices.get(ticker, []):
            day = str(row.get("date", ""))
            close = row.get("close")
            if day and isinstance(close, (int, float)):
                unique[day] = {
                    "date": day,
                    "close": float(close),  # split-adjusted
                    "close_raw": row.get("close_raw"),  # actual traded close; null on older rows
                    "adj_close": row.get("adj_close"),  # split- and dividend-adjusted; charts use this
                    "volume": row.get("volume"),
                }
        history = [unique[day] for day in sorted(unique)][-MAX_PRICE_ROWS:]
        bundle = None if news is None else news.get(ticker) or {}
        filing_rows = None if filings is None else filings.get(ticker) or []
        interest = latest_short_interest((short_interest or {}).get(ticker, []))
        instruments[ticker] = {
            "ticker": ticker,
            "price_history": history,
            "trend": trends.get(ticker),
            "news": None
            if bundle is None
            else build_news(ticker, bundle.get("daily") or [], bundle.get("articles") or [], clock),
            "intraday": build_intraday((hourly or {}).get(ticker, []), history),
            "short_interest": interest.get(ticker),
            "filings": None if filing_rows is None else build_filings(filing_rows),
            "insider_30d": None if filing_rows is None else build_insider(filing_rows, clock.date()),
            "price_status": "available" if history else "unavailable",
            "price_as_of": history[-1]["date"] if history else None,
        }

    normalized_fundamentals = [
        row for row in fundamentals if row.get("ticker") in instruments and row.get("metric")
    ]
    available = any(item["price_status"] == "available" for item in instruments.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "data_status": "available" if available else "unavailable",
        "tickers": instruments,
        "fundamentals": normalized_fundamentals,
        "events": None if events is None else build_events(events, clock.date()),
        "status": status,
    }


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from lake import read_json, read_parquet_prefix, read_prices, write_json
    from observability import job_handler, logger
    from universe import collection_universe, user_ticker_union

    @job_handler("DASHBOARD", emit_event=False)
    def run(event, context):
        tickers = collection_universe(user_ticker_union())["equities"]
        price_data: dict[str, list[dict]] = {}
        trend_data: dict[str, dict | None] = {}
        article_frame = read_parquet_prefix("curated/news_articles/")
        daily_frame = read_parquet_prefix("curated/news_daily/")
        article_rows = article_frame.to_dicts() if not article_frame.is_empty() else []
        daily_rows = daily_frame.to_dicts() if not daily_frame.is_empty() else []
        news_data = {
            ticker: {"daily": [row for row in daily_rows if row.get("ticker") == ticker], "articles": article_rows}
            for ticker in tickers
        }
        filing_frame = read_parquet_prefix("curated/filings/")
        event_frame = read_parquet_prefix("curated/events/")
        filing_rows = filing_frame.to_dicts() if not filing_frame.is_empty() else []
        event_rows = event_frame.to_dicts() if not event_frame.is_empty() else []
        filings_by_ticker: dict[str, list[dict]] = {ticker: [] for ticker in tickers}
        for row in filing_rows:
            symbol = str(row.get("ticker") or "").upper()
            if symbol in filings_by_ticker:
                filings_by_ticker[symbol].append(row)
        hourly_data: dict[str, list[dict]] = {}
        for ticker in tickers:
            frame = read_prices(ticker)  # ~1 GET per year of history (yearly partitions)
            price_data[ticker] = frame.to_dicts() if not frame.is_empty() else []
            trend_data[ticker] = read_json(f"serving/trend_metrics/latest/{ticker}.json")
            hourly = read_parquet_prefix(f"curated/prices_hourly/ticker={ticker}/")
            hourly_data[ticker] = hourly.to_dicts() if not hourly.is_empty() else []
        short_frame = read_parquet_prefix("curated/short_interest/")
        short_rows = short_frame.to_dicts() if not short_frame.is_empty() else []
        short_by_ticker: dict[str, list[dict]] = {}
        for row in short_rows:
            short_by_ticker.setdefault(str(row.get("ticker") or "").upper(), []).append(row)

        fundamental_doc = read_json("serving/fundamentals_quarterly.json") or {}
        status = read_json("serving/status.json")
        snapshot = build_snapshot(
            tickers=tickers,
            prices=price_data,
            trends=trend_data,
            fundamentals=fundamental_doc.get("rows", []),
            status=status,
            news=news_data,
            hourly=hourly_data,
            short_interest=short_by_ticker,
            filings=filings_by_ticker,
            events=event_rows,
        )
        write_json(snapshot, "serving/dashboard.json", cache_seconds=30)
        logger.info(
            "dashboard_snapshot_published",
            extra={
                "tickers": len(tickers),
                "tickers_with_prices": sum(bool(row["price_history"]) for row in snapshot["tickers"].values()),
                "fundamentals": len(snapshot["fundamentals"]),
                "generated_at": snapshot["generated_at"],
            },
        )
        return {
            "status": snapshot["data_status"],
            "tickers": len(tickers),
            "tickers_with_prices": sum(bool(row["price_history"]) for row in snapshot["tickers"].values()),
        }

    return run(event, context)
