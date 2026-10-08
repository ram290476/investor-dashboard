"""Alpaca market data (DS-02 bars, DS-92 options). Free plan: IEX stock feed, indicative options feed.

Bars: one multi-symbol call per 100 symbols covers the user-ticker union and the index ETF
proxies (SPY, DIA, QQQ, IWM, XLY, ITA, SMH) in H1 and the D4 close check.

Options (DS-92, job options_daily): chain snapshots on the free 'indicative' feed give
15-minute-delayed derived quotes. IV30 is null when the feed omits implied volatility.
"""

from __future__ import annotations

import re
from datetime import date, datetime

DATA = "https://data.alpaca.markets"
BARS_PATH = "/v2/stocks/bars"
CHAIN_PATH = "/v1beta1/options/snapshots/{underlying}"
OCC_RE = re.compile(r"^(?P<root>[A-Z.]{1,6})(?P<ymd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


def bars_params(
    symbols: list[str],
    start: str,
    end: str | None = None,
    timeframe: str = "1Hour",
    adjustment: str = "split",
) -> list[dict]:
    """One request per 100 symbols; follow next_page_token within each.

    adjustment: "raw" (traded prices), "split", "dividend" or "all" (split + dividend), each
    relative to the request date.
    """
    out = []
    for i in range(0, len(symbols), 100):
        p = {"symbols": ",".join(symbols[i : i + 100]), "timeframe": timeframe, "start": start, "feed": "iex"}
        p["limit"] = 10000
        p["adjustment"] = adjustment
        if end:
            p["end"] = end
        out.append(p)
    return out


def parse_bars(payload: dict) -> list[dict]:
    rows = []
    for sym, bars in (payload.get("bars") or {}).items():
        for b in bars:
            rows.append(
                {
                    "ticker": sym,
                    "bar_ts": b["t"],
                    "open": b["o"],
                    "high": b["h"],
                    "low": b["l"],
                    "close": b["c"],
                    "volume": b["v"],
                    "vwap": b.get("vw"),
                    "trade_count": b.get("n"),
                    "feed": "iex",
                }
            )
    return rows


def parse_occ(symbol: str) -> dict | None:
    m = OCC_RE.match(symbol)
    if not m:
        return None
    ymd = m["ymd"]
    return {
        "root": m["root"],
        "expiration": date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:])),
        "type": "call" if m["cp"] == "C" else "put",
        "strike": int(m["strike"]) / 1000.0,
    }


def options_metrics(snapshots: dict, spot: float, as_of: date) -> dict:
    """Put/call volume ratio over the whole chain and a 30-day at-the-money IV (iv30).

    iv30 = mean IV of the call and put at the strike nearest spot, on the expiration
    nearest 30 calendar days out among those 20-45 days away.
    """
    vol = {"call": 0.0, "put": 0.0}
    near: dict[date, list[tuple[float, str, float | None]]] = {}
    for sym, snap in snapshots.items():
        c = parse_occ(sym)
        if not c:
            continue
        vol[c["type"]] += float((snap.get("dailyBar") or {}).get("v") or 0)
        days = (c["expiration"] - as_of).days
        if 20 <= days <= 45:
            near.setdefault(c["expiration"], []).append((c["strike"], c["type"], snap.get("impliedVolatility")))
    pcr = vol["put"] / vol["call"] if vol["call"] else None
    iv30, expiry = None, None
    if near:
        expiry = min(near, key=lambda e: abs((e - as_of).days - 30))
        contracts = near[expiry]
        atm = min({s for s, _, _ in contracts}, key=lambda s: abs(s - spot))
        ivs = [iv for s, _, iv in contracts if s == atm and iv is not None]
        iv30 = sum(ivs) / len(ivs) if ivs else None
    return {
        "put_call_volume_ratio": pcr,
        "call_volume": vol["call"],
        "put_volume": vol["put"],
        "iv30": iv30,
        "iv30_expiration": expiry.isoformat() if expiry else None,
        "iv_available": iv30 is not None,
    }


def chain_params(as_of: date, page_token: str | None = None) -> dict:
    p = {"feed": "indicative", "limit": 1000}
    if page_token:
        p["page_token"] = page_token
    return p


def iso_date(ts: str) -> date:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
