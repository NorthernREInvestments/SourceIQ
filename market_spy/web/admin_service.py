"""Admin dashboard data for SourceIQ."""

from datetime import date, datetime

from market_spy.config import CREDIT_LOG_FILE
from market_spy.web.database import get_db, _row_to_dict
from market_spy.web.logger import ERROR_LOG_FILE

ERROR_LOG_SEPARATOR = "=" * 72


def get_admin_stats() -> dict:
    today = date.today().isoformat()

    with get_db() as conn:
        total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        tier_rows = conn.execute(
            "SELECT tier, COUNT(*) AS c FROM users GROUP BY tier ORDER BY c DESC"
        ).fetchall()
        searches_stage1_today = conn.execute(
            """
            SELECT COUNT(*) AS c FROM search_history
            WHERE stage = 1 AND searched_at LIKE ?
            """,
            (f"{today}%",),
        ).fetchone()["c"]
        searches_stage2_today = conn.execute(
            """
            SELECT COUNT(*) AS c FROM search_history
            WHERE stage = 2 AND searched_at LIKE ?
            """,
            (f"{today}%",),
        ).fetchone()["c"]
        recent_signups = conn.execute(
            """
            SELECT id, email, tier, created_at
            FROM users
            ORDER BY created_at DESC
            LIMIT 10
            """
        ).fetchall()

    return {
        "total_users": total_users,
        "users_by_tier": {row["tier"]: row["c"] for row in tier_rows},
        "searches_stage1_today": searches_stage1_today,
        "searches_stage2_today": searches_stage2_today,
        "scrapingbee_credits_today": _credits_used_today(today),
        "recent_errors": _recent_errors(10),
        "recent_signups": [_row_to_dict(r) for r in recent_signups],
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


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
