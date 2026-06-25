"""Shared ScrapingBee fetch helper with per-request credit logging."""

import os
import threading
import traceback
from datetime import datetime, timezone

from scrapingbee import ScrapingBeeClient

from market_spy.config import (
    CREDIT_LOG_FILE,
    OUTPUT_DIR,
    SCRAPINGBEE_REQUEST_TIMEOUT,
    get_scrapingbee_api_key,
)

_session_total = 0
_log_lock = threading.Lock()


class ScrapingBeeFetchError(Exception):
    """Raised when ScrapingBee cannot return page HTML."""

    def __init__(self, source: str, url: str, detail: str):
        self.source = source
        self.url = url
        self.detail = detail
        super().__init__(f"{source} GET failed: {detail} (url={url[:120]})")


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
    ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    safe_url = (url or "").replace("\t", " ").replace("\n", " ")
    safe_source = (source or "unknown").replace("\t", " ")
    line = f"{ts}\t{safe_source}\t{safe_url}\t{cost}\t{total}\n"
    with open(CREDIT_LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line)
    try:
        from market_spy.web.credit_store import record_credit_event

        record_credit_event(safe_source, safe_url, cost, used_at=ts)
    except Exception as exc:
        print(f"[scrapingbee] credit persist failed: {exc}", flush=True)


def fetch_scrapingbee(
    url,
    source="",
    render_js=True,
    premium_proxy=True,
    stealth_proxy=False,
    wait=3000,
    timeout=None,
    *,
    raise_on_error: bool = False,
):
    """Fetch a URL via ScrapingBee; returns HTML string or None."""
    source_label = source or "unknown"
    api_key = get_scrapingbee_api_key()
    if not api_key:
        detail = "SCRAPINGBEE_API_KEY not set"
        print(f"[scrapingbee] skipped {source_label}: {detail}", flush=True)
        _log_credit(source_label, url, 0)
        if raise_on_error:
            raise ScrapingBeeFetchError(source_label, url or "", detail)
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
        detail = f"{type(exc).__name__}: {exc}"
        print(
            f"[scrapingbee] GET error {source_label} url={url}: {detail}\n"
            f"{traceback.format_exc()}",
            flush=True,
        )
        _log_credit(source_label, url, 0)
        if raise_on_error:
            raise ScrapingBeeFetchError(source_label, url or "", detail) from exc
        return None
    cost = response.headers.get("Spb-cost") or response.headers.get("spb-cost") or 0
    if response.status_code != 200:
        body_preview = ""
        try:
            body_preview = response.content[:300].decode("utf-8", errors="replace")
        except Exception:
            body_preview = ""
        detail = f"HTTP {response.status_code}"
        if body_preview:
            detail += f" body={body_preview!r}"
        print(
            f"[scrapingbee] GET {source_label} url={url}: {detail}",
            flush=True,
        )
        _log_credit(source_label, url, cost)
        if raise_on_error:
            raise ScrapingBeeFetchError(source_label, url or "", detail)
        return None
    _log_credit(source_label, url, cost)
    return response.content.decode("utf-8", errors="replace")
