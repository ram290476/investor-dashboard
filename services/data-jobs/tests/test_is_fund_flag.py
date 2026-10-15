"""Write-once is_fund flag (#223): stamped on the first definitive collection, never flipped."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError

import fund_filings as ff
import fund_serving as fs
import lake

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
ARKK = {"ticker": "ARKK", "status": "resolved", "kind": "fund", "cik": "1579982", "method": "company_tickers_mf",
        "managers_status": "resolved", "managers": [{"cik": "1697748"}]}
TSLA = {"ticker": "TSLA", "status": "resolved", "kind": "operating", "cik": "1318605"}


class Store:
    """In-memory lake with S3 If-None-Match semantics."""

    def __init__(self):
        self.objects = {}

    def write_once(self, obj, key):
        if key in self.objects:
            return False
        self.objects[key] = obj
        return True

    def run(self, funds, managers, when=NOW):
        collected = {"results": [], "managers": managers, "fund_map": {"funds": funds}, "state": {}}
        return ff.publish(collected, write_text=lambda *a: None, write_json=lambda *a: None,
                          write_parquet=lambda *a: None, write_json_once=self.write_once, now=when)


WITH_13F = [{"cik": "1697748", "status": "ok", "report": {"holdings": [{"cusip": "88160R101"}]}}]
EMPTY_13F = [{"cik": "1697748", "status": "failed", "reason": "13f_unavailable"}]


def test_stamped_on_first_collection():
    store = Store()
    assert store.run({"ARKK": ARKK, "TSLA": TSLA}, WITH_13F) == ["ARKK", "TSLA"]
    assert store.objects[ff.is_fund_key("ARKK")]["is_fund"] is True
    assert store.objects[ff.is_fund_key("TSLA")]["is_fund"] is False
    assert store.objects[ff.is_fund_key("ARKK")]["basis"]["manager_ciks"] == ["1697748"]


def test_empty_later_13f_does_not_change_it():
    store = Store()
    store.run({"ARKK": ARKK}, WITH_13F)
    first = dict(store.objects[ff.is_fund_key("ARKK")])
    # Later quarter: the manager's 13F is empty/unavailable, and even a remap that loses the manager.
    assert store.run({"ARKK": ARKK}, EMPTY_13F, when=datetime(2027, 2, 1, tzinfo=UTC)) == []
    lost = {**ARKK, "managers_status": "no_13f_filer", "managers": []}
    assert store.run({"ARKK": lost}, [], when=datetime(2027, 5, 1, tzinfo=UTC)) == []
    assert store.objects[ff.is_fund_key("ARKK")] == first


def test_rerun_does_not_change_it():
    store = Store()
    store.run({"ARKK": ARKK, "TSLA": TSLA}, WITH_13F)
    snapshot = {k: dict(v) for k, v in store.objects.items()}
    assert store.run({"ARKK": ARKK, "TSLA": TSLA}, WITH_13F) == []
    assert store.objects == snapshot


def test_not_definitive_is_not_stamped():
    assert ff.is_fund_decision({**ARKK, "managers_status": "retry"}, {"1697748"}) is None
    assert ff.is_fund_decision(ARKK, set()) is None  # manager known, 13F did not load this run
    assert ff.is_fund_decision({"status": "unresolved", "reason": "ticker_collision"}, set()) is None
    assert ff.is_fund_decision({**ARKK, "managers_status": "no_adviser", "managers": []}, set()) is False


def test_lake_write_json_once_uses_if_none_match():
    calls = []

    class Client:
        def __init__(self, exists):
            self.exists = exists

        def put_object(self, **kwargs):
            calls.append(kwargs)
            if self.exists:
                raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")

    assert lake.write_json_once({"is_fund": True}, "k", bucket="b", client=Client(False)) is True
    assert lake.write_json_once({"is_fund": False}, "k", bucket="b", client=Client(True)) is False
    assert all(c["IfNoneMatch"] == "*" for c in calls)
    with pytest.raises(ClientError):
        class Broken:
            def put_object(self, **kwargs):
                raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        lake.write_json_once({}, "k", bucket="b", client=Broken())


def test_serving_reads_the_stamp_and_reports_conflicts_without_overriding():
    stamps = {
        "curated/fund_universe/is_fund/AGIQ.json": {"is_fund": False},
        "curated/fund_universe/is_fund/SPCX.json": {"is_fund": True},
        "curated/fund_universe/is_fund/ARKK.json": {"is_fund": True},
    }
    fund_map = {"funds": {
        "AGIQ": {"kind": "fund", "status": "resolved", "cik": "1742912", "managers": []},
        "SPCX": {"kind": "operating", "status": "resolved", "cik": "1181412"},
        "ARKK": {"kind": "fund", "status": "resolved", "cik": "1579982", "managers": []},
    }}
    docs = {fs.FUND_MAP_KEY: fund_map, **stamps}
    built = fs.build_fund_documents(["AGIQ", "SPCX", "ARKK"], read_json=docs.get, read_rows=lambda p: [],
                                    now=NOW, generated_at="g")
    assert built["flags"]["ARKK"] == {"is_fund": True, "conflict": None}
    assert built["flags"]["AGIQ"]["conflict"] == "is_fund_stamped_false_but_nport_fund"
    assert built["flags"]["SPCX"]["conflict"] == "is_fund_stamped_true_but_now_operating"
    # Both sides are kept as-is: the stamp stays, and the fund map still decides the document.
    assert built["funds"]["AGIQ"]["is_fund"] is False and "SPCX" not in built["funds"]
