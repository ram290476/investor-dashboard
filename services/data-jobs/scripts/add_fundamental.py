#!/usr/bin/env python3
"""Add or correct a manual fundamentals row (deliveries, FSD subscribers).

Usage:
    scripts/add_fundamental.py --bucket invdash-lake-<acct> TSLA deliveries 2026Q3 2026-10-02 497099 \
        [--source DS-12] [--note "Q3 2026 production and deliveries release"]

Validates the row with the same rules the Q1 job uses, appends it to
s3://<bucket>/manual/fundamentals/fundamentals_manual.csv (a later row for the same
ticker/metric/quarter replaces an earlier one), and prints the file's new row count.
The bucket is versioned, so every edit is recoverable.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "functions" / "q1_fundamentals"))
from fundamentals import ManualRowError, parse_manual_csv

KEY = "manual/fundamentals/fundamentals_manual.csv"
HEADER = ["ticker", "metric", "fiscal_quarter", "release_date", "value", "source_id", "note"]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", required=True)
    p.add_argument("ticker")
    p.add_argument("metric", choices=["deliveries", "fsd_subscribers"])
    p.add_argument("fiscal_quarter")
    p.add_argument("release_date", help="YYYY-MM-DD, the day the number was published")
    p.add_argument("value")
    p.add_argument("--source", default="DS-12")
    p.add_argument("--note", default="")
    a = p.parse_args()

    s3 = boto3.client("s3")
    try:
        existing = s3.get_object(Bucket=a.bucket, Key=KEY)["Body"].read().decode()
    except s3.exceptions.NoSuchKey:
        existing = ",".join(HEADER) + "\n"

    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow(
        [a.ticker.upper(), a.metric, a.fiscal_quarter, a.release_date, a.value, a.source, a.note]
    )
    updated = existing if existing.endswith("\n") else existing + "\n"
    updated += buf.getvalue()
    try:
        rows = parse_manual_csv(updated)
    except ManualRowError as exc:
        print(f"Not saved: {exc}", file=sys.stderr)
        return 1
    s3.put_object(Bucket=a.bucket, Key=KEY, Body=updated.encode(), ContentType="text/csv")
    print(f"Saved. {len(rows)} manual rows; the next Q1 run publishes them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
