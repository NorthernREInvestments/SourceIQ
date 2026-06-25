"""ScrapingBee credit totals — persisted monthly in DB, resets on the 1st."""

from __future__ import annotations

from market_spy.config import CREDIT_LOG_FILE
from market_spy.scrapers.scrapingbee_client import get_session_credit_total
from market_spy.web.credit_store import current_billing_month
from market_spy.web.database import get_database


def _normalize_since_prefix(since_prefix: str) -> str:
    """Normalize scrape_log ISO timestamps for comparison."""
    if not since_prefix:
        return ""
    return since_prefix.replace("T", " ").replace("Z", "")[:19]


async def credits_used_since_async(since_prefix: str) -> int:
    """Sum persisted credits at or after since_prefix."""
    since_iso = since_prefix.replace("Z", "")[:26] if since_prefix else ""
    since_norm = _normalize_since_prefix(since_prefix)
    db = get_database()
    if since_iso:
        total = await db.fetch_val(
            """
            SELECT COALESCE(SUM(credits), 0) FROM credit_events
            WHERE used_at >= :since_iso
            """,
            {"since_iso": since_iso},
        )
        if int(total or 0) > 0:
            return int(total)

    if since_norm and CREDIT_LOG_FILE:
        try:
            with open(CREDIT_LOG_FILE, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            lines = []
        file_total = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("timestamp"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            ts, _source, _url, credits = parts[0], parts[1], parts[2], parts[3]
            if ts < since_norm:
                continue
            try:
                file_total += int(credits)
            except ValueError:
                continue
        if file_total:
            return file_total

    return 0


async def credits_used_this_month() -> int:
    """Total ScrapingBee credits this billing month (resets on the 1st UTC)."""
    month = current_billing_month()
    db = get_database()
    events_total = int(
        await db.fetch_val(
            """
            SELECT COALESCE(SUM(credits), 0) FROM credit_events
            WHERE billing_month = :month
            """,
            {"month": month},
        )
        or 0
    )
    logs_total = int(
        await db.fetch_val(
            """
            SELECT COALESCE(SUM(credits_used), 0) FROM scrape_log
            WHERE started_at LIKE :prefix AND credits_used > 0
            """,
            {"prefix": f"{month}%"},
        )
        or 0
    )
    return max(events_total, logs_total)


async def live_credit_totals() -> dict:
    """Monthly credit total from DB plus any in-process credits not yet flushed."""
    month_db = await credits_used_this_month()
    session = get_session_credit_total()
    return {
        "credits_month": month_db,
        "billing_month": current_billing_month(),
        "credits_today": month_db,
        "session_credits": session,
    }
