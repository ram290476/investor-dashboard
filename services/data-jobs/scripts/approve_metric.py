#!/usr/bin/env python3
"""Set a catalog metric to approved or rejected.

Usage:
    python scripts/approve_metric.py TSLA total_deliveries --by ram
    python scripts/approve_metric.py TSLA total_deliveries --by ram --status rejected

This wraps set_catalog_status. It does not invent an approval: --by is required
when status is approved, and CI rejects an approved entry with no approved_by.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "functions" / "company_ir"))
from company_ir import load_catalog, set_catalog_status, validate_catalog

CATALOG_DIR = Path(__file__).resolve().parents[1] / "src" / "functions" / "company_ir" / "catalog"


def apply_status(catalog: dict, metric_id: str, status: str, reviewer: str, approved_at: str | None) -> dict:
    if status == "approved" and not reviewer.strip():
        raise ValueError("approved entries require --by")
    updated = set_catalog_status(
        catalog,
        metric_id,
        status,
        reviewer.strip() if status == "approved" else None,
        approved_at if status == "approved" else None,
    )
    errors = validate_catalog(updated)
    if errors:
        raise ValueError("; ".join(errors))
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker")
    parser.add_argument("metric_id")
    parser.add_argument("--by", default="", help="Reviewer name. Required when approving.")
    parser.add_argument("--status", choices=["approved", "rejected", "proposed"], default="approved")
    parser.add_argument("--at", default="", help="ISO timestamp. Defaults to now (UTC) for approvals.")
    args = parser.parse_args()
    path = CATALOG_DIR / f"{args.ticker.upper()}.json"
    catalog = load_catalog(args.ticker) if path.is_file() else {"ticker": args.ticker.upper(), "metrics": []}
    if not path.is_file():
        print(f"No catalog at {path}", file=sys.stderr)
        return 1
    stamp = args.at or datetime.now(UTC).isoformat(timespec="seconds")
    try:
        updated = apply_status(catalog, args.metric_id, args.status, args.by, stamp)
    except (KeyError, ValueError) as exc:
        print(f"Not saved: {exc}", file=sys.stderr)
        return 1
    path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {path.name}: {args.metric_id} is {args.status}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
