import pytest

import fundamentals as fq


def _fact(start, end, val, filed, form="10-Q", frame=None):
    f = {"start": start, "end": end, "val": val, "filed": filed, "form": form, "fy": int(end[:4]), "fp": "Q"}
    if frame:
        f["frame"] = frame
    return f


COMPANYFACTS = {
    "facts": {
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        _fact("2025-03-31", "2025-03-31", 3_200_000_000, "2025-04-24"),
                        _fact("2025-06-30", "2025-06-30", 3_210_000_000, "2025-07-24"),
                        _fact("2025-06-30", "2025-06-30", 3_220_000_000, "2025-07-25", form="10-Q/A"),
                    ]
                }
            },
            "EntityPublicFloat": {
                "units": {
                    "USD": [
                        _fact("2025-06-30", "2025-06-30", 900_000_000_000, "2026-01-29", form="10-K"),
                        _fact("2025-06-30", "2025-06-30", 910_000_000_000, "2026-02-02", form="10-K/A"),
                    ]
                }
            },
        },
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
    shares = {r["fiscal_quarter"]: r for r in rows if r["metric"] == "shares_outstanding"}
    assert shares["2025Q1"]["value"] == 3_200_000_000
    assert shares["2025Q2"]["value"] == 3_210_000_000
    assert shares["2025Q2"]["unit"] == "shares"
    assert str(shares["2025Q2"]["release_date"]) == "2025-07-24"
    public_float = [r for r in rows if r["metric"] == "public_float_usd"]
    assert len(public_float) == 1
    assert public_float[0]["value"] == 900_000_000_000
    assert public_float[0]["unit"] == "USD"
    assert str(public_float[0]["measurement_date"]) == "2025-06-30"
    assert str(public_float[0]["release_date"]) == "2026-01-29"


def test_resolve_any_sec_ticker_and_non_filer():
    assert fq.resolve_ciks(["TSLA", "AAPL", "BRK.B", "UNKNOWN"], {
        "0": {"ticker": "TSLA", "cik_str": 1318605},
        "1": {"ticker": "AAPL", "cik_str": 320193},
        "2": {"ticker": "BRK-B", "cik_str": 1067983},
    }) == {"TSLA": "0001318605", "AAPL": "0000320193", "BRK.B": "0001067983"}


def test_non_calendar_fiscal_year_and_q4_derivation():
    facts = [
        _fact("2024-10-01", "2024-12-31", 100, "2025-02-01"),
        _fact("2025-01-01", "2025-03-31", 120, "2025-05-01"),
        _fact("2025-04-01", "2025-06-30", 130, "2025-08-01"),
        _fact("2024-10-01", "2025-09-30", 500, "2025-11-01", form="10-K"),
    ]
    rows = fq.xbrl_rows("AAPL", {"facts": {"us-gaap": {"Revenues": {"units": {"USD": facts}}}}})
    by_quarter = {row["fiscal_quarter"]: row["value"] for row in rows}
    assert by_quarter == {"2025Q1": 100, "2025Q2": 120, "2025Q3": 130, "2025Q4": 150}


@pytest.mark.parametrize("provider_failure", [False, True])
def test_added_ticker_collection_and_failure_does_not_publish(monkeypatch, provider_failure):
    from contextlib import contextmanager, nullcontext

    import httpx

    import http_client
    import lake
    import observability
    import universe

    monkeypatch.setenv("LAKE_BUCKET", "test-lake")
    monkeypatch.setattr(universe, "user_ticker_union", lambda: ["AAPL", "UNKNOWN", "SPY"])
    monkeypatch.setattr(observability, "job_handler", lambda _job: lambda fn: fn)
    monkeypatch.setattr(lake, "job_lease", lambda _key: nullcontext())
    writes = []
    monkeypatch.setattr(lake, "write_parquet", lambda table, key: writes.append((key, table.to_dicts())))
    monkeypatch.setattr(lake, "write_json", lambda doc, key, **_kwargs: writes.append((key, doc)))

    @contextmanager
    def source(_source):
        record = {"outcome": "success"}
        try:
            yield record
        except httpx.HTTPStatusError as exc:
            record.update(outcome="failure", error=str(exc))

    monkeypatch.setattr(observability, "source_run", source)

    class FakeS3:
        class exceptions:
            class NoSuchKey(Exception):
                pass

        def get_object(self, **_kwargs):
            raise self.exceptions.NoSuchKey

        def get_paginator(self, _name):
            class _Pager:
                def paginate(self, **_kwargs):
                    return iter([{}])

            return _Pager()

    monkeypatch.setattr(lake, "s3", FakeS3)
    requests = []

    def respond(request):
        requests.append(str(request.url))
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json={
                "0": {"ticker": "AAPL", "cik_str": 320193},
                "1": {"ticker": "SPY", "cik_str": 884394},
            })
        if "0000884394" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(503 if provider_failure else 200, json=COMPANYFACTS)

    monkeypatch.setattr(http_client, "get_client", lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    if provider_failure:
        with pytest.raises(RuntimeError, match="Fundamentals failed for AAPL"):
            fq.handler({"detail-type": "TickerAdded", "detail": {"ticker": "AAPL"}}, None)
        assert writes == []
    else:
        result = fq.handler({"detail-type": "TickerAdded", "detail": {"ticker": "AAPL"}}, None)
        doc = writes[-1][1]
        assert result["rows"] > 0
        assert {row["ticker"] for row in doc["rows"]} == {"AAPL"}
        assert doc["ticker_status"]["UNKNOWN"] == "not_an_sec_filer"
        assert doc["ticker_status"]["SPY"] == "no_xbrl"
        assert any("CIK0000320193" in url for url in requests)


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
