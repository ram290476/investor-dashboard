"""Kalshi public market data (DS-89): meeting-level Fed decision odds and CPI outcome odds.

No login needed for market data. Documented base is external-api.kalshi.com (the catalog's
api.elections.kalshi.com still works and is allowlisted too). Prices are fixed-point
dollar strings (e.g. "0.5600"), read as probabilities. Thin markets can be noisy, so
each row keeps bid, ask and volume next to the mid used as the probability.
"""

from __future__ import annotations


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_markets(payload: dict, series_ticker: str, as_of: str) -> list[dict]:
    """Rows for the rate_odds table: source, meeting_or_horizon, as_of, outcome, probability."""
    rows = []
    for m in payload.get("markets", []):
        bid, ask, last = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars")), _f(m.get("last_price_dollars"))
        mid = (bid + ask) / 2 if bid is not None and ask is not None and ask > 0 else last
        rows.append(
            {
                "source": "kalshi",
                "series": series_ticker,
                "meeting_or_horizon": m.get("event_ticker"),
                "market_ticker": m.get("ticker"),
                "as_of": as_of,
                "outcome": m.get("yes_sub_title") or m.get("subtitle") or m.get("ticker"),
                "probability": mid,
                "yes_bid": bid,
                "yes_ask": ask,
                "volume": _f(m.get("volume_fp") or m.get("volume")),
                "close_time": m.get("close_time"),
                "source_id": "DS-89",
            }
        )
    return rows
