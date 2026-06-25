"""Admin dashboard data for SourceIQ."""

from datetime import date, datetime, timedelta

from market_spy.config import CREDIT_LOG_FILE
from market_spy.web.database import (
    _row_to_dict,
    count_product_niches,
    count_products,
    get_cancelled_users,
    get_database,
    get_last_scrape_info,
    get_recent_scrape_logs,
    get_running_scrape_logs,
)
from market_spy.web.database_builder import (
    NICHE_SCRAPE_EXCEPTION_DATE,
    get_active_batch_jobs,
    is_initial_scrape_running,
    scheduled_nightly_hour,
)
from market_spy.web.logger import ERROR_LOG_FILE

ERROR_LOG_SEPARATOR = "=" * 72


async def get_admin_stats() -> dict:
    today = date.today().isoformat()
    db = get_database()
    today_prefix = f"{today}%"

    total_users = await db.fetch_val("SELECT COUNT(*) FROM users")
    tier_rows = await db.fetch_all(
        "SELECT tier, COUNT(*) AS c FROM users GROUP BY tier ORDER BY c DESC",
    )
    searches_stage1_today = await db.fetch_val(
        """
        SELECT COUNT(*) FROM search_history
        WHERE stage = 1 AND searched_at LIKE :prefix
        """,
        {"prefix": today_prefix},
    )
    searches_stage2_today = await db.fetch_val(
        """
        SELECT COUNT(*) FROM search_history
        WHERE stage = 2 AND searched_at LIKE :prefix
        """,
        {"prefix": today_prefix},
    )
    recent_signups = await db.fetch_all(
        """
        SELECT id, email, tier, created_at
        FROM users
        ORDER BY created_at DESC
        LIMIT 10
        """,
    )

    last_scrape = await get_last_scrape_info()
    next_scheduled = _next_scheduled_scrape_label()

    return {
        "total_users": total_users or 0,
        "users_by_tier": {row["tier"]: row["c"] for row in tier_rows},
        "searches_stage1_today": searches_stage1_today or 0,
        "searches_stage2_today": searches_stage2_today or 0,
        "scrapingbee_credits_today": _credits_used_today(today),
        "recent_errors": _recent_errors(10),
        "recent_signups": [_row_to_dict(r) for r in recent_signups],
        "cancelled_users": await get_cancelled_users(20),
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "db_product_count": await count_products(),
        "db_niche_count": await count_product_niches(),
        "last_scrape": last_scrape,
        "next_scheduled_scrape": next_scheduled,
        "recent_scrape_logs": await get_recent_scrape_logs(10),
        "running_scrape_logs": await get_running_scrape_logs(20),
        "active_batch_jobs": get_active_batch_jobs(),
        "initial_scrape_running": is_initial_scrape_running(),
    }


def _next_scheduled_scrape_label() -> str:
    now = datetime.utcnow()
    hour = scheduled_nightly_hour()
    label = f"Nightly new niches — {hour:02d}:00 UTC"
    if date.today() == NICHE_SCRAPE_EXCEPTION_DATE:
        label += " (exception schedule today)"
    trend_next = now.replace(hour=1, minute=0, second=0, microsecond=0)
    if now.hour >= 1:
        trend_next += timedelta(days=1)
    nightly_next = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now.hour >= hour:
        nightly_next += timedelta(days=1)
    return (
        f"{label}; next trend refresh {trend_next.strftime('%Y-%m-%d %H:%M')} UTC; "
        f"next nightly {nightly_next.strftime('%Y-%m-%d %H:%M')} UTC; "
        f"weekly sourcing Sun 03:00 UTC"
    )


def _credits_used_today(today_prefix: str) -> int:
    if not CREDIT_LOG_FILE:
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
        if not ts.startswith(today_prefix):
            continue
        try:
            total += int(credits)
        except ValueError:
            continue
    return total


def _recent_errors(limit: int) -> list[str]:
    try:
        with open(ERROR_LOG_FILE, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return []

    if not content.strip():
        return []

    chunks = [chunk.strip() for chunk in content.split(ERROR_LOG_SEPARATOR) if chunk.strip()]
    return list(reversed(chunks[-limit:]))
