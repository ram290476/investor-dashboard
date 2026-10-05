"""Which tickers the collectors cover.

The universe is the union of every user's "My tickers" (DynamoDB user_prefs),
the base tickers, and the index ETF proxies, capped so free-tier limits hold.

Free-tier budget per extra ticker (one run each):
- Alpaca bars (H1): one multi-symbol call covers up to 100 symbols, so the cap barely matters.
- Finnhub company news (H2): one call per ticker per hour; 60 calls/min allows ~50 tickers per run.
- Massive daily close check (D4): one call per ticker at 5 calls/min, so 40 tickers take ~8 minutes.
- Alpha Vantage NEWS_SENTIMENT: 25 calls/day, schedule uses 16. Its `tickers` filter returns
  articles that mention ALL listed tickers, so each call covers one ticker. Sentiment therefore
  stays on the base tickers plus a small daily rotation (sentiment_plan below), not the union.
"""

from __future__ import annotations

import os
from datetime import date

import boto3

BASE_TICKERS: tuple[str, ...] = ("TSLA", "SPCX")
INDEX_PROXIES: tuple[str, ...] = ("SPY", "DIA", "QQQ", "IWM", "XLY", "ITA", "SMH")
MAX_USER_TICKERS = int(os.getenv("MAX_USER_TICKERS", "25"))  # cap on the user-ticker union beyond the base list
PREFS_TABLE = os.getenv("PREFS_TABLE", "invdash-user-prefs")

SENTIMENT_CALLS_PER_DAY = 16  # stays under Alpha Vantage's 25/day with headroom for failover (DS-03)
SENTIMENT_ROTATION_SLOTS = 4  # calls per day given to user tickers beyond the base list


def _valid(symbol: str) -> bool:
    return 1 <= len(symbol) <= 10 and symbol.replace(".", "").replace("-", "").isalnum() and symbol.isupper()


def user_ticker_union(table_name: str = PREFS_TABLE, dynamodb=None) -> list[str]:
    """Scan only user_sub and tickers (the collector policy allows nothing else), most popular first."""
    table = (dynamodb or boto3.resource("dynamodb")).Table(table_name)
    counts: dict[str, int] = {}
    kwargs = {"ProjectionExpression": "user_sub, tickers", "Select": "SPECIFIC_ATTRIBUTES"}
    while True:
        page = table.scan(**kwargs)
        for item in page.get("Items", []):
            for t in item.get("tickers", []) or []:
                sym = str(t).upper()
                if _valid(sym):
                    counts[sym] = counts.get(sym, 0) + 1
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    ranked = sorted(counts, key=lambda s: (-counts[s], s))
    return ranked


def collection_universe(user_tickers: list[str], cap: int = MAX_USER_TICKERS) -> dict[str, list[str]]:
    """Return {'equities': base + capped user tickers, 'etfs': index proxies, 'dropped': over the cap}."""
    extra = [t for t in user_tickers if t not in BASE_TICKERS and t not in INDEX_PROXIES]
    return {
        "equities": list(BASE_TICKERS) + extra[:cap],
        "etfs": list(INDEX_PROXIES),
        "dropped": extra[cap:],
    }


def sentiment_plan(equities: list[str], day: date) -> list[str]:
    """Ticker for each of the day's sentiment calls, in run order (hourly 06:15-21:15 ET).

    Base tickers alternate in the remaining slots; SENTIMENT_ROTATION_SLOTS calls rotate
    through the other tickers so each is refreshed every few days.
    """
    others = [t for t in equities if t not in BASE_TICKERS]
    rotation: list[str] = []
    if others:
        start = (day.toordinal() * SENTIMENT_ROTATION_SLOTS) % len(others)
        rotation = [others[(start + i) % len(others)] for i in range(min(SENTIMENT_ROTATION_SLOTS, len(others)))]
    base_slots = SENTIMENT_CALLS_PER_DAY - len(rotation)
    plan = [BASE_TICKERS[i % len(BASE_TICKERS)] for i in range(base_slots)]
    # Spread rotation calls through the day instead of bunching them at the end.
    step = max(1, SENTIMENT_CALLS_PER_DAY // max(1, len(rotation)))
    for i, t in enumerate(rotation):
        plan.insert(min(len(plan), i * step + step // 2), t)
    return plan[:SENTIMENT_CALLS_PER_DAY]
