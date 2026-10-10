# Company metrics catalog

The collector discovers row labels from SEC exhibits and writes `proposed` entries. Nothing is auto-approved, including optional narrative candidates. Proposed metrics are served with status, confidence and source so the company page can label them Pending review. Approved metrics stay primary. A metric is treated as approved only after a person sets `status` to `approved` and names `approved_by`. Rejected metrics are not served.

## Approve or reject

Edit `catalog/{TICKER}.json` in a pull request, or run:

```bash
python services/data-jobs/scripts/approve_metric.py TSLA total_deliveries --by ram
```

`approved_at` is filled in UTC. CI loads every catalog file and fails when an approved entry has no `approved_by`. Rejecting clears the reviewer fields:

```bash
python services/data-jobs/scripts/approve_metric.py TSLA total_deliveries --by ram --status rejected
```

Merging the catalog deploys it with the job image. An owner-only approval control and an S3 catalog override are a follow-up, not part of this pipeline.

## Propose a catalog for another ticker

```bash
python services/data-jobs/scripts/propose_catalog.py SPCX --fixtures path/to/exhibit.html --write
```

Every new entry is `proposed`. The script does not approve metrics and does not open the pull request. Review the file, commit it, and open a PR. Tickers without an approved catalog stay on "No company-specific metrics discovered".

## What the collector will and will not fetch

EDGAR is primary: submissions, 8-K Item 2.02 EX-99.1 (deliveries release versus Update deck, by exhibit title), the latest 10-Q and 10-K, and companyfacts. The filing index is `{accession-without-dashes}/index.json`. Requests send `InvestorDashboardIR/1.0 (<SEC_USER_AGENT>)` and stay under 10 requests per second.

A collect that stores some documents and fails others publishes outcome `partial`. Extract and serve still run for what landed. A collect that stores nothing publishes `failure` and does not continue.

`www.tesla.com` is not fetched. `ir.tesla.com` HTML is not fetched. A deck PDF on that host is a fallback only, after a cached robots.txt check, and a 403 or 429 stops the host. Leave `ir_sources[].urls` empty to stay on EDGAR.

`COMPANY_IR_LLM_SECRET_ID` stays unset. Narrative candidates run only when that id is set, at confidence 0.40, and they are never approved.
