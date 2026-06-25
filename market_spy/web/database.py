"""SQLite user storage for the SourceIQ web app."""

import os
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import bcrypt

from market_spy.config import PRO_OWN_KEY_STAGE2_LIMIT, TIER_LIMITS, VALID_TIERS

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")
TRIAL_DAYS = 7

CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'trial',
    stage1_used INTEGER NOT NULL DEFAULT 0,
    stage2_used INTEGER NOT NULL DEFAULT 0,
    trial_start_date TEXT NOT NULL,
    own_scrapingbee_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

CREATE_SEARCH_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    niche TEXT NOT NULL,
    stage INTEGER NOT NULL,
    opportunity_score REAL,
    margin_tier TEXT NOT NULL DEFAULT '',
    margin_summary TEXT NOT NULL DEFAULT '',
    searched_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

CREATE_WATCHLIST_SQL = """
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    niche TEXT NOT NULL,
    last_searched_at TEXT,
    last_opportunity_score REAL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, niche),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

CREATE_PRICE_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    niche TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    budget_margin REAL,
    mid_margin REAL,
    premium_margin REAL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute(CREATE_USERS_SQL)
        conn.execute(CREATE_SEARCH_HISTORY_SQL)
        conn.execute(CREATE_WATCHLIST_SQL)
        conn.execute(CREATE_PRICE_HISTORY_SQL)
        _migrate_users(conn)


def _migrate_users(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "stripe_customer_id" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN stripe_customer_id TEXT NOT NULL DEFAULT ''"
        )
    if "stripe_subscription_id" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT NOT NULL DEFAULT ''"
        )
    if "cancellation_date" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN cancellation_date TEXT NOT NULL DEFAULT ''"
        )
    if "cancelled_from_tier" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN cancelled_from_tier TEXT NOT NULL DEFAULT ''"
        )


def _effective_tier(user: dict) -> str:
    tier = user.get("tier", "trial")
    if tier == "cancelling":
        return user.get("cancelled_from_tier") or "starter"
    return tier


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _row_to_dict(row) -> dict:
    return dict(row) if row else None


def get_user_by_id(user_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_dict(row)


def get_user_by_email(email: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    return _row_to_dict(row)


def create_user(email: str, password: str):
    return create_user_with_tier(email, password, "trial")


def create_user_with_tier(email: str, password: str, tier: str):
    """Create a user with a specific tier (admin/CLI use)."""
    email = email.strip().lower()
    tier = (tier or "trial").strip().lower()
    if tier not in VALID_TIERS:
        return None, f"Invalid tier '{tier}'. Choose from: {', '.join(VALID_TIERS)}"
    if get_user_by_email(email):
        return None, "An account with this email already exists."
    if len(password) < 8:
        return None, "Password must be at least 8 characters."
    today = date.today().isoformat()
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (
                email, password_hash, tier, stage1_used, stage2_used,
                trial_start_date, own_scrapingbee_key, created_at
            ) VALUES (?, ?, ?, 0, 0, ?, '', ?)
            """,
            (email, _hash_password(password), tier, today, now),
        )
        user_id = cur.lastrowid
    return get_user_by_id(user_id), None


def update_user_tier_and_password(user_id: int, password: str, tier: str):
    """Update tier and password for an existing user."""
    tier = (tier or "trial").strip().lower()
    if tier not in VALID_TIERS:
        return None, f"Invalid tier '{tier}'. Choose from: {', '.join(VALID_TIERS)}"
    if len(password) < 8:
        return None, "Password must be at least 8 characters."
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET tier = ?, password_hash = ? WHERE id = ?",
            (tier, _hash_password(password), user_id),
        )
    return get_user_by_id(user_id), None


def authenticate_user(email: str, password: str):
    user = get_user_by_email(email)
    if not user or not _verify_password(password, user["password_hash"]):
        return None, "Invalid email or password."
    return user, None


def get_tier_limits_for_user(user: dict) -> dict:
    limits_tier = _effective_tier(user)
    limits = dict(TIER_LIMITS.get(limits_tier, TIER_LIMITS["none"]))
    if limits_tier == "pro" and user.get("own_scrapingbee_key"):
        limits["stage2"] = PRO_OWN_KEY_STAGE2_LIMIT
    return limits


def is_pro_user(user: dict) -> bool:
    return _effective_tier(user) == "pro"


def check_trial_expiry(
    user_id: int,
    send_expiry_email: Callable[[str], bool] | None = None,
) -> bool:
    """If trial expired, downgrade to no tier and optionally notify via email."""
    user = get_user_by_id(user_id)
    if not user or user.get("tier") != "trial":
        return False
    start = date.fromisoformat(user["trial_start_date"])
    if date.today() <= start + timedelta(days=TRIAL_DAYS):
        return False
    update_user_tier(user_id, "none")
    if send_expiry_email:
        send_expiry_email(user["email"])
    return True


def check_all_trial_expiries(
    send_expiry_email: Callable[[str], bool] | None = None,
) -> int:
    """Run expiry check for every trial user. Returns count downgraded."""
    with get_db() as conn:
        rows = conn.execute("SELECT id FROM users WHERE tier = 'trial'").fetchall()
    expired = 0
    for row in rows:
        if check_trial_expiry(row["id"], send_expiry_email=send_expiry_email):
            expired += 1
    return expired


def update_user_password(user_id: int, password: str):
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (_hash_password(password), user_id),
        )


def add_search_history(
    user_id: int,
    niche: str,
    stage: int,
    *,
    opportunity_score=None,
    margin_tier: str = "",
    margin_summary: str = "",
):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO search_history (
                user_id, niche, stage, opportunity_score,
                margin_tier, margin_summary, searched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                niche.strip(),
                stage,
                opportunity_score,
                margin_tier or "",
                margin_summary or "",
                now,
            ),
        )


def get_search_history(user_id: int, limit: int = 50) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM search_history
            WHERE user_id = ?
            ORDER BY searched_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_search_history_entry(user_id: int, history_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM search_history WHERE id = ? AND user_id = ?",
            (history_id, user_id),
        ).fetchone()
    return _row_to_dict(row)


def add_watchlist_item(user_id: int, niche: str):
    niche = niche.strip()
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO watchlist (user_id, niche, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, niche) DO NOTHING
            """,
            (user_id, niche, now),
        )


def remove_watchlist_item(user_id: int, niche: str):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND niche = ?",
            (user_id, niche.strip()),
        )


def get_watchlist(user_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM watchlist
            WHERE user_id = ?
            ORDER BY COALESCE(last_searched_at, '') DESC, created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_watchlist_after_search(user_id: int, niche: str, opportunity_score: float):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE watchlist
            SET last_searched_at = ?, last_opportunity_score = ?
            WHERE user_id = ? AND niche = ?
            """,
            (now, opportunity_score, user_id, niche.strip()),
        )


def save_price_history(
    user_id: int,
    niche: str,
    budget_margin,
    mid_margin,
    premium_margin,
):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO price_history (
                user_id, niche, recorded_at,
                budget_margin, mid_margin, premium_margin
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, niche.strip(), now, budget_margin, mid_margin, premium_margin),
        )


def get_price_history(user_id: int, niche: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM price_history
            WHERE user_id = ? AND niche = ?
            ORDER BY recorded_at ASC
            """,
            (user_id, niche.strip()),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _best_margin_tier(by_tier: dict) -> tuple[str, str]:
    """Return (best tier key, summary string) from Stage 2 by_tier data."""
    if not by_tier:
        return "", ""
    parts = []
    best_key = ""
    best_margin = -1.0
    for key in ("budget", "mid", "premium"):
        tier = by_tier.get(key) or {}
        margin = tier.get("tier_margin_percent")
        label = tier.get("label") or key.title()
        if margin is not None:
            parts.append(f"{label.split('(')[0].strip()}: {margin}%")
            if margin > best_margin:
                best_margin = margin
                best_key = key
    return best_key, " | ".join(parts)


def margin_meta_from_stage2(by_tier: dict) -> tuple[str, str]:
    """Public helper: best margin tier key and summary for Stage 2 results."""
    return _best_margin_tier(by_tier)


def get_remaining_for_user(user: dict) -> dict:
    limits = get_tier_limits_for_user(user)
    stage1_used = int(user.get("stage1_used", 0))
    stage2_used = int(user.get("stage2_used", 0))
    return {
        "tier": user.get("tier", "trial"),
        "stage1_limit": limits["stage1"],
        "stage2_limit": limits["stage2"],
        "stage1_used": stage1_used,
        "stage2_used": stage2_used,
        "stage1_remaining": max(0, limits["stage1"] - stage1_used),
        "stage2_remaining": max(0, limits["stage2"] - stage2_used),
    }


def can_user_stage1(user: dict, count: int = 1) -> bool:
    return get_remaining_for_user(user)["stage1_remaining"] >= count


def can_user_stage2(user: dict) -> bool:
    return get_remaining_for_user(user)["stage2_remaining"] > 0


def increment_user_stage1(user_id: int, count: int = 1):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET stage1_used = stage1_used + ? WHERE id = ?",
            (count, user_id),
        )


def increment_user_stage2(user_id: int, count: int = 1):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET stage2_used = stage2_used + ? WHERE id = ?",
            (count, user_id),
        )


def update_user_scrapingbee_key(user_id: int, api_key: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET own_scrapingbee_key = ? WHERE id = ?",
            (api_key.strip(), user_id),
        )


def update_user_tier(user_id: int, tier: str):
    if tier not in VALID_TIERS:
        return
    with get_db() as conn:
        conn.execute("UPDATE users SET tier = ? WHERE id = ?", (tier, user_id))


def cancel_user_subscription(user_id: int):
    """Mark subscription as cancelling; retain access until renewal date."""
    user = get_user_by_id(user_id)
    if not user:
        return None, "User not found."
    if user.get("tier") == "cancelling":
        return None, "Your subscription is already cancelled."
    if user.get("tier") == "none":
        return None, "You do not have an active subscription to cancel."
    cancelled_from = user.get("tier", "starter")
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE users
            SET tier = 'cancelling', cancellation_date = ?, cancelled_from_tier = ?
            WHERE id = ?
            """,
            (now, cancelled_from, user_id),
        )
    return get_user_by_id(user_id), None


def get_cancelled_users(limit: int = 50) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, email, tier, cancelled_from_tier, cancellation_date, created_at
            FROM users
            WHERE tier = 'cancelling' OR cancellation_date != ''
            ORDER BY cancellation_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_user_stripe_ids(user_id: int, customer_id: str, subscription_id: str):
    with get_db() as conn:
        conn.execute(
            """
            UPDATE users
            SET stripe_customer_id = ?, stripe_subscription_id = ?
            WHERE id = ?
            """,
            (customer_id or "", subscription_id or "", user_id),
        )


def get_user_by_stripe_customer_id(customer_id: str):
    if not customer_id:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE stripe_customer_id = ?",
            (customer_id,),
        ).fetchone()
    return _row_to_dict(row)


def check_database_connected() -> bool:
    try:
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False
