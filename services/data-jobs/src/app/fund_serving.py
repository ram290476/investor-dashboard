"""serving/funds/<T>.json and serving/managers/<cik>.json (#223): the Fund holdings panel contract.

The dashboard build merges the fund sources by precedence. Holdings: SEC N-PORT (the ARK daily
source is designed but disabled), else Yahoo top holdings (listed names only). Net assets: Yahoo
totalAssets when its profile is recent, else N-PORT. Every block names its source and as-of date.
"""

from __future__ import annotations

from datetime import datetime, timedelta

SCHEMA_VERSION = 1
FUND_MAP_KEY = "curated/fund_universe/fund_map.json"
PROFILE_NET_ASSETS_MAX_AGE_DAYS = 10
UNNAMED = "Unnamed holding"
_BLANK_NAMES = {"", "N/A", "NA", "NONE", "NULL", "-", "--", "UNKNOWN"}


def fund_document_key(ticker: str) -> str:
    return f"serving/funds/{ticker.upper()}.json"


def manager_document_key(cik: str) -> str:
    return f"serving/managers/{int(cik)}.json"


def _blank(value) -> bool:
    return value is None or str(value).strip().upper() in _BLANK_NAMES


def display_name(row: dict) -> str:
    """Filed name, else the issuer, else the security title, else 'Unnamed holding'."""
    for field in ("name", "issuer", "issuer_name", "title"):
        value = row.get(field)
        if not _blank(value):
            return str(value).strip()
    return UNNAMED


def _holding_key(row: dict) -> str:
    for field in ("cusip", "isin", "other_id", "ticker"):
        if row.get(field):
            return f"{field}:{row[field]}"
    return f"name:{display_name(row).lower()}"


def _num(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def nport_holdings(rows: list[dict], previous: list[dict] | None = None) -> list[dict]:
    prev = {_holding_key(r): _num(r.get("pct_nav")) for r in previous or []}
    out = []
    for row in rows:
        name = display_name(row)
        out.append({
            "ticker": row.get("ticker"), "name": name,
            "name_filed": row.get("name") if name != row.get("name") else None,
            "title": row.get("title"), "cusip": row.get("cusip"), "isin": row.get("isin"),
            "other_id": row.get("other_id"), "shares": _num(row.get("balance")), "units": row.get("units"),
            "value_usd": _num(row.get("value_usd")), "pct_nav": _num(row.get("pct_nav")),
            "asset_cat": row.get("asset_cat"), "payoff": row.get("payoff"),
            "fair_val_level": row.get("fair_val_level"), "restricted": bool(row.get("restricted")),
            "is_private": not row.get("cusip") and not row.get("isin") and not row.get("ticker"),
            "prev_pct_nav": prev.get(_holding_key(row)) if previous is not None else None,
        })
    out.sort(key=lambda r: -(r["pct_nav"] if r["pct_nav"] is not None else -1e9))
    return out


def _recent(stamp, now: datetime, days: int) -> bool:
    try:
        return now - datetime.fromisoformat(str(stamp)) < timedelta(days=days)
    except ValueError:
        return False


def build_fund_document(
    ticker: str, *, entry: dict, header: dict | None, rows: list[dict], previous_rows: list[dict] | None,
    profile: dict | None, managers: list[dict], generated_at: str, now: datetime,
) -> dict:
    header = header or {}
    holdings = nport_holdings(rows, previous_rows) if rows else []
    source = "sec_nport" if holdings else None
    as_of = header.get("period_end") if holdings else None
    note = None
    if not holdings and profile and profile.get("top_holdings"):
        holdings = [{"ticker": h.get("symbol"), "name": display_name({"name": h.get("name")}),
                     "pct_nav": (_num(h.get("pct")) or 0) * 100, "value_usd": None, "cusip": None,
                     "is_private": False, "prev_pct_nav": None} for h in profile["top_holdings"]]
        source, as_of = "yahoo_top_holdings", str(profile.get("fetched_at") or "")[:10] or None
        note = "SEC holdings not published yet; showing Yahoo Finance top holdings (listed names only)"
    net_assets, net_source = _num(header.get("net_assets")), "sec_nport" if header.get("net_assets") else None
    if profile and profile.get("total_assets") and _recent(profile.get("fetched_at"), now,
                                                           PROFILE_NET_ASSETS_MAX_AGE_DAYS):
        net_assets, net_source = _num(profile["total_assets"]), "yahoo"
    status = "ok" if holdings else ("unresolved" if entry.get("status") != "resolved" else "not_published")
    cik = entry.get("cik")
    accession = header.get("accession")
    return {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker.upper(),
        "status": status,
        "fund_name": header.get("series_name") or entry.get("name"),
        "series_id": header.get("series_id") or entry.get("series_id"),
        "filer_cik": cik,
        "mapping_method": entry.get("method"),
        "holdings_source": source,
        "holdings_as_of": as_of,
        "holdings_note": note,
        "form": header.get("submission_type") if holdings and source == "sec_nport" else None,
        "accession": accession if source == "sec_nport" else None,
        "filing_url": (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
                       if cik and accession and source == "sec_nport" else None),
        "period_end": header.get("period_end"),
        "filed_at": header.get("filed_at"),
        "net_assets_usd": net_assets,
        "net_assets_source": net_source,
        "holdings_count": len(holdings),
        "profile": ({k: profile.get(k) for k in ("source", "fetched_at", "expense_ratio", "category", "family",
                                                 "yield", "ytd_return", "turnover", "summary", "sector_weights")}
                    | {"total_assets_usd": profile.get("total_assets")}) if profile else None,
        "managers": managers,
        "generated_at": generated_at,
        "holdings": holdings,
    }


def manager_rows(rows: list[dict]) -> list[dict]:
    out = [{
        "name": display_name({"name": r.get("name"), "title": r.get("title")}), "title": r.get("title"),
        "cusip": r.get("cusip"), "value_usd": _num(r.get("value_usd")), "shares": _num(r.get("shares")),
        "share_type": r.get("share_type"), "put_call": r.get("put_call"), "is_option": bool(r.get("is_option")),
        "pct_portfolio": _num(r.get("pct_portfolio")),
    } for r in rows]
    out.sort(key=lambda r: (r["is_option"], -(r["value_usd"] or 0)))
    return out


def manager_summary(header: dict, rows: list[dict], fund_rows: list[dict]) -> dict:
    """Small block for the fund document: the adviser's latest 13F and its overlap with this fund."""
    fund_cusips = {r.get("cusip") for r in fund_rows if r.get("cusip")}
    book = [r for r in rows if not r.get("is_option")]
    return {
        "cik": header.get("manager_cik"), "name": header.get("name"), "source": "sec_13f",
        "period_end": header.get("period_end"), "filed_at": header.get("filed_at"),
        "positions": len(book), "option_rows": len(rows) - len(book),
        "value_total_usd": sum(_num(r.get("value_usd")) or 0 for r in book),
        "overlap_positions": sum(1 for r in book if r.get("cusip") in fund_cusips),
    }


ISSUER_SECTORS_KEY = "curated/fund_universe/issuer_sectors.json"
OTHER_SECTOR = "Other"
MANAGER_TOP_N = 10
SUMMARY_13F_FIELDS = ("period_end", "previous_period_end", "positions", "value_total_usd", "top", "sectors",
                      "sector_source", "changes")
CHANGES_TOP_N = 10


def _positions(rows: list[dict]) -> dict[str, dict]:
    """Long, non-option 13F rows summed per CUSIP (a manager can report one issuer on several rows)."""
    out: dict[str, dict] = {}
    for row in rows:
        if row.get("is_option") or not row.get("cusip"):
            continue
        cur = out.setdefault(row["cusip"], {"cusip": row["cusip"], "name": display_name(
            {"name": row.get("name"), "title": row.get("title")}), "value_usd": 0.0, "shares": 0.0})
        cur["value_usd"] += _num(row.get("value_usd")) or 0.0
        cur["shares"] += _num(row.get("shares")) or 0.0
    return out


def quarter_changes(current: list[dict], previous: list[dict] | None, tickers: dict[str, str]) -> dict | None:
    """New buys, full sells, and the biggest share-count increases and decreases (by dollar size)."""
    if previous is None:
        return None
    now, before = _positions(current), _positions(previous)

    def item(cusip, cur, old):
        price = (cur["value_usd"] / cur["shares"]) if cur and cur["shares"] else (
            (old["value_usd"] / old["shares"]) if old and old["shares"] else 0.0)
        shares_now = cur["shares"] if cur else 0.0
        shares_old = old["shares"] if old else 0.0
        return {
            "cusip": cusip, "ticker": tickers.get(cusip), "name": (cur or old)["name"],
            "shares": shares_now, "shares_prev": shares_old,
            "shares_change_pct": ((shares_now - shares_old) / shares_old * 100) if shares_old else None,
            "value_usd": cur["value_usd"] if cur else 0.0, "value_prev_usd": old["value_usd"] if old else 0.0,
            "trade_value_usd": (shares_now - shares_old) * price,
        }

    new_buys = [item(c, now[c], None) for c in now if c not in before]
    sells = [item(c, None, before[c]) for c in before if c not in now]
    held = [item(c, now[c], before[c]) for c in now if c in before and now[c]["shares"] != before[c]["shares"]]
    increases = sorted((r for r in held if r["trade_value_usd"] > 0), key=lambda r: -r["trade_value_usd"])
    decreases = sorted((r for r in held if r["trade_value_usd"] < 0), key=lambda r: r["trade_value_usd"])
    return {
        "new_buys": sorted(new_buys, key=lambda r: -r["value_usd"]),
        "full_sells": sorted(sells, key=lambda r: -r["value_prev_usd"]),
        "increases": increases[:CHANGES_TOP_N],
        "decreases": decreases[:CHANGES_TOP_N],
        "counts": {"new_buys": len(new_buys), "full_sells": len(sells), "increased": len(increases),
                   "decreased": len(decreases), "unchanged": sum(1 for c in now if c in before) - len(held)},
    }


def sector_breakdown(rows: list[dict]) -> list[dict]:
    book = [r for r in rows if not r["is_option"]]
    total = sum(r["value_usd"] or 0 for r in book) or 0.0
    groups: dict[str, dict] = {}
    for r in book:
        g = groups.setdefault(r.get("sector") or OTHER_SECTOR, {"sector": r.get("sector") or OTHER_SECTOR,
                                                                "value_usd": 0.0, "positions": 0})
        g["value_usd"] += r["value_usd"] or 0.0
        g["positions"] += 1
    out = [{**g, "pct_portfolio": g["value_usd"] / total * 100 if total else None} for g in groups.values()]
    # "Other" always last so the known sectors read first.
    return sorted(out, key=lambda g: (g["sector"] == OTHER_SECTOR, -g["value_usd"]))


def build_manager_document(header: dict, rows: list[dict], cusip_tickers: dict[str, str], generated_at: str,
                           previous_rows: list[dict] | None = None, previous_header: dict | None = None,
                           sectors: dict | None = None) -> dict:
    """13F has no tickers or sectors: tickers come from N-PORT CUSIPs and the SEC-derived issuer map,
    sectors from the issuer map (unknown -> 'Other'). Adds totals, top names, sector weights and the
    quarter-over-quarter diff when the previous quarter is in the lake."""
    sectors = sectors or {}
    tickers = {c: t for c, t in cusip_tickers.items() if t}
    for cusip, info in sectors.items():
        if info.get("ticker"):
            tickers.setdefault(cusip, info["ticker"])
    out = manager_rows(rows)
    for row in out:
        info = sectors.get(row["cusip"] or "") or {}
        row["ticker"] = tickers.get(row["cusip"] or "")
        row["sector"] = info.get("sector") or OTHER_SECTOR
        row["industry"] = info.get("industry")
    book = [r for r in out if not r["is_option"]]
    changes = quarter_changes(rows, previous_rows, tickers)
    return {"schema_version": SCHEMA_VERSION, "source": "sec_13f", **{k: header.get(k) for k in (
        "manager_cik", "name", "period_end", "filed_at", "accession", "amendments", "form")},
        "positions": len(book), "option_rows": len(out) - len(book),
        "value_total_usd": sum(r["value_usd"] or 0 for r in book),
        "top": sorted(book, key=lambda r: -(r["value_usd"] or 0))[:MANAGER_TOP_N],
        "sectors": sector_breakdown(out),
        "sector_source": "SEC filer office (ownerOrg) via issuer CIK; unmatched issuers are 'Other'",
        "previous_period_end": (previous_header or {}).get("period_end") if previous_rows is not None else None,
        "changes": changes,
        "generated_at": generated_at, "holdings": out}


def latest_period(rows: list[dict]) -> tuple[str | None, str | None]:
    periods = sorted({str(r.get("period_end")) for r in rows if r.get("period_end")}, reverse=True)
    return (periods[0] if periods else None, periods[1] if len(periods) > 1 else None)


def build_fund_documents(tickers: list[str], *, read_json, read_rows, now: datetime, generated_at: str) -> dict:
    """Pure merge over injected readers. Returns {'funds': {T: doc}, 'managers': {cik: doc}, 'kinds': {T: kind}}."""
    fund_map = (read_json(FUND_MAP_KEY) or {}).get("funds") or {}
    funds: dict[str, dict] = {}
    managers: dict[str, dict] = {}
    manager_cache: dict[str, tuple[dict, list[dict]]] = {}
    prior_cache: dict[str, tuple[dict | None, list[dict] | None]] = {}
    kinds: dict[str, str] = {}
    cusip_tickers: dict[str, str] = {}
    flags: dict[str, dict] = {}
    for ticker in tickers:
        entry = fund_map.get(ticker) or {}
        stamp = read_json(f"curated/fund_universe/is_fund/{ticker}.json")
        if isinstance(stamp, dict) and isinstance(stamp.get("is_fund"), bool):
            flags[ticker] = {"is_fund": stamp["is_fund"], "conflict": classification_conflict(stamp["is_fund"], entry)}
        if entry.get("kind") != "fund":
            continue
        kinds[ticker] = "fund"
        rows: list[dict] = []
        previous: list[dict] | None = None
        header: dict | None = None
        cik = entry.get("cik")
        if cik and entry.get("status") == "resolved":
            all_rows = read_rows(f"curated/fund_holdings/cik={int(cik)}/fund={ticker}/")
            latest, prior = latest_period(all_rows)
            if latest:
                rows = [r for r in all_rows if str(r.get("period_end")) == latest]
                previous = [r for r in all_rows if str(r.get("period_end")) == prior] if prior else None
                header = read_json(f"curated/fund_holdings/cik={int(cik)}/fund={ticker}/period={latest}/_fund.json")
        for row in rows:
            if row.get("cusip") and row.get("ticker"):
                cusip_tickers.setdefault(row["cusip"], row["ticker"])
        profile = read_json(f"curated/fund_profile/{ticker}.json")
        blocks = []
        for manager in entry.get("managers") or []:
            mcik = str(int(manager["cik"]))
            if mcik not in manager_cache:
                mrows = read_rows(f"curated/manager_holdings/cik={mcik}/")
                period, prior = latest_period(mrows)
                mheader = None
                if period:
                    mheader = read_json(f"curated/manager_holdings/cik={mcik}/period={period}/_manager.json")
                manager_cache[mcik] = (mheader or {}, [r for r in mrows if str(r.get("period_end")) == period])
                prior_cache[mcik] = ({"period_end": prior}, [r for r in mrows if str(r.get("period_end")) == prior]) \
                    if prior else (None, None)
            mheader, mrows = manager_cache[mcik]
            if mrows:
                blocks.append(manager_summary(mheader | {"manager_cik": mcik}, mrows, rows))
        funds[ticker] = build_fund_document(ticker, entry=entry, header=header, rows=rows, previous_rows=previous,
                                            profile=profile if isinstance(profile, dict) else None,
                                            managers=blocks, generated_at=generated_at, now=now)
    sectors = read_json(ISSUER_SECTORS_KEY) if manager_cache else None
    for mcik, (mheader, mrows) in manager_cache.items():
        if mrows:
            prev_header, prev_rows = prior_cache.get(mcik, (None, None))
            managers[mcik] = build_manager_document(
                mheader | {"manager_cik": mcik}, mrows, cusip_tickers, generated_at,
                previous_rows=prev_rows, previous_header=prev_header,
                sectors=sectors if isinstance(sectors, dict) else None)
    # The fund page shows the adviser's 13F summary; embed it (no full holdings list) in each fund block.
    for doc in funds.values():
        for block in doc.get("managers") or []:
            full = managers.get(str(block.get("cik")))
            if full:
                block["summary_13f"] = {k: full.get(k) for k in SUMMARY_13F_FIELDS}
    for ticker, flag in flags.items():
        if ticker in funds:
            funds[ticker]["is_fund"] = flag["is_fund"]
            funds[ticker]["classification_conflict"] = flag["conflict"]
    return {"funds": funds, "managers": managers, "kinds": kinds, "flags": flags}


def classification_conflict(is_fund: bool, entry: dict) -> str | None:
    """The write-once is_fund stamp never changes; the live fund map can. Report disagreements
    instead of overriding either side."""
    kind = (entry or {}).get("kind")
    if is_fund and kind == "operating":
        return "is_fund_stamped_true_but_now_operating"
    if not is_fund and kind == "fund" and (entry or {}).get("status") == "resolved":
        return "is_fund_stamped_false_but_nport_fund"
    return None


def stock_flag_fields(flag: dict) -> dict:
    return {"is_fund": flag["is_fund"], "fund_classification_conflict": flag.get("conflict")}


def stock_fund_fields(document: dict) -> dict:
    """Small block for serving/dashboard.json so the page can pick the panel state without a second fetch."""
    return {"instrument_kind": "fund", "fund": {k: document.get(k) for k in (
        "status", "holdings_source", "holdings_as_of", "period_end", "filed_at", "net_assets_usd", "holdings_count")}}
