"""Yahoo Finance chart API (DS-05), used for history backfill and consolidated volume.

One call per ticker: 5 years of daily bars at setup (job O1) and when a user adds a
ticker nobody followed before (TickerAdded event -> backfill job). price_reconcile reads
volume from the chart it already requests; it does not add a call. Unofficial endpoint
with no SLA: Alpaca daily bars are the price fallback when it throttles. Yahoo volume is
consolidated US volume. Alpaca's free feed is IEX-only and must not be mixed in unlabeled.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


class YahooRateLimited(RuntimeError):
    """HTTP 429 from the chart host. Do not send another chart request in this run."""


# A 400 is a pre-inception candidate only when Yahoo says the requested dates have no bars.
# Other 400s (and every 404) stay failures. 404 is an unknown symbol: never rewrite it.
_DATE_WINDOW_MARKERS = (
    "data doesn't exist",
    "data does not exist",
    "invalid date",
    "startdate",
    "period1",
    "period2",
)


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


def classify_chart(status: int, payload: dict | None) -> str:
    """Classify one chart response. The caller decides whether a date-window 400 is terminal.

    unknown_symbol: HTTP 404. The requested ticker is kept as-is.
    pre_inception_candidate: HTTP 400 whose body says the requested dates have no data.
    client_error: any other HTTP 400, including a 400 whose text only says the symbol is missing.
    rate_limited: HTTP 429. Do not retry it in this call.
    ok: HTTP 200 with a JSON object. An empty window is still ok; the backfill checks the body.
    """
    if status == 429:
        return "rate_limited"
    if status == 404:
        return "unknown_symbol"
    if status == 400:
        error = ((payload or {}).get("chart") or {}).get("error") or {}
        text = f"{error.get('code') or ''} {error.get('description') or ''}".lower()
        if "not found" in text:
            return "client_error"
        if any(marker in text for marker in _DATE_WINDOW_MARKERS):
            return "pre_inception_candidate"
        return "client_error"
    if status >= 500:
        return "upstream"
    if status >= 400 or not isinstance(payload, dict):
        return "client_error"
    return "ok"


def parse_chart(payload: dict, ticker: str) -> list[dict]:
    """Rows for curated/prices_daily: ticker, date (exchange-local), close, adj_close, volume, source_id.

    volume is consolidated and volume_source is DS-05. volume_iex is null; Alpaca is the IEX print.
    """
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
        raw_volume = (quote.get("volume") or [None])[i] if i < len(quote.get("volume") or []) else None
        rows.append(
            {
                "ticker": ticker,
                "date": local.date(),
                "close": float(close),
                "adj_close": float(adj[i]) if i < len(adj) and adj[i] is not None else None,
                "volume": int(raw_volume) if raw_volume is not None else None,
                "volume_iex": None,
                "volume_source": "DS-05",
                "source_id": "DS-05",
            }
        )
    return rows
