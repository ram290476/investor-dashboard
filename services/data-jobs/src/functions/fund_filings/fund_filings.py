"""SEC N-PORT fund holdings (#223, step 1).

Funds come only from the watchlist union (app/universe.py). Each ticker is mapped to a filer CIK
(and series/class when the SEC lists one) from company_tickers_mf.json, then from an EDGAR
full-text-search fallback for funds that file N-PORT but are not in that file (interval and
closed-end funds). The resolved map is cached in the lake, never in config.

For every resolved fund the latest public NPORT-P is fetched and parsed into curated holdings,
keyed by filer CIK plus fund ticker because closed-end filings carry no seriesId. SEC calls reuse
sec_filings.fetch_sec and the company_ir 10 req/s pace.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime, timedelta

import polars as pl
from issuer_sectors import resolve_issuers

from company_ir import Pace, crawler_user_agent
from manager_holdings import (
    MANAGER_MAP_MAX_AGE_DAYS,
    NCEN_FORMS,
    LookupUnavailable,
    advisers_from_ncen,
    latest_13f,
    latest_form,
    norm_name,
    resolve_manager,
)
from sec_filings import cik_map_from_company_tickers, fetch_sec, target_tickers

ISSUER_SECTORS_KEY = "curated/fund_universe/issuer_sectors.json"  # literal so the IAM read-prefix test can see it

JOB_ID = "FUND"
MF_URL = "https://www.sec.gov/files/company_tickers_mf.json"
OPERATING_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EFTS_URL = "https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22"
SERIES_FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={series_id}&type=NPORT-P"
    "&dateb=&owner=include&count=40&output=atom"
)
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/primary_doc.xml"

FUND_MAP_KEY = "curated/fund_universe/fund_map.json"
STATE_KEY = "curated/fund_filings/_state.json"
LEASE_KEY = "curated/fund_filings/_lease.json"
FUND_MAP_MAX_AGE_DAYS = 30
NPORT_FORMS = ("NPORT-P", "NPORT-P/A")
FUND_FORMS = frozenset({"NPORT-P", "NPORT-P/A", "N-CEN", "N-CEN/A", "N-2", "N-2/A", "N-23C3A", "N-CSR", "N-1A"})
MAX_NPORT_DOCS_PER_FUND = 12  # a trust files one NPORT-P per series; stop after this many misses
MIN_FALLBACK_CONFIDENCE = 0.5
MAX_NCEN_DOCS = 4

NS = {"n": "http://www.sec.gov/edgar/nport"}

HOLDINGS_SCHEMA = {
    "cik": pl.Utf8, "fund_ticker": pl.Utf8, "series_id": pl.Utf8, "period_end": pl.Utf8, "filed_at": pl.Utf8,
    "accession": pl.Utf8, "name": pl.Utf8, "title": pl.Utf8, "lei": pl.Utf8, "cusip": pl.Utf8, "isin": pl.Utf8,
    "ticker": pl.Utf8, "other_id": pl.Utf8, "other_id_desc": pl.Utf8, "balance": pl.Float64, "units": pl.Utf8,
    "currency": pl.Utf8, "value_usd": pl.Float64, "pct_nav": pl.Float64, "payoff": pl.Utf8, "asset_cat": pl.Utf8,
    "issuer_cat": pl.Utf8, "country": pl.Utf8, "fair_val_level": pl.Utf8, "restricted": pl.Boolean,
}
MANAGER_SCHEMA = {
    "manager_cik": pl.Utf8, "period_end": pl.Utf8, "filed_at": pl.Utf8, "accession": pl.Utf8, "name": pl.Utf8,
    "title": pl.Utf8, "cusip": pl.Utf8, "value_usd": pl.Float64, "shares": pl.Float64, "share_type": pl.Utf8,
    "put_call": pl.Utf8, "is_option": pl.Boolean, "discretion": pl.Utf8, "pct_portfolio": pl.Float64,
}


def cik10(cik) -> str:
    return str(int(str(cik))).zfill(10)


def raw_nport_key(cik: str, accession: str) -> str:
    return f"raw/fund_filings/nport/cik={int(cik)}/{accession}.xml"


def holdings_key(cik: str, fund_ticker: str, period: str) -> str:
    return f"curated/fund_holdings/cik={int(cik)}/fund={fund_ticker.upper()}/period={period}/holdings.parquet"


def raw_13f_key(cik: str, accession: str, name: str) -> str:
    return f"raw/fund_filings/13f/cik={int(cik)}/{accession}/{name}"


def manager_holdings_key(cik: str, period: str) -> str:
    return f"curated/manager_holdings/cik={int(cik)}/period={period}/holdings.parquet"


def manager_header_key(cik: str, period: str) -> str:
    return f"curated/manager_holdings/cik={int(cik)}/period={period}/_manager.json"


def is_fund_key(ticker: str) -> str:
    return f"curated/fund_universe/is_fund/{ticker.upper()}.json"


def fund_header_key(cik: str, fund_ticker: str, period: str) -> str:
    return f"curated/fund_holdings/cik={int(cik)}/fund={fund_ticker.upper()}/period={period}/_fund.json"


# ---------- universe mapping ----------

def mf_map_from_payload(payload: dict) -> dict[str, dict]:
    """company_tickers_mf.json ({fields, data}) -> {TICKER: {cik, series_id, class_id}}."""
    fields = list(payload.get("fields") or [])
    out: dict[str, dict] = {}
    for row in payload.get("data") or []:
        rec = dict(zip(fields, row, strict=False))
        sym = str(rec.get("symbol") or "").upper().strip()
        if sym and rec.get("cik") is not None and sym not in out:
            out[sym] = {"cik": str(int(rec["cik"])), "series_id": rec.get("seriesId"), "class_id": rec.get("classId")}
    return out


def efts_candidates(payload: dict) -> list[tuple[str, int, str]]:
    """Rank filer CIKs in a full-text-search result by hit count: [(cik, hits, display_name)]."""
    counts: Counter[str] = Counter()
    names: dict[str, str] = {}
    for hit in (payload.get("hits") or {}).get("hits") or []:
        src = hit.get("_source") or {}
        ciks = src.get("ciks") or []
        display = src.get("display_names") or []
        for i, cik in enumerate(ciks):
            key = str(int(cik))
            counts[key] += 1
            if i < len(display):
                names.setdefault(key, str(display[i]).split("  (CIK")[0].strip())
    return [(cik, n, names.get(cik, "")) for cik, n in counts.most_common()]


def has_fund_forms(submissions: dict) -> bool:
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    return any(form in FUND_FORMS for form in recent.get("form") or [])


def resolve_by_fulltext(ticker: str, get_json) -> dict:
    """Fallback: the most-cited filer in full-text hits whose own filings are fund forms."""
    payload = get_json(EFTS_URL.format(ticker=ticker))
    if not isinstance(payload, dict):
        return {"status": "unresolved", "reason": "efts_unavailable"}
    total = len((payload.get("hits") or {}).get("hits") or [])
    for cik, hits, name in efts_candidates(payload)[:5]:
        subs = get_json(SUBMISSIONS_URL.format(cik=cik10(cik)))
        if not isinstance(subs, dict) or not has_fund_forms(subs):
            continue  # people (Form 4 filers) and operating companies drop out here
        confidence = round(hits / total, 2) if total else 0.0
        if confidence < MIN_FALLBACK_CONFIDENCE:
            return {"status": "unresolved", "reason": "ambiguous", "cik": cik, "confidence": confidence}
        return {
            "status": "resolved", "kind": "fund", "cik": cik, "series_id": None, "class_id": None,
            "name": subs.get("name") or name, "method": "efts_fulltext", "confidence": confidence,
        }
    return {"status": "unresolved", "reason": "no_fund_filer", "kind": "unknown"}


def app_identity(document: dict | None) -> dict:
    """How the app already classifies a ticker: its SEC filings job (serving/filings/<T>.json).

    status ok/partial with a CIK means the app shows an operating company under that name
    (e.g. SPCX = SPACE EXPLORATION TECHNOLOGIES CORP); no_sec_filer means no operating filer."""
    doc = document if isinstance(document, dict) else {}
    status = str(doc.get("status") or "")
    if status in ("ok", "partial") and doc.get("cik"):
        kind = "operating"
    elif status == "no_sec_filer":
        kind = "non_operating"
    else:
        kind = None
    cik = str(int(doc["cik"])) if str(doc.get("cik") or "").isdigit() else None
    return {"kind": kind, "status": status or None, "cik": cik, "name": doc.get("entity_name")}


_FUND_WORDS = {"etf", "fund", "trust", "shares", "the", "inc", "corp", "corporation", "llc", "co"}


def names_agree(left: str | None, right: str | None) -> bool:
    def words(value):
        return [w for w in norm_name(value).split() if w not in _FUND_WORDS]

    a, b = words(left), words(right)
    return bool(a) and a == b


def resolve_ticker(ticker: str, mf_map: dict, operating_map: dict, get_json, app: dict | None = None,
                   fund_name_for=None) -> dict:
    """Map one watchlist ticker. A fund is accepted only when it cannot be the app's own entity.

    - The app shows an operating SEC filer for the ticker: it stays operating unless the SEC fund's
      series name agrees with the app's stored name (ticker collisions like SPCX stay SpaceX).
    - The ticker is in both company_tickers.json and the fund file and the app has no identity yet:
      unresolved (ticker_collision), retried next run, never guessed.
    """
    sym = ticker.upper()
    app = app or {"kind": None}
    fund = mf_map.get(sym)
    operating = operating_map.get(sym)
    collision = {"fund_cik": fund["cik"], "series_id": fund.get("series_id")} if fund else None
    if app.get("kind") == "operating":
        if fund and fund_name_for is not None and names_agree(app.get("name"), fund_name_for(fund)):
            return {"status": "resolved", "kind": "fund", **fund, "method": "company_tickers_mf+app_name",
                    "confidence": 1.0}
        return {"status": "resolved", "kind": "operating", "cik": app.get("cik"), "name": app.get("name"),
                "method": "app_sec_filer", "confidence": 1.0, **({"collision": collision} if fund else {})}
    if fund and operating:
        return {"status": "unresolved", "kind": "unknown", "reason": "ticker_collision", "collision": collision,
                "operating_cik": str(int(operating["cik"]))}
    if fund:
        return {"status": "resolved", "kind": "fund", **fund, "method": "company_tickers_mf", "confidence": 1.0}
    if operating:
        return {"status": "resolved", "kind": "operating", "cik": str(int(operating["cik"])),
                "method": "company_tickers", "confidence": 1.0}
    return resolve_by_fulltext(sym, get_json)


def map_entry_fresh(entry: dict | None, now: datetime, max_age_days: int = FUND_MAP_MAX_AGE_DAYS,
                    app: dict | None = None) -> bool:
    """Cached entries expire after max_age_days, after 1 day when unresolved, and at once when the
    app's own classification of the ticker changed since the entry was resolved."""
    if app is not None and entry and (entry.get("app_kind"), entry.get("app_cik")) != (app.get("kind"), app.get("cik")):
        return False
    if entry and entry.get("status") == "unresolved":
        max_age_days = min(max_age_days, 1)
    try:
        stamp = datetime.fromisoformat(str((entry or {}).get("resolved_at")))
    except ValueError:
        return False
    return now - stamp < timedelta(days=max_age_days)


# ---------- N-PORT ----------

def nport_filings(submissions: dict) -> list[dict]:
    """NPORT-P rows from submissions.recent, newest filing first."""
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    rows = []
    for i, form in enumerate(recent.get("form") or []):
        if form not in NPORT_FORMS:
            continue
        rows.append({
            "form": form,
            "accession": recent["accessionNumber"][i],
            "filed_at": (recent.get("filingDate") or [""])[i] if i < len(recent.get("filingDate") or []) else "",
            "report_date": (recent.get("reportDate") or [""])[i] if i < len(recent.get("reportDate") or []) else "",
        })
    rows.sort(key=lambda r: (r["filed_at"], r["accession"]), reverse=True)
    return rows


def series_feed_filings(atom_text: str | None) -> list[dict]:
    """NPORT-P rows from the EDGAR per-series Atom feed, newest filing first.

    A trust files one NPORT-P per series under the same CIK, so the trust's submissions JSON cannot
    tell series apart; the series feed lists only this series' accessions."""
    if not atom_text:
        return []
    try:
        root = ET.fromstring(atom_text)
    except ET.ParseError:
        return []
    atom = "{http://www.w3.org/2005/Atom}"
    rows = []
    for entry in root.iter(f"{atom}entry"):
        content = entry.find(f"{atom}content")
        if content is None:
            continue
        fields = {child.tag.replace(atom, ""): (child.text or "").strip() for child in content}
        form = fields.get("filing-type") or ""
        if form in NPORT_FORMS and fields.get("accession-number"):
            rows.append({"form": form, "accession": fields["accession-number"],
                         "filed_at": fields.get("filing-date") or "", "report_date": ""})
    rows.sort(key=lambda r: (r["filed_at"], r["accession"]), reverse=True)
    return rows


def _text(node, path: str) -> str | None:
    found = node.find(path, NS) if node is not None else None
    value = (found.text or "").strip() if found is not None and found.text else None
    return value or None


def _num(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def parse_nport(xml_text: str) -> dict:
    """Header facts and holdings rows from an NPORT-P primary_doc.xml."""
    root = ET.fromstring(xml_text)
    gen = root.find("n:formData/n:genInfo", NS)
    fund = root.find("n:formData/n:fundInfo", NS)
    header = {
        "submission_type": _text(root, "n:headerData/n:submissionType"),
        "reg_cik": str(int(_text(gen, "n:regCik"))) if _text(gen, "n:regCik") else None,
        "reg_name": _text(gen, "n:regName"),
        "series_id": _text(gen, "n:seriesId"),
        "series_name": _text(gen, "n:seriesName"),
        "period_end": _text(gen, "n:repPdDate"),
        "fiscal_year_end": _text(gen, "n:repPdEnd"),
        "total_assets": _num(_text(fund, "n:totAssets")),
        "net_assets": _num(_text(fund, "n:netAssets")),
    }
    rows = []
    for sec in root.iterfind("n:formData/n:invstOrSecs/n:invstOrSec", NS):
        ids = sec.find("n:identifiers", NS)
        isin = ids.find("n:isin", NS) if ids is not None else None
        tick = ids.find("n:ticker", NS) if ids is not None else None
        other = ids.find("n:other", NS) if ids is not None else None
        cusip = _text(sec, "n:cusip")
        rows.append({
            "name": _text(sec, "n:name"),
            "title": _text(sec, "n:title"),
            "lei": None if _text(sec, "n:lei") in (None, "N/A") else _text(sec, "n:lei"),
            "cusip": None if cusip in (None, "000000000", "N/A") else cusip,
            "isin": isin.get("value") if isin is not None else None,
            "ticker": tick.get("value") if tick is not None else None,
            "other_id": other.get("value") if other is not None else None,
            "other_id_desc": other.get("otherDesc") if other is not None else None,
            "balance": _num(_text(sec, "n:balance")),
            "units": _text(sec, "n:units"),
            "currency": _text(sec, "n:curCd"),
            "value_usd": _num(_text(sec, "n:valUSD")),
            "pct_nav": _num(_text(sec, "n:pctVal")),
            "payoff": _text(sec, "n:payoffProfile"),
            "asset_cat": _text(sec, "n:assetCat") or _text(sec, "n:assetConditional"),
            "issuer_cat": _text(sec, "n:issuerCat"),
            "country": _text(sec, "n:invCountry"),
            "fair_val_level": _text(sec, "n:fairValLevel"),
            "restricted": _text(sec, "n:isRestrictedSec") == "Y",
        })
    rows.sort(key=lambda r: -(r["value_usd"] or 0.0))
    return {"header": header, "holdings": rows}


def latest_nport(fund: dict, submissions: dict, get_text, series_rows: list[dict] | None = None) -> dict | None:
    """Newest NPORT-P for this fund. A multi-series trust is matched on seriesId; a filer with no
    series (closed-end/interval fund) takes its newest filing. Amendments win for the same period."""
    want = fund.get("series_id")
    cik = str(int(fund["cik"]))
    candidates = series_rows or nport_filings(submissions)
    for tried, row in enumerate(candidates):
        if tried >= MAX_NPORT_DOCS_PER_FUND:
            break
        xml_text = get_text(ARCHIVE_URL.format(cik=cik, acc=row["accession"].replace("-", "")))
        if not xml_text:
            continue
        parsed = parse_nport(xml_text)
        if want and parsed["header"].get("series_id") != want:
            continue
        # Newest filed first, so an amendment filed later already wins for its period.
        return {**row, **parsed, "xml": xml_text, "period_end": parsed["header"]["period_end"] or row["report_date"]}
    return None


def holdings_frame(fund_ticker: str, filing: dict) -> pl.DataFrame:
    head = filing["header"]
    base = {
        "cik": head.get("reg_cik"), "fund_ticker": fund_ticker.upper(), "series_id": head.get("series_id"),
        "period_end": filing["period_end"], "filed_at": filing["filed_at"], "accession": filing["accession"],
    }
    return pl.DataFrame([{**base, **row} for row in filing["holdings"]], schema=HOLDINGS_SCHEMA)


# ---------- run ----------

def run_collection(
    tickers: list[str], *, fetch, pace, sleep, fund_map: dict | None, state: dict, now: datetime,
    app_documents: dict[str, dict | None] | None = None, issuer_sectors: dict | None = None,
) -> dict:
    """Resolve the universe, then fetch only funds whose latest accession is new. Pure: no lake I/O."""

    def get(url: str):
        response, _reason = fetch_sec(url, fetch, pace, sleep)
        return response

    def get_json(url: str):
        response = get(url)
        try:
            return response.json() if response is not None else None
        except ValueError:
            return None

    def get_text(url: str):
        response = get(url)
        return response.text if response is not None else None

    def fund_name_for(rec: dict) -> str | None:
        rows = series_feed_filings(get_text(SERIES_FEED_URL.format(series_id=rec["series_id"]))) \
            if rec.get("series_id") else []
        for row in rows[:1]:
            xml_text = get_text(ARCHIVE_URL.format(cik=int(rec["cik"]), acc=row["accession"].replace("-", "")))
            if xml_text:
                return parse_nport(xml_text)["header"].get("series_name")
        return None

    apps = {t: app_identity((app_documents or {}).get(t)) for t in tickers}
    entries = dict((fund_map or {}).get("funds") or {})
    stale = [t for t in tickers if not map_entry_fresh(entries.get(t), now, app=apps[t])]
    mf_map, operating = {}, {}
    if stale:
        mf_map = mf_map_from_payload(get_json(MF_URL) or {})
        operating = cik_map_from_company_tickers(get_json(OPERATING_URL) or {})
    stamp = now.isoformat(timespec="seconds")
    for ticker in stale:
        app = apps[ticker]
        resolved = resolve_ticker(ticker, mf_map, operating, get_json, app=app, fund_name_for=fund_name_for)
        entries[ticker] = {"ticker": ticker, **resolved, "app_kind": app["kind"], "app_cik": app["cik"],
                           "app_name": app["name"], "resolved_at": stamp}

    def managers_for(ticker: str, entry: dict, subs: dict) -> None:
        """Derive the fund's 13F managers from its latest N-CEN adviser list (cached in the fund map)."""
        try:
            fresh = now - datetime.fromisoformat(str(entry.get("managers_resolved_at"))) < timedelta(
                days=MANAGER_MAP_MAX_AGE_DAYS)
        except ValueError:
            fresh = False
        if fresh and "managers" in entry:
            return
        managers: list[dict] = []
        advisers: list[dict] = []
        # A trust can file several N-CENs (one per fiscal-year group); find the one listing this series.
        for filing in latest_form(subs, NCEN_FORMS)[:MAX_NCEN_DOCS]:
            acc = filing["accession"].replace("-", "")
            xml_text = get_text(ARCHIVE_URL.format(cik=int(entry["cik"]), acc=acc))
            try:
                advisers = advisers_from_ncen(xml_text, entry.get("series_id")) if xml_text else []
            except ET.ParseError:
                advisers = []
            if advisers:
                break
        try:
            for adviser in advisers:
                found = resolve_manager(adviser, get_json, SUBMISSIONS_URL)
                if found and all(m["cik"] != found["cik"] for m in managers):
                    managers.append(found)
        except LookupUnavailable:
            entry["managers_status"] = "retry"
            return
        entry["advisers"] = [a["name"] for a in advisers]
        entry["managers"] = managers
        entry["managers_status"] = "resolved" if managers else ("no_13f_filer" if advisers else "no_adviser")
        entry["managers_resolved_at"] = stamp
        entries[ticker] = entry

    seen = dict(state.get("latest_accession") or {})
    seen_13f = dict(state.get("latest_13f") or {})
    results = []
    for ticker in tickers:
        entry = entries.get(ticker) or {}
        if entry.get("kind") != "fund" or entry.get("status") != "resolved":
            continue
        subs = get_json(SUBMISSIONS_URL.format(cik=cik10(entry["cik"])))
        if not isinstance(subs, dict):
            results.append({"ticker": ticker, "status": "failed", "reason": "submissions_unavailable"})
            continue
        filings = nport_filings(subs)
        if not filings:
            results.append({"ticker": ticker, "status": "ok", "reason": "no_nport"})
            continue
        series_rows = None
        if entry.get("series_id"):
            series_rows = series_feed_filings(get_text(SERIES_FEED_URL.format(series_id=entry["series_id"])))
        filing = latest_nport(entry, subs, get_text, series_rows)
        if filing is None:
            results.append({"ticker": ticker, "status": "failed", "reason": "series_not_found"})
            continue
        managers_for(ticker, entry, subs)
        new = seen.get(ticker) != filing["accession"]
        seen[ticker] = filing["accession"]
        results.append({"ticker": ticker, "status": "ok", "new": new, "cik": entry["cik"], "filing": filing})
    manager_ciks = list(dict.fromkeys(
        m["cik"] for r in results if r.get("filing") for m in (entries.get(r["ticker"]) or {}).get("managers") or []
    ))
    managers = []
    for cik in manager_ciks:
        subs = get_json(SUBMISSIONS_URL.format(cik=cik10(cik)))
        report = latest_13f(cik, subs, get_json, get_text) if isinstance(subs, dict) else None
        if report is None:
            managers.append({"cik": cik, "status": "failed", "reason": "13f_unavailable"})
            continue
        key = "+".join([report["accession"], *report["amendments"]])
        new = seen_13f.get(cik) != key
        seen_13f[cik] = key
        # The previous quarter, for the quarter-over-quarter comparison; fetched once per new quarter.
        previous = None
        prev_key = f"{cik}:prev"
        if new or not seen_13f.get(prev_key):
            previous = latest_13f(cik, subs, get_json, get_text, before=report["period_end"])
            if previous is not None:
                seen_13f[prev_key] = previous["accession"]
        managers.append({"cik": cik, "status": "ok", "new": new, "name": (subs or {}).get("name"), "report": report,
                         "previous": previous})

    # Sector/industry for every 13F issuer (current and previous quarter), from SEC data only.
    issuer_rows = [row for m in managers for rep in (m.get("report"), m.get("previous")) if rep
                   for row in rep["holdings"] if not row.get("is_option")]
    cusip_tickers = {h["cusip"]: h["ticker"] for r in results if r.get("filing")
                     for h in r["filing"]["holdings"] if h.get("cusip") and h.get("ticker")}
    sectors = dict(issuer_sectors or {})
    if issuer_rows:
        if not operating:
            operating = cik_map_from_company_tickers(get_json(OPERATING_URL) or {})
        sectors = resolve_issuers(issuer_rows, cusip_tickers=cusip_tickers, operating_map=operating, cache=sectors,
                                  get_json=get_json, submissions_url=SUBMISSIONS_URL, now=now)
    return {
        "fund_map": {"updated_at": stamp, "funds": entries},
        "state": {"latest_accession": seen, "latest_13f": seen_13f, "updated_at": stamp},
        "results": results,
        "managers": managers,
        "issuer_sectors": sectors,
    }


def is_fund_decision(entry: dict | None, managers_with_13f: set[str]) -> bool | None:
    """The one-time is_fund stamp: True when 13F data is present for the ticker (its fund's derived
    manager has a parsed 13F), False when the ticker is definitely not covered by 13F data.
    None means 'not definitive yet' (unresolved mapping or collision, EDGAR search outage, 13F fetch
    failed), so nothing is stamped and the first definitive collection decides."""
    entry = entry or {}
    if entry.get("status") != "resolved":
        return None
    if entry.get("kind") == "operating":
        return False
    if entry.get("kind") != "fund":
        return None
    status = entry.get("managers_status")
    ciks = {str(m.get("cik")) for m in entry.get("managers") or []}
    if status == "resolved":
        if ciks & managers_with_13f:
            return True
        return None  # manager known but its 13F did not load this run
    if status in ("no_13f_filer", "no_adviser"):
        return False
    return None


def is_fund_stamps(collected: dict, now: datetime) -> list[dict]:
    with_13f = {str(m["cik"]) for m in collected.get("managers") or [] if m.get("report")}
    out = []
    for ticker, entry in sorted((collected.get("fund_map") or {}).get("funds", {}).items()):
        decision = is_fund_decision(entry, with_13f)
        if decision is None:
            continue
        out.append({
            "ticker": ticker, "is_fund": decision, "stamped_at": now.isoformat(timespec="seconds"),
            "basis": {"kind": entry.get("kind"), "method": entry.get("method"),
                      "managers_status": entry.get("managers_status"),
                      "manager_ciks": sorted(str(m.get("cik")) for m in entry.get("managers") or [])},
        })
    return out


def manager_frame(cik: str, report: dict) -> pl.DataFrame:
    base = {"manager_cik": str(int(cik)), "period_end": report["period_end"], "filed_at": report["filed_at"],
            "accession": report["accession"]}
    return pl.DataFrame([{**base, **row} for row in report["holdings"]], schema=MANAGER_SCHEMA)


def fund_summary(result: dict) -> dict:
    filing = result.get("filing") or {}
    head = filing.get("header") or {}
    return {
        "ticker": result["ticker"], "status": result["status"], "reason": result.get("reason"),
        "accession": filing.get("accession"), "period_end": filing.get("period_end"),
        "filed_at": filing.get("filed_at"),
        "series_name": head.get("series_name"), "net_assets": head.get("net_assets"),
        "holdings": len(filing.get("holdings") or []),
        "top": [
            {k: h[k] for k in ("name", "ticker", "cusip", "value_usd", "pct_nav")}
            for h in (filing.get("holdings") or [])[:10]
        ],
    }


def publish(collected: dict, *, write_text, write_json, write_parquet, write_json_once=None,
            now: datetime | None = None) -> list[str]:
    """Write the run's outputs. Returns the tickers whose is_fund flag was stamped this run."""
    for result in collected["results"]:
        filing = result.get("filing")
        if not filing or not result.get("new"):
            continue
        cik, ticker, period = result["cik"], result["ticker"], filing["period_end"]
        write_text(filing["xml"], raw_nport_key(cik, filing["accession"]))
        write_parquet(holdings_frame(ticker, filing), holdings_key(cik, ticker, period))
        write_json({**filing["header"], "accession": filing["accession"], "filed_at": filing["filed_at"],
                    "fund_ticker": ticker}, fund_header_key(cik, ticker, period))
    for manager in collected.get("managers") or []:
        writers = (write_text, write_json, write_parquet)
        if manager.get("previous"):
            _publish_13f(manager["cik"], manager.get("name"), manager["previous"], *writers)
        if manager.get("report") and manager.get("new"):
            _publish_13f(manager["cik"], manager.get("name"), manager["report"], *writers)
    if collected.get("issuer_sectors"):
        write_json(collected["issuer_sectors"], ISSUER_SECTORS_KEY)
    write_json(collected["fund_map"], FUND_MAP_KEY)
    write_json(collected["state"], STATE_KEY)
    stamped: list[str] = []
    if write_json_once is not None:
        # Write-once: an existing stamp is never replaced, whatever later quarters or remaps say.
        for stamp in is_fund_stamps(collected, now or datetime.now(UTC)):
            if write_json_once(stamp, is_fund_key(stamp["ticker"])):
                stamped.append(stamp["ticker"])
    return stamped


def _publish_13f(cik, name, report, write_text, write_json, write_parquet) -> None:
    period = report["period_end"]
    write_text(report["cover_xml"], raw_13f_key(cik, report["accession"], "primary_doc.xml"))
    write_text(report["table_xml"], raw_13f_key(cik, report["accession"], report["table_name"]))
    write_parquet(manager_frame(cik, report), manager_holdings_key(cik, period))
    write_json({"manager_cik": str(int(cik)), "name": name, "period_end": period,
                "filed_at": report["filed_at"], "accession": report["accession"],
                "amendments": report["amendments"], "entry_total": report.get("entry_total"),
                "value_total_usd": report.get("value_total"), "form": report["form"]},
               manager_header_key(cik, period))


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    import os

    from lake import read_json, s3, write_json, write_json_once, write_parquet
    from observability import job_handler
    from regulatory_feeds import assert_prod_user_agent
    from universe import user_ticker_union, watchlist_universe

    @job_handler(JOB_ID, lease_key=LEASE_KEY, skip_when_leased=True)
    def run(event, context):
        if os.getenv("INVDASH_ENV") == "prod":
            assert_prod_user_agent(os.getenv("SEC_USER_AGENT"))
        planned = target_tickers([], event)
        if planned["reason"] != "ticker-added":
            planned = {"tickers": list(watchlist_universe(user_ticker_union()).get("tickers") or [])}
        from http_client import get_client

        def write_text(body: str, key: str) -> None:
            s3().put_object(
                Bucket=os.environ["LAKE_BUCKET"], Key=key, Body=body.encode(), ContentType="application/xml"
            )

        pace = Pace()
        headers = {"User-Agent": crawler_user_agent(), "Accept": "application/json,application/xml,*/*"}
        with get_client(headers=headers) as http:
            collected = run_collection(
                planned["tickers"], fetch=lambda url: http.get(url), pace=pace, sleep=pace.sleep,
                fund_map=read_json(FUND_MAP_KEY), state=read_json(STATE_KEY) or {}, now=datetime.now(UTC),
                app_documents={t: read_json(f"serving/filings/{t}.json") for t in planned["tickers"]},
                issuer_sectors=read_json(ISSUER_SECTORS_KEY) or {},
            )
        stamped = publish(collected, write_text=write_text, write_json=write_json, write_parquet=write_parquet,
                          write_json_once=write_json_once, now=datetime.now(UTC))
        return {
            "status": "ok",
            "funds": [{k: v for k, v in fund_summary(r).items() if k != "top"} for r in collected["results"]],
            "managers": [{k: m.get(k) for k in ("cik", "status", "reason", "new")} for m in collected["managers"]],
            "is_fund_stamped": stamped,
        }

    return run(event, context)
