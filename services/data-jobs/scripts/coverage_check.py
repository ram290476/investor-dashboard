#!/usr/bin/env python3
"""Per-ticker coverage of the lake series the dashboard serves.

Operator credentials (this is not a job role). Prices, trend metrics, short interest,
fundamentals, serving/dashboard.json and serving/chart_data are required. Hourly bars
and options are reported and are not required: those collectors run on weekdays.

A required series passes when it has at least one row. The dashboard row also has to
be present with price_status "available" and a non-empty price_history. Listing date
is not checked, so a name that started trading inside the five-year window still passes.

    LAKE_BUCKET=invdash-lake-<acct> python scripts/coverage_check.py
    python scripts/coverage_check.py --bucket invdash-lake-<acct> --tickers TSLA,SPCX,META

Exits 1 when any required series is missing.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REQUIRED = ("prices", "trend_metrics", "short_interest", "fundamentals", "dashboard", "chart_data")
OPTIONAL = ("hourly", "options")
_DATE_FIELDS = {
    "prices": ("date",),
    "trend_metrics": ("date",),
    "short_interest": ("settlement_date",),
    "fundamentals": ("period_end", "release_date", "date"),
    "hourly": ("ts_utc", "ts", "date"),
    "options": ("date",),
}


def _dates(rows: list[dict], fields: tuple[str, ...]) -> list[str]:
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for field in fields:
            raw = row.get(field)
            if raw:
                found.append(str(raw)[:10])
                break
    return [day for day in found if len(day) >= 10]


def span(rows: list[dict] | None, fields: tuple[str, ...]) -> dict:
    """Row count and the min/max date prefix. Empty input has no start."""
    rows = list(rows or [])
    dates = _dates(rows, fields)
    return {
        "rows": len(rows),
        "start": min(dates) if dates else None,
        "end": max(dates) if dates else None,
    }


def _ticker_rows(rows: list[dict], ticker: str) -> list[dict]:
    symbol = ticker.upper()
    return [row for row in rows if str(row.get("ticker") or "").upper() == symbol]


def dashboard_view(ticker: str, dashboard: dict | None) -> dict:
    block = None
    if isinstance(dashboard, dict):
        tickers = dashboard.get("tickers")
        if isinstance(tickers, dict):
            block = tickers.get(ticker)
    history = block.get("price_history") if isinstance(block, dict) else None
    history = history if isinstance(history, list) else []
    dates = _dates([row for row in history if isinstance(row, dict)], ("date",))
    return {
        "present": isinstance(block, dict),
        "price_status": block.get("price_status") if isinstance(block, dict) else None,
        "rows": len(history),
        "start": min(dates) if dates else None,
        "end": max(dates) if dates else None,
    }


def chart_view(document: dict | None) -> dict:
    present = isinstance(document, dict)
    return {"present": present, "rows": 1 if present else 0, "start": None, "end": None}


def _select(rows: list[dict] | None, ticker: str) -> list[dict]:
    """Keep this ticker. A frame that has no ticker column is already one name's prefix."""
    clean = [row for row in rows or [] if isinstance(row, dict)]
    if any(row.get("ticker") for row in clean):
        return _ticker_rows(clean, ticker)
    return clean


def assess(ticker: str, series: dict) -> dict:
    """Build one ticker's coverage record from already loaded rows and documents.

    `series` holds row lists for prices, trend_metrics, short_interest, fundamentals,
    hourly and options. `dashboard` is the serving document (or None). `chart` is the
    chart document, or None when the object is missing.
    """
    views = {
        name: span(_select(series.get(name), ticker), _DATE_FIELDS[name])
        for name in ("prices", "trend_metrics", "short_interest", "fundamentals", "hourly", "options")
    }
    views["dashboard"] = dashboard_view(ticker, series.get("dashboard"))
    views["chart_data"] = chart_view(series.get("chart"))
    return {"ticker": ticker, "series": views, "gaps": required_gaps(views)}


def required_gaps(views: dict) -> list[str]:
    """Names of required series that are missing or, for the dashboard, not servable."""
    gaps = []
    for name in ("prices", "trend_metrics", "short_interest", "fundamentals"):
        if int(views.get(name, {}).get("rows") or 0) < 1:
            gaps.append(name)
    dashboard = views.get("dashboard") or {}
    rows = int(dashboard.get("rows") or 0)
    served = dashboard.get("present") and dashboard.get("price_status") == "available" and rows > 0
    if not served:
        gaps.append("dashboard")
    if not (views.get("chart_data") or {}).get("present"):
        gaps.append("chart_data")
    return gaps


def format_report(report: list[dict]) -> str:
    lines = []
    for item in report:
        parts = [item["ticker"]]
        for name in (*REQUIRED, *OPTIONAL):
            info = item["series"][name]
            if name == "dashboard":
                parts.append(
                    f"dashboard {info.get('price_status') or 'missing'} "
                    f"{info.get('start') or '-'}..{info.get('end') or '-'} ({info.get('rows') or 0})"
                )
            elif name == "chart_data":
                parts.append("chart yes" if info.get("present") else "chart missing")
            else:
                parts.append(f"{name} {info.get('start') or '-'}..{info.get('end') or '-'} ({info.get('rows') or 0})")
        lines.append("  ".join(parts))
        lines.append("  gaps: " + (", ".join(item["gaps"]) if item["gaps"] else "none"))
    return "\n".join(lines)


def _frame_rows(frame, ticker: str | None = None) -> list[dict]:
    if frame is None or frame.is_empty():
        return []
    rows = frame.to_dicts()
    if ticker and "ticker" in frame.columns:
        return _ticker_rows(rows, ticker)
    return rows


def load_report(bucket: str, tickers: list[str]) -> list[dict]:
    """Read the lake and assess each ticker. Uses the caller's credentials."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "app"))
    import lake

    dashboard = lake.read_json("serving/dashboard.json", bucket) or {}
    short_rows = _frame_rows(lake.read_parquet_prefix("curated/short_interest/", bucket))
    options_rows = _frame_rows(lake.read_parquet_prefix("curated/options_daily/", bucket))
    curated_fundamentals = _frame_rows(lake.read_parquet_prefix("curated/fundamentals_quarterly/", bucket))
    served_fundamentals = []
    document = lake.read_json("serving/fundamentals_quarterly.json", bucket) or {}
    if isinstance(document, dict):
        served_fundamentals = [row for row in document.get("rows") or [] if isinstance(row, dict)]
    report = []
    for ticker in tickers:
        symbol = ticker.upper()
        fundamental_rows = _ticker_rows(curated_fundamentals, symbol) or _ticker_rows(served_fundamentals, symbol)
        report.append(assess(symbol, {
            "prices": _frame_rows(lake.read_prices(symbol, bucket), symbol),
            "trend_metrics": _frame_rows(lake.read_parquet_prefix(f"serving/trend_metrics/ticker={symbol}/", bucket)),
            "short_interest": short_rows,
            "fundamentals": fundamental_rows,
            "hourly": _frame_rows(lake.read_parquet_prefix(f"curated/prices_hourly/ticker={symbol}/", bucket), symbol),
            "options": options_rows,
            "dashboard": dashboard,
            "chart": lake.read_json(f"serving/chart_data/{symbol}.json", bucket),
        }))
    return report


def _watchlist(tickers: str) -> list[str]:
    if tickers.strip():
        return [part.strip().upper() for part in tickers.split(",") if part.strip()]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "app"))
    from universe import user_ticker_union, watchlist_universe

    listed = watchlist_universe(user_ticker_union()).get("tickers") or []
    return [str(ticker).upper() for ticker in listed]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report per-ticker lake coverage for the watchlist.")
    parser.add_argument("--bucket", default=os.environ.get("LAKE_BUCKET", ""), help="Lake bucket (or LAKE_BUCKET).")
    parser.add_argument("--tickers", default="", help="Comma-separated symbols. Default: the watchlist universe.")
    args = parser.parse_args(argv)
    if not args.bucket:
        print("LAKE_BUCKET is required", file=sys.stderr)
        return 2
    report = load_report(args.bucket, _watchlist(args.tickers))
    print(format_report(report))
    return 1 if any(item["gaps"] for item in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
