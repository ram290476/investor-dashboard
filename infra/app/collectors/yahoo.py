"""Yahoo Finance chart API (DS-05), used only for history backfill.

One call per ticker: 5 years of daily bars at setup (job O1) and when a user adds a
ticker nobody followed before (TickerAdded event -> backfill job). Unofficial endpoint
with no SLA: Alpaca daily bars are the fallback when it throttles.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


def chart_params(years: int = 5) -> dict:
    return {"interval": "1d", "range": f"{years}y", "events": "split,div", "includeAdjustedClose": "true"}


def parse_chart(payload: dict, ticker: str) -> list[dict]:
    """Rows for curated/prices_daily: ticker, date (exchange-local), close, adj_close, volume, source_id."""
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        err = (payload.get("chart") or {}).get("error")
        raise ValueError(f"Yahoo returned no data for {ticker}: {err}")
    r = result[0]
    offset = timedelta(seconds=int(r.get("meta", {}).get("gmtoffset", 0)))
    quote = (r.get("indicators", {}).get("quote") or [{}])[0]
    adj = (r.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []
    rows = []
    for i, ts in enumerate(r.get("timestamp") or []):
        close = (quote.get("close") or [None])[i] if i < len(quote.get("close") or []) else None
        if close is None:
            continue
        local = datetime.fromtimestamp(ts, tz=UTC) + offset
        rows.append(
            {
                "ticker": ticker,
                "date": local.date(),
                "close": float(close),
                "adj_close": float(adj[i]) if i < len(adj) and adj[i] is not None else None,
                "volume": (quote.get("volume") or [None])[i],
                "source_id": "DS-05",
            }
        )
    return rows
