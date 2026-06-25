"""Health-check helpers for external services."""

import os
import urllib.error
import urllib.request

from market_spy.config import SCRAPINGBEE_API_KEY


def check_scrapingbee_connected() -> bool:
    api_key = (SCRAPINGBEE_API_KEY or os.getenv("SCRAPINGBEE_API_KEY", "")).strip()
    if not api_key:
        return False
    url = f"https://app.scrapingbee.com/api/v1/usage?api_key={api_key}"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
