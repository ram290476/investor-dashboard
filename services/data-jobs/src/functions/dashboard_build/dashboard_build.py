"""Build the compact, read-only serving document consumed by the dashboard API."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from market_session import market_block
from yield_curve import CURVE_SERIES_IDS, build_rates

MAX_PRICE_ROWS = 1260
SCHEMA_VERSION = 3
HEADLINE_LIMIT = 30
ET = ZoneInfo("America/New_York")
CONTRACT_TICKERS = frozenset({"TSLA", "SPCX"})
# DS-05 is Yahoo consolidated volume. DS-02 is Alpaca's free IEX feed.
VOLUME_SOURCES = frozenset({"DS-02", "DS-05"})


def number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def _headline_score(row: dict) -> tuple[float | None, str | None]:
    score = number(row.get("sentiment_score"))
    if score is None:
        return None, None
    if number(row.get("provider_sentiment")) is not None:
        return score, "provider"
    return score, "lexicon"


def build_news(ticker: str, daily_rows: list[dict], articles: list[dict], now: datetime) -> dict:
    """Last 7 days of headlines and observed seven-calendar-day sentiment history."""
    from news_sentiment import headline_relevance, plain_text, rolling_mean, strip_publisher_suffix

    today = now.astimezone(UTC).date()
    daily_by_date: dict[str, dict] = {}
    for row in daily_rows:
        value = number(row.get("mean_sentiment"))
        if row.get("ticker", ticker) != ticker or value is None:
            continue
        try:
            day = date.fromisoformat(str(row.get("date") or "")[:10])
        except ValueError:
            continue
        if day <= today:
            daily_by_date[day.isoformat()] = {**row, "date": day.isoformat(), "mean_sentiment": value}
    daily_rows = list(daily_by_date.values())
    cutoff = now.astimezone(UTC) - timedelta(days=7)
    company: list[dict] = []
    sector: list[dict] = []
    count_7d = 0
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
        publisher = str(row.get("publisher") or "")
        score, score_source = _headline_score(row)
        relevance = headline_relevance(row, ticker)
        headline = {
            "title": strip_publisher_suffix(str(row.get("title") or ""), publisher),
            "url": row.get("url") or "",
            "publisher": publisher,
            "published_at": published,
            "date_precision": "minute",
            "score": score,
            "score_source": score_source,
            "label": row.get("sentiment_label") or "neutral",
            "relevance": relevance,
        }
        snippet = plain_text(row.get("snippet") or "")
        if snippet:
            headline["snippet"] = snippet
        if relevance == "sector":
            if len(sector) < HEADLINE_LIMIT:
                sector.append(headline)
            continue
        count_7d += 1
        if len(company) < HEADLINE_LIMIT:
            company.append(headline)
    headlines = company + sector
    return {
        "as_of": company[0]["published_at"] if company else None,
        "sentiment_7d": rolling_mean(daily_rows, today),
        "sentiment_7d_prior": rolling_mean(daily_rows, today - timedelta(days=7)),
        "count_7d": count_7d,
        "sentiment_history": [
            {
                "date": day,
                "value": rolling_mean(daily_rows, date.fromisoformat(day)),
            }
            for day in sorted(daily_by_date)[-90:]
        ],
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
                "source": row.get("source"),
                "source_url": row.get("source_url"),
                "observed_at": row.get("ingested_at"),
                "tickers": row.get("tickers") or [],
            }
        )
        if len(chosen) == 20:
            break
    return chosen


def build_releases(rows: list[dict], calendar: list[dict]) -> dict:
    latest: dict[str, dict] = {}
    for row in rows:
        series_id = row.get("series_id")
        period = str(row.get("period") or "")
        if not series_id:
            continue
        current = latest.get(series_id)
        if current is None or period > str(current.get("period") or ""):
            latest[series_id] = {
                "series": series_id,
                "period": period,
                "actual": row.get("actual"),
                "consensus": row.get("consensus"),
                "surprise": row.get("surprise"),
            }
    upcoming = sorted(calendar, key=lambda row: str(row.get("release_ts") or ""))[:5]
    return {
        "latest": [latest[key] for key in sorted(latest)],
        "next": [{"series": row.get("series"), "release_ts": row.get("release_ts")} for row in upcoming],
    }


def build_release_links(ticker: str, rows: list[dict]) -> dict:
    scoped = [row for row in rows if row.get("ticker") == ticker]
    latest: dict[str, dict] = {}
    for row in scoped:
        if row.get("row_kind") != "release":
            continue
        series = str(row.get("series_id") or "")
        if series not in latest or str(row.get("release_date") or "") > str(latest[series].get("release_date") or ""):
            latest[series] = row
    return {
        "summaries": [row for row in scoped if row.get("row_kind") == "summary"],
        "latest": [latest[key] for key in sorted(latest)],
    }


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


def contract_freshness(as_of: object, generated_at: datetime) -> str:
    """Describe the age of the stored rollup, not the age of its underlying awards."""
    try:
        rollup_date = date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return "unknown"
    age = (generated_at.date() - rollup_date).days
    if age < 0:
        return "unknown"
    return "stale" if age > 7 else "fresh"


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


def build_chart_data(
    ticker: str,
    trend_rows: list[dict],
    short_interest_rows: list[dict],
    options_rows: list[dict],
    fundamentals: list[dict],
    price_rows: list[dict] | None = None,
    generated_at: str | None = None,
) -> dict:
    """Build one ticker's compact chart-only history document."""
    trends_by_series: dict[str, dict[str, float]] = {}
    pressure_by_date: dict[str, float] = {}
    correlation_by_series: dict[str, list[dict]] = {}
    for row in trend_rows:
        if row.get("ticker") not in (None, ticker):
            continue
        day = str(row.get("date") or "")[:10]
        series_id = str(row.get("series_id") or "")
        value = number(row.get("value"))
        pressure = number(row.get("net_pressure"))
        if not day:
            continue
        if series_id and value is not None:
            trends_by_series.setdefault(series_id, {})[day] = value
        if series_id:
            correlation_by_series.setdefault(series_id, []).append({
                "date": day, "corr_30d": number(row.get("corr_30d")), "corr_90d": number(row.get("corr_90d")),
            })
        if pressure is not None:
            pressure_by_date[day] = pressure

    shares_rows = sorted(
        (
            (str(row.get("release_date") or "")[:10], number(row.get("value")))
            for row in fundamentals
            if row.get("ticker") == ticker and row.get("metric") == "shares_outstanding"
        ),
        key=lambda item: item[0],
    )
    prices_by_date = {
        str(row.get("date") or "")[:10]: number(row.get("close"))
        for row in (price_rows or [])
        if row.get("date") and (not row.get("ticker") or str(row.get("ticker")).upper() == ticker.upper())
    }
    float_rows = sorted(
        (
            (
                str(row.get("release_date") or "")[:10],
                number(row.get("value")) / prices_by_date.get(str(row.get("measurement_date") or "")[:10]),
            )
            for row in fundamentals
            if row.get("ticker") == ticker
            and row.get("metric") == "public_float_usd"
            and number(row.get("value")) is not None
            and number(row.get("value")) > 0
            and prices_by_date.get(str(row.get("measurement_date") or "")[:10])
            and prices_by_date[str(row.get("measurement_date"))[:10]] > 0
        ),
        key=lambda item: item[0],
    )
    short_interest = []
    for row in sorted(short_interest_rows, key=lambda item: str(item.get("settlement_date") or "")):
        if str(row.get("ticker") or "").upper() != ticker.upper():
            continue
        day = str(row.get("settlement_date") or "")[:10]
        shares_short = number(row.get("short_interest", row.get("shares_short")))
        if not day or shares_short is None:
            continue
        float_shares_as_of = next(
            (value for filed, value in reversed(float_rows) if filed <= day and value is not None and value > 0),
            None,
        )
        shares_proxy_as_of = next(
            (value for filed, value in reversed(shares_rows) if filed <= day and value is not None and value > 0),
            None,
        )
        denominator = float_shares_as_of if float_shares_as_of is not None else shares_proxy_as_of
        short_interest.append(
            {
                "date": day,
                "shares_short": shares_short,
                "shares_denominator": denominator,
                "denominator_type": (
                    "estimated_public_float" if float_shares_as_of is not None
                    else "shares_outstanding_proxy" if shares_proxy_as_of is not None
                    else None
                ),
                "short_pct_denominator": None if denominator is None else 100 * shares_short / denominator,
                "days_to_cover": number(row.get("days_to_cover")),
            }
        )

    options = []
    for row in sorted(options_rows, key=lambda item: str(item.get("date") or "")):
        if str(row.get("ticker") or "").upper() != ticker.upper():
            continue
        day = str(row.get("date") or "")[:10]
        if not day:
            continue
        options.append(
            {
                "date": day,
                "put_call_volume_ratio": number(row.get("put_call_volume_ratio")),
                "iv30": number(row.get("iv30")),
                "iv_available": bool(row.get("iv_available")),
            }
        )

    company_series = [
        {
            "date": str(row.get("release_date") or "")[:10],
            "series_id": str(row.get("metric")),
            "value": number(row.get("value")),
            "unit": row.get("unit"),
            "fiscal_quarter": row.get("fiscal_quarter"),
            "measurement_date": row.get("measurement_date"),
            "source_id": row.get("source_id"),
        }
        for row in fundamentals
        if row.get("ticker") == ticker
        and row.get("release_date")
        and number(row.get("value")) is not None
    ]
    return {
        "schema_version": 1,
        "ticker": ticker,
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "macro_series": {
            series_id: [{"date": day, "value": values[day]} for day in sorted(values)[-MAX_PRICE_ROWS:]]
            for series_id, values in sorted(trends_by_series.items())
        },
        "macro_pressure": [
            {"date": day, "value": pressure_by_date[day]} for day in sorted(pressure_by_date)[-MAX_PRICE_ROWS:]
        ],
        "correlation_history": {
            series: sorted(rows, key=lambda row: row["date"])[-90:]
            for series, rows in sorted(correlation_by_series.items())
        },
        "short_interest": short_interest[-MAX_PRICE_ROWS:],
        "options": options[-MAX_PRICE_ROWS:],
        "fundamentals": sorted(company_series, key=lambda row: (row["date"], row["series_id"])),
    }


def volume_source_of(row: dict) -> str | None:
    """Stored volume_source, or source_id for a row written before that column existed."""
    explicit = row.get("volume_source")
    if explicit in VOLUME_SOURCES:
        return str(explicit)
    price_source = row.get("source_id")
    if price_source in VOLUME_SOURCES:
        return str(price_source)
    return None


def volume_source_changes(history: list[dict]) -> list[dict]:
    """Dates in a served series where volume_source differs from the previous bar."""
    changes = []
    previous = None
    for row in history:
        source = row.get("volume_source")
        if previous is not None and source is not None and source != previous:
            changes.append({"date": row["date"], "from": previous, "to": source})
        if source is not None:
            previous = source
    return changes


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
    contracts: dict[str, dict] | None = None,
    releases: list[dict] | None = None,
    release_calendar: list[dict] | None = None,
    release_links: list[dict] | None = None,
    rates_observations: dict | None = None,
    rates_fomc: dict | None = None,
    rates_attempts: dict | None = None,
) -> dict:
    """Create a deterministic API document; absent source data remains explicitly unavailable."""
    clock = _as_of(generated_at)
    instruments = {}
    for ticker in tickers:
        unique: dict[str, dict] = {}
        for row in prices.get(ticker, []):
            # A mixed frame (another symbol's rows under this key) must not overwrite
            # this ticker's close for the day. Rows with no ticker are already scoped.
            owner = str(row.get("ticker") or "").upper()
            if owner and owner != ticker.upper():
                continue
            day = str(row.get("date", ""))
            close = row.get("close")
            if day and isinstance(close, (int, float)):
                source = volume_source_of(row)
                iex = row.get("volume_iex")
                # A legacy IEX row stored the print in volume and had no volume_iex column.
                if iex is None and source == "DS-02":
                    iex = row.get("volume")
                unique[day] = {
                    "date": day,
                    "close": float(close),  # split-adjusted
                    "close_raw": row.get("close_raw"),  # actual traded close; null on older rows
                    "adj_close": row.get("adj_close"),  # split- and dividend-adjusted; charts use this
                    "volume": row.get("volume"),
                    "volume_source": source,
                    "volume_iex": iex,
                }
        history = [unique[day] for day in sorted(unique)][-MAX_PRICE_ROWS:]
        bundle = None if news is None else news.get(ticker) or {}
        filing_rows = None if filings is None else filings.get(ticker) or []
        award = None
        if contracts is not None and ticker in CONTRACT_TICKERS:
            award = contracts.get(ticker)
            if award is not None:
                award_count = award.get("ttm_awards_count")
                coverage = (
                    "available"
                    if isinstance(award_count, int)
                    and award_count > 0
                    and number(award.get("ttm_obligated")) is not None
                    else "unavailable"
                )
                award = {
                    **award,
                    "coverage": coverage,
                    "freshness": contract_freshness(award.get("as_of"), clock),
                    "ttm_obligated": award.get("ttm_obligated") if coverage == "available" else None,
                }
        interest = latest_short_interest((short_interest or {}).get(ticker, []))
        instruments[ticker] = {
            "ticker": ticker,
            "price_history": history,
            "volume_source_changes": volume_source_changes(history),
            "trend": trends.get(ticker),
            "release_links": None if release_links is None else build_release_links(ticker, release_links),
            "news": None
            if bundle is None
            else build_news(ticker, bundle.get("daily") or [], bundle.get("articles") or [], clock),
            "intraday": build_intraday((hourly or {}).get(ticker, []), history),
            "short_interest": interest.get(ticker),
            "filings": None if filing_rows is None else build_filings(filing_rows),
            "insider_30d": None if filing_rows is None else build_insider(filing_rows, clock.date()),
            "contracts": award,
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
        "releases": (
            None
            if releases is None and release_calendar is None
            else build_releases(releases or [], release_calendar or [])
        ),
        "status": status,
        "rates": build_rates(rates_observations, rates_fomc, rates_attempts),
        "market": market_block(clock),
    }


def publish_serving_documents(snapshot: dict, charts: dict[str, dict]) -> dict[str, int]:
    """Write the dashboard and chart documents plus gzip siblings, and record the dashboard size."""
    from lake import write_json_and_gzip

    for ticker, document in charts.items():
        write_json_and_gzip(document, f"serving/chart_data/{ticker}.json", cache_seconds=30)
    size = write_json_and_gzip(snapshot, "serving/dashboard.json", cache_seconds=30)
    _emit_dashboard_size(size["raw_bytes"], size["gzip_bytes"])
    return size


def _emit_dashboard_size(raw_bytes: int, gzip_bytes: int) -> None:
    """CloudWatch pair the 4 MiB payload alarm reads. Same service dimension as the other job metrics."""
    from aws_lambda_powertools.metrics import MetricUnit

    from observability import logger, metrics

    metrics.add_metric(name="DashboardRawBytes", unit=MetricUnit.Bytes, value=raw_bytes)
    metrics.add_metric(name="DashboardGzipBytes", unit=MetricUnit.Bytes, value=gzip_bytes)
    logger.info(
        "dashboard_document_size",
        extra={
            "raw_bytes": raw_bytes,
            "gzip_bytes": gzip_bytes,
            "alarm_bytes": 4 * 1024 * 1024,
            "lambda_response_limit_bytes": 6 * 1024 * 1024,
        },
    )


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from lake import read_json, read_parquet_prefix, read_prices
    from observability import job_handler, logger
    from universe import collection_universe, user_ticker_union

    @job_handler("DASHBOARD", emit_event=False, lease_key="serving/dashboard-build/_lease.json")
    def run(event, context):
        covered = collection_universe(user_ticker_union())
        tickers = covered["equities"] + covered["etfs"]
        price_data: dict[str, list[dict]] = {}
        trend_data: dict[str, dict | None] = {}
        trend_history_data: dict[str, list[dict]] = {}
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
        rollup = read_json("curated/contracts_rollup/latest.json") or []
        contracts_data = {row.get("ticker"): row for row in rollup if isinstance(row, dict)}
        hourly_data: dict[str, list[dict]] = {}
        for ticker in tickers:
            frame = read_prices(ticker)  # ~1 GET per year of history (yearly partitions)
            price_data[ticker] = frame.to_dicts() if not frame.is_empty() else []
            trend_data[ticker] = read_json(f"serving/trend_metrics/latest/{ticker}.json")
            trend_history = read_parquet_prefix(f"serving/trend_metrics/ticker={ticker}/")
            trend_history_data[ticker] = trend_history.to_dicts() if not trend_history.is_empty() else []
            hourly = read_parquet_prefix(f"curated/prices_hourly/ticker={ticker}/")
            hourly_data[ticker] = hourly.to_dicts() if not hourly.is_empty() else []
        short_frame = read_parquet_prefix("curated/short_interest/")
        short_rows = short_frame.to_dicts() if not short_frame.is_empty() else []
        short_by_ticker: dict[str, list[dict]] = {}
        for row in short_rows:
            short_by_ticker.setdefault(str(row.get("ticker") or "").upper(), []).append(row)

        options_frame = read_parquet_prefix("curated/options_daily/")
        options_rows = options_frame.to_dicts() if not options_frame.is_empty() else []
        options_by_ticker: dict[str, list[dict]] = {}
        for row in options_rows:
            options_by_ticker.setdefault(str(row.get("ticker") or "").upper(), []).append(row)

        fundamental_doc = read_json("serving/fundamentals_quarterly.json") or {}
        fundamental_rows = fundamental_doc.get("rows", [])
        status = read_json("serving/status.json")
        release_frame = read_parquet_prefix("curated/releases/")
        calendar = read_json("curated/release_calendar/upcoming.json") or []
        links = read_parquet_prefix("curated/release_links/")
        curve_observations: dict[str, list[dict]] = {}
        for series_id in CURVE_SERIES_IDS:
            frame = read_parquet_prefix(f"curated/macro_daily/source=fred/series_id={series_id}/")
            curve_observations[series_id] = frame.to_dicts() if not frame.is_empty() else []
        snapshot = build_snapshot(
            tickers=tickers,
            prices=price_data,
            trends=trend_data,
            fundamentals=fundamental_rows,
            status=status,
            news=news_data,
            hourly=hourly_data,
            short_interest=short_by_ticker,
            filings=filings_by_ticker,
            events=event_rows,
            contracts=contracts_data,
            releases=release_frame.to_dicts() if not release_frame.is_empty() else [],
            release_calendar=calendar if isinstance(calendar, list) else [],
            release_links=links.to_dicts() if not links.is_empty() else [],
            rates_observations=curve_observations,
            rates_fomc=read_json("serving/rates/fomc.json"),
            rates_attempts=read_json("serving/rates/attempts.json"),
        )
        charts: dict[str, dict] = {}
        for ticker in tickers:
            chart_document = build_chart_data(
                ticker=ticker,
                trend_rows=trend_history_data.get(ticker, []),
                short_interest_rows=short_by_ticker.get(ticker, []),
                options_rows=options_by_ticker.get(ticker, []),
                fundamentals=fundamental_rows,
                price_rows=price_data.get(ticker, []),
                generated_at=snapshot["generated_at"],
            )
            charts[ticker] = chart_document
        size = publish_serving_documents(snapshot, charts)
        logger.info(
            "dashboard_snapshot_published",
            extra={
                "tickers": len(tickers),
                "tickers_with_prices": sum(bool(row["price_history"]) for row in snapshot["tickers"].values()),
                "fundamentals": len(snapshot["fundamentals"]),
                "generated_at": snapshot["generated_at"],
                "raw_bytes": size["raw_bytes"],
                "gzip_bytes": size["gzip_bytes"],
            },
        )
        return {
            "status": snapshot["data_status"],
            "tickers": len(tickers),
            "tickers_with_prices": sum(bool(row["price_history"]) for row in snapshot["tickers"].values()),
        }

    return run(event, context)
