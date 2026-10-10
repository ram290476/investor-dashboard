"""Daily volume keeps a source, and only the last 10 sessions are rewritten from Yahoo."""

from datetime import UTC, date, datetime

import boto3
import httpx
import polars as pl
import pytest
from moto import mock_aws

import http_client
import lake
import observability
import price_reconcile as pr
import universe

AS_OF = date(2026, 10, 10)  # Saturday after the issue snapshot; the window ends 2026-10-09
OUTSIDE = date(2026, 9, 25)  # session before the window; issue snapshot still had consolidated volume
TOLERANCE = 0.02

# Yahoo chart volumes for 2026-09-25 and the last 10 sessions through 2026-10-09.
# The 2026-09-25 and 2026-09-28 figures match the issue's pre-cutover snapshot exactly.
YAHOO = {
    "TSLA": {
        OUTSIDE: 45_999_500,
        date(2026, 9, 28): 39_452_900,
        date(2026, 9, 29): 32_492_400,
        date(2026, 9, 30): 39_235_800,
        date(2026, 10, 1): 31_080_800,
        date(2026, 10, 2): 55_330_100,
        date(2026, 10, 5): 42_232_700,
        date(2026, 10, 6): 27_735_400,
        date(2026, 10, 7): 25_594_600,
        date(2026, 10, 8): 28_394_900,
        date(2026, 10, 9): 38_897_700,
    },
    "SPCX": {
        OUTSIDE: 54_595_200,
        date(2026, 9, 28): 83_516_700,
        date(2026, 9, 29): 80_056_700,
        date(2026, 9, 30): 79_614_200,
        date(2026, 10, 1): 69_092_500,
        date(2026, 10, 2): 119_859_100,
        date(2026, 10, 5): 135_688_900,
        date(2026, 10, 6): 106_952_400,
        date(2026, 10, 7): 77_473_000,
        date(2026, 10, 8): 71_897_800,
        date(2026, 10, 9): 92_679_500,
    },
    "AAPL": {
        OUTSIDE: 30_002_500,
        date(2026, 9, 28): 32_820_800,
        date(2026, 9, 29): 38_478_000,
        date(2026, 9, 30): 49_988_600,
        date(2026, 10, 1): 36_306_300,
        date(2026, 10, 2): 33_278_600,
        date(2026, 10, 5): 34_400_900,
        date(2026, 10, 6): 30_449_000,
        date(2026, 10, 7): 34_147_900,
        date(2026, 10, 8): 35_332_400,
        date(2026, 10, 9): 37_855_000,
    },
    "AMZN": {
        OUTSIDE: 34_263_900,
        date(2026, 9, 28): 32_596_700,
        date(2026, 9, 29): 33_730_400,
        date(2026, 9, 30): 41_913_100,
        date(2026, 10, 1): 33_243_900,
        date(2026, 10, 2): 33_433_400,
        date(2026, 10, 5): 39_273_900,
        date(2026, 10, 6): 34_120_800,
        date(2026, 10, 7): 36_225_800,
        date(2026, 10, 8): 38_649_900,
        date(2026, 10, 9): 34_710_500,
    },
    "GOOG": {
        OUTSIDE: 13_858_700,
        date(2026, 9, 28): 13_870_800,
        date(2026, 9, 29): 14_002_300,
        date(2026, 9, 30): 24_240_100,
        date(2026, 10, 1): 20_045_000,
        date(2026, 10, 2): 17_497_900,
        date(2026, 10, 5): 13_982_200,
        date(2026, 10, 6): 13_177_400,
        date(2026, 10, 7): 11_344_700,
        date(2026, 10, 8): 13_987_000,
        date(2026, 10, 9): 11_129_700,
    },
    "NVDA": {
        OUTSIDE: 89_947_700,
        date(2026, 9, 28): 142_344_300,
        date(2026, 9, 29): 101_494_900,
        date(2026, 9, 30): 121_732_200,
        date(2026, 10, 1): 98_591_400,
        date(2026, 10, 2): 135_167_800,
        date(2026, 10, 5): 127_171_500,
        date(2026, 10, 6): 101_701_900,
        date(2026, 10, 7): 81_772_400,
        date(2026, 10, 8): 118_697_400,
        date(2026, 10, 9): 84_470_000,
    },
    "PLTR": {
        OUTSIDE: 17_807_100,
        date(2026, 9, 28): 18_464_100,
        date(2026, 9, 29): 14_754_100,
        date(2026, 9, 30): 17_921_200,
        date(2026, 10, 1): 17_902_000,
        date(2026, 10, 2): 17_932_100,
        date(2026, 10, 5): 17_710_000,
        date(2026, 10, 6): 17_651_200,
        date(2026, 10, 7): 15_729_200,
        date(2026, 10, 8): 41_865_400,
        date(2026, 10, 9): 37_479_600,
    },
}

# IEX prints from the issue's mixed snapshot. These are the rows that collapsed the Volume lane.
ISSUE_IEX = {
    ("TSLA", date(2026, 9, 29)): 524_882,
    ("TSLA", date(2026, 9, 30)): 779_713,
    ("SPCX", date(2026, 9, 29)): 1_885_365,
    ("SPCX", date(2026, 9, 30)): 3_594_549,
    ("AAPL", date(2026, 9, 30)): 1_479_671,
    ("NVDA", date(2026, 9, 30)): 2_961_971,
    ("PLTR", date(2026, 9, 30)): 319_316,
}


def _window() -> list[date]:
    return pr.recent_sessions(AS_OF)


def _iex(ticker: str, day: date) -> int:
    return ISSUE_IEX.get((ticker, day), YAHOO[ticker][day] // 50)


def _frame(ticker: str) -> pl.DataFrame:
    days = [OUTSIDE, *_window()]
    rows = []
    for day in days:
        iex = day != OUTSIDE
        rows.append(
            {
                "ticker": ticker,
                "date": day,
                "close": 100.0,
                "close_raw": 100.0,
                "adj_close": 100.0,
                "volume": YAHOO[ticker][day] if not iex else _iex(ticker, day),
                "source_id": "DS-02" if iex else "DS-05",
            }
        )
    return pl.DataFrame(rows)


def _yahoo_rows(ticker: str, skip: set[date] | None = None) -> list[dict]:
    skip = skip or set()
    return [
        {"ticker": ticker, "date": day, "volume": volume}
        for day, volume in YAHOO[ticker].items()
        if day not in skip
    ]


def _by_day(frame: pl.DataFrame) -> dict[date, dict]:
    return {row["date"]: row for row in frame.to_dicts()}


def test_recent_sessions_match_the_issue_cutover_window():
    expected = [
        date(2026, 9, 28),
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
        date(2026, 10, 8),
        date(2026, 10, 9),
    ]
    assert _window() == expected
    assert pr.recent_sessions(date(2026, 10, 9)) == expected
    assert OUTSIDE not in expected


def test_reconcile_volume_overwrites_only_the_window_and_marks_the_rest():
    before = _frame("TSLA")
    outside_volume = _by_day(before)[OUTSIDE]["volume"]
    older_iex = date(2026, 9, 24)
    mixed = pl.concat(
        [
            before,
            pl.DataFrame(
                {
                    "ticker": ["TSLA"],
                    "date": [older_iex],
                    "close": [100.0],
                    "volume": [12_345],
                    "source_id": ["DS-02"],
                }
            ),
        ],
        how="diagonal_relaxed",
    )
    out = _by_day(pr.reconcile_volume(mixed, _yahoo_rows("TSLA"), AS_OF))

    assert out[OUTSIDE]["volume"] == outside_volume
    assert out[OUTSIDE]["volume_source"] == "DS-05"
    assert out[OUTSIDE]["volume_iex"] is None
    assert out[older_iex]["volume"] == 12_345
    assert out[older_iex]["volume_source"] == "DS-02"
    assert out[older_iex]["volume_iex"] == 12_345
    for day in _window():
        assert out[day]["volume"] == YAHOO["TSLA"][day]
        assert out[day]["volume_source"] == "DS-05"
        assert out[day]["volume_iex"] == _iex("TSLA", day)
        assert out[day]["source_id"] == "DS-02"


def test_reconcile_leaves_a_session_yahoo_did_not_return():
    missing = date(2026, 10, 9)
    before = _frame("TSLA")
    out = _by_day(pr.reconcile_volume(before, _yahoo_rows("TSLA", skip={missing}), AS_OF))
    assert out[missing]["volume"] == _iex("TSLA", missing)
    assert out[missing]["volume_source"] == "DS-02"
    assert out[date(2026, 10, 8)]["volume"] == YAHOO["TSLA"][date(2026, 10, 8)]
    assert out[date(2026, 10, 8)]["volume_source"] == "DS-05"


def test_last_10_sessions_match_consolidated_volume_within_2_percent():
    """Option 1 spot-check: after reconcile, each watchlist name is within 2% of Yahoo."""
    for ticker, yahoo in YAHOO.items():
        out = _by_day(pr.reconcile_volume(_frame(ticker), _yahoo_rows(ticker), AS_OF))
        for day in _window():
            consolidated = yahoo[day]
            delta = abs(out[day]["volume"] - consolidated) / consolidated
            assert delta <= TOLERANCE, f"{ticker} {day} delta {delta}"
            assert out[day]["volume_source"] == "DS-05"
        # The issue's IEX prints are not within 2% of the same consolidated figures.
        for (name, day), iex in ISSUE_IEX.items():
            if name != ticker or day not in _window():
                continue
            assert abs(iex - yahoo[day]) / yahoo[day] > TOLERANCE


def test_rebuild_keeps_an_already_reconciled_iex_print():
    day = date(2026, 10, 5)
    existing = pl.DataFrame(
        {
            "ticker": ["TSLA"],
            "date": [day],
            "close": [100.0],
            "close_raw": [100.0],
            "adj_close": [100.0],
            "volume": [42_232_700],
            "volume_iex": [844_654],
            "volume_source": ["DS-05"],
            "source_id": ["DS-02"],
        }
    )
    yahoo_rows = [{"ticker": "TSLA", "date": day, "close": 100.0, "adj_close": 100.0, "volume": 42_232_700}]
    row = pr.rebuild_history(existing, yahoo_rows, []).to_dicts()[0]
    assert row["volume"] == 42_232_700
    assert row["volume_iex"] == 844_654
    assert row["volume_source"] == "DS-05"
    assert row["source_id"] == "DS-02"


class _Context:
    function_name = "test"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test"
    aws_request_id = "req-1"


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="lake")
        monkeypatch.setattr(lake, "LAKE_BUCKET", "lake")
        monkeypatch.setattr(lake, "_s3", None)
        monkeypatch.setattr(observability, "_events", None)
        monkeypatch.setattr(universe, "user_ticker_union", lambda *a, **k: [])
        monkeypatch.setattr(universe, "BASE_TICKERS", ("TSLA",))
        monkeypatch.setattr(universe, "INDEX_PROXIES", ())
        monkeypatch.setattr(pr, "current_date", lambda: date(2026, 10, 9))
        yield client


def _chart(days: list[date], volume: int):
    calls: list[tuple[date, date]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        query = request.url.params
        start = datetime.fromtimestamp(int(query["period1"]), tz=UTC).date()
        end = datetime.fromtimestamp(int(query["period2"]), tz=UTC).date()
        calls.append((start, end))
        chosen = [day for day in days if start <= day < end]
        stamps = [int(datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC).timestamp()) for day in chosen]
        result = {
            "meta": {"gmtoffset": -14400},
            "timestamp": stamps,
            "events": {},
            "indicators": {
                "quote": [{"close": [10.0] * len(chosen), "volume": [volume] * len(chosen)}],
                "adjclose": [{"adjclose": [10.0] * len(chosen)}],
            },
        }
        return httpx.Response(200, json={"chart": {"result": [result], "error": None}})

    return calls, respond


def test_nightly_reconcile_writes_the_window_once(s3, monkeypatch):
    sessions = pr.recent_sessions(date(2026, 10, 9), 12)
    lake.upsert_prices(
        pl.DataFrame(
            {
                "ticker": ["TSLA"] * len(sessions),
                "date": sessions,
                "close": [10.0] * len(sessions),
                "close_raw": [10.0] * len(sessions),
                "adj_close": [10.0] * len(sessions),
                "volume": [1_000] * len(sessions),
                "source_id": ["DS-02"] * len(sessions),
            }
        )
    )
    calls, respond = _chart(sessions, 80_000)
    real_get_client = http_client.get_client
    monkeypatch.setattr(
        http_client, "get_client", lambda **kw: real_get_client(transport=httpx.MockTransport(respond), **kw)
    )

    first = pr.handler({}, _Context())
    assert first["rebuilt"] == [] and first["status"] == "success"
    assert len(calls) == 1  # the event-window chart; volume comes from that response
    stored = _by_day(lake.read_prices("TSLA"))
    window = set(pr.recent_sessions(date(2026, 10, 9)))
    for day, row in stored.items():
        if day in window:
            assert row["volume"] == 80_000 and row["volume_source"] == "DS-05" and row["volume_iex"] == 1_000
        else:
            assert row["volume"] == 1_000 and row["volume_source"] == "DS-02" and row["volume_iex"] == 1_000

    key = lake.price_partition_key("TSLA", 2026)
    etag = s3.head_object(Bucket="lake", Key=key)["ETag"]
    second = pr.handler({}, _Context())
    assert second["rebuilt"] == []
    assert len(calls) == 2
    assert s3.head_object(Bucket="lake", Key=key)["ETag"] == etag


def test_needs_volume_write_is_false_when_nothing_changed():
    frame = pr.reconcile_volume(_frame("AAPL"), _yahoo_rows("AAPL"), AS_OF)
    assert pr.needs_volume_write(frame, pr.reconcile_volume(frame, _yahoo_rows("AAPL"), AS_OF)) is False
    assert pr.needs_volume_write(_frame("AAPL"), frame) is True
