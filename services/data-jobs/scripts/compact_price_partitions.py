#!/usr/bin/env python3
"""One-off: compact legacy daily-price objects into yearly partitions.

Before the yearly layout, daily_prices (D4) wrote one object per ticker-day and the
backfill wrote one object per 90-day batch:

    curated/prices_daily/ticker=<T>/date=<D>/daily.parquet
    curated/prices_daily/ticker=<T>/batch_start=<D>/batch_end=<D>/prices_daily.parquet

This folds them into curated/prices_daily/ticker=<T>/year=<YYYY>/prices.parquet with the
same (ticker, date) dedupe the jobs use (daily_prices DS-02 rows beat backfill rows; rows
already in a yearly object beat legacy rows of the same source, because they are newer).

Usage (operator credentials with s3:GetObject/PutObject/DeleteObject/ListBucket on the lake
and kms:Decrypt/GenerateDataKey on the data key):

    scripts/compact_price_partitions.py --bucket invdash-lake-<acct> --dry-run
    scripts/compact_price_partitions.py --bucket invdash-lake-<acct>                  # write, keep legacy
    scripts/compact_price_partitions.py --bucket invdash-lake-<acct> --delete-legacy  # write, verify, delete

Safe to rerun: writes are upserts and legacy objects are only deleted after every legacy
(ticker, date) is confirmed present in the yearly objects. The lake bucket is versioned, so
deleted objects remain recoverable as noncurrent versions.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "app"))
import lake

LEGACY_KEY = re.compile(r"^curated/prices_daily/ticker=(?P<ticker>[^/]+)/(date=|batch_start=)[^/].*\.parquet$")
YEARLY_KEY = re.compile(r"^curated/prices_daily/ticker=[^/]+/year=\d{4}/prices\.parquet$")


def legacy_objects(bucket: str, tickers: list[str] | None = None) -> dict[str, list[str]]:
    """Legacy price keys grouped by ticker, in S3 (lexicographic) order: batch_start= before date=."""
    found: dict[str, list[str]] = defaultdict(list)
    for page in lake.s3().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=lake.PRICES_PREFIX):
        for obj in page.get("Contents", []):
            m = LEGACY_KEY.match(obj["Key"])
            if m and (not tickers or m["ticker"] in tickers):
                found[m["ticker"]].append(obj["Key"])
    return dict(found)


def _read(bucket: str, keys: list[str]) -> pl.DataFrame:
    frames = [pl.read_parquet(io.BytesIO(lake.s3().get_object(Bucket=bucket, Key=k)["Body"].read())) for k in keys]
    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


def _delete(bucket: str, keys: list[str]) -> int:
    for i in range(0, len(keys), 1000):
        resp = lake.s3().delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[i : i + 1000]], "Quiet": True}
        )
        if resp.get("Errors"):
            raise RuntimeError(f"delete failed for {len(resp['Errors'])} object(s): {resp['Errors'][:3]}")
    return len(keys)


def compact(bucket: str, tickers: list[str] | None = None, dry_run: bool = False, delete_legacy: bool = False) -> dict:
    summary = {"tickers": {}, "legacy_objects": 0, "rows": 0, "written": 0, "deleted": 0, "dry_run": dry_run}
    for ticker, keys in sorted(legacy_objects(bucket, tickers).items()):
        legacy = lake.dedupe_prices(_read(bucket, keys))
        years = sorted(legacy["date"].dt.year().unique().to_list()) if not legacy.is_empty() else []
        info = {"legacy_objects": len(keys), "rows": legacy.height, "years": years, "written": [], "deleted": 0}
        summary["legacy_objects"] += len(keys)
        summary["rows"] += legacy.height
        if not dry_run:
            # Legacy rows go first so newer yearly rows of the same source win (incoming_wins=False).
            info["written"] = lake.upsert_prices(legacy, bucket, incoming_wins=False)
            summary["written"] += len(info["written"])
            if delete_legacy:
                compacted = lake.read_parquet_prefix(f"{lake.PRICES_PREFIX}ticker={ticker}/year=", bucket)
                missing = legacy.join(compacted.select("ticker", "date"), on=["ticker", "date"], how="anti")
                if missing.height:
                    raise RuntimeError(f"{ticker}: {missing.height} legacy rows missing after compaction; not deleting")
                info["deleted"] = _delete(bucket, keys)
                summary["deleted"] += info["deleted"]
        summary["tickers"][ticker] = info
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bucket", required=True, help="lake bucket name (terraform output lake_bucket)")
    parser.add_argument("--ticker", action="append", type=str.upper, help="limit to these tickers (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="report what would be compacted; write nothing")
    parser.add_argument("--delete-legacy", action="store_true", help="delete legacy objects after verifying")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = compact(args.bucket, args.ticker, dry_run=args.dry_run, delete_legacy=args.delete_legacy)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
