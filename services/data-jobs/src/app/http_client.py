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
import random
import time

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
        "www.war.gov",  # defense.gov contract RSS redirects here; article pages stay unfetched
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


RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _check_host(request: httpx.Request) -> None:
    host = request.url.host
    if host not in ALLOWED_HOSTS:
        raise HostNotAllowedError(f"Outbound request to {host!r} blocked: not in the provider allowlist")


def get_client(timeout_s: float = 30.0, headers: dict[str, str] | None = None, **kwargs) -> httpx.Client:
    """httpx client: TLS verified, 10 s connect / 30 s read, redirects re-checked against the allowlist.

    Caller headers (e.g. provider auth) are merged over the default User-Agent.
    """
    return httpx.Client(
        timeout=httpx.Timeout(timeout_s, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        event_hooks={"request": [_check_host]},
        **kwargs,
    )


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_attempts: int = 4,
    base_delay_s: float = 0.5,
    retry_statuses: frozenset[int] | None = None,
    return_statuses: frozenset[int] | None = None,
    **kwargs,
) -> httpx.Response:
    """Retry transient network/upstream failures with bounded exponential backoff and jitter.

    retry_statuses replaces the default transient set. return_statuses come back to the
    caller without a raise and without a retry, so a 429 or a 400 body can be classified.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    retryable = RETRYABLE_STATUS_CODES if retry_statuses is None else retry_statuses
    passthrough = return_statuses or frozenset()
    for attempt in range(max_attempts):
        try:
            response = client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt + 1 == max_attempts:
                raise
            delay = min(base_delay_s * (2**attempt) * (0.5 + random.random()), 30.0)
            time.sleep(delay)
            continue

        if response.status_code in passthrough:
            return response
        if response.status_code not in retryable:
            response.raise_for_status()
            return response
        if attempt + 1 == max_attempts:
            response.raise_for_status()

        try:
            retry_after = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry_after = 0.0
        delay = min(max(retry_after, base_delay_s * (2**attempt) * (0.5 + random.random())), 30.0)
        time.sleep(delay)

    raise RuntimeError("request retry loop ended unexpectedly")
