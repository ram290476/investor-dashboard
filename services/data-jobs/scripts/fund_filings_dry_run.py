"""Read-only live dry run of the fund_filings collector (#223).

Reads the watchlist union from DynamoDB (scan only), calls the real SEC endpoints with the
SEC_USER_AGENT contact under the shared pace, and prints the fund map and parsed holdings.
Nothing is written to S3. Optional --save-dir keeps the fetched NPORT XML for fixtures.

    AWS_PROFILE=invdash-admin AWS_REGION=us-west-1 SEC_USER_AGENT="..." \
        python scripts/fund_filings_dry_run.py [--tickers ARKK ...] [--save-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"
for p in [ROOT / "app", *sorted((ROOT / "functions").iterdir())]:
    if p.is_dir():
        sys.path.insert(0, str(p))

import fund_filings as ff  # noqa: E402
from company_ir import Pace, crawler_user_agent  # noqa: E402
from http_client import get_client  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*", help="override the watchlist (still no hard-coded list)")
    parser.add_argument("--save-dir")
    args = parser.parse_args()
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        from universe import user_ticker_union, watchlist_universe

        tickers = list(watchlist_universe(user_ticker_union()).get("tickers") or [])
    pace = Pace()
    headers = {"User-Agent": crawler_user_agent(), "Accept": "application/json,application/xml,*/*"}
    with get_client(headers=headers) as http:
        collected = ff.run_collection(
            tickers, fetch=lambda url: http.get(url), pace=pace, sleep=pace.sleep,
            fund_map=None, state={}, now=datetime.now(UTC),
        )
    if args.save_dir:
        out = Path(args.save_dir)
        out.mkdir(parents=True, exist_ok=True)
        for r in collected["results"]:
            if r.get("filing"):
                (out / f"{r['ticker']}_{r['filing']['accession']}.xml").write_text(r["filing"]["xml"])
        for m in collected.get("managers") or []:
            if m.get("report"):
                (out / f"13f_{m['cik']}_cover.xml").write_text(m["report"]["cover_xml"])
                (out / f"13f_{m['cik']}_table.xml").write_text(m["report"]["table_xml"])
    print(json.dumps({
        "universe": tickers,
        "fund_map": {k: v for k, v in collected["fund_map"]["funds"].items() if v.get("kind") != "operating"},
        "funds": [ff.fund_summary(r) for r in collected["results"]],
        "managers": [
            {"cik": m["cik"], "name": m.get("name"), "status": m["status"],
             "period_end": (m.get("report") or {}).get("period_end"),
             "filed_at": (m.get("report") or {}).get("filed_at"),
             "accession": (m.get("report") or {}).get("accession"),
             "positions": len((m.get("report") or {}).get("holdings") or []),
             "top": [{k: h[k] for k in ("name", "cusip", "value_usd", "pct_portfolio", "put_call")}
                     for h in ((m.get("report") or {}).get("holdings") or [])[:5]]}
            for m in collected.get("managers") or []
        ],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
