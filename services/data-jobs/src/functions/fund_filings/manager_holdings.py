"""13F manager holdings for fund advisers (#223).

Managers are never configured. For each resolved fund, the adviser named in its latest N-CEN is
looked up in EDGAR full-text search over 13F-HR filings; a candidate is kept only when its EDGAR name
matches the adviser name and its own submissions contain 13F-HR (an empty same-name CIK is rejected).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

NCEN_NS = "{http://www.sec.gov/edgar/ncen}"
INFO_NS = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}"
F13_NS = "{http://www.sec.gov/edgar/thirteenffiler}"
NCEN_FORMS = ("N-CEN", "N-CEN/A")
F13_FORMS = ("13F-HR", "13F-HR/A")
EFTS_13F_URL = "https://efts.sec.gov/LATEST/search-index?q=%22{name}%22&forms=13F-HR"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
FILING_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"
MANAGER_MAP_MAX_AGE_DAYS = 90  # N-CEN is annual

_LEGAL = re.compile(r"\b(llc|l\.l\.c|lp|l\.p|inc|incorporated|corp|corporation|co|ltd|limited|plc)\b\.?")


def norm_name(name: str | None) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", _LEGAL.sub(" ", str(name or "").lower()))
    return " ".join(text.split())


def latest_form(submissions: dict, forms: tuple[str, ...]) -> list[dict]:
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    out = []
    for i, form in enumerate(recent.get("form") or []):
        if form in forms:
            out.append({"form": form, "accession": recent["accessionNumber"][i],
                        "filed_at": (recent.get("filingDate") or [""] * (i + 1))[i],
                        "report_date": (recent.get("reportDate") or [""] * (i + 1))[i]})
    out.sort(key=lambda r: (r["filed_at"], r["accession"]), reverse=True)
    return out


def advisers_from_ncen(xml_text: str, series_id: str | None) -> list[dict]:
    """Advisers (not sub-advisers) for this series; a filer with one fund block and no seriesId uses it."""
    root = ET.fromstring(xml_text)
    blocks = list(root.iter(f"{NCEN_NS}managementInvestmentQuestion"))
    chosen = [b for b in blocks if series_id and b.findtext(f"{NCEN_NS}mgmtInvSeriesId") == series_id]
    if not chosen and not series_id:
        chosen = blocks[:1]
    if not chosen and not blocks:
        chosen = [root]
    if not chosen:
        return []  # closed-end N-CEN layouts keep advisers outside a per-series block
    out = []
    for block in chosen:
        for adv in block.iter(f"{NCEN_NS}investmentAdviser"):
            name = (adv.findtext(f"{NCEN_NS}investmentAdviserName") or "").strip()
            if name and all(norm_name(o["name"]) != norm_name(name) for o in out):
                out.append({
                    "name": name,
                    "file_no": adv.findtext(f"{NCEN_NS}investmentAdviserFileNo"),
                    "crd": adv.findtext(f"{NCEN_NS}investmentAdviserCrdNo"),
                    "lei": adv.findtext(f"{NCEN_NS}investmentAdviserLei"),
                })
    return out


class LookupUnavailable(RuntimeError):
    """EDGAR search did not answer; retry next run instead of caching 'no manager'."""


def resolve_manager(adviser: dict, get_json, submissions_url: str) -> dict | None:
    payload = get_json(EFTS_13F_URL.format(name=quote(adviser["name"])))
    if not isinstance(payload, dict) or "hits" not in payload:
        raise LookupUnavailable(adviser["name"])
    target = norm_name(adviser["name"])
    counts: dict[str, int] = {}
    for hit in (payload.get("hits") or {}).get("hits") or []:
        src = hit.get("_source") or {}
        for i, cik in enumerate(src.get("ciks") or []):
            names = src.get("display_names") or []
            label = str(names[i]).split("  (CIK")[0] if i < len(names) else ""
            if norm_name(label) == target:
                key = str(int(cik))
                counts[key] = counts.get(key, 0) + 1
    for cik, _n in sorted(counts.items(), key=lambda kv: -kv[1]):
        subs = get_json(submissions_url.format(cik=str(int(cik)).zfill(10)))
        if isinstance(subs, dict) and latest_form(subs, ("13F-HR",)):
            return {"cik": cik, "name": subs.get("name") or adviser["name"], "method": "n-cen_adviser",
                    "adviser_crd": adviser.get("crd"), "adviser_file_no": adviser.get("file_no")}
    return None


def _iso_period(value: str | None) -> str | None:
    match = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", (value or "").strip())
    return f"{match.group(3)}-{match.group(1)}-{match.group(2)}" if match else (value or None)


def parse_13f_cover(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)

    def find(tag):
        node = next(root.iter(f"{F13_NS}{tag}"), None)
        return (node.text or "").strip() if node is not None and node.text else None

    return {
        "period_end": _iso_period(find("periodOfReport") or find("reportCalendarOrQuarter")),
        "is_amendment": (find("isAmendment") or "").lower() == "true",
        "amendment_type": find("amendmentType"),
        "manager_name": next((n.text for n in root.iter(f"{F13_NS}name") if n.text), None),
        "entry_total": int(find("tableEntryTotal") or 0) or None,
        "value_total": float(find("tableValueTotal") or 0) or None,
    }


def parse_13f_table(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    ns = INFO_NS if root.tag.startswith(INFO_NS) else ""
    rows = []
    for row in root.iter(f"{ns}infoTable"):
        def get(path, node=row):
            text = node.findtext("/".join(f"{ns}{p}" for p in path.split("/")))
            return text.strip() if text else None

        put_call = get("putCall")
        rows.append({
            "name": get("nameOfIssuer"), "title": get("titleOfClass"), "cusip": get("cusip"),
            "value_usd": float(get("value") or 0), "shares": float(get("shrsOrPrnAmt/sshPrnamt") or 0),
            "share_type": get("shrsOrPrnAmt/sshPrnamtType"), "put_call": put_call, "is_option": bool(put_call),
            "discretion": get("investmentDiscretion"),
        })
    return rows


def with_portfolio_pct(rows: list[dict]) -> list[dict]:
    """% of the manager's long, non-option book; option rows stay separate with no %."""
    total = sum(r["value_usd"] for r in rows if not r["is_option"])
    out = [{**r, "pct_portfolio": (r["value_usd"] / total * 100 if total and not r["is_option"] else None)}
           for r in rows]
    out.sort(key=lambda r: (r["is_option"], -r["value_usd"]))
    return out


def info_table_name(index_json: dict | None) -> str | None:
    items = ((index_json or {}).get("directory") or {}).get("item") or []
    xmls = [i.get("name") for i in items if str(i.get("name", "")).lower().endswith(".xml")]
    others = [n for n in xmls if n != "primary_doc.xml"]
    return others[0] if others else None


def latest_13f(cik: str, submissions: dict, get_json, get_text, max_docs: int = 6,
               before: str | None = None) -> dict | None:
    """Newest reporting period (or the newest before `before`, for the quarter-over-quarter
    comparison). Restatements replace it; 'NEW HOLDINGS' amendments add rows."""
    filings = latest_form(submissions, F13_FORMS)
    if before:
        filings = [f for f in filings if f["report_date"] and f["report_date"] < before]
    if not filings:
        return None
    period = None
    base = None
    adds: list[tuple[dict, list[dict]]] = []
    for row in filings[:max_docs]:
        acc = row["accession"].replace("-", "")
        cover_xml = get_text(FILING_DOC_URL.format(cik=int(cik), acc=acc, name="primary_doc.xml"))
        name = info_table_name(get_json(FILING_INDEX_URL.format(cik=int(cik), acc=acc)))
        table_xml = get_text(FILING_DOC_URL.format(cik=int(cik), acc=acc, name=name)) if name else None
        if not cover_xml or not table_xml:
            continue
        cover = parse_13f_cover(cover_xml)
        period = period or cover["period_end"] or row["report_date"]
        if (cover["period_end"] or row["report_date"]) != period:
            break
        doc = {**row, **cover, "cover_xml": cover_xml, "table_xml": table_xml, "table_name": name}
        rows = parse_13f_table(table_xml)
        is_restatement = cover["is_amendment"] and (cover["amendment_type"] or "").upper() == "RESTATEMENT"
        if not cover["is_amendment"] or is_restatement:
            base = (doc, rows)
            break  # newest original or restatement for the period; amendments seen before it add to it
        adds.append((doc, rows))
    if base is None:
        return None
    doc, rows = base
    for _extra, extra_rows in adds:
        rows = rows + extra_rows
    return {**doc, "period_end": period, "amendments": [a["accession"] for a, _r in adds],
            "holdings": with_portfolio_pct(rows)}
