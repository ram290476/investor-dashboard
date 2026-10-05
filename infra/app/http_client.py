"""Outbound HTTP for collectors, limited to an allowlist of provider hosts.

Compensating control for the accepted risk that job Lambdas run outside a VPC
(SC-7(5), AC-4): a request to any host not listed here is refused before it
leaves the function, and the refusal is logged. Add a host here and in the
architecture doc's source catalog together.

    from http_client import get_client
    with get_client() as http:
        r = http.get("https://data.alpaca.markets/v2/stocks/bars", params=...)
"""

from __future__ import annotations

import os

import httpx

# Hosts from the data-source catalog (DS-xx) plus the 2026-10 additions.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        # Market data
        "data.alpaca.markets",
        "paper-api.alpaca.markets",  # DS-02, DS-82, options (DS-92)
        "api.massive.com",
        "api.polygon.io",  # DS-01, DS-42
        "query1.finance.yahoo.com",
        "query2.finance.yahoo.com",  # DS-05 backfill
        "finnhub.io",  # DS-04, DS-13, DS-41
        "www.alphavantage.co",  # DS-03, DS-40
        # Rates, inflation, macro
        "api.stlouisfed.org",  # FRED
        "markets.newyorkfed.org",  # DS-17
        "home.treasury.gov",  # DS-14
        "api.bls.gov",
        "www.bls.gov",  # DS-21, DS-22
        "apps.bea.gov",  # DS-24
        "api.census.gov",  # DS-34
        "www.federalreserve.gov",  # DS-18, DS-19
        "www.atlantafed.org",  # DS-88
        "external-api.kalshi.com",
        "api.elections.kalshi.com",  # DS-89
        "www.clevelandfed.org",  # DS-90
        # Short interest (DS-91)
        "api.finra.org",
        "ews.fip.finra.org",
        # Filings, regulation, government
        "www.sec.gov",
        "data.sec.gov",  # DS-06, DS-10, DS-11, DS-75
        "www.federalregister.gov",
        "www.whitehouse.gov",
        "ustr.gov",
        "hts.usitc.gov",
        "www.cbp.gov",
        "www.bis.gov",
        "english.mofcom.gov.cn",
        "ofac.treasury.gov",
        "sanctionslist.ofac.treas.gov",
        "static.nhtsa.gov",
        "api.nhtsa.gov",
        "www.nhtsa.gov",
        "www.dmv.ca.gov",
        "www.cpuc.ca.gov",
        "www.txdmv.gov",
        "www.business.nv.gov",
        "dmv.nv.gov",
        "azdot.gov",
        "www.flhsmv.gov",
        "www.rdw.nl",
        "www.miit.gov.cn",
        "www.gov.cn",
        "www.faa.gov",
        "www.nasa.gov",
        "www.ssc.spaceforce.mil",
        "www.defense.gov",
        "www.gao.gov",
        "api.usaspending.gov",
        "api.sam.gov",
        "publicapi.fcc.gov",
        # SpaceX, Tesla, launches
        "ll.thespacedevs.com",
        "api.spaceflightnewsapi.net",
        "celestrak.org",
        "planet4589.org",
        "www.spacex.com",
        "www.starlink.com",
        "ir.tesla.com",
        "www.tesla.com",
        # News, geopolitics, trackers
        "api.gdeltproject.org",
        "news.google.com",
        "acleddata.com",
        "www.matteoiacoviello.com",
        "robotaxitracker.com",
        "thechargeport.com",
        "notanfsdtracker.com",
        # Calendars
        "date.nager.at",
        "www.nyse.com",
        "www.sifma.org",
        "www.opm.gov",
    }
)

USER_AGENT = os.getenv("SEC_USER_AGENT", "invdash-collector (contact: set SEC_USER_AGENT)")


class HostNotAllowedError(RuntimeError):
    """Raised when a collector tries to reach a host outside ALLOWED_HOSTS."""


def _check_host(request: httpx.Request) -> None:
    host = request.url.host
    if host not in ALLOWED_HOSTS:
        raise HostNotAllowedError(f"Outbound request to {host!r} blocked: not in the provider allowlist")


def get_client(timeout_s: float = 30.0, **kwargs) -> httpx.Client:
    """httpx client: TLS verified, 10 s connect / 30 s read, redirects re-checked against the allowlist."""
    return httpx.Client(
        timeout=httpx.Timeout(timeout_s, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [_check_host]},
        **kwargs,
    )
