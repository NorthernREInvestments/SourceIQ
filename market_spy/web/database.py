"""User storage for SourceIQ — PostgreSQL in production, SQLite for local dev."""

import os
from collections.abc import Callable
from datetime import date, datetime, timedelta

import bcrypt
from databases import Database

from market_spy.config import PRO_OWN_KEY_STAGE2_LIMIT, TIER_LIMITS, VALID_TIERS

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")
TRIAL_DAYS = 7

_database: Database | None = None

_SQLITE_USERS = """
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

_POSTGRES_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'trial',
    stage1_used INTEGER NOT NULL DEFAULT 0,
    stage2_used INTEGER NOT NULL DEFAULT 0,
    trial_start_date TEXT NOT NULL,
    own_scrapingbee_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    stripe_customer_id TEXT NOT NULL DEFAULT '',
    stripe_subscription_id TEXT NOT NULL DEFAULT '',
    cancellation_date TEXT NOT NULL DEFAULT '',
    cancelled_from_tier TEXT NOT NULL DEFAULT ''
);
"""

_SEARCH_HISTORY = """
CREATE TABLE IF NOT EXISTS search_history (
    id {id_col},
    user_id INTEGER NOT NULL,
    niche TEXT NOT NULL,
    stage INTEGER NOT NULL,
    opportunity_score REAL,
    margin_tier TEXT NOT NULL DEFAULT '',
    margin_summary TEXT NOT NULL DEFAULT '',
    searched_at TEXT NOT NULL
);
"""

_WATCHLIST = """
CREATE TABLE IF NOT EXISTS watchlist (
    id {id_col},
    user_id INTEGER NOT NULL,
    niche TEXT NOT NULL,
    last_searched_at TEXT,
    last_opportunity_score REAL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, niche)
);
"""

_PRICE_HISTORY = """
CREATE TABLE IF NOT EXISTS price_history (
    id {id_col},
    user_id INTEGER NOT NULL,
    niche TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    budget_margin REAL,
    mid_margin REAL,
    premium_margin REAL
);
"""

_QUICK_START_JOBS = """
CREATE TABLE IF NOT EXISTS quick_start_jobs (
    id {id_col},
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    total INTEGER NOT NULL DEFAULT 12,
    completed INTEGER NOT NULL DEFAULT 0,
    current_niche TEXT NOT NULL DEFAULT '',
    results_json TEXT NOT NULL DEFAULT '[]',
    error_message TEXT NOT NULL DEFAULT '',
    stage1_credited INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        return url
    return f"sqlite:///{DB_PATH}"


def uses_postgres() -> bool:
    return _resolve_database_url().startswith("postgresql")


def get_database() -> Database:
    global _database
    if _database is None:
        _database = Database(_resolve_database_url())
    return _database


async def connect_db() -> None:
    db = get_database()
    if not db.is_connected:
        await db.connect()


async def disconnect_db() -> None:
    db = get_database()
    if db.is_connected:
        await db.disconnect()


def _row_to_dict(row) -> dict | None:
    return dict(row) if row else None


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _effective_tier(user: dict) -> str:
    tier = user.get("tier", "trial")
    if tier == "cancelling":
        return user.get("cancelled_from_tier") or "starter"
    return tier


async def _migrate_users_sqlite() -> None:
    db = get_database()
    rows = await db.fetch_all("PRAGMA table_info(users)")
    cols = {row["name"] for row in rows}
    migrations = {
        "stripe_customer_id": "ALTER TABLE users ADD COLUMN stripe_customer_id TEXT NOT NULL DEFAULT ''",
        "stripe_subscription_id": "ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT NOT NULL DEFAULT ''",
        "cancellation_date": "ALTER TABLE users ADD COLUMN cancellation_date TEXT NOT NULL DEFAULT ''",
        "cancelled_from_tier": "ALTER TABLE users ADD COLUMN cancelled_from_tier TEXT NOT NULL DEFAULT ''",
    }
    for col, sql in migrations.items():
        if col not in cols:
            await db.execute(sql)


async def _migrate_users_postgres() -> None:
    db = get_database()
    migrations = {
        "stripe_customer_id": "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT NOT NULL DEFAULT ''",
        "stripe_subscription_id": "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT NOT NULL DEFAULT ''",
        "cancellation_date": "ALTER TABLE users ADD COLUMN IF NOT EXISTS cancellation_date TEXT NOT NULL DEFAULT ''",
        "cancelled_from_tier": "ALTER TABLE users ADD COLUMN IF NOT EXISTS cancelled_from_tier TEXT NOT NULL DEFAULT ''",
    }
    for sql in migrations.values():
        await db.execute(sql)


async def init_db() -> None:
    await connect_db()
    db = get_database()
    id_col = "SERIAL PRIMARY KEY" if uses_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    if uses_postgres():
        await db.execute(_POSTGRES_USERS)
    else:
        await db.execute(_SQLITE_USERS)
    for template in (_SEARCH_HISTORY, _WATCHLIST, _PRICE_HISTORY, _QUICK_START_JOBS):
        await db.execute(template.format(id_col=id_col))
    if uses_postgres():
        await _migrate_users_postgres()
    else:
        await _migrate_users_sqlite()


async def get_user_by_id(user_id: int):
    row = await get_database().fetch_one(
        "SELECT * FROM users WHERE id = :id",
        {"id": user_id},
    )
    return _row_to_dict(row)


async def get_user_by_email(email: str):
    row = await get_database().fetch_one(
        "SELECT * FROM users WHERE email = :email",
        {"email": email.strip().lower()},
    )
    return _row_to_dict(row)


async def create_user(email: str, password: str):
    return await create_user_with_tier(email, password, "trial")


async def create_user_with_tier(email: str, password: str, tier: str):
    email = email.strip().lower()
    tier = (tier or "trial").strip().lower()
    if tier not in VALID_TIERS:
        return None, f"Invalid tier '{tier}'. Choose from: {', '.join(VALID_TIERS)}"
    if await get_user_by_email(email):
        return None, "An account with this email already exists."
    if len(password) < 8:
        return None, "Password must be at least 8 characters."
    today = date.today().isoformat()
    now = datetime.utcnow().isoformat()
    row = await get_database().fetch_one(
        """
        INSERT INTO users (
            email, password_hash, tier, stage1_used, stage2_used,
            trial_start_date, own_scrapingbee_key, created_at
        ) VALUES (
            :email, :password_hash, :tier, 0, 0, :trial_start_date, '', :created_at
        )
        RETURNING id
        """,
        {
            "email": email,
            "password_hash": _hash_password(password),
            "tier": tier,
            "trial_start_date": today,
            "created_at": now,
        },
    )
    return await get_user_by_id(row["id"]), None


async def update_user_tier_and_password(user_id: int, password: str, tier: str):
    tier = (tier or "trial").strip().lower()
    if tier not in VALID_TIERS:
        return None, f"Invalid tier '{tier}'. Choose from: {', '.join(VALID_TIERS)}"
    if len(password) < 8:
        return None, "Password must be at least 8 characters."
    await get_database().execute(
        "UPDATE users SET tier = :tier, password_hash = :password_hash WHERE id = :id",
        {"tier": tier, "password_hash": _hash_password(password), "id": user_id},
    )
    return await get_user_by_id(user_id), None


async def authenticate_user(email: str, password: str):
    user = await get_user_by_email(email)
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


async def check_trial_expiry(
    user_id: int,
    send_expiry_email: Callable[[str], bool] | None = None,
) -> bool:
    user = await get_user_by_id(user_id)
    if not user or user.get("tier") != "trial":
        return False
    start = date.fromisoformat(user["trial_start_date"])
    if date.today() <= start + timedelta(days=TRIAL_DAYS):
        return False
    await update_user_tier(user_id, "none")
    if send_expiry_email:
        send_expiry_email(user["email"])
    return True


async def check_all_trial_expiries(
    send_expiry_email: Callable[[str], bool] | None = None,
) -> int:
    rows = await get_database().fetch_all(
        "SELECT id FROM users WHERE tier = 'trial'",
    )
    expired = 0
    for row in rows:
        if await check_trial_expiry(row["id"], send_expiry_email=send_expiry_email):
            expired += 1
    return expired


async def update_user_password(user_id: int, password: str):
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    await get_database().execute(
        "UPDATE users SET password_hash = :password_hash WHERE id = :id",
        {"password_hash": _hash_password(password), "id": user_id},
    )


async def add_search_history(
    user_id: int,
    niche: str,
    stage: int,
    *,
    opportunity_score=None,
    margin_tier: str = "",
    margin_summary: str = "",
):
    now = datetime.utcnow().isoformat()
    await get_database().execute(
        """
        INSERT INTO search_history (
            user_id, niche, stage, opportunity_score,
            margin_tier, margin_summary, searched_at
        ) VALUES (
            :user_id, :niche, :stage, :opportunity_score,
            :margin_tier, :margin_summary, :searched_at
        )
        """,
        {
            "user_id": user_id,
            "niche": niche.strip(),
            "stage": stage,
            "opportunity_score": opportunity_score,
            "margin_tier": margin_tier or "",
            "margin_summary": margin_summary or "",
            "searched_at": now,
        },
    )


async def get_search_history(user_id: int, limit: int = 50) -> list[dict]:
    rows = await get_database().fetch_all(
        """
        SELECT * FROM search_history
        WHERE user_id = :user_id
        ORDER BY searched_at DESC
        LIMIT :limit
        """,
        {"user_id": user_id, "limit": limit},
    )
    return [_row_to_dict(r) for r in rows]


async def get_search_history_entry(user_id: int, history_id: int) -> dict | None:
    row = await get_database().fetch_one(
        "SELECT * FROM search_history WHERE id = :id AND user_id = :user_id",
        {"id": history_id, "user_id": user_id},
    )
    return _row_to_dict(row)


async def add_watchlist_item(user_id: int, niche: str):
    niche = niche.strip()
    now = datetime.utcnow().isoformat()
    await get_database().execute(
        """
        INSERT INTO watchlist (user_id, niche, created_at)
        VALUES (:user_id, :niche, :created_at)
        ON CONFLICT (user_id, niche) DO NOTHING
        """,
        {"user_id": user_id, "niche": niche, "created_at": now},
    )


async def remove_watchlist_item(user_id: int, niche: str):
    await get_database().execute(
        "DELETE FROM watchlist WHERE user_id = :user_id AND niche = :niche",
        {"user_id": user_id, "niche": niche.strip()},
    )


async def get_watchlist(user_id: int) -> list[dict]:
    rows = await get_database().fetch_all(
        """
        SELECT * FROM watchlist
        WHERE user_id = :user_id
        ORDER BY COALESCE(last_searched_at, '') DESC, created_at DESC
        """,
        {"user_id": user_id},
    )
    return [_row_to_dict(r) for r in rows]


async def update_watchlist_after_search(user_id: int, niche: str, opportunity_score: float):
    now = datetime.utcnow().isoformat()
    await get_database().execute(
        """
        UPDATE watchlist
        SET last_searched_at = :searched_at, last_opportunity_score = :score
        WHERE user_id = :user_id AND niche = :niche
        """,
        {
            "searched_at": now,
            "score": opportunity_score,
            "user_id": user_id,
            "niche": niche.strip(),
        },
    )


async def save_price_history(
    user_id: int,
    niche: str,
    budget_margin,
    mid_margin,
    premium_margin,
):
    now = datetime.utcnow().isoformat()
    await get_database().execute(
        """
        INSERT INTO price_history (
            user_id, niche, recorded_at,
            budget_margin, mid_margin, premium_margin
        ) VALUES (
            :user_id, :niche, :recorded_at,
            :budget_margin, :mid_margin, :premium_margin
        )
        """,
        {
            "user_id": user_id,
            "niche": niche.strip(),
            "recorded_at": now,
            "budget_margin": budget_margin,
            "mid_margin": mid_margin,
            "premium_margin": premium_margin,
        },
    )


async def get_price_history(user_id: int, niche: str) -> list[dict]:
    rows = await get_database().fetch_all(
        """
        SELECT * FROM price_history
        WHERE user_id = :user_id AND niche = :niche
        ORDER BY recorded_at ASC
        """,
        {"user_id": user_id, "niche": niche.strip()},
    )
    return [_row_to_dict(r) for r in rows]


def _best_margin_tier(by_tier: dict) -> tuple[str, str]:
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
    return _best_margin_tier(by_tier)


def get_remaining_for_user(user: dict) -> dict:
    stage1_used = int(user.get("stage1_used", 0))
    stage2_used = int(user.get("stage2_used", 0))
    if user.get("tier") == "pro":
        return {
            "tier": "pro",
            "stage1_limit": "Unlimited",
            "stage2_limit": "Unlimited",
            "stage1_used": stage1_used,
            "stage2_used": stage2_used,
            "stage1_remaining": "Unlimited",
            "stage2_remaining": "Unlimited",
            "unlimited": True,
        }
    limits = get_tier_limits_for_user(user)
    return {
        "tier": user.get("tier", "trial"),
        "stage1_limit": limits["stage1"],
        "stage2_limit": limits["stage2"],
        "stage1_used": stage1_used,
        "stage2_used": stage2_used,
        "stage1_remaining": max(0, limits["stage1"] - stage1_used),
        "stage2_remaining": max(0, limits["stage2"] - stage2_used),
        "unlimited": False,
    }


def can_user_stage1(user: dict, count: int = 1) -> bool:
    if user.get("tier") == "pro":
        return True
    return get_remaining_for_user(user)["stage1_remaining"] >= count


def can_user_stage2(user: dict) -> bool:
    if user.get("tier") == "pro":
        return True
    return get_remaining_for_user(user)["stage2_remaining"] > 0


async def increment_user_stage1(user_id: int, count: int = 1):
    await get_database().execute(
        "UPDATE users SET stage1_used = stage1_used + :count WHERE id = :id",
        {"count": count, "id": user_id},
    )


async def increment_user_stage2(user_id: int, count: int = 1):
    await get_database().execute(
        "UPDATE users SET stage2_used = stage2_used + :count WHERE id = :id",
        {"count": count, "id": user_id},
    )


async def update_user_scrapingbee_key(user_id: int, api_key: str):
    await get_database().execute(
        "UPDATE users SET own_scrapingbee_key = :api_key WHERE id = :id",
        {"api_key": api_key.strip(), "id": user_id},
    )


async def update_user_tier(user_id: int, tier: str):
    if tier not in VALID_TIERS:
        return
    await get_database().execute(
        "UPDATE users SET tier = :tier WHERE id = :id",
        {"tier": tier, "id": user_id},
    )


async def cancel_user_subscription(user_id: int):
    user = await get_user_by_id(user_id)
    if not user:
        return None, "User not found."
    if user.get("tier") == "cancelling":
        return None, "Your subscription is already cancelled."
    if user.get("tier") == "none":
        return None, "You do not have an active subscription to cancel."
    cancelled_from = user.get("tier", "starter")
    now = datetime.utcnow().isoformat()
    await get_database().execute(
        """
        UPDATE users
        SET tier = 'cancelling', cancellation_date = :cancellation_date,
            cancelled_from_tier = :cancelled_from_tier
        WHERE id = :id
        """,
        {
            "cancellation_date": now,
            "cancelled_from_tier": cancelled_from,
            "id": user_id,
        },
    )
    return await get_user_by_id(user_id), None


async def get_cancelled_users(limit: int = 50) -> list[dict]:
    rows = await get_database().fetch_all(
        """
        SELECT id, email, tier, cancelled_from_tier, cancellation_date, created_at
        FROM users
        WHERE tier = 'cancelling' OR cancellation_date != ''
        ORDER BY cancellation_date DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return [_row_to_dict(r) for r in rows]


async def update_user_stripe_ids(user_id: int, customer_id: str, subscription_id: str):
    await get_database().execute(
        """
        UPDATE users
        SET stripe_customer_id = :customer_id, stripe_subscription_id = :subscription_id
        WHERE id = :id
        """,
        {
            "customer_id": customer_id or "",
            "subscription_id": subscription_id or "",
            "id": user_id,
        },
    )


async def get_user_by_stripe_customer_id(customer_id: str):
    if not customer_id:
        return None
    row = await get_database().fetch_one(
        "SELECT * FROM users WHERE stripe_customer_id = :customer_id",
        {"customer_id": customer_id},
    )
    return _row_to_dict(row)


async def check_database_connected() -> bool:
    try:
        await get_database().fetch_one("SELECT 1 AS ok")
        return True
    except Exception:
        return False
