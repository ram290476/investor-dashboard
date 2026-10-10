#!/usr/bin/env python3
"""Propose a company-metrics catalog from exhibit fixtures or saved HTML.

Usage:
    python scripts/propose_catalog.py SPCX --fixtures tests/fixtures/company_ir/spcx_exhibit.html --write

Every entry is proposed. The script never approves a metric and it does not open a
pull request. Review the written catalog, commit it, and open a PR.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "functions" / "company_ir"))
from company_ir import load_catalog, merge_proposals, parse_company_document, sha256_hex, validate_catalog

CATALOG_DIR = Path(__file__).resolve().parents[1] / "src" / "functions" / "company_ir" / "catalog"


def build_proposed_catalog(ticker: str, documents: list[dict]) -> dict:
    """Seed or extend a catalog. Existing approvals are left untouched; new labels are proposed."""
    catalog = load_catalog(ticker)
    if not catalog.get("metrics"):
        catalog = {
            "ticker": ticker.upper(),
            "catalog_version": 1,
            "approval_note": "Every entry is proposed. Nothing is auto-approved.",
            "metrics": [],
        }
    extracted_at = datetime.now(UTC)
    proposals = []
    for document in documents:
        text = document["text"]
        parsed = parse_company_document(
            text,
            catalog,
            ticker=ticker,
            source_url=document.get("source_url") or "https://www.sec.gov/Archives/edgar/data/fixture",
            source_doc_hash=sha256_hex(text.encode("utf-8")),
            extracted_at=extracted_at,
            published_date=document.get("published_date"),
            source_kind=document.get("source_kind") or "deck",
            fiscal_period=document.get("fiscal_period"),
        )
        proposals.extend(parsed["proposals"])
    merged = merge_proposals(catalog, proposals)
    for item in merged.get("metrics") or []:
        if item.get("status") != "approved":
            item["status"] = item.get("status") if item.get("status") in {"proposed", "rejected"} else "proposed"
            if item["status"] != "approved":
                item["approved_by"] = None
                item["approved_at"] = None
    errors = validate_catalog(merged)
    if errors:
        raise ValueError("; ".join(errors))
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker")
    parser.add_argument("--fixtures", nargs="+", type=Path, required=True)
    parser.add_argument("--fiscal-period", default="2026Q3")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write catalog/{TICKER}.json. Does not approve or open a PR.",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    documents = []
    for path in args.fixtures:
        documents.append({
            "text": path.read_text(encoding="utf-8"),
            "fiscal_period": args.fiscal_period,
            "source_kind": "deck",
            "source_url": f"https://www.sec.gov/Archives/edgar/data/fixture/{path.name}",
        })
    catalog = build_proposed_catalog(args.ticker, documents)
    payload = json.dumps(catalog, indent=2) + "\n"
    if args.write:
        destination = CATALOG_DIR / f"{args.ticker.upper()}.json"
        destination.write_text(payload, encoding="utf-8")
        print(f"Wrote {destination}. All new entries are proposed. Commit the file and open a PR.")
    elif args.out:
        args.out.write_text(payload, encoding="utf-8")
        print(f"Wrote {args.out}.")
    else:
        sys.stdout.write(payload)
    print(
        "Next step: review aliases and units, then open a pull request. This script does not approve metrics.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
