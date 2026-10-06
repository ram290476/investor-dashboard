import io
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from collectors import alpaca, fed_sources, finra, kalshi, yahoo


def test_alpaca_bars_chunking_and_parse():
    syms = [f"T{i}" for i in range(150)]
    params = alpaca.bars_params(syms, "2026-10-01")
    assert len(params) == 2 and params[0]["feed"] == "iex" and params[1]["symbols"].count(",") == 49
    rows = alpaca.parse_bars(
        {"bars": {"SPY": [{"t": "2026-10-02T14:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}]}}
    )
    assert rows[0]["ticker"] == "SPY" and rows[0]["close"] == 1.5


def test_occ_parse_and_options_metrics():
    assert alpaca.parse_occ("TSLA261120C00400000") == {
        "root": "TSLA",
        "expiration": date(2026, 11, 20),
        "type": "call",
        "strike": 400.0,
    }
    as_of = date(2026, 10, 19)
    snaps = {
        "TSLA261120C00400000": {"dailyBar": {"v": 100}, "impliedVolatility": 0.60},
        "TSLA261120P00400000": {"dailyBar": {"v": 50}, "impliedVolatility": 0.64},
        "TSLA261120C00450000": {"dailyBar": {"v": 100}, "impliedVolatility": 0.55},
        "TSLA261023P00400000": {"dailyBar": {"v": 150}},  # 4 days out: counts for volume, not IV30
    }
    m = alpaca.options_metrics(snaps, spot=405.0, as_of=as_of)
    assert m["put_call_volume_ratio"] == pytest.approx(200 / 200)
    assert m["iv30"] == pytest.approx(0.62) and m["iv30_expiration"] == "2026-11-20"


def test_options_metrics_without_iv():
    m = alpaca.options_metrics({"TSLA261120C00400000": {"dailyBar": {"v": 10}}}, 400.0, date(2026, 10, 19))
    assert m["iv30"] is None and m["iv_available"] is False and m["put_call_volume_ratio"] == 0.0


def test_finra_parse_both_field_sets_and_new_dates():
    recs = [
        {
            "settlementDate": "2026-09-30",
            "symbolCode": "tsla",
            "currentShortPositionQuantity": "75000000",
            "previousShortPositionQuantity": 70000000,
            "averageDailyVolumeQuantity": 90000000,
            "daysToCoverQuantity": "0.83",
        },
        {"settlementDate": "2026-09-15", "issueSymbolIdentifier": "SPCX", "currentShortShareNumber": 1000},
        {"settlementDate": "2026-09-15"},  # no symbol: dropped
    ]
    rows = finra.parse(recs)
    assert [r["ticker"] for r in rows] == ["TSLA", "SPCX"]
    assert rows[0]["short_interest"] == 75_000_000 and rows[0]["days_to_cover"] == pytest.approx(0.83)
    assert finra.new_settlement_dates(rows, {"2026-09-15"}) == {"2026-09-30"}
    body = finra.query_body(["TSLA"], "2026-09-15")
    assert body["compareFilters"][0]["fieldValue"] == "2026-09-15"


def test_kalshi_mid_price_and_fallback():
    payload = {
        "markets": [
            {
                "ticker": "KXFEDDECISION-26OCT-H0",
                "event_ticker": "KXFEDDECISION-26OCT",
                "yes_sub_title": "Fed maintains rate",
                "yes_bid_dollars": "0.6100",
                "yes_ask_dollars": "0.6300",
                "volume_fp": "1200",
            },
            {
                "ticker": "KXFEDDECISION-26OCT-C25",
                "event_ticker": "KXFEDDECISION-26OCT",
                "yes_sub_title": "Cut 25bps",
                "yes_bid_dollars": "0",
                "yes_ask_dollars": "0",
                "last_price_dollars": "0.3500",
            },
        ]
    }
    rows = kalshi.parse_markets(payload, "KXFEDDECISION", "2026-10-03")
    assert rows[0]["probability"] == pytest.approx(0.62) and rows[0]["outcome"] == "Fed maintains rate"
    assert rows[1]["probability"] == pytest.approx(0.35)


def test_yahoo_parse_chart():
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": -14400},
                    "timestamp": [1759411800, 1759498200],
                    "indicators": {
                        "quote": [{"close": [440.0, None], "volume": [1, 2]}],
                        "adjclose": [{"adjclose": [440.0, None]}],
                    },
                }
            ]
        }
    }
    rows = yahoo.parse_chart(payload, "TSLA")
    assert len(rows) == 1 and rows[0]["date"] == date(2025, 10, 2)
    with pytest.raises(ValueError):
        yahoo.parse_chart({"chart": {"result": None, "error": {"code": "Not Found"}}}, "ZZZZ")


def test_atlanta_xlsx_header_driven():
    wb = Workbook()
    ws = wb.active
    ws.append(["Market Probability Tracker"])
    ws.append(["Date", "Expected rate 3m", "P(cut)"])
    ws.append([datetime(2026, 10, 2), 3.88, 0.41])  # noqa: DTZ001 - Excel cells are naive
    buf = io.BytesIO()
    wb.save(buf)
    rows = fed_sources.parse_atlanta_xlsx(buf.getvalue())
    assert {r["column"] for r in rows} == {"Expected rate 3m", "P(cut)"} and rows[0]["date"] == date(2026, 10, 2)
    wb2 = Workbook()
    wb2.active.append(["no", "header"])
    b2 = io.BytesIO()
    wb2.save(b2)
    with pytest.raises(fed_sources.LayoutChangedError):
        fed_sources.parse_atlanta_xlsx(b2.getvalue())


def test_cleveland_html_tables():
    html = """<table><tr><th>Month</th><th>October 2026</th><th>September 2026</th></tr>
      <tr><td>CPI</td><td>0.25</td><td>0.31%</td></tr>
      <tr><td>Core CPI</td><td>0.28</td><td>n/a</td></tr>
      <tr><td>Footnote</td><td>x</td></tr></table>"""
    rows = fed_sources.parse_cleveland_html(html, date(2026, 10, 3))
    assert {(r["measure"], r["period"], r["value"]) for r in rows} == {
        ("CPI", "October 2026", 0.25),
        ("CPI", "September 2026", 0.31),
        ("Core CPI", "October 2026", 0.28),
    }
    with pytest.raises(fed_sources.LayoutChangedError):
        fed_sources.parse_cleveland_html("<p>redesigned</p>", date(2026, 10, 3))
