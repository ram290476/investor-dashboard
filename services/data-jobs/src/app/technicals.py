"""Live Technical Summary (#191, #196): indicators and per-horizon votes from daily closes.

Pure functions over the served price history (``[{date, close, volume?}]``, oldest first,
split- and dividend-adjusted). dashboard_build publishes the result on
``serving/chart_data/<T>.json`` as ``technical_summary`` in the shape that
apps/web/technical-summary.js renders.

The lake stores daily closes and volume only (no intraday high/low). Stochastic(14,3,3),
ADX(14) and support/resistance therefore use the close-only forms: the 14-day high/low
range of closes, directional movement from close-to-close changes, and swing closes. Each
such indicator carries ``close_only: true`` and the method note says so.
"""

from __future__ import annotations

import math
from datetime import date

HORIZONS = (
    {"id": "short", "label": "Short-term", "window": "Next week", "requiredBars": 35},
    {"id": "intermediate", "label": "Intermediate-term", "window": "3 weeks \u2013 1 month", "requiredBars": 60},
    {"id": "long", "label": "Long-term", "window": "Next 3 months", "requiredBars": 200},
)
# Lookback (sessions) for support/resistance per horizon.
SR_LOOKBACK = {"short": 20, "intermediate": 60, "long": 200}
SWING_SPAN = 3
RSI_LOW, RSI_HIGH = 30.0, 70.0
STOCH_LOW, STOCH_HIGH = 20.0, 80.0
ADX_TREND = 20.0


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


# ---------------------------------------------------------------- indicator math


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    """EMA seeded with the SMA of the first ``period`` values, alpha = 2 / (period + 1)."""
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    out[period - 1] = current
    for i in range(period, len(values)):
        current = alpha * values[i] + (1 - alpha) * current
        out[i] = current
    return out


def _wilder(values: list[float], period: int, start: int = 0) -> list[float | None]:
    """Wilder smoothing (RMA) seeded with the mean of values[start:start+period]."""
    out: list[float | None] = [None] * len(values)
    if len(values) - start < period:
        return out
    current = sum(values[start:start + period]) / period
    out[start + period - 1] = current
    for i in range(start + period, len(values)):
        current = (current * (period - 1) + values[i]) / period
        out[i] = current
    return out


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder RSI."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [0.0] + [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [0.0] + [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = _wilder(gains, period, start=1)
    avg_loss = _wilder(losses, period, start=1)
    for i in range(len(closes)):
        gain, loss = avg_gain[i], avg_loss[i]
        if gain is None or loss is None:
            continue
        if loss == 0:
            out[i] = 100.0 if gain > 0 else 50.0
        else:
            out[i] = 100.0 - 100.0 / (1 + gain / loss)
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """(macd line, signal line, histogram). Signal is the EMA of the defined MACD values."""
    fast_e, slow_e = ema(closes, fast), ema(closes, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(fast_e, slow_e, strict=True)]
    first = next((i for i, value in enumerate(line) if value is not None), None)
    sig: list[float | None] = [None] * len(closes)
    if first is not None:
        sig[first:] = ema([value for value in line[first:]], signal)  # type: ignore[misc]
    hist = [None if a is None or b is None else a - b for a, b in zip(line, sig, strict=True)]
    return line, sig, hist


def _sma_optional(series: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(series)
    for i in range(period - 1, len(series)):
        window = series[i - period + 1:i + 1]
        if all(value is not None for value in window):
            out[i] = sum(window) / period  # type: ignore[arg-type]
    return out


def stochastic(closes: list[float], period: int = 14, smooth_k: int = 3, smooth_d: int = 3):
    """Close-only slow Stochastic(14,3,3): raw %K over the 14-close range, %K = SMA3, %D = SMA3 of %K."""
    raw: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        high, low = max(window), min(window)
        raw[i] = 50.0 if high == low else 100.0 * (closes[i] - low) / (high - low)
    k = _sma_optional(raw, smooth_k)
    return k, _sma_optional(k, smooth_d)


def adx(closes: list[float], period: int = 14):
    """Close-only ADX(14): +DM/-DM from close-to-close moves, TR = |close change|, Wilder smoothing.

    Returns (adx, plus_di, minus_di).
    """
    n = len(closes)
    plus_dm, minus_dm, true_range = [0.0] * n, [0.0] * n, [0.0] * n
    for i in range(1, n):
        change = closes[i] - closes[i - 1]
        plus_dm[i] = max(change, 0.0)
        minus_dm[i] = max(-change, 0.0)
        true_range[i] = abs(change)
    s_tr, s_plus, s_minus = (_wilder(series, period, start=1) for series in (true_range, plus_dm, minus_dm))
    plus_di: list[float | None] = [None] * n
    minus_di: list[float | None] = [None] * n
    dx: list[float | None] = [None] * n
    for i in range(n):
        if s_tr[i] is None:
            continue
        if s_tr[i] == 0:
            plus_di[i] = minus_di[i] = dx[i] = 0.0
            continue
        p = 100 * s_plus[i] / s_tr[i]  # type: ignore[operator]
        m = 100 * s_minus[i] / s_tr[i]  # type: ignore[operator]
        plus_di[i], minus_di[i] = p, m
        dx[i] = 0.0 if p + m == 0 else 100 * abs(p - m) / (p + m)
    first = next((i for i, value in enumerate(dx) if value is not None), None)
    out: list[float | None] = [None] * n
    if first is not None:
        out[first:] = _wilder([value for value in dx[first:]], period)  # type: ignore[misc]
    return out, plus_di, minus_di


def bollinger(closes: list[float], period: int = 20, width: float = 2.0):
    """(middle, upper, lower, %B) with the population standard deviation."""
    mid = sma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    pct_b: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        mean = mid[i]
        sd = math.sqrt(sum((value - mean) ** 2 for value in window) / period)  # type: ignore[operator]
        upper[i], lower[i] = mean + width * sd, mean - width * sd  # type: ignore[operator]
        pct_b[i] = 0.5 if sd == 0 else (closes[i] - lower[i]) / (upper[i] - lower[i])  # type: ignore[operator]
    return mid, upper, lower, pct_b


def obv(closes: list[float], volumes: list[float]) -> list[float]:
    out = [0.0] * len(closes)
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            out[i] = out[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            out[i] = out[i - 1] - volumes[i]
        else:
            out[i] = out[i - 1]
    return out


def support_resistance(closes: list[float], lookback: int, span: int = SWING_SPAN, limit: int = 2):
    """Nearest swing-low closes below and swing-high closes above the last close in the lookback.

    A swing is a close that is the min (max) of the ``span`` closes on each side. Falls back to the
    window low (high) when no swing qualifies.
    """
    if len(closes) < 2:
        return [], []
    window = closes[-lookback:]
    price = closes[-1]
    lows, highs = [], []
    for i in range(span, len(window) - span):
        around = window[i - span:i + span + 1]
        if window[i] == min(around) and window[i] < price:
            lows.append(window[i])
        if window[i] == max(around) and window[i] > price:
            highs.append(window[i])
    support = sorted(set(lows), reverse=True)[:limit]
    resistance = sorted(set(highs))[:limit]
    if not support and min(window) < price:
        support = [min(window)]
    if not resistance and max(window) > price:
        resistance = [max(window)]
    return [round(v, 2) for v in support], [round(v, 2) for v in resistance]


# ---------------------------------------------------------------- votes


def _cmp_vote(a: float | None, b: float | None) -> int | None:
    if a is None or b is None:
        return None
    return 1 if a > b else -1 if a < b else 0


def _sign(value: float | None) -> int | None:
    if value is None:
        return None
    return 1 if value > 0 else -1 if value < 0 else 0


def _fmt(value: float | None, digits: int = 2) -> str:
    return "--" if value is None else f"{value:,.{digits}f}"


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value * 100:+.1f}%"


def _perf(closes: list[float], sessions: int) -> float | None:
    if len(closes) <= sessions or closes[-1 - sessions] == 0:
        return None
    return closes[-1] / closes[-1 - sessions] - 1


def _last(series: list) -> float | None:
    return series[-1] if series else None


def compute_indicators(closes: list[float], volumes: list[float | None]) -> dict:
    """Latest value of every indicator the votes use. Missing values are None."""
    out: dict = {"close": closes[-1] if closes else None, "bars": len(closes)}
    for period in (10, 20, 50, 100, 150, 200):
        out[f"sma{period}"] = _last(sma(closes, period))
    for period in (20, 50):
        out[f"ema{period}"] = _last(ema(closes, period))
    out["rsi14"] = _last(rsi(closes))
    line, sig, hist = macd(closes)
    out.update(macd=_last(line), macd_signal=_last(sig), macd_hist=_last(hist))
    k, d = stochastic(closes)
    out.update(stoch_k=_last(k), stoch_d=_last(d))
    trend, plus_di, minus_di = adx(closes)
    out.update(adx=_last(trend), plus_di=_last(plus_di), minus_di=_last(minus_di))
    _, upper, lower, pct_b = bollinger(closes)
    out.update(bb_upper=_last(upper), bb_lower=_last(lower), bb_pct_b=_last(pct_b))
    vols = [v for v in volumes if v is not None]
    if closes and len(vols) == len(closes):
        series = obv(closes, vols)
        out.update(obv=series[-1], obv_sma20=_last(sma(series, 20)))
        for period in (20, 50, 100):
            out[f"vol_avg{period}"] = _last(sma(vols, period))
    else:
        out.update(obv=None, obv_sma20=None, vol_avg20=None, vol_avg50=None, vol_avg100=None)
    for key, sessions in (("perf_1w", 5), ("perf_1m", 21), ("perf_3m", 63), ("perf_6m", 126)):
        out[key] = _perf(closes, sessions)
    return out


def _ind(name: str, value: str, vote: int | None, group: str, **extra) -> dict | None:
    if vote is None:
        return None
    return {"name": name, "value": value, "vote": vote, "group": group, **extra}


def _relation_text(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "--"
    return f"{_fmt(a)} {'>' if a > b else '<' if a < b else '='} {_fmt(b)}"


def _price_vs(ind: dict, key: str, label: str) -> dict | None:
    return _ind(f"Price vs {label}", _relation_text(ind["close"], ind.get(key)),
                _cmp_vote(ind["close"], ind.get(key)), "ma")


def _cross(ind: dict, fast: str, slow: str, label: str) -> dict | None:
    return _ind(label, _relation_text(ind.get(fast), ind.get(slow)), _cmp_vote(ind.get(fast), ind.get(slow)), "ma")


def _rsi_vote(value: float | None) -> int | None:
    if value is None:
        return None
    return 1 if value < RSI_LOW else -1 if value > RSI_HIGH else 0


def _stoch_vote(k: float | None, d: float | None) -> int | None:
    if k is None or d is None:
        return None
    if k < STOCH_LOW and k > d:
        return 1
    if k > STOCH_HIGH and k < d:
        return -1
    return 0


def _adx_vote(strength: float | None, plus: float | None, minus: float | None) -> int | None:
    if strength is None or plus is None or minus is None:
        return None
    if strength < ADX_TREND:
        return 0
    return _cmp_vote(plus, minus)


def _bb_vote(pct_b: float | None) -> int | None:
    if pct_b is None:
        return None
    return 1 if pct_b < 0 else -1 if pct_b > 1 else 0


def horizon_indicators(horizon: str, ind: dict) -> list[dict]:
    if horizon == "short":
        rows = [
            _price_vs(ind, "sma10", "SMA 10"),
            _price_vs(ind, "sma20", "SMA 20"),
            _price_vs(ind, "ema20", "EMA 20"),
            _cross(ind, "sma10", "sma20", "SMA 10 / SMA 20 cross"),
            _ind("RSI (14)", _fmt(ind["rsi14"], 0), _rsi_vote(ind["rsi14"]), "osc"),
            _ind("Stochastic (14,3,3)", f"%K {_fmt(ind['stoch_k'], 0)} / %D {_fmt(ind['stoch_d'], 0)}",
                 _stoch_vote(ind["stoch_k"], ind["stoch_d"]), "osc", close_only=True),
            _ind("MACD (12,26,9)", _relation_text(ind["macd"], ind["macd_signal"]), _sign(ind["macd_hist"]), "osc"),
            _ind("1W performance", _pct(ind["perf_1w"]), _sign(ind["perf_1w"]), "osc"),
        ]
    elif horizon == "intermediate":
        obv_vote = _cmp_vote(ind["obv"], ind["obv_sma20"])
        volume_text = "--"
        if ind.get("vol_avg50"):
            side = "above" if obv_vote == 1 else "below" if obv_vote == -1 else "at"
            volume_text = f"OBV {side} 20-day avg; volume 20d/50d {ind['vol_avg20'] / ind['vol_avg50']:.2f}x"
        rows = [
            _price_vs(ind, "sma50", "SMA 50"),
            _price_vs(ind, "ema50", "EMA 50"),
            _cross(ind, "sma20", "sma50", "SMA 20 / SMA 50 cross"),
            _cross(ind, "ema20", "ema50", "EMA 20 / EMA 50 cross"),
            _ind("ADX (14)",
                 f"{_fmt(ind['adx'], 0)} (+DI {_fmt(ind['plus_di'], 0)} / -DI {_fmt(ind['minus_di'], 0)})",
                 _adx_vote(ind["adx"], ind["plus_di"], ind["minus_di"]), "osc", close_only=True),
            _ind("Bollinger (20,2) %B", _fmt(ind["bb_pct_b"]), _bb_vote(ind["bb_pct_b"]), "osc"),
            _ind("OBV / volume", volume_text, obv_vote, "osc"),
            _ind("1M performance", _pct(ind["perf_1m"]), _sign(ind["perf_1m"]), "osc"),
        ]
    else:
        rows = [
            _price_vs(ind, "sma100", "SMA 100"),
            _price_vs(ind, "sma150", "SMA 150"),
            _price_vs(ind, "sma200", "SMA 200"),
            _cross(ind, "sma50", "sma200", "SMA 50 / SMA 200 cross (golden/death)"),
            _cross(ind, "sma100", "sma200", "SMA 100 / SMA 200 cross"),
            _ind("3M performance", _pct(ind["perf_3m"]), _sign(ind["perf_3m"]), "osc"),
            _ind("6M performance", _pct(ind["perf_6m"]), _sign(ind["perf_6m"]), "osc"),
        ]
    return [row for row in rows if row is not None]


def _counts(rows: list[dict]) -> dict:
    return {
        "buy": sum(row["vote"] > 0 for row in rows),
        "neutral": sum(row["vote"] == 0 for row in rows),
        "sell": sum(row["vote"] < 0 for row in rows),
    }


def _phrase(row: dict) -> str:
    name, up = row["name"], row["vote"] > 0
    if name.startswith("Price vs "):
        return f"price {'above' if up else 'below'} {name[9:]}"
    if "cross" in name:
        fast, slow = (part.strip() for part in name.split(" cross")[0].split("/"))
        return f"{fast} {'above' if up else 'below'} {slow}"
    if name.startswith("MACD"):
        return f"MACD {'above' if up else 'below'} signal"
    return f"{name} {row['value']}"


def explanation(horizon: str, rows: list[dict], ind: dict) -> str:
    """Rule-based: up to three signals in the majority direction, plus RSI or ADX context."""
    counts = _counts(rows)
    majority = 1 if counts["buy"] >= counts["sell"] else -1
    words = [_phrase(row) for row in rows if row["vote"] == majority][:3]
    if horizon == "short" and ind.get("rsi14") is not None:
        value = ind["rsi14"]
        state = "oversold" if value < RSI_LOW else "overbought" if value > RSI_HIGH else "not overbought or oversold"
        words.append(f"RSI {value:.0f} ({state})")
    if horizon == "intermediate" and ind.get("adx") is not None:
        words.append(f"ADX {ind['adx']:.0f} ({'trending' if ind['adx'] >= ADX_TREND else 'weak trend'})")
    text = ", ".join(words)
    return f"{text[:1].upper()}{text[1:]}." if text else ""


def _bars(history: list[dict]) -> tuple[list[str], list[float], list[float | None]]:
    dates, closes, volumes = [], [], []
    for bar in history:
        close = _finite(bar.get("close"))
        day = str(bar.get("date") or "")[:10]
        if close is None or not day:
            continue
        dates.append(day)
        closes.append(close)
        volumes.append(_finite(bar.get("volume")))
    return dates, closes, volumes


def as_of_text(day: str) -> str | None:
    """Daily bars close at 4:00 PM ET on their session date."""
    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        return None
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year} 4:00 PM ET"


def build_technical_summary(ticker: str, history: list[dict]) -> dict:
    """The live TechnicalSummary document for one ticker (``example: false``)."""
    dates, closes, volumes = _bars(history)
    ind = compute_indicators(closes, volumes) if closes else {}
    horizons = []
    for base in HORIZONS:
        row = {**base, "availableBars": len(closes)}
        if len(closes) >= base["requiredBars"]:
            rows = horizon_indicators(base["id"], ind)
            support, resistance = support_resistance(closes, SR_LOOKBACK[base["id"]])
            row.update(
                summary=_counts(rows),
                groups=[
                    {"id": "ma", "label": "Moving Averages", **_counts([r for r in rows if r["group"] == "ma"])},
                    {"id": "osc", "label": "Oscillators", **_counts([r for r in rows if r["group"] == "osc"])},
                ],
                indicators=[{k: v for k, v in r.items() if k != "group"} for r in rows],
                explanation=explanation(base["id"], rows, ind),
                support=support,
                resistance=resistance,
            )
        horizons.append(row)
    return {
        "ticker": ticker,
        "example": False,
        "as_of_date": dates[-1] if dates else None,
        "asOf": as_of_text(dates[-1]) if dates else None,
        "delayed": True,
        "close_only": True,
        "horizons": horizons,
    }


def rating_label(counts: dict | None) -> str | None:
    """Same TradingView thresholds as apps/web/technical-summary.js."""
    if not counts:
        return None
    total = counts.get("buy", 0) + counts.get("neutral", 0) + counts.get("sell", 0)
    if not total:
        return None
    score = (counts.get("buy", 0) - counts.get("sell", 0)) / total
    if score < -0.5:
        return "Strong Sell"
    if score < -0.1:
        return "Sell"
    if score <= 0.1:
        return "Neutral"
    if score <= 0.5:
        return "Buy"
    return "Strong Buy"


def ratings_for(summary: dict) -> dict[str, str | None]:
    return {h["id"]: rating_label(h.get("summary")) for h in summary.get("horizons", [])}
