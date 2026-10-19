"""Macro and pricing-impact signals for each Technical Summary horizon (#198).

Context only: these signals do not change a horizon's vote counts or rating. For each signal
they state the driver's direction over that horizon, whether it is a tailwind or headwind for
this ticker, whether it supports or contradicts the horizon's technical rating, and how it
shifts confidence. Each horizon ends with a one-line net roll-up.

Inputs are data the dashboard already serves; nothing is invented:
    trend_doc       serving/trend_metrics/latest/<T>.json (FRED drivers, ETF overlays, corr/effect)
    rates           dashboard rates block (FOMC from Kalshi when collected)
    releases        curated/releases rows (CPI/PCE actual vs consensus)
    calendar        curated/release_calendar/upcoming.json (scheduled CPI etc.)
    contracts       curated/contracts_rollup row for the ticker
    news            news_sentiment 7-day mean and the prior 7 days

A group with no source shows ``status: "no_data"`` and a reason.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

# z-score key and the change it describes, per horizon.
HORIZON_Z = {"short": ("z_1w", "1W"), "intermediate": ("z_1m", "1M"), "long": ("z_3m", "3M")}
HORIZON_DAYS = {"short": 7, "intermediate": 31, "long": 92}
MOVE_Z = 0.5
LINK_CORR = 0.2
NET_BAND = 0.15
# Horizon weighting (#198): volatility and scheduled releases weigh most short-term;
# rates, inflation and index trend weigh most for the intermediate and long term.
WEIGHTS = {
    "short": {"politics": 1, "rates": 1, "inflation": 1, "usd": 1, "oil": 1, "vix": 2, "credit": 1, "index": 1},
    "intermediate": {"politics": 1, "rates": 1.5, "inflation": 1.5, "usd": 1, "oil": 1, "vix": 1, "credit": 1,
                     "index": 1.5},
    "long": {"politics": 1, "rates": 2, "inflation": 2, "usd": 1, "oil": 1, "vix": 0.5, "credit": 1, "index": 2},
}

# series_id -> (group, label, generic sign of a RISE for equities). +1: rise is a tailwind.
SERIES = {
    "USEPUINDXD": ("politics", "Economic policy uncertainty index", -1),
    "DGS2": ("rates", "2Y Treasury yield", -1),
    "DGS10": ("rates", "10Y Treasury yield", -1),
    "DGS30": ("rates", "30Y Treasury yield", -1),
    "T10Y2Y": ("rates", "Yield curve 2s10s", 0),
    "DFEDTARU": ("rates", "Fed funds target (upper)", -1),
    "CPI_YOY": ("inflation", "CPI YoY", -1),
    "CORE_CPI_YOY": ("inflation", "Core CPI YoY", -1),
    "PCE_YOY": ("inflation", "PCE YoY", -1),
    "CORE_PCE_YOY": ("inflation", "Core PCE YoY", -1),
    "T10YIE": ("inflation", "10Y breakeven inflation", -1),
    "DTWEXBGS": ("usd", "Broad trade-weighted USD", -1),
    "DCOILWTICO": ("oil", "WTI crude oil", 0),
    "VIXCLS": ("vix", "VIX", -1),
    "ETF:SPY": ("index", "S&P 500 (SPY)", 1),
    "ETF:QQQ": ("index", "Nasdaq 100 (QQQ)", 1),
}
GROUPS = (
    ("politics", "Politics & geopolitics"),
    ("rates", "Rates & Fed"),
    ("inflation", "Inflation"),
    ("usd", "US dollar"),
    ("oil", "Oil"),
    ("vix", "Volatility (VIX)"),
    ("credit", "Credit spreads"),
    ("index", "Index trend"),
)
NO_SOURCE = {
    "credit": "No credit-spread series is collected yet (e.g. FRED BAMLH0A0HYM2 / BAMLC0A0CM).",
}
RELEASE_LABELS = {"cpi": "CPI", "pce": "PCE", "ppi": "PPI", "jobs": "Jobs report", "gdp": "GDP"}


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _direction(z: float | None) -> str | None:
    if z is None:
        return None
    return "rising" if z >= MOVE_Z else "falling" if z <= -MOVE_Z else "flat"


def _impact(row: dict, z_key: str, generic: int) -> tuple[int, str]:
    """(+1 tailwind / -1 headwind / 0 neutral, basis). Ticker-linked when |corr_90d| >= 0.2."""
    z = _num(row.get(z_key))
    corr = _num(row.get("corr_90d"))
    if z is None or abs(z) < MOVE_Z:
        return 0, "no meaningful move"
    if corr is not None and abs(corr) >= LINK_CORR:
        return (1 if corr * z > 0 else -1), f"90-day correlation with this stock {corr:+.2f}"
    if generic == 0:
        return 0, "no stable link to this stock"
    return (generic if z > 0 else -generic), "typical equity sensitivity"


def _tone(rating: str | None) -> int:
    if rating in ("Buy", "Strong Buy"):
        return 1
    if rating in ("Sell", "Strong Sell"):
        return -1
    return 0


def _relation(impact: int, tone: int, rating: str | None) -> tuple[str, str]:
    """(supports | contradicts | neutral, confidence effect)."""
    if impact == 0:
        return "neutral", "No change to confidence"
    if tone == 0:
        side = "bullish" if impact > 0 else "bearish"
        return "neutral", f"Tilts the {rating or 'Neutral'} rating {side}"
    if impact == tone:
        return "supports", f"Reinforces the {rating}; raises confidence"
    return "contradicts", f"Contradicts the {rating}; lowers confidence"


def _fmt_value(row: dict) -> str:
    value = _num(row.get("value"))
    if value is None:
        return "--"
    unit = row.get("unit") or ""
    return f"{value:,.2f}{unit if unit == '%' else (' ' + unit if unit else '')}"


def _parse_ts(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def upcoming_events(as_of: date, days: int, calendar: list[dict] | None, rates: dict | None) -> list[dict]:
    end = as_of + timedelta(days=days)
    events = []
    for row in calendar or []:
        stamp = _parse_ts(row.get("release_ts"))
        if stamp is None or not (as_of < stamp.date() <= end):
            continue
        series = str(row.get("series") or "").lower()
        events.append({"name": RELEASE_LABELS.get(series, series.upper()), "date": stamp.date().isoformat(),
                       "group": "inflation" if series in ("cpi", "pce", "ppi") else "macro"})
    fomc = (rates or {}).get("fomc") or {}
    meeting = str(fomc.get("meeting_date") or "")[:10]
    try:
        meeting_day = date.fromisoformat(meeting) if meeting else None
    except ValueError:
        meeting_day = None
    if meeting_day and as_of < meeting_day <= end:
        events.append({"name": "FOMC decision", "date": meeting, "group": "rates"})
    return sorted(events, key=lambda event: event["date"])


def _series_signal(row: dict, horizon: str, rating: str | None) -> dict:
    _group, label, generic = SERIES[row["series_id"]]
    z_key, window = HORIZON_Z[horizon]
    z = _num(row.get(z_key))
    impact, basis = _impact(row, z_key, generic)
    relation, confidence = _relation(impact, _tone(rating), rating)
    direction = _direction(z)
    effect = "tailwind" if impact > 0 else "headwind" if impact < 0 else "neutral"
    strength = None if z is None else "strong" if abs(z) >= 1.5 else "moderate" if abs(z) >= 1 else "weak"
    return {
        "id": row["series_id"],
        "label": label,
        "value": _fmt_value(row),
        "direction": direction or "unknown",
        "effect": effect,
        "impact": impact,
        "strength": strength,
        "relation": relation,
        "confidence": confidence,
        "as_of": str(row.get("obs_date") or row.get("date") or "")[:10] or None,
        "note": f"{label} {direction or 'n/a'} over {window} (z {z:+.1f}); {effect} ({basis})."
        if z is not None else f"{label}: no {window} change available.",
    }


def _news_signal(news: dict | None, rating: str | None) -> dict | None:
    current = _num((news or {}).get("sentiment_7d"))
    if current is None:
        return None
    prior = _num((news or {}).get("sentiment_7d_prior"))
    change = None if prior is None else current - prior
    impact = 1 if current >= 0.15 else -1 if current <= -0.15 else 0
    relation, confidence = _relation(impact, _tone(rating), rating)
    direction = "flat" if change is None or abs(change) < 0.05 else "rising" if change > 0 else "falling"
    return {
        "id": "news_sentiment", "label": "News sentiment (7-day)", "value": f"{current:+.2f}",
        "direction": direction, "effect": "tailwind" if impact > 0 else "headwind" if impact < 0 else "neutral",
        "impact": impact, "strength": None, "relation": relation, "confidence": confidence, "as_of": None,
        "note": f"Headline sentiment {current:+.2f} ({direction} vs prior week); includes policy and regulatory news.",
    }


def _contracts_signal(contracts: dict | None) -> dict | None:
    if not contracts or contracts.get("coverage") == "unavailable":
        return None
    count = contracts.get("ttm_awards_count")
    obligated = _num(contracts.get("ttm_obligated"))
    if not isinstance(count, int) or count <= 0 or obligated is None:
        return None
    return {
        "id": "gov_contracts", "label": "Government contracts (TTM)",
        "value": f"${obligated / 1e6:,.1f}M · {count} awards",
        "direction": "unknown", "effect": "neutral", "impact": 0, "strength": None, "relation": "neutral",
        "confidence": "Context only; no change to confidence",
        "as_of": str(contracts.get("as_of") or "")[:10] or None,
        "note": "Federal award exposure; policy or budget shifts can move this stock.",
    }


def _inflation_surprise(releases: list[dict] | None, rating: str | None) -> dict | None:
    latest = None
    for row in releases or []:
        if row.get("series_id") != "CUSR0000SA0" or _num(row.get("surprise")) is None:
            continue
        if latest is None or str(row.get("period") or "") > str(latest.get("period") or ""):
            latest = row
    if latest is None:
        return None
    surprise = _num(latest["surprise"])
    impact = -1 if surprise > 0 else 1 if surprise < 0 else 0  # hotter than consensus is a headwind
    relation, confidence = _relation(impact, _tone(rating), rating)
    return {
        "id": "cpi_surprise", "label": "Last CPI vs consensus",
        "value": f"{surprise:+.3f} index pts ({latest.get('period')})",
        "direction": "hotter" if surprise > 0 else "cooler" if surprise < 0 else "in line",
        "effect": "headwind" if impact < 0 else "tailwind" if impact > 0 else "neutral", "impact": impact,
        "strength": None, "relation": relation, "confidence": confidence, "as_of": str(latest.get("period") or ""),
        "note": f"CPI printed {'above' if surprise > 0 else 'below' if surprise < 0 else 'in line with'} consensus.",
    }


def _no_data(label: str) -> dict:
    return {"id": None, "label": label, "status": "no_data"}


def horizon_macro(horizon: str, rating: str | None, ctx: dict) -> dict:
    trend_rows = {row.get("series_id"): row for row in (ctx.get("trend_doc") or {}).get("rows") or []
                  if isinstance(row, dict)}
    as_of = ctx["as_of"]
    events = upcoming_events(as_of, HORIZON_DAYS[horizon], ctx.get("calendar"), ctx.get("rates"))
    groups = []
    weights = WEIGHTS[horizon]
    weighted, weight_total, tally = 0.0, 0.0, {"tailwind": 0, "headwind": 0, "neutral": 0}
    missing = []
    for gid, glabel in GROUPS:
        signals = [_series_signal(trend_rows[sid], horizon, rating) for sid, meta in SERIES.items()
                   if meta[0] == gid and sid in trend_rows and sid != f"ETF:{ctx.get('ticker')}"]
        if gid == "politics":
            extra = (_contracts_signal(ctx.get("contracts")), _news_signal(ctx.get("news"), rating))
            signals += [s for s in extra if s]
        if gid == "inflation":
            surprise = _inflation_surprise(ctx.get("releases"), rating)
            if surprise:
                signals.append(surprise)
            signals.append(_no_data("PPI") | {"note": "No PPI series is collected yet."})
        if gid == "rates" and not (ctx.get("rates") or {}).get("fomc"):
            note = "FOMC meeting date and probabilities feed (Kalshi) is not live yet."
            signals.append(_no_data("FOMC path") | {"note": note})
        live = [s for s in signals if s.get("status") != "no_data"]
        group_events = [e for e in events if e["group"] == gid]
        if not live:
            missing.append(glabel)
            groups.append({"id": gid, "label": glabel, "status": "no_data",
                           "reason": NO_SOURCE.get(gid, "No source collected for this group yet."),
                           "signals": signals, "events": group_events})
            continue
        score = sum(s["impact"] for s in live)
        effect = "tailwind" if score > 0 else "headwind" if score < 0 else "neutral"
        relation, confidence = _relation(1 if score > 0 else -1 if score < 0 else 0, _tone(rating), rating)
        if group_events:
            named = ", ".join(f"{e['name']} {e['date']}" for e in group_events)
            confidence += f"; {named} inside this window raises event risk"
        tally[effect] += 1
        weighted += weights[gid] * (1 if score > 0 else -1 if score < 0 else 0)
        weight_total += weights[gid]
        groups.append({"id": gid, "label": glabel, "status": "live", "effect": effect, "relation": relation,
                       "confidence": confidence, "signals": signals, "events": group_events})
    net_score = weighted / weight_total if weight_total else None
    net = None if net_score is None else (
        "net tailwind" if net_score > NET_BAND else "net headwind" if net_score < -NET_BAND else "neutral")
    target = f"this {HORIZON_LABEL[horizon]} {rating}" if rating else "this technical outlook"
    if net is None:
        summary = "Macro/pricing signals: No data for this horizon."
    else:
        parts = [f"{tally['headwind']} headwind", f"{tally['tailwind']} tailwind", f"{tally['neutral']} neutral"]
        if missing:
            parts.append(f"No data: {', '.join(missing)}")
        summary = f"Macro/pricing signals: {net} for {target} ({'; '.join(parts)})."
    return {"net": net, "net_score": None if net_score is None else round(net_score, 3), "summary": summary,
            "groups": groups, "events": events}


HORIZON_LABEL = {"short": "short-term", "intermediate": "intermediate-term", "long": "long-term"}


def attach_macro(summary: dict, ctx: dict) -> dict:
    """Adds ``macro`` and an event-risk note to every horizon of a technical summary document."""
    from technicals import ratings_for

    as_of_text = summary.get("as_of_date") or ctx.get("as_of")
    try:
        as_of = date.fromisoformat(str(as_of_text)[:10])
    except ValueError:
        return summary
    ctx = {**ctx, "as_of": as_of, "ticker": summary.get("ticker")}
    ratings = ratings_for(summary)
    for horizon in summary.get("horizons", []):
        block = horizon_macro(horizon["id"], ratings.get(horizon["id"]), ctx)
        horizon["macro"] = block
        if block["events"] and horizon.get("summary"):
            names = ", ".join(f"{e['name']} {e['date']}" for e in block["events"])
            horizon["eventRisk"] = f"Scheduled inside this horizon: {names} — event risk."
    return summary
