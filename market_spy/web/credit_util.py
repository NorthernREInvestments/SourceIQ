"""ScrapingBee credit totals from credit_log.txt and in-process session counter."""

from datetime import date

from market_spy.config import CREDIT_LOG_FILE
from market_spy.scrapers.scrapingbee_client import get_session_credit_total


def credits_used_today() -> int:
    return credits_used_since(date.today().isoformat())


def credits_used_since(since_prefix: str) -> int:
    """Sum credits logged at or after since_prefix (ISO date or timestamp prefix)."""
    if not CREDIT_LOG_FILE or not since_prefix:
        return 0
    try:
        with open(CREDIT_LOG_FILE, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return 0

    total = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("timestamp"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        ts, _source, _url, credits = parts[0], parts[1], parts[2], parts[3]
        if ts < since_prefix:
            continue
        try:
            total += int(credits)
        except ValueError:
            continue
    return total


def live_credit_totals() -> dict:
    """Credits from log file today plus this process session (updates per API call)."""
    file_today = credits_used_today()
    session = get_session_credit_total()
    return {
        "credits_today": max(file_today, session) if session else file_today,
        "session_credits": session,
    }
