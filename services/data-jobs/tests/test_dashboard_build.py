import dashboard_build


def test_snapshot_includes_prices_trends_status_and_only_matching_fundamentals():
    snapshot = dashboard_build.build_snapshot(
        tickers=["TSLA", "SPCX"],
        prices={
            "TSLA": [
                {"date": "2026-10-02", "close": 440.0, "adj_close": 440.0, "volume": 100},
                {"date": "2026-10-02", "close": 441.0, "adj_close": 441.0, "volume": 101},
            ]
        },
        trends={"TSLA": {"ticker": "TSLA", "rows": [{"series_id": "DGS10", "net_pressure": 0.2}]}},
        fundamentals=[
            {"ticker": "TSLA", "metric": "deliveries", "value": 500000},
            {"ticker": "UNKNOWN", "metric": "deliveries", "value": 1},
        ],
        status={"jobs": [{"job": "D4", "status": "ok"}]},
        generated_at="2026-10-04T22:00:00+00:00",
    )

    assert snapshot["schema_version"] == 1
    assert snapshot["generated_at"] == "2026-10-04T22:00:00+00:00"
    assert snapshot["data_status"] == "available"
    assert snapshot["tickers"]["TSLA"]["price_history"] == [
        {"date": "2026-10-02", "close": 441.0, "close_raw": None, "adj_close": 441.0, "volume": 101}
    ]
    assert snapshot["tickers"]["TSLA"]["trend"]["ticker"] == "TSLA"
    assert snapshot["tickers"]["SPCX"]["price_status"] == "unavailable"
    assert snapshot["fundamentals"] == [{"ticker": "TSLA", "metric": "deliveries", "value": 500000}]
    assert snapshot["status"]["jobs"][0]["job"] == "D4"


def test_snapshot_exposes_empty_dataset_as_unavailable():
    snapshot = dashboard_build.build_snapshot(["TSLA"], {}, {}, [], None)
    assert snapshot["data_status"] == "unavailable"
    assert snapshot["tickers"]["TSLA"]["price_history"] == []
    assert snapshot["tickers"]["TSLA"]["price_as_of"] is None
    assert snapshot["status"] is None


def test_snapshot_carries_close_raw_and_tolerates_rows_without_it():
    snapshot = dashboard_build.build_snapshot(
        tickers=["TSLA"],
        prices={
            "TSLA": [
                {"date": "2026-10-01", "close": 440.0, "adj_close": None, "volume": 1},  # legacy row
                {"date": "2026-10-02", "close": 150.0, "close_raw": 300.0, "adj_close": 149.0, "volume": 2},
            ]
        },
        trends={},
        fundamentals=[],
        status=None,
    )
    history = snapshot["tickers"]["TSLA"]["price_history"]
    assert history[0]["close_raw"] is None and history[0]["adj_close"] is None
    assert history[1] == {"date": "2026-10-02", "close": 150.0, "close_raw": 300.0, "adj_close": 149.0, "volume": 2}
