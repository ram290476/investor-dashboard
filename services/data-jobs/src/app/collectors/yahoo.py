"""Yahoo Finance chart API (DS-05), used only for history backfill.

One call per ticker: 5 years of daily bars at setup (job O1) and when a user adds a
ticker nobody followed before (TickerAdded event -> backfill job). Unofficial endpoint
with no SLA: Alpaca daily bars are the fallback when it throttles.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


def chart_params(years: int = 5, start: date | None = None, end: date | None = None) -> dict:
    """Return a historical range or an explicit [start, end) batch."""
    params = {"interval": "1d", "events": "split,div", "includeAdjustedClose": "true"}
    if start is None and end is None:
        return {**params, "range": f"{years}y"}
    if start is None or end is None or start >= end:
        raise ValueError("start and end must define a non-empty half-open date range")
    params.update(
        period1=int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp()),
        period2=int(datetime.combine(end, datetime.min.time(), tzinfo=UTC).timestamp()),
    )
    return params


def _local_date(ts: int, offset: timedelta) -> date:
    return (datetime.fromtimestamp(int(ts), tz=UTC) + offset).date()


def parse_events(payload: dict) -> list[dict]:
    """Splits and dividends in the response window, by exchange-local ex-date, oldest first.

    split:    {"type": "split", "date", "ratio": numerator / denominator, "label": "N:D"}
              (prices before the ex-date divide by ratio to become split-adjusted)
    dividend: {"type": "dividend", "date", "amount"}  (split-adjusted cash amount)
    Requires events=split,div in the request (chart_params sets it).
    """
    result = ((payload.get("chart") or {}).get("result") or [{}])[0]
    offset = timedelta(seconds=int(result.get("meta", {}).get("gmtoffset", 0)))
    events = result.get("events") or {}
    out = []
    for split in (events.get("splits") or {}).values():
        num, den = float(split["numerator"]), float(split["denominator"])
        if num > 0 and den > 0:
            out.append(
                {
                    "type": "split",
                    "date": _local_date(split["date"], offset),
                    "ratio": num / den,
                    "label": split.get("splitRatio") or f"{num:g}:{den:g}",
                }
            )
    for div in (events.get("dividends") or {}).values():
        out.append({"type": "dividend", "date": _local_date(div["date"], offset), "amount": float(div["amount"])})
    return sorted(out, key=lambda e: (e["date"], e["type"]))


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
