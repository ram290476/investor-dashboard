"""Macro and pricing-impact signals per Technical Summary horizon (#198)."""

from datetime import date

from macro_signals import attach_macro, horizon_macro, upcoming_events


def _row(series_id, z_1w=0.0, z_1m=0.0, z_3m=0.0, corr=None, value=1.0, unit="%"):
    return {"series_id": series_id, "value": value, "unit": unit, "z_1w": z_1w, "z_1m": z_1m, "z_3m": z_3m,
            "corr_90d": corr, "obs_date": "2026-10-12"}


TREND = {"rows": [
    _row("DGS10", z_1m=1.2, z_3m=1.6),
    _row("DGS2", z_1m=0.1),
    _row("VIXCLS", z_1w=1.1, unit=""),
    _row("DTWEXBGS", z_1m=-0.8, unit=""),
    _row("DCOILWTICO", z_1m=0.9, corr=0.4, unit="$/bbl"),
    _row("CPI_YOY", z_3m=0.7),
    _row("ETF:SPY", z_1m=1.0, unit=""),
    _row("USEPUINDXD", z_1m=0.2, unit=""),
]}
CTX = {
    "as_of": date(2026, 10, 13),
    "ticker": "ANY",
    "trend_doc": TREND,
    "rates": {"fomc": None},
    "calendar": [{"series": "cpi", "release_ts": "2026-10-14T08:35-04:00"},
                 {"series": "cpi", "release_ts": "2026-11-10T08:35-05:00"}],
    "releases": [{"series_id": "CUSR0000SA0", "period": "2026-09", "surprise": 0.4}],
    "contracts": None,
    "news": {"sentiment_7d": 0.3, "sentiment_7d_prior": 0.1},
}


def _group(block, gid):
    return next(g for g in block["groups"] if g["id"] == gid)


def test_rising_yield_contradicts_a_buy_and_lowers_confidence():
    block = horizon_macro("intermediate", "Buy", CTX)
    rates = _group(block, "rates")
    assert rates["effect"] == "headwind" and rates["relation"] == "contradicts"
    assert "lowers confidence" in rates["confidence"]
    ten = next(s for s in rates["signals"] if s.get("id") == "DGS10")
    assert ten["direction"] == "rising" and ten["effect"] == "headwind"
    assert next(s for s in rates["signals"] if s["label"] == "FOMC path")["status"] == "no_data"


def test_same_signal_differs_by_horizon():
    short = _group(horizon_macro("short", "Sell", CTX), "vix")
    inter = _group(horizon_macro("intermediate", "Sell", CTX), "vix")
    assert short["effect"] == "headwind" and short["relation"] == "supports"
    assert inter["effect"] == "neutral"


def test_ticker_link_overrides_generic_sign():
    oil = _group(horizon_macro("intermediate", "Buy", CTX), "oil")
    assert oil["effect"] == "tailwind"
    assert "correlation" in oil["signals"][0]["note"]


def test_missing_groups_degrade_to_no_data_and_roll_up_names_them():
    block = horizon_macro("long", "Strong Buy", CTX)
    credit = _group(block, "credit")
    assert credit["status"] == "no_data" and "credit-spread" in credit["reason"]
    inflation = _group(block, "inflation")
    assert any(s["label"] == "PPI" and s["status"] == "no_data" for s in inflation["signals"])
    assert block["summary"].startswith("Macro/pricing signals: ")
    assert "No data: Credit spreads" in block["summary"]
    empty = horizon_macro("short", None, {**CTX, "trend_doc": None, "releases": [], "news": None})
    assert [g["id"] for g in empty["groups"] if g["status"] == "live"] == []


def test_no_inputs_at_all_is_no_data():
    block = horizon_macro("short", "Buy", {"as_of": date(2026, 10, 13)})
    assert block["net"] is None
    assert block["summary"] == "Macro/pricing signals: No data for this horizon."
    assert all(g["status"] == "no_data" for g in block["groups"])


def test_events_are_limited_to_the_horizon_window():
    assert [e["date"] for e in upcoming_events(date(2026, 10, 13), 7, CTX["calendar"], None)] == ["2026-10-14"]
    assert len(upcoming_events(date(2026, 10, 13), 31, CTX["calendar"], None)) == 2
    fomc = upcoming_events(date(2026, 10, 13), 31, [], {"fomc": {"meeting_date": "2026-10-28"}})
    assert fomc == [{"name": "FOMC decision", "date": "2026-10-28", "group": "rates"}]


def test_attach_macro_adds_block_and_event_risk_per_horizon():
    summary = {"ticker": "ANY", "as_of_date": "2026-10-13", "horizons": [
        {"id": "short", "summary": {"buy": 5, "neutral": 1, "sell": 1}},
        {"id": "intermediate", "summary": {"buy": 1, "neutral": 1, "sell": 5}},
        {"id": "long"},
    ]}
    out = attach_macro(summary, CTX)
    short, inter, long = out["horizons"]
    assert "short-term Strong Buy" in short["macro"]["summary"]
    assert "CPI 2026-10-14" in short["eventRisk"]
    assert "intermediate-term Strong Sell" in inter["macro"]["summary"]
    assert "eventRisk" not in long and "this technical outlook" in long["macro"]["summary"]
