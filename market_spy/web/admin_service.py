"""Admin dashboard data for SourceIQ."""

from datetime import date, datetime, timedelta

from market_spy.web.credit_util import live_credit_totals
from market_spy.web.database import (
    _row_to_dict,
    count_product_niches,
    count_products,
    get_cancelled_users,
    get_database,
    get_initial_scrape_stats_today,
    get_last_scrape_info,
    get_recent_scrape_logs,
    get_running_scrape_logs,
)
from market_spy.web.database_builder import (
    NICHE_SCRAPE_EXCEPTION_DATE,
    get_active_batch_jobs,
    is_any_scrape_active,
    is_initial_scrape_active,
    scheduled_nightly_hour,
)
from market_spy.web.logger import ERROR_LOG_FILE

ERROR_LOG_SEPARATOR = "=" * 72


async def get_scrape_status_payload() -> dict:
    """Live scrape progress for admin polling."""
    credits = await live_credit_totals()
    recent = await get_recent_scrape_logs(10)
    running = await get_running_scrape_logs(20)
    enriched_recent = [await _enrich_log_row(row) for row in recent]
    enriched_running = [await _enrich_log_row(row) for row in running]
    return {
        "product_count": await count_products(),
        "niche_count": await count_product_niches(),
        "credits_month": credits["credits_month"],
        "billing_month": credits["billing_month"],
        "credits_today": credits["credits_month"],
        "session_credits": credits["session_credits"],
        "today_initial": await get_initial_scrape_stats_today(),
        "active_batch_jobs": get_active_batch_jobs(),
        "running_scrape_logs": enriched_running,
        "recent_scrape_logs": enriched_recent,
        "initial_scrape_running": await is_initial_scrape_active(),
        "any_scrape_running": await is_any_scrape_active(),
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


async def _enrich_log_row(row: dict) -> dict:
    enriched = dict(row)
    if enriched.get("started_at"):
        from market_spy.web.database_builder import credits_for_scrape_async

        log_id = int(enriched.get("id") or 0)
        stored = int(enriched.get("credits_used") or 0)
        live = await credits_for_scrape_async(log_id, enriched["started_at"]) if log_id else 0
        enriched["credits_used"] = max(stored, live)
    if enriched.get("started_at") and enriched.get("completed_at"):
        enriched["duration_sec"] = _scrape_duration_sec(
            enriched["started_at"],
            enriched["completed_at"],
        )
    return enriched


def _scrape_duration_sec(started_at: str, completed_at: str) -> int | None:
    try:
        start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        return max(0, int((end_dt - start_dt).total_seconds()))
    except (TypeError, ValueError):
        return None


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
    scrape_status = await get_scrape_status_payload()

    return {
        "total_users": total_users or 0,
        "users_by_tier": {row["tier"]: row["c"] for row in tier_rows},
        "searches_stage1_today": searches_stage1_today or 0,
        "searches_stage2_today": searches_stage2_today or 0,
        "scrapingbee_credits_today": scrape_status["credits_month"],
        "scrapingbee_credits_month": scrape_status["credits_month"],
        "billing_month": scrape_status.get("billing_month", ""),
        "session_credits": scrape_status.get("session_credits", 0),
        "today_initial": scrape_status.get("today_initial", {}),
        "recent_errors": _recent_errors(10),
        "recent_signups": [_row_to_dict(r) for r in recent_signups],
        "cancelled_users": await get_cancelled_users(20),
        "generated_at": scrape_status["generated_at"],
        "db_product_count": scrape_status["product_count"],
        "db_niche_count": scrape_status["niche_count"],
        "last_scrape": last_scrape,
        "next_scheduled_scrape": next_scheduled,
        "recent_scrape_logs": scrape_status["recent_scrape_logs"],
        "running_scrape_logs": scrape_status["running_scrape_logs"],
        "active_batch_jobs": scrape_status["active_batch_jobs"],
        "initial_scrape_running": scrape_status["initial_scrape_running"],
        "any_scrape_running": scrape_status["any_scrape_running"],
    }


def _next_scheduled_scrape_label() -> str:
    now = datetime.utcnow()
    hour = scheduled_nightly_hour()
    label = f"Nightly new products (5 existing + 5 new niches) — {hour:02d}:00 UTC"
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
