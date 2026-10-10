import io
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from collectors import alpaca, fed_sources, finra, kalshi, yahoo


def test_alpaca_bars_chunking_and_parse():
    syms = [f"T{i}" for i in range(150)]
    params = alpaca.bars_params(syms, "2026-10-01")
    assert len(params) == 2 and params[0]["feed"] == "iex" and params[1]["symbols"].count(",") == 49
    assert params[0]["adjustment"] == "split"
    assert alpaca.bars_params(syms[:2], "2026-10-01", adjustment="raw")[0]["adjustment"] == "raw"
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
    assert rows[0]["volume"] == 1 and rows[0]["volume_source"] == "DS-05" and rows[0]["volume_iex"] is None
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


CLEVELAND_HTML = """
<table><caption>Inflation, month-over-month percent change</caption>
  <tr><th>Month</th><th>CPI</th><th>Core CPI</th><th>PCE</th><th>Core PCE</th><th>Updated</th></tr>
  <tr><td>October 2026</td><td>0.27</td><td>0.20</td><td>0.28</td><td>0.25</td><td>10/09</td></tr>
  <tr><td>September 2026</td><td></td><td>0.20</td><td>0.43</td><td>0.25</td><td>10/09</td></tr>
</table>
<table><caption>Inflation, year-over-year percent change</caption>
  <tr><th>Month</th><th>CPI</th><th>Core CPI</th><th>PCE</th><th>Core PCE</th><th>Updated</th></tr>
  <tr><td>October 2026</td><td>3.63</td><td>2.30</td><td>3.69</td><td>3.10</td><td>10/09</td></tr>
</table>
<table><caption>Quarterly annualized percent change</caption>
  <tr><th>Quarter</th><th>CPI</th><th>Core CPI</th><th>PCE</th><th>Core PCE</th><th>Updated</th></tr>
  <tr><td>2026:Q4</td><td>4.39</td><td>2.56</td><td>4.00</td><td>3.05</td><td>10/09</td></tr>
</table>
"""


def test_cleveland_html_tables_keep_mom_yoy_and_quarterly_apart():
    rows = fed_sources.parse_cleveland_html(CLEVELAND_HTML, date(2026, 10, 9))
    mom = {(r["measure"], r["period"]): r["value"] for r in rows if r["basis"] == "mom"}
    yoy = {(r["measure"], r["period"]): r["value"] for r in rows if r["basis"] == "yoy"}
    quarter = {(r["measure"], r["period"]): r["value"] for r in rows if r["basis"] == "annualized_quarterly"}
    assert mom[("CPI", "October 2026")] == 0.27
    assert ("CPI", "September 2026") not in mom  # blank cell: the print was already released
    assert mom[("Core CPI", "September 2026")] == 0.20
    assert yoy[("CPI", "October 2026")] == 3.63
    assert quarter[("CPI", "2026:Q4")] == 4.39
    assert {r["basis"] for r in rows} == {"mom", "yoy", "annualized_quarterly"}
    with pytest.raises(fed_sources.LayoutChangedError):
        fed_sources.parse_cleveland_html("<p>redesigned</p>", date(2026, 10, 3))
    with pytest.raises(fed_sources.LayoutChangedError):
        fed_sources.parse_cleveland_html(
            "<table><tr><th>Month</th><th>October 2026</th></tr><tr><td>CPI</td><td>0.25</td></tr></table>",
            date(2026, 10, 3),
        )
