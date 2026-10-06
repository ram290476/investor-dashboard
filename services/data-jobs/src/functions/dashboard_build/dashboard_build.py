"""Build the compact, read-only serving document consumed by the dashboard API."""

from __future__ import annotations

from datetime import UTC, datetime

MAX_PRICE_ROWS = 1260


def build_snapshot(
    tickers: list[str],
    prices: dict[str, list[dict]],
    trends: dict[str, dict | None],
    fundamentals: list[dict],
    status: dict | None,
    generated_at: str | None = None,
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
        instruments[ticker] = {
            "ticker": ticker,
            "price_history": history,
            "trend": trends.get(ticker),
            "price_status": "available" if history else "unavailable",
            "price_as_of": history[-1]["date"] if history else None,
        }

    normalized_fundamentals = [
        row for row in fundamentals if row.get("ticker") in instruments and row.get("metric")
    ]
    available = any(item["price_status"] == "available" for item in instruments.values())
    return {
        "schema_version": 1,
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "data_status": "available" if available else "unavailable",
        "tickers": instruments,
        "fundamentals": normalized_fundamentals,
        "status": status,
    }


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from lake import read_json, read_prices, write_json
    from observability import job_handler, logger
    from universe import collection_universe, user_ticker_union

    @job_handler("DASHBOARD", emit_event=False)
    def run(event, context):
        tickers = collection_universe(user_ticker_union())["equities"]
        price_data: dict[str, list[dict]] = {}
        trend_data: dict[str, dict | None] = {}
        for ticker in tickers:
            frame = read_prices(ticker)  # ~1 GET per year of history (yearly partitions)
            price_data[ticker] = frame.to_dicts() if not frame.is_empty() else []
            trend_data[ticker] = read_json(f"serving/trend_metrics/latest/{ticker}.json")

        fundamental_doc = read_json("serving/fundamentals_quarterly.json") or {}
        status = read_json("serving/status.json")
        snapshot = build_snapshot(
            tickers=tickers,
            prices=price_data,
            trends=trend_data,
            fundamentals=fundamental_doc.get("rows", []),
            status=status,
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
