#!/usr/bin/env python3
"""Add a manual deep-dive observation. The value is always stored as pending review.

Usage:
    python scripts/add_deep_dive_entry.py --bucket BUCKET TSLA robotaxi_fleet 2026Q2 120 \
        --unit vehicles --confidence low --source-url https://www.sec.gov/Archives/edgar/data/example \
        --source-title "Q2 2026 update" --note "Fleet size stated on the call"

    Add --dimension place=Austin --dimension status=driverless for a map point.

Nothing in this script sets status to approved. A later collector copies the row
into curated/deep_dive and the serving document still marks it proposed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "functions" / "deep_dive"))
from deep_dive import manual_key, normalize_manual_entry

CONFIDENCE = ("low", "medium", "high")


def _parse_dimensions(items: list[str]) -> dict:
    found = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"dimension must be key=value, got {item!r}")
        found[key.strip()] = value.strip()
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("ticker")
    parser.add_argument("metric_id")
    parser.add_argument("period", help="YYYYQ[1-4] or YYYY-MM")
    parser.add_argument("value", help="Number. Use 'blank' when the period was not disclosed.")
    parser.add_argument("--unit", default="")
    parser.add_argument("--confidence", choices=CONFIDENCE, required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-title", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--dimension", action="append", default=[], help="key=value, repeatable")
    args = parser.parse_args()
    if args.value == "blank":
        value = None
    else:
        try:
            value = float(args.value)
        except ValueError:
            print("value must be a number or 'blank'", file=sys.stderr)
            return 1
    entry = normalize_manual_entry({
        "metric_id": args.metric_id,
        "period": args.period,
        "value": value,
        "unit": args.unit,
        "confidence": args.confidence,
        "source_url": args.source_url,
        "source_title": args.source_title,
        "note": args.note,
        "dimensions": _parse_dimensions(args.dimension),
        "status": "approved",
        "approved_by": "ignored",
    })
    if entry is None:
        print("Not saved: metric id, period, or https source URL is not valid.", file=sys.stderr)
        return 1
    if entry["approval_state"] != "proposed" or entry["approved"] is not False:
        print("Not saved: manual entries cannot be approved by this script.", file=sys.stderr)
        return 1

    key = manual_key(args.ticker)
    client = boto3.client("s3")
    existing = {"ticker": args.ticker.upper(), "entries": []}
    try:
        existing = json.loads(client.get_object(Bucket=args.bucket, Key=key)["Body"].read())
    except client.exceptions.NoSuchKey:
        existing = {"ticker": args.ticker.upper(), "entries": []}
    except client.exceptions.ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code not in {"NoSuchKey", "404", "AccessDenied", "NotFound"}:
            raise
    if not isinstance(existing, dict):
        existing = {"ticker": args.ticker.upper(), "entries": []}
    entries = [item for item in existing.get("entries") or [] if isinstance(item, dict)]
    identity = (entry["metric_id"], entry["period"], tuple(sorted(entry["dimensions"].items())))
    kept = []
    for item in entries:
        cleaned = normalize_manual_entry(item)
        if cleaned is None:
            continue
        other = (cleaned["metric_id"], cleaned["period"], tuple(sorted(cleaned["dimensions"].items())))
        if other == identity:
            continue
        kept.append(cleaned)
    kept.append(entry)
    document = {"ticker": args.ticker.upper(), "entries": kept}
    client.put_object(
        Bucket=args.bucket,
        Key=key,
        Body=json.dumps(document, indent=2).encode(),
        ContentType="application/json",
    )
    print(f"Saved {len(kept)} pending entries to s3://{args.bucket}/{key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
