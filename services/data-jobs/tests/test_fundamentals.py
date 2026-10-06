import pytest

import fundamentals as fq


def _fact(start, end, val, filed, form="10-Q", frame=None):
    f = {"start": start, "end": end, "val": val, "filed": filed, "form": form, "fy": int(end[:4]), "fp": "Q"}
    if frame:
        f["frame"] = frame
    return f


COMPANYFACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        _fact("2025-01-01", "2025-03-31", 100.0, "2025-04-24"),
                        _fact("2025-04-01", "2025-06-30", 120.0, "2025-07-24"),
                        _fact("2025-01-01", "2025-06-30", 220.0, "2025-07-24"),  # YTD six months: ignored
                        _fact("2025-07-01", "2025-09-30", 130.0, "2025-10-23"),
                        _fact("2025-01-01", "2025-12-31", 500.0, "2026-01-29", form="10-K"),
                        _fact("2025-01-01", "2025-03-31", 999.0, "2026-04-23"),  # re-reported later: first filing wins
                    ]
                }
            },
            "GrossProfit": {
                "units": {
                    "USD": [
                        _fact("2025-01-01", "2025-03-31", 20.0, "2025-04-24"),
                        _fact("2025-04-01", "2025-06-30", 24.0, "2025-07-24"),
                        _fact("2025-07-01", "2025-09-30", 26.0, "2025-10-23"),
                        _fact("2025-01-01", "2025-12-31", 100.0, "2026-01-29", form="10-K"),
                    ]
                }
            },
        }
    }
}


def test_quarters_and_derived_q4():
    rows = fq.xbrl_rows("TSLA", COMPANYFACTS)
    gm = {r["fiscal_quarter"]: r for r in rows if r["metric"] == "gross_margin_gaap"}
    assert set(gm) == {"2025Q1", "2025Q2", "2025Q3", "2025Q4"}
    assert gm["2025Q1"]["value"] == pytest.approx(0.20)
    # Q4 = FY - Q1..Q3: revenue 150, gross profit 30
    assert gm["2025Q4"]["value"] == pytest.approx(30 / 150)
    assert str(gm["2025Q4"]["release_date"]) == "2026-01-29"
    rev = {r["fiscal_quarter"]: r["value"] for r in rows if r["metric"] == "revenue_gaap"}
    assert rev["2025Q1"] == 100.0  # not the later 999 re-report


def test_manual_csv_validation_and_override():
    text = (
        "ticker,metric,fiscal_quarter,release_date,value,source_id,note\n"
        'tsla,deliveries,2025Q3,2025-10-02,"497,099",DS-12,\n'
        "TSLA,deliveries,2025Q3,2025-10-02,497100,DS-12,correction\n"
    )
    manual = fq.parse_manual_csv(text)
    table = fq.build_table(fq.xbrl_rows("TSLA", COMPANYFACTS), manual)
    d = table.filter(table["metric"] == "deliveries")
    assert d.height == 1 and d["value"][0] == 497100 and d["ticker"][0] == "TSLA"
    assert table.select(["ticker", "metric", "fiscal_quarter"]).is_duplicated().sum() == 0


@pytest.mark.parametrize(
    "line",
    [
        "TSLA,margin,2025Q3,2025-10-02,1,DS-12,",
        "TSLA,deliveries,2025-Q3,2025-10-02,1,DS-12,",
        "TSLA,deliveries,2025Q3,10/02/2025,1,DS-12,",
        "TSLA,deliveries,2025Q3,2025-10-02,-5,DS-12,",
        "TSLA,fsd_subscribers,2025Q3,2025-10-02,1.5,DS-12,",
    ],
)
def test_manual_rejects_bad_rows(line):
    with pytest.raises(fq.ManualRowError, match="line 2"):
        fq.parse_manual_csv("ticker,metric,fiscal_quarter,release_date,value,source_id,note\n" + line + "\n")
