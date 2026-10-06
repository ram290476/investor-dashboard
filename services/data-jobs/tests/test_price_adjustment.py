"""Split/dividend adjustment: Yahoo events, nightly reconcile rebuilds, legacy rows without close_raw."""

from datetime import UTC, date, datetime, timedelta

import boto3
import httpx
import numpy as np
import polars as pl
import pytest
from moto import mock_aws

import http_client
import lake
import observability
import price_reconcile as pr
import trend_metrics
import universe
from collectors import yahoo

TODAY = date(2026, 1, 30)  # Friday; history spans 2025 and 2026 partitions
GMT_OFFSET = -18000  # EST


class _Context:
    function_name = "test"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test"
    aws_request_id = "req-1"


def _business_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 14, 30, tzinfo=UTC).timestamp())


class FakeYahoo:
    """Yahoo chart API over a raw (actually traded) price series with splits and dividends.

    Like Yahoo: `close` is split-adjusted, `adjclose` is split- and dividend-adjusted, and dividend
    amounts are split-adjusted. Serves only the requested [period1, period2) window.
    """

    def __init__(self, days, raw, splits=(), dividends=()):
        self.days, self.raw = list(days), np.asarray(raw, dtype=float)
        self.splits = list(splits)  # (ex_date, numerator, denominator)
        self.dividends = list(dividends)  # (ex_date, split-adjusted amount)
        self.calls: list[tuple[date, date]] = []
        factor = np.ones(len(self.days))
        for ex, num, den in self.splits:
            factor *= np.where(np.array(self.days) < ex, num / den, 1.0)
        self.close = self.raw / factor
        div = np.ones(len(self.days))
        for ex, amount in self.dividends:
            prev = max(i for i, d in enumerate(self.days) if d < ex)
            div *= np.where(np.array(self.days) < ex, 1 - amount / self.close[prev], 1.0)
        self.adjclose = self.close * div

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = request.url.params
        start = datetime.fromtimestamp(int(q["period1"]), tz=UTC).date()
        end = datetime.fromtimestamp(int(q["period2"]), tz=UTC).date()
        self.calls.append((start, end))
        idx = [i for i, d in enumerate(self.days) if start <= d < end]
        events = {
            "splits": {
                str(_ts(ex)): {"date": _ts(ex), "numerator": n, "denominator": dn, "splitRatio": f"{n}:{dn}"}
                for ex, n, dn in self.splits
                if start <= ex < end
            },
            "dividends": {
                str(_ts(ex)): {"amount": a, "date": _ts(ex)} for ex, a in self.dividends if start <= ex < end
            },
        }
        result = {
            "meta": {"gmtoffset": GMT_OFFSET},
            "timestamp": [_ts(self.days[i]) for i in idx],
            "events": events,
            "indicators": {
                "quote": [{"close": [float(self.close[i]) for i in idx], "volume": [1000] * len(idx)}],
                "adjclose": [{"adjclose": [float(self.adjclose[i]) for i in idx]}],
            },
        }
        return httpx.Response(200, json={"chart": {"result": [result], "error": None}})


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
        monkeypatch.setattr(pr, "current_date", lambda: TODAY)
        yield client


def _use_yahoo(monkeypatch, fake):
    real_get_client = http_client.get_client
    monkeypatch.setattr(
        http_client, "get_client", lambda **kw: real_get_client(transport=httpx.MockTransport(fake), **kw)
    )


def _d4_history(days, raw, adj_as_written=None):
    """What daily_prices stored day by day: actual closes, adjusted only as of each write."""
    adj = raw if adj_as_written is None else adj_as_written
    return pl.DataFrame(
        {
            "ticker": "TSLA",
            "date": days,
            "close": np.asarray(raw, dtype=float),
            "close_raw": np.asarray(raw, dtype=float),
            "adj_close": np.asarray(adj, dtype=float),
            "volume": 10,
            "source_id": "DS-02",
        }
    )


def _etag(client, year):
    return client.head_object(Bucket="lake", Key=lake.price_partition_key("TSLA", year))["ETag"]


# --- Yahoo events -----------------------------------------------------------------------------


def test_parse_events_splits_and_dividends_to_exchange_dates():
    fake = FakeYahoo(
        _business_days(date(2026, 1, 2), date(2026, 1, 30)),
        np.full(21, 100.0),
        splits=[(date(2026, 1, 15), 3, 1)],
        dividends=[(date(2026, 1, 20), 0.25)],
    )
    params = yahoo.chart_params(start=date(2026, 1, 1), end=date(2026, 2, 1))
    request = httpx.Request("GET", yahoo.CHART_URL.format(ticker="TSLA"), params=params)
    payload = fake(request).json()
    events = yahoo.parse_events(payload)
    assert events == [
        {"type": "split", "date": date(2026, 1, 15), "ratio": 3.0, "label": "3:1"},
        {"type": "dividend", "date": date(2026, 1, 20), "amount": 0.25},
    ]
    assert pr.event_id(events[0]) == "split:2026-01-15:3:1"
    assert pr.event_id(events[1]) == "dividend:2026-01-20:0.250000"


# --- split partway through a series -----------------------------------------------------------


def test_split_reconcile_restores_continuity_and_rewrites_partitions(s3, monkeypatch):
    days = _business_days(date(2025, 11, 3), TODAY - timedelta(days=1))
    split_day = date(2026, 1, 15)
    rng = np.random.default_rng(5)
    adjusted_path = 100 * np.cumprod(1 + rng.normal(0, 0.005, len(days)))
    raw = np.where(np.array(days) < split_day, adjusted_path * 2, adjusted_path)  # 2:1 split
    lake.upsert_prices(_d4_history(days, raw))  # pre-split rows still carry pre-split adj_close
    before = lake.read_prices("TSLA")
    jump = (
        before.filter(pl.col("date") == split_day)["adj_close"][0]
        / before.filter(pl.col("date") == max(d for d in days if d < split_day))["adj_close"][0]
    )
    assert jump < 0.55  # the broken state: adj_close halves overnight
    etags = {y: _etag(s3, y) for y in (2025, 2026)}

    fake = FakeYahoo(days, raw, splits=[(split_day, 2, 1)])
    _use_yahoo(monkeypatch, fake)
    result = pr.handler({}, _Context())

    assert result["rebuilt"] == ["TSLA"]
    after = lake.read_prices("TSLA")
    assert after.height == len(days) and after.select("date").is_duplicated().sum() == 0
    log_returns = np.diff(np.log(after["adj_close"].to_numpy()))
    assert np.abs(log_returns).max() < 0.05  # continuous across the split
    assert np.allclose(after["adj_close"].to_numpy(), adjusted_path)
    assert np.allclose(after["close_raw"].to_numpy(), raw)  # actual traded closes preserved
    assert np.allclose(after["close"].to_numpy(), adjusted_path)  # split-adjusted as of today
    assert set(after["source_id"]) == {"DS-02"}  # daily_prices rows stay daily_prices rows
    assert all(_etag(s3, y) != etags[y] for y in (2025, 2026))  # both yearly partitions rewritten

    state = lake.read_json(pr.STATE_KEY)
    assert "split:2026-01-15:2:1" in state["tickers"]["TSLA"]["seen_events"]
    etags = {y: _etag(s3, y) for y in (2025, 2026)}
    calls = len(fake.calls)
    second = pr.handler({}, _Context())  # same event again: nothing to do
    assert second["rebuilt"] == []
    assert len(fake.calls) == calls + 1  # only the recent-window event check
    assert all(_etag(s3, y) == etags[y] for y in (2025, 2026))


# --- dividends --------------------------------------------------------------------------------


def test_dividend_reconcile_adjusts_rows_before_ex_date(s3, monkeypatch):
    days = _business_days(date(2025, 12, 1), TODAY - timedelta(days=1))
    ex = date(2026, 1, 22)
    raw = np.full(len(days), 100.0)
    lake.upsert_prices(_d4_history(days, raw))
    fake = FakeYahoo(days, raw, dividends=[(ex, 2.0)])
    _use_yahoo(monkeypatch, fake)

    assert pr.handler({}, _Context())["rebuilt"] == ["TSLA"]
    after = lake.read_prices("TSLA")
    pre = after.filter(pl.col("date") < ex)
    post = after.filter(pl.col("date") >= ex)
    assert np.allclose(pre["adj_close"].to_numpy(), 98.0)  # 100 * (1 - 2/100)
    assert np.allclose(post["adj_close"].to_numpy(), 100.0)
    assert np.allclose(after["close_raw"].to_numpy(), 100.0)
    assert np.allclose(after["close"].to_numpy(), 100.0)  # dividends do not change close


def test_rebuild_history_uses_yahoo_dividend_factor_for_dates_yahoo_lacks():
    days = _business_days(date(2026, 1, 5), date(2026, 1, 16))
    yahoo_rows = [
        {"ticker": "TSLA", "date": d, "close": 50.0, "adj_close": 49.0 if d < date(2026, 1, 12) else 50.0}
        for d in days[:-1]  # Yahoo has not published the last day yet
    ]
    existing = _d4_history(days, np.full(len(days), 100.0))
    out = pr.rebuild_history(existing, yahoo_rows, [{"type": "split", "date": date(2026, 1, 9), "ratio": 2.0}])
    by_day = {r["date"]: r for r in out.to_dicts()}
    assert by_day[date(2026, 1, 8)]["close"] == 50.0  # 100 raw / 2 (split after this day)
    assert by_day[date(2026, 1, 8)]["adj_close"] == pytest.approx(49.0)  # 50 * 49/50
    assert by_day[days[-1]]["close"] == 100.0 and by_day[days[-1]]["adj_close"] == 100.0  # factor 1


# --- legacy rows without close_raw ------------------------------------------------------------


def test_legacy_rows_without_close_raw_still_read_and_get_rebuilt(s3, monkeypatch):
    days = _business_days(date(2026, 1, 5), TODAY - timedelta(days=1))
    legacy = pl.DataFrame(  # pre-change schema: no close_raw column, backfill (Yahoo) rows
        {
            "ticker": "TSLA",
            "date": days[:5],
            "close": [100.0] * 5,
            "adj_close": [100.0] * 5,
            "volume": [1] * 5,
            "source_id": "DS-05",
        }
    )
    lake.write_parquet(legacy, lake.price_partition_key("TSLA", 2026))
    lake.upsert_prices(_d4_history(days[5:], np.full(len(days) - 5, 101.0)))

    mixed = lake.read_prices("TSLA")
    assert mixed.height == len(days)
    assert mixed["close_raw"].null_count() == 5
    assert pr.needs_rebuild(mixed, [], set()) == "missing close_raw"
    px = trend_metrics.analysis_prices(mixed)
    assert px["close"].to_list() == [100.0] * 5 + [101.0] * (len(days) - 5)  # adj_close, else close

    fake = FakeYahoo(days, [100.0] * 5 + [101.0] * (len(days) - 5))
    _use_yahoo(monkeypatch, fake)
    assert pr.handler({}, _Context())["rebuilt"] == ["TSLA"]
    healed = lake.read_prices("TSLA")
    assert healed["close_raw"].null_count() == 0 and healed["adj_close"].null_count() == 0
    assert healed.filter(pl.col("source_id") == "DS-05")["close_raw"].to_list() == [100.0] * 5


def test_analysis_prices_falls_back_to_close_without_adj_close_column():
    frame = pl.DataFrame({"ticker": ["SPY"], "date": [date(2026, 1, 2)], "close": [500.0]})
    assert trend_metrics.analysis_prices(frame)["close"].to_list() == [500.0]


def test_no_history_and_no_events_means_no_rebuild():
    assert pr.needs_rebuild(pl.DataFrame(), [{"type": "split", "date": date(2026, 1, 2), "ratio": 2.0}], set()) is None
    complete = _d4_history([date(2026, 1, 5)], [1.0])
    old_event = {"type": "dividend", "date": date(2025, 1, 2), "amount": 1.0}  # before stored history
    assert pr.needs_rebuild(complete, [old_event], set()) is None
    new_event = {"type": "dividend", "date": date(2026, 1, 2), "amount": 1.0}
    assert pr.needs_rebuild(complete, [new_event], set()) is None  # on/before the first stored day
    later = {"type": "dividend", "date": date(2026, 1, 6), "amount": 1.0}
    assert pr.needs_rebuild(complete, [later], set()) == "new event dividend:2026-01-06:1.000000"
    assert pr.needs_rebuild(complete, [later], {pr.event_id(later)}) is None
