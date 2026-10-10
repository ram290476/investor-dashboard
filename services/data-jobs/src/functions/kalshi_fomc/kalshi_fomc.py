"""Kalshi FOMC outcome probabilities (DS-89).

The public market-data API needs no key. Live collection stays off until Ram accepts
the Kalshi terms (issue #89, question 2). Parsing, 4xx/429 handling and the serving
shape are covered here. A skipped run does not invent probabilities.
"""

from __future__ import annotations

from datetime import date, datetime

# Issue #89 question 2 is still open. Do not flip this on from a schedule or a deploy.
TERMS_ACCEPTED = False
SOURCE = "Kalshi"
MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
FOMC_KEY = "serving/rates/fomc.json"
ATTEMPTS_KEY = "serving/rates/attempts.json"


def collection_plan(terms_accepted: bool = TERMS_ACCEPTED) -> dict:
    """Live fetch is refused until the terms question is answered yes."""
    if not terms_accepted:
        return {"action": "skip", "fetch": False, "reason": "kalshi_terms_not_accepted"}
    return {"action": "fetch", "fetch": True, "reason": "terms_accepted"}


def fetch_decision(status_code: int) -> dict:
    """4xx and 429 stop. They are not retried and not worked around (#69)."""
    if status_code == 429 or status_code == 403:
        return {"action": "stop", "retry": False, "store": False, "reason": "provider_policy"}
    if 400 <= status_code <= 499:
        return {"action": "stop", "retry": False, "store": False, "reason": "client_error"}
    if 500 <= status_code <= 599:
        return {"action": "retry", "retry": True, "store": False, "reason": "upstream"}
    if status_code == 200:
        return {"action": "store", "retry": False, "store": True, "reason": "ok"}
    return {"action": "stop", "retry": False, "store": False, "reason": "unexpected"}


def _probability(market: dict) -> float | None:
    """Kalshi prices are cents. A missing price is not zero."""
    price = market.get("last_price")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        bid, ask = market.get("yes_bid"), market.get("yes_ask")
        if isinstance(bid, bool) or isinstance(ask, bool):
            return None
        if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
            return None
        price = (float(bid) + float(ask)) / 2
    value = float(price)
    if value != value or value < 0 or value > 100:
        return None
    return round(value / 100, 4)


def _outcome(text: str) -> str | None:
    folded = text.lower()
    if any(word in folded for word in ("hike", "raise", "increase")):
        return "hike"
    if any(word in folded for word in ("hold", "unchanged", "no change")):
        return "hold"
    if any(word in folded for word in ("cut", "lower", "ease")):
        return "cut"
    return None


def _meeting_day(market: dict) -> date | None:
    raw = str(market.get("close_time") or market.get("expiration_time") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def parse_fomc_payload(payload: dict, today: date) -> dict | None:
    """Next meeting's cut/hold/hike probabilities plus up to 14 history points.

    Markets without a price or a recognizable outcome are dropped. Probabilities for
    the same outcome (cut 25 and cut 50) are summed. They are not renormalized.
    """
    if not isinstance(payload, dict):
        return None
    grouped: dict[date, dict[str, float]] = {}
    for market in payload.get("markets") or []:
        if not isinstance(market, dict):
            continue
        if str(market.get("status") or "active").lower() in {"settled", "finalized", "closed"}:
            continue
        meeting = _meeting_day(market)
        label = _outcome(str(market.get("yes_sub_title") or market.get("subtitle") or "")) or _outcome(
            str(market.get("title") or "")
        )
        prob = _probability(market)
        if meeting is None or label is None or prob is None:
            continue
        bucket = grouped.setdefault(meeting, {})
        bucket[label] = round(bucket.get(label, 0.0) + prob, 4)
    upcoming = sorted(day for day in grouped if day >= today)
    chosen = upcoming[0] if upcoming else (max(grouped) if grouped else None)
    if chosen is None:
        return None
    names = {"cut": "Cut", "hold": "Hold", "hike": "Hike"}
    outcomes = [
        {"label": names[key], "prob": grouped[chosen][key]}
        for key in ("cut", "hold", "hike")
        if key in grouped[chosen]
    ]
    history = []
    for row in payload.get("history") or []:
        if not isinstance(row, dict):
            continue
        row_meeting = str(row.get("meeting_date") or chosen.isoformat())[:10]
        if row_meeting != chosen.isoformat():
            continue
        try:
            day = date.fromisoformat(str(row.get("date") or "")[:10])
        except ValueError:
            continue
        cut = row.get("cut")
        if isinstance(cut, bool) or not isinstance(cut, (int, float)) or not 0 <= float(cut) <= 1:
            continue
        history.append(
            {
                "date": day.isoformat(),
                "cut": float(cut),
                "hold": float(row["hold"]) if isinstance(row.get("hold"), (int, float)) else None,
                "hike": float(row["hike"]) if isinstance(row.get("hike"), (int, float)) else None,
            }
        )
    history = sorted(history, key=lambda item: item["date"])[-14:]
    return {
        "meeting_date": chosen.isoformat(),
        "outcomes": outcomes,
        "history_14d": history,
        "source": SOURCE,
    }


def interpret_response(status_code: int, payload: dict | None, today: date, attempted_at: str) -> dict:
    """Turn one HTTP result into a store-or-stop decision. 429 never stores a probability."""
    decision = fetch_decision(status_code)
    attempt = {
        "source": SOURCE,
        "last_attempt": attempted_at,
        "state": "ok" if decision["store"] else "error",
        "detail": None if decision["store"] else decision["reason"],
    }
    if not decision["store"]:
        return {"store": False, "document": None, "attempt": attempt, "decision": decision}
    parsed = parse_fomc_payload(payload or {}, today)
    if parsed is None:
        attempt["state"] = "error"
        attempt["detail"] = "unusable_payload"
        return {"store": False, "document": None, "attempt": attempt, "decision": decision}
    attempt["detail"] = None
    return {"store": True, "document": parsed, "attempt": attempt, "decision": decision}


def skipped_attempt(attempted_at: str) -> dict:
    return {
        "source": SOURCE,
        "last_attempt": attempted_at,
        "state": "unavailable",
        "detail": "Live collection waits until the Kalshi terms are accepted.",
    }


def fetch_once(http, url: str = MARKETS_URL) -> tuple[int, dict | None]:
    """One GET. Does not use the shared retry helper, which would retry 429."""
    response = http.get(url, params={"status": "open", "limit": 200})
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = None
    return response.status_code, payload if isinstance(payload, dict) else None


def collect(http, today: date, attempted_at: str, terms_accepted: bool = TERMS_ACCEPTED) -> dict:
    """Skip without a request until terms are accepted. A 4xx/429 response is stored as an error, not as odds."""
    plan = collection_plan(terms_accepted)
    if not plan["fetch"]:
        return {
            "status": "skipped",
            "reason": plan["reason"],
            "attempt": skipped_attempt(attempted_at),
            "document": None,
        }
    status, payload = fetch_once(http)
    interpreted = interpret_response(status, payload, today, attempted_at)
    return {
        "status": "success" if interpreted["store"] else "failed",
        "reason": interpreted["decision"]["reason"],
        "attempt": interpreted["attempt"],
        "document": interpreted["document"],
        "retry": interpreted["decision"]["retry"],
    }


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    from datetime import UTC

    from http_client import get_client
    from lake import write_json
    from observability import job_handler

    @job_handler("KALSHI", emit_event=True)
    def run(event, context):
        now = datetime.now(UTC).isoformat(timespec="seconds")
        if not collection_plan()["fetch"]:
            write_json({"fomc": skipped_attempt(now)}, ATTEMPTS_KEY)
            return {"status": "skipped", "reason": "kalshi_terms_not_accepted"}
        with get_client() as http:
            result = collect(http, datetime.now(UTC).date(), now, terms_accepted=True)
        write_json({"fomc": result["attempt"]}, ATTEMPTS_KEY)
        if result.get("document"):
            write_json(result["document"], FOMC_KEY)
        if result["status"] == "failed" and result.get("retry"):
            raise RuntimeError("Kalshi upstream failure")
        return {"status": result["status"], "reason": result["reason"]}

    return run(event, context)
