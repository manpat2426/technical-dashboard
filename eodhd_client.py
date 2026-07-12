"""
Thin wrapper around the EODHD API: technical indicators and raw EOD prices.

Every function returns a list of plain dicts (one per date) so the rest of
the pipeline doesn't need to know anything about HTTP or EODHD's response
shape. Every request pauses afterwards using config.EODHD_REQUEST_PAUSE_SECONDS,
since EODHD bills each technical request as 5 API credits and this script
runs unattended overnight, where staying safely under the rate limit
matters more than raw speed.

All requests reuse one requests.Session() (_session below) instead of
opening a fresh connection per call. Measured against the live API: a
one-off request pays ~1.1-1.3s just for a new TCP/TLS handshake to
eodhd.com; reusing a connection drops that to ~0.1-0.25s. Across a full
113-symbol run (~800 requests) that handshake cost was the actual
dominant cost of the whole pipeline -- far more than the rate-limit pause
itself.
"""

import time
from datetime import date, timedelta

import requests

from config import (
    EODHD_API_KEY,
    EODHD_EOD_URL,
    EODHD_TECHNICAL_URL,
    EODHD_REQUEST_PAUSE_SECONDS,
    HISTORY_YEARS,
)

_session = requests.Session()


def _history_start_date() -> str:
    """First date to request, based on HISTORY_YEARS of lookback buffer."""
    start = date.today() - timedelta(days=365 * HISTORY_YEARS)
    return start.isoformat()


def _get(url: str, params: dict) -> list:
    """Issue one GET request, raise a clear error on failure, pause to
    respect the rate limit, then return the parsed JSON list."""
    request_params = {**params, "api_token": EODHD_API_KEY, "fmt": "json"}
    response = _session.get(url, params=request_params, timeout=30)
    time.sleep(EODHD_REQUEST_PAUSE_SECONDS)

    if response.status_code != 200:
        raise RuntimeError(
            f"EODHD request failed ({response.status_code}) for {url} "
            f"params={params}: {response.text[:300]}"
        )

    data = response.json()
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected EODHD response shape for {url}: {data}")
    return data


def get_eod(symbol: str) -> list:
    """Raw daily EOD prices, ~HISTORY_YEARS of history. Each row includes
    'adjusted_close' (split+dividend adjusted) -- use that field, not
    'close', per the brief."""
    url = EODHD_EOD_URL.format(symbol=symbol)
    return _get(url, {"from": _history_start_date()})


def get_technical(symbol: str, function: str, period: int = None) -> list:
    """One technical indicator's full time series, e.g. function='sma',
    period=60, or function='macd' with no period (EODHD uses its own
    fast/slow/signal defaults for macd -- see note in scoring.py). EODHD's
    technical functions compute on split+dividend adjusted data by
    default, which matches the brief's requirement."""
    url = EODHD_TECHNICAL_URL.format(symbol=symbol)
    params = {"function": function, "from": _history_start_date()}
    if period is not None:
        params["period"] = period
    return _get(url, params)
