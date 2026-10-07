"""Build the compact, read-only serving document consumed by the dashboard API."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

MAX_PRICE_ROWS = 1260
SCHEMA_VERSION = 2
ET = ZoneInfo("America/New_York")


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
    hourly: dict[str, list[dict]] | None = None,
    short_interest: dict[str, list[dict]] | None = None,
    releases: list[dict] | None = None,
    release_calendar: list[dict] | None = None,
) -> dict:
    """Create a deterministic API document; absent source data remains explicitly unavailable."""
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
        interest = latest_short_interest((short_interest or {}).get(ticker, []))
        instruments[ticker] = {
            "ticker": ticker,
            "price_history": history,
            "trend": trends.get(ticker),
            "intraday": build_intraday((hourly or {}).get(ticker, []), history),
            "short_interest": interest.get(ticker),
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
        "releases": (
            None
            if releases is None and release_calendar is None
            else build_releases(releases or [], release_calendar or [])
        ),
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
        release_frame = read_parquet_prefix("curated/releases/")
        calendar = read_json("curated/release_calendar/upcoming.json") or []
        snapshot = build_snapshot(
            tickers=tickers,
            prices=price_data,
            trends=trend_data,
            fundamentals=fundamental_doc.get("rows", []),
            status=status,
            hourly=hourly_data,
            short_interest=short_by_ticker,
            releases=release_frame.to_dicts() if not release_frame.is_empty() else [],
            release_calendar=calendar if isinstance(calendar, list) else [],
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
