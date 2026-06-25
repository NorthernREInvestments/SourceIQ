"""Shared ScrapingBee fetch helper with per-request credit logging."""

import os
import threading
from datetime import datetime

from scrapingbee import ScrapingBeeClient

from market_spy.config import (
    CREDIT_LOG_FILE,
    OUTPUT_DIR,
    SCRAPINGBEE_REQUEST_TIMEOUT,
    get_scrapingbee_api_key,
)

_session_total = 0
_log_lock = threading.Lock()


def get_session_credit_total():
    """Return running ScrapingBee credit total for this process."""
    with _log_lock:
        return _session_total


def reset_session_credit_total():
    """Reset the in-process session credit counter."""
    global _session_total
    with _log_lock:
        _session_total = 0


def _ensure_credit_log_header():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if not os.path.exists(CREDIT_LOG_FILE) or os.path.getsize(CREDIT_LOG_FILE) == 0:
        with open(CREDIT_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write("timestamp\tsource\turl\tcredits\tsession_total\n")


def _log_credit(source, url, credits):
    global _session_total
    try:
        cost = int(credits) if credits is not None else 0
    except (TypeError, ValueError):
        cost = 0
    with _log_lock:
        _session_total += cost
        total = _session_total
    _ensure_credit_log_header()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_url = (url or "").replace("\t", " ").replace("\n", " ")
    safe_source = (source or "unknown").replace("\t", " ")
    line = f"{ts}\t{safe_source}\t{safe_url}\t{cost}\t{total}\n"
    with open(CREDIT_LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line)


def fetch_scrapingbee(
    url,
    source="",
    render_js=True,
    premium_proxy=True,
    stealth_proxy=False,
    wait=3000,
    timeout=None,
):
    """Fetch a URL via ScrapingBee; returns HTML string or None."""
    api_key = get_scrapingbee_api_key()
    if not api_key:
        print(
            f"[scrapingbee] skipped {source or 'unknown'}: SCRAPINGBEE_API_KEY not set",
            flush=True,
        )
        _log_credit(source or "unknown", url, 0)
        return None
    if timeout is None:
        timeout = SCRAPINGBEE_REQUEST_TIMEOUT
    client = ScrapingBeeClient(api_key=api_key)
    params = {"render_js": render_js}
    if premium_proxy:
        params["premium_proxy"] = True
    if stealth_proxy:
        params["stealth_proxy"] = True
    if wait and render_js:
        params["wait"] = wait
    try:
        response = client.get(url, params=params, timeout=timeout)
    except Exception as exc:
        print(
            f"[scrapingbee] error {source or 'unknown'} url={url}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        _log_credit(source or "unknown", url, 0)
        return None
    cost = response.headers.get("Spb-cost") or response.headers.get("spb-cost") or 0
    if response.status_code != 200:
        print(
            f"[scrapingbee] HTTP {response.status_code} {source or 'unknown'} url={url}",
            flush=True,
        )
        _log_credit(source or "unknown", url, cost)
        return None
    _log_credit(source or "unknown", url, cost)
    return response.content.decode("utf-8", errors="replace")
