"""Build the compact, read-only serving document consumed by the dashboard API."""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl

from market_session import market_block
from yield_curve import CURVE_SERIES_IDS, build_rates

MAX_PRICE_ROWS = 1260
# Recent closes for first paint: the 22-session spark, the 1M chip, the 63-session
# comparison spark, and the 3M chip (one bar more than 63 sessions). 1Y/3Y/5Y,
# moving averages beyond this window, and market-overlay history live on chart_data.
SUMMARY_PRICE_ROWS = 64
# Raw serving/dashboard.json budget for a 25-ticker build. The CloudWatch alarm stays at 4 MiB.
DASHBOARD_RAW_BUDGET_BYTES = 2_000_000
SCHEMA_VERSION = 3
HEADLINE_LIMIT = 30
ET = ZoneInfo("America/New_York")
# DS-05 is Yahoo consolidated volume. DS-02 is Alpaca's free IEX feed.
VOLUME_SOURCES = frozenset({"DS-02", "DS-05"})
_UNSET = object()
PRICE_COLUMNS = [
    "ticker", "date", "close", "close_raw", "adj_close", "volume", "volume_source", "volume_iex", "source_id",
]
TREND_COLUMNS = ["ticker", "date", "series_id", "value", "net_pressure", "corr_30d", "corr_90d"]
HOURLY_COLUMNS = ["ticker", "ts_utc", "ts", "close", "volume"]
SHORT_COLUMNS = [
    "ticker", "settlement_date", "short_interest", "shares_short", "previous_short_interest", "days_to_cover",
]
OPTIONS_COLUMNS = ["ticker", "date", "put_call_volume_ratio", "iv30", "iv_available"]
MACRO_COLUMNS = ["series_id", "obs_date", "date", "value", "ingested_at"]
# Same symbols as universe.INDEX_PROXIES. Kept here so this module does not import the prefs client.
INDEX_PROXIES = ("SPY", "DIA", "QQQ", "IWM", "XLY", "ITA", "SMH")
# Six months of sessions for the correlation-drift sparkline. The dashboard file does not carry it.
CORRELATION_HISTORY_ROWS = 126
BETA_WINDOW = 90
BETA_MIN_PAIRS = 20
# Session counts match apps/web/chart-period.js. YTD and 5Y use the same availability rules.
PERIOD_SESSIONS = (
    ("1D", 1),
    ("1W", 5),
    ("1M", 21),
    ("3M", 63),
    ("YTD", None),
    ("1Y", 252),
    ("3Y", 756),
    ("5Y", 1259),
)
FIVE_YEAR_MIN_SPAN_YEARS = 4.75
FIVE_YEAR_MIN_BARS = 1000


def number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def compact_number(value: object, digits: int = 4) -> int | float | None:
    """Round a finite number for JSON. Whole values become ints so the dump omits ``.0``."""
    parsed = number(value)
    if parsed is None:
        return None
    rounded = round(parsed, digits)
    if rounded == 0:
        return 0
    whole = round(rounded)
    if abs(rounded - whole) < 10 ** (-(digits + 1)) and abs(whole) < 10**15:
        return int(whole)
    return rounded


def _compact_tree(value: object, digits: int = 6) -> object:
    if isinstance(value, dict):
        return {key: _compact_tree(item, digits) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact_tree(item, digits) for item in value]
    if isinstance(value, float):
        return compact_number(value, digits)
    return value


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
                **(
                    {"report_date": str(row.get("report_date") or "")[:10]}
                    if row.get("report_date")
                    else {}
                ),
                **({"items": list(row.get("items") or [])} if row.get("items") else {}),
                **({"fiscal_period": row.get("fiscal_period")} if row.get("fiscal_period") else {}),
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
        "inflation": build_inflation_block(rows),
    }


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_CPI_SERIES = "CUSR0000SA0"
_CORE_SERIES = "CUSR0000SA0L1E"


def _yoy_by_period(rows: list[dict], series_id: str) -> tuple[list[str], dict[str, float]]:
    chosen: dict[str, float] = {}
    for row in rows or []:
        if row.get("series_id") != series_id:
            continue
        yoy = row.get("yoy")
        if isinstance(yoy, bool) or not isinstance(yoy, (int, float)):
            continue
        period = str(row.get("period") or "")[:7]
        if len(period) != 7 or period[4] != "-":
            continue
        chosen[period] = float(yoy)
    return sorted(chosen), chosen


def _yoy_direction(ordered: list[str], values: dict[str, float]) -> str | None:
    if len(ordered) < 2:
        return None
    current = values[ordered[-1]]
    previous = values[ordered[-2]]
    if current > previous:
        return "firming"
    if current < previous:
        return "easing"
    return "range-bound"


def build_inflation_block(rows: list[dict]) -> dict:
    """Last six CPI YoY prints. The change is versus the previous print, not a nowcast."""
    ordered, values = _yoy_by_period(rows, _CPI_SERIES)
    core_ordered, core_values = _yoy_by_period(rows, _CORE_SERIES)
    prints = []
    if ordered:
        window = ordered[-6:]
        earlier = [period for period in ordered if period < window[0]]
        previous = values[earlier[-1]] if earlier else None
        for period in window:
            yoy = values[period]
            surprise = None if previous is None else yoy - previous
            prints.append({
                "period": period,
                "month": _MONTHS[int(period[5:7]) - 1],
                "yoy": yoy,
                "surprise_pp": surprise,
            })
            previous = yoy
    return {
        "prints": prints,
        "headline": _yoy_direction(ordered, values),
        "core": _yoy_direction(core_ordered, core_values),
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
    last = float(today[-1]["close"])
    bars = []
    for row in today:
        bar = {"ts": row["ts"], "close": compact_number(row["close"], 4)}
        volume = compact_number(row.get("volume"), 0)
        if volume is not None:
            bar["volume"] = volume
        bars.append(bar)
    return {
        "as_of": today[-1]["ts"],
        "last": compact_number(last, 4),
        "change_pct": None if not prior else compact_number(last / prior - 1, 6),
        "bars": bars,
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
            "shares_short": compact_number(shares, 0) if number(shares) is not None else shares,
            "days_to_cover": compact_number(row.get("days_to_cover"), 4),
            "pct_change": compact_number(pct, 6),
        }
    return latest


def _chart_bars(history: list[dict]) -> list[dict]:
    """Adjusted closes, falling back to close, in stored order. Matches chartValue()."""
    bars = []
    for row in history:
        value = number(row.get("adj_close"))
        if value is None:
            value = number(row.get("close"))
        day = str(row.get("date") or "")[:10]
        if value is None or not day:
            continue
        bars.append({"date": day, "value": value})
    return bars


def _span_years(bars: list[dict]) -> float | None:
    try:
        start = date.fromisoformat(bars[0]["date"])
        end = date.fromisoformat(bars[-1]["date"])
    except ValueError:
        return None
    if end < start:
        return None
    return (end - start).days / 365.25


def _period_available(bars: list[dict], period: str, sessions: int | None) -> bool:
    if len(bars) < 2:
        return False
    if period == "YTD":
        year = bars[-1]["date"][:4]
        return sum(1 for bar in bars if bar["date"].startswith(year)) > 1
    if period == "5Y":
        if len(bars) >= MAX_PRICE_ROWS:
            return True
        span = _span_years(bars)
        return span is not None and span >= FIVE_YEAR_MIN_SPAN_YEARS and len(bars) >= FIVE_YEAR_MIN_BARS
    return sessions is not None and len(bars) > sessions


def _period_offset(bars: list[dict], period: str, sessions: int | None) -> int:
    if period == "YTD":
        year = bars[-1]["date"][:4]
        first = next(index for index, bar in enumerate(bars) if bar["date"].startswith(year))
        return len(bars) - first - 1
    if period == "5Y":
        return min(sessions or 0, len(bars) - 1)
    return sessions or 0


def period_returns(history: list[dict]) -> dict[str, float | None]:
    """One adjusted return per chart period. Missing windows stay null, never zero."""
    bars = _chart_bars(history)
    returns: dict[str, float | None] = {}
    for period, sessions in PERIOD_SESSIONS:
        if not _period_available(bars, period, sessions):
            returns[period] = None
            continue
        offset = _period_offset(bars, period, sessions)
        before = bars[-1 - offset]["value"]
        latest = bars[-1]["value"]
        returns[period] = None if before == 0 else latest / before - 1
    return returns


def _daily_returns(bars: list[dict]) -> list[tuple[str, float]]:
    out = []
    for index in range(1, len(bars)):
        previous = bars[index - 1]["value"]
        current = bars[index]["value"]
        if previous == 0:
            continue
        out.append((bars[index]["date"], current / previous - 1))
    return out


def beta_rho(
    stock_history: list[dict],
    index_history: list[dict],
    window: int = BETA_WINDOW,
    min_pairs: int = BETA_MIN_PAIRS,
) -> dict[str, float | int | None]:
    """Pearson correlation and beta of the stock on the index over the newest paired sessions.

    Beta is cov(stock, index) / var(index). Pairs share a date. A flat series, or
    fewer than min_pairs, leaves both links null and still reports n.
    """
    stock = _daily_returns(_chart_bars(stock_history))
    index = {day: value for day, value in _daily_returns(_chart_bars(index_history))}
    paired: list[tuple[float, float]] = []
    for day, stock_return in reversed(stock):
        index_return = index.get(day)
        if index_return is None:
            continue
        paired.append((index_return, stock_return))
        if len(paired) == window:
            break
    paired.reverse()
    count = len(paired)
    empty = {"rho_90d": None, "beta_90d": None, "n": count}
    if count < min_pairs:
        return empty
    mean_x = sum(row[0] for row in paired) / count
    mean_y = sum(row[1] for row in paired) / count
    cov = var_x = var_y = 0.0
    for index_return, stock_return in paired:
        cov += (index_return - mean_x) * (stock_return - mean_y)
        var_x += (index_return - mean_x) ** 2
        var_y += (stock_return - mean_y) ** 2
    if var_x <= 1e-18 or var_y <= 1e-18:
        return empty
    return {"rho_90d": cov / math.sqrt(var_x * var_y), "beta_90d": cov / var_x, "n": count}


def market_comparison_block(
    ticker: str,
    stock_history: list[dict],
    index_prices: dict[str, list[dict]],
) -> dict:
    """Period returns plus 90-session correlation and beta against each index proxy except itself."""
    indexes = {}
    symbol = ticker.upper()
    for proxy in INDEX_PROXIES:
        if proxy == symbol:
            continue
        history = price_history_for(proxy, index_prices.get(proxy) or [])
        indexes[proxy] = {"returns": period_returns(history), **beta_rho(stock_history, history)}
    return {
        "window_sessions": BETA_WINDOW,
        "min_pairs": BETA_MIN_PAIRS,
        "returns": period_returns(stock_history),
        "indexes": indexes,
    }


def load_index_proxy_prices(read_prices, symbols: tuple[str, ...] = INDEX_PROXIES) -> dict[str, list[dict]]:
    """Read proxy histories once, before equities are published, so β can be paired by date."""
    loaded: dict[str, list[dict]] = {}
    for symbol in symbols:
        frame = read_prices(symbol)
        loaded[symbol] = _price_rows(frame)
        del frame
    return loaded


def build_chart_data(
    ticker: str,
    trend_rows: list[dict],
    short_interest_rows: list[dict],
    options_rows: list[dict],
    fundamentals: list[dict],
    price_rows: list[dict] | None = None,
    generated_at: str | None = None,
    index_prices: dict[str, list[dict]] | None = None,
) -> dict:
    """Build one ticker's compact chart-only history document.

    ``index_prices`` adds ``market_comparison`` (period returns, correlation, beta). It stays off
    this document's caller when omitted, and it is never copied onto dashboard.json.
    """
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
            trends_by_series.setdefault(series_id, {})[day] = compact_number(value, 6)
        if series_id:
            correlation_by_series.setdefault(series_id, []).append({
                "date": day,
                "corr_30d": compact_number(row.get("corr_30d"), 6),
                "corr_90d": compact_number(row.get("corr_90d"), 6),
            })
        if pressure is not None:
            pressure_by_date[day] = compact_number(pressure, 6)

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
                "shares_short": compact_number(shares_short, 0),
                "shares_denominator": compact_number(denominator, 0),
                "denominator_type": (
                    "estimated_public_float" if float_shares_as_of is not None
                    else "shares_outstanding_proxy" if shares_proxy_as_of is not None
                    else None
                ),
                "short_pct_denominator": (
                    None if denominator is None else compact_number(100 * shares_short / denominator, 6)
                ),
                "days_to_cover": compact_number(row.get("days_to_cover"), 4),
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
                "put_call_volume_ratio": compact_number(row.get("put_call_volume_ratio"), 6),
                "iv30": compact_number(row.get("iv30"), 6),
                "iv_available": bool(row.get("iv_available")),
            }
        )

    history = price_history_for(ticker, price_rows or [])
    company_series = [
        {
            "date": str(row.get("release_date") or "")[:10],
            "series_id": str(row.get("metric")),
            "value": compact_number(row.get("value"), 6),
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
    return _compact_tree({
        "schema_version": 1,
        "ticker": ticker,
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "price_history": history,
        "volume_source_changes": volume_source_changes(history),
        "macro_series": {
            series_id: [{"date": day, "value": values[day]} for day in sorted(values)[-MAX_PRICE_ROWS:]]
            for series_id, values in sorted(trends_by_series.items())
        },
        "macro_pressure": [
            {"date": day, "value": pressure_by_date[day]} for day in sorted(pressure_by_date)[-MAX_PRICE_ROWS:]
        ],
        "correlation_history": {
            series: sorted(rows, key=lambda row: row["date"])[-CORRELATION_HISTORY_ROWS:]
            for series, rows in sorted(correlation_by_series.items())
        },
        "short_interest": short_interest[-MAX_PRICE_ROWS:],
        "options": options[-MAX_PRICE_ROWS:],
        "fundamentals": sorted(company_series, key=lambda row: (row["date"], row["series_id"])),
        **(
            {}
            if index_prices is None
            else {"market_comparison": market_comparison_block(ticker, history, index_prices)}
        ),
    })


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


def _price_bar(day: str, row: dict) -> dict | None:
    """One served close. Equal or missing adjusted/traded closes and duplicate IEX volume are omitted."""
    close = compact_number(row.get("close"), 4)
    if close is None:
        return None
    bar: dict = {"date": day, "close": close}
    traded = compact_number(row.get("close_raw"), 4)
    adjusted = compact_number(row.get("adj_close"), 4)
    if traded is not None and traded != close:
        bar["close_raw"] = traded
    if adjusted is not None and adjusted != close:
        bar["adj_close"] = adjusted
    volume = compact_number(row.get("volume"), 0)
    if volume is not None:
        bar["volume"] = volume
    source = volume_source_of(row)
    if source:
        bar["volume_source"] = source
    iex = row.get("volume_iex")
    if iex is None and source == "DS-02":
        iex = row.get("volume")
    iex_n = compact_number(iex, 0)
    if iex_n is not None and iex_n != volume:
        bar["volume_iex"] = iex_n
    return bar


def price_history_for(ticker: str, rows: list[dict]) -> list[dict]:
    """Split-adjusted daily bars for one ticker, capped at the five-year serving window."""
    unique: dict[str, dict] = {}
    symbol = ticker.upper()
    for row in rows:
        owner = str(row.get("ticker") or "").upper()
        if owner and owner != symbol:
            continue
        day = str(row.get("date") or "")[:10]
        bar = _price_bar(day, row) if day else None
        if bar is not None:
            unique[day] = bar
    return [unique[day] for day in sorted(unique)][-MAX_PRICE_ROWS:]


# Display names for SEC conformed names that title-casing cannot fix.
COMPANY_NAME_OVERRIDES = {
    "AMZN": "Amazon.com, Inc.",
    "SPCX": "SpaceX",
}
# Words kept upper case when an all-caps SEC name is title-cased.
NAME_ACRONYMS = frozenset(
    {"NVIDIA", "AMD", "IBM", "ASML", "TSMC", "AT&T", "USA", "US", "UK", "AI", "ETF", "SPDR", "ADR", "REIT"}
    | {"II", "III", "IV"}
)
NAME_SUFFIXES = {
    "CORP": "Corp.", "CORPORATION": "Corporation", "INC": "Inc.", "INCORPORATED": "Incorporated", "CO": "Co.",
    "COMPANY": "Company", "LTD": "Ltd.", "LIMITED": "Limited", "LLC": "LLC", "LP": "LP", "PLC": "plc",
    "NV": "N.V.", "SA": "S.A.", "AG": "AG", "SE": "SE", "HOLDINGS": "Holdings",
}
NAME_SMALL_WORDS = frozenset({"OF", "AND", "THE", "FOR", "IN", "ON", "AT", "DE"})


def display_company_name(ticker: str, name: str | None) -> str | None:
    """Readable display name from an SEC entity name.

    Ticker overrides win. Names that already contain lower-case letters are left as filed. All-caps names are
    title-cased word by word, keeping known acronyms upper case and normalizing legal suffixes (CORP -> Corp.,
    INC -> Inc.).
    """
    symbol = str(ticker or "").upper()
    if symbol in COMPANY_NAME_OVERRIDES:
        return COMPANY_NAME_OVERRIDES[symbol]
    text = re.sub(r"\s*/[A-Za-z]{2,3}/?$", "", " ".join(str(name or "").split()))  # SEC state tag, e.g. /DE/
    if not text or any(char.islower() for char in text):
        return text or None
    words = []
    for index, raw in enumerate(text.split(" ")):
        core = raw.rstrip(",.")
        trail = raw[len(core):].replace(".", "")
        key = core.upper()
        if key in NAME_ACRONYMS:
            word = key
        elif key in NAME_SUFFIXES and index > 0:
            word = NAME_SUFFIXES[key]
        elif key in NAME_SMALL_WORDS and index > 0:
            word = key.lower()
        else:
            word = "-".join(part[:1].upper() + part[1:].lower() for part in core.split("-"))
        words.append(word + trail)
    return " ".join(words)


def company_name_for(ticker: str, served: dict | None, filing_rows: list[dict] | None) -> str | None:
    """Display name for any watchlist ticker from the SEC entity name: served filings doc, then curated rows."""
    symbol = str(ticker or "").upper()
    candidates = [(served or {}).get("entity_name")] + [row.get("entity_name") for row in filing_rows or []]
    for value in candidates:
        name = " ".join(str(value or "").split())
        if name and name.upper() != symbol:
            return display_company_name(symbol, name)
    return None


def company_description_for(served: dict | None) -> str | None:
    """Short business description from the latest annual report, or None."""
    text = " ".join(str((served or {}).get("description") or "").split())
    return text or None


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
    served_filings: dict[str, dict] | None = None,
    events: list[dict] | None = None,
    contracts: dict[str, dict] | None = None,
    releases: list[dict] | None = None,
    release_calendar: list[dict] | None = None,
    release_links: list[dict] | None = None,
    rates_observations: dict | None = None,
    rates_fomc: dict | None = None,
    rates_attempts: dict | None = None,
    rates: dict | object | None = _UNSET,
) -> dict:
    """Create a deterministic API document; absent source data remains explicitly unavailable.

    ``price_history`` on each ticker is the recent first-paint tail. The five-year
    series is written to ``serving/chart_data/<T>.json``. Quarterly fundamentals are
    not repeated on this document.
    """
    clock = _as_of(generated_at)
    instruments = {}
    for ticker in tickers:
        # Full five-year bars go to chart_data. The dashboard keeps the recent tail
        # the watchlist, 1M chip, and comparison sparklines draw before that fetch.
        full_history = price_history_for(ticker, prices.get(ticker, []))
        history = full_history[-SUMMARY_PRICE_ROWS:]
        bundle = None if news is None else news.get(ticker) or {}
        filing_rows = None if filings is None else filings.get(ticker) or []
        served = None if served_filings is None else served_filings.get(ticker)
        if isinstance(served, dict):
            display_rows = list(served.get("rows") or [])
            filing_status = served.get("status") if isinstance(served.get("status"), str) else None
        else:
            display_rows = filing_rows
            filing_status = None
        award = None
        if contracts is not None and ticker in contracts:
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
            "company_name": company_name_for(
                ticker,
                (served_filings or {}).get(ticker),
                (filings or {}).get(ticker),
            ),
            "company_description": company_description_for((served_filings or {}).get(ticker)),
            "price_history": history,
            "volume_source_changes": volume_source_changes(history),
            "trend": trends.get(ticker),
            "release_links": None if release_links is None else build_release_links(ticker, release_links),
            "news": None
            if bundle is None
            else build_news(ticker, bundle.get("daily") or [], bundle.get("articles") or [], clock),
            "intraday": build_intraday((hourly or {}).get(ticker, []), full_history),
            "short_interest": interest.get(ticker),
            "filings": None if display_rows is None else build_filings(display_rows),
            "insider_30d": None if filing_rows is None else build_insider(filing_rows, clock.date()),
            "contracts": award,
            "price_status": "available" if history else "unavailable",
            "price_as_of": history[-1]["date"] if history else None,
        }
        if filing_status:
            instruments[ticker]["filings_status"] = filing_status

    # Quarterly fundamentals are per ticker on chart_data. They are not repeated here.
    del fundamentals
    available = any(item["price_status"] == "available" for item in instruments.values())
    return _compact_tree({
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "data_status": "available" if available else "unavailable",
        "tickers": instruments,
        "events": None if events is None else build_events(events, clock.date()),
        "releases": (
            None
            if releases is None and release_calendar is None
            else build_releases(releases or [], release_calendar or [])
        ),
        "status": status,
        "rates": (
            build_rates(rates_observations, rates_fomc, rates_attempts) if rates is _UNSET else rates
        ),
        "market": market_block(clock),
    })


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


def _frame_rows(frame: pl.DataFrame, date_column: str | None = None, last_dates: int | None = None) -> list[dict]:
    """Materialize a frame, keeping only the columns already loaded and the newest dates."""
    if frame.is_empty():
        return []
    if date_column and date_column in frame.columns and last_dates:
        dated = frame.with_columns(pl.col(date_column).cast(pl.Utf8).str.slice(0, 10).alias("_day"))
        days = dated.select("_day").drop_nulls().unique().sort("_day")
        if days.height > last_dates:
            cutoff = days["_day"][days.height - last_dates]
            dated = dated.filter(pl.col("_day") >= cutoff)
        frame = dated.drop("_day")
    return frame.to_dicts()


def _price_rows(frame: pl.DataFrame) -> list[dict]:
    if frame.is_empty():
        return []
    keep = [name for name in PRICE_COLUMNS if name in frame.columns]
    if keep:
        frame = frame.select(keep)
    if "date" in frame.columns:
        frame = frame.sort("date")
    return frame.to_dicts()


def _hourly_rows(frame: pl.DataFrame) -> list[dict]:
    column = "ts_utc" if "ts_utc" in frame.columns else "ts" if "ts" in frame.columns else None
    return _frame_rows(frame, column, 5)


def _group_rows(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("ticker") or "").upper(), []).append(row)
    return grouped


def load_rates_block() -> dict | None:
    """Build rates, or return None when a macro or rates object is missing or forbidden.

    A missing key without ListBucket permission surfaces as AccessDenied. Either that or
    NoSuchKey must degrade this block instead of failing the dashboard build.
    """
    from lake import lake_read_denied_or_missing, read_json, read_parquet_prefix
    from observability import logger

    observations: dict[str, list[dict]] = {}
    try:
        for series_id in CURVE_SERIES_IDS:
            frame = read_parquet_prefix(
                f"curated/macro_daily/source=fred/series_id={series_id}/", columns=MACRO_COLUMNS,
            )
            observations[series_id] = frame.to_dicts() if not frame.is_empty() else []
        fomc = read_json("serving/rates/fomc.json")
        attempts = read_json("serving/rates/attempts.json")
    except Exception as exc:  # AccessDenied and a missing object are optional
        if not lake_read_denied_or_missing(exc):
            raise
        logger.warning(
            "rates_input_unavailable",
            extra={"error_type": type(exc).__name__, "error_code": _rates_error_code(exc)},
        )
        return None
    if fomc is None and attempts is None and not any(observations.values()):
        logger.warning("rates_input_unavailable", extra={"error_type": "Missing", "error_code": "NoSuchKey"})
        return None
    return build_rates(
        observations,
        fomc if isinstance(fomc, dict) else None,
        attempts if isinstance(attempts, dict) else None,
    )


def _rates_error_code(exc: BaseException) -> str:
    from lake import lake_error_code

    return lake_error_code(exc)


def publish_charts_sequentially(tickers: list[str], load_chart_inputs, generated_at: str) -> None:
    """Build and write one ticker's chart, then drop its inputs before the next read."""
    from lake import write_json_and_gzip

    for ticker in tickers:
        inputs = load_chart_inputs(ticker)
        document = build_chart_data(ticker=ticker, generated_at=generated_at, **inputs)
        write_json_and_gzip(document, f"serving/chart_data/{ticker}.json", cache_seconds=30)
        del inputs, document


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from lake import read_json, read_parquet_prefix, read_prices, write_json_and_gzip
    from observability import job_handler, logger
    from universe import collection_universe, user_ticker_union

    @job_handler(
        "DASHBOARD",
        emit_event=False,
        lease_key="serving/dashboard-build/_lease.json",
        skip_when_leased=True,
    )
    def run(event, context):
        covered = collection_universe(user_ticker_union())
        tickers = covered["equities"] + covered["etfs"]
        article_frame = read_parquet_prefix("curated/news_articles/")
        daily_frame = read_parquet_prefix("curated/news_daily/")
        article_rows = article_frame.to_dicts() if not article_frame.is_empty() else []
        daily_rows = daily_frame.to_dicts() if not daily_frame.is_empty() else []
        del article_frame, daily_frame
        news_data = {
            ticker: {"daily": [row for row in daily_rows if row.get("ticker") == ticker], "articles": article_rows}
            for ticker in tickers
        }
        filing_frame = read_parquet_prefix("curated/filings/")
        event_frame = read_parquet_prefix("curated/events/")
        filing_rows = filing_frame.to_dicts() if not filing_frame.is_empty() else []
        event_rows = event_frame.to_dicts() if not event_frame.is_empty() else []
        del filing_frame, event_frame
        filings_by_ticker: dict[str, list[dict]] = {ticker: [] for ticker in tickers}
        for row in filing_rows:
            symbol = str(row.get("ticker") or "").upper()
            if symbol in filings_by_ticker:
                filings_by_ticker[symbol].append(row)
        from sec_filings import dashboard_rows_from_serving

        served_filings: dict[str, dict] = {}
        for ticker in tickers:
            document = read_json(f"serving/filings/{ticker}.json")
            if not isinstance(document, dict):
                continue
            rows, status = dashboard_rows_from_serving(document)
            served_filings[ticker] = {
                "rows": rows,
                "status": status,
                "entity_name": document.get("entity_name"),
                "description": document.get("description"),
            }
        rollup = read_json("curated/contracts_rollup/latest.json") or []
        contracts_data = {row.get("ticker"): row for row in rollup if isinstance(row, dict)}
        short_frame = read_parquet_prefix("curated/short_interest/", columns=SHORT_COLUMNS)
        short_rows = short_frame.to_dicts() if not short_frame.is_empty() else []
        del short_frame
        short_by_ticker = _group_rows(short_rows)
        options_frame = read_parquet_prefix("curated/options_daily/", columns=OPTIONS_COLUMNS)
        options_rows = options_frame.to_dicts() if not options_frame.is_empty() else []
        del options_frame
        options_by_ticker = _group_rows(options_rows)
        fundamental_doc = read_json("serving/fundamentals_quarterly.json") or {}
        fundamental_rows = fundamental_doc.get("rows", [])
        status = read_json("serving/status.json")
        release_frame = read_parquet_prefix("curated/releases/")
        calendar = read_json("curated/release_calendar/upcoming.json") or []
        links = read_parquet_prefix("curated/release_links/")
        release_rows = release_frame.to_dicts() if not release_frame.is_empty() else []
        link_rows = links.to_dicts() if not links.is_empty() else []
        del release_frame, links
        rates_block = load_rates_block()
        generated_at = datetime.now(UTC).isoformat(timespec="seconds")
        price_data: dict[str, list[dict]] = {}
        trend_data: dict[str, dict | None] = {}
        hourly_data: dict[str, list[dict]] = {}
        # Equities are published before the ETF loop reaches their prices. β needs both
        # calendars, so the proxies are read once up front and reused.
        index_prices = load_index_proxy_prices(
            read_prices, tuple(symbol for symbol in INDEX_PROXIES if symbol in tickers),
        )

        def load_chart_inputs(ticker: str) -> dict:
            if ticker in index_prices:
                price_rows = index_prices[ticker]
            else:
                price_frame = read_prices(ticker)  # ~1 GET per year of history (yearly partitions)
                price_rows = _price_rows(price_frame)
                del price_frame
            price_data[ticker] = price_rows
            trend_data[ticker] = read_json(f"serving/trend_metrics/latest/{ticker}.json")
            trend_frame = read_parquet_prefix(f"serving/trend_metrics/ticker={ticker}/", columns=TREND_COLUMNS)
            trend_rows = _frame_rows(trend_frame, "date", MAX_PRICE_ROWS)
            del trend_frame
            hourly_frame = read_parquet_prefix(f"curated/prices_hourly/ticker={ticker}/", columns=HOURLY_COLUMNS)
            hourly_data[ticker] = _hourly_rows(hourly_frame)
            del hourly_frame
            return {
                "trend_rows": trend_rows,
                "short_interest_rows": short_by_ticker.get(ticker, []),
                "options_rows": options_by_ticker.get(ticker, []),
                "fundamentals": fundamental_rows,
                "price_rows": price_rows,
                "index_prices": index_prices,
            }

        publish_charts_sequentially(tickers, load_chart_inputs, generated_at)
        snapshot = build_snapshot(
            tickers=tickers,
            prices=price_data,
            trends=trend_data,
            fundamentals=fundamental_rows,
            status=status,
            generated_at=generated_at,
            news=news_data,
            hourly=hourly_data,
            short_interest=short_by_ticker,
            filings=filings_by_ticker,
            served_filings=served_filings,
            events=event_rows,
            contracts=contracts_data,
            releases=release_rows,
            release_calendar=calendar if isinstance(calendar, list) else [],
            release_links=link_rows,
            rates=rates_block,
        )
        size = write_json_and_gzip(snapshot, "serving/dashboard.json", cache_seconds=30)
        _emit_dashboard_size(size["raw_bytes"], size["gzip_bytes"])
        logger.info(
            "dashboard_snapshot_published",
            extra={
                "tickers": len(tickers),
                "tickers_with_prices": sum(bool(row["price_history"]) for row in snapshot["tickers"].values()),
                "fundamentals": sum(
                    1 for row in fundamental_rows if row.get("ticker") in tickers and row.get("metric")
                ),
                "generated_at": snapshot["generated_at"],
                "rates": rates_block is not None,
                "raw_bytes": size["raw_bytes"],
                "gzip_bytes": size["gzip_bytes"],
            },
        )
        return {
            "status": snapshot["data_status"],
            "tickers": len(tickers),
            "tickers_with_prices": sum(bool(row["price_history"]) for row in snapshot["tickers"].values()),
            "rates": rates_block is not None,
        }

    return run(event, context)
