"""User storage for SourceIQ — PostgreSQL in production, SQLite for local dev."""

import json
import os
from collections.abc import Callable
from datetime import date, datetime, timedelta

import bcrypt
from databases import Database

from market_spy.config import PAID_TIERS, PRO_OWN_KEY_STAGE2_LIMIT, TIER_LIMITS, VALID_TIERS, is_test_account_email
from market_spy.web.json_util import dumps_json_safe

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

_STAGE1_RESULT_CACHE = """
CREATE TABLE IF NOT EXISTS stage1_result_cache (
    id {id_col},
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_DRILLDOWN_JOBS = """
CREATE TABLE IF NOT EXISTS drilldown_jobs (
    id {id_col},
    user_id INTEGER NOT NULL,
    niche TEXT NOT NULL,
    parent_category TEXT NOT NULL DEFAULT '',
    return_to TEXT NOT NULL DEFAULT '/dashboard',
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    stage2_credited INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_TRENDS_CACHE = """
CREATE TABLE IF NOT EXISTS trends_cache (
    id {id_col},
    search_term TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    found INTEGER NOT NULL DEFAULT 0,
    direction TEXT NOT NULL DEFAULT 'stable',
    change_val REAL NOT NULL DEFAULT 0,
    series_json TEXT NOT NULL DEFAULT '[]',
    cached_at TEXT NOT NULL,
    UNIQUE(search_term, timeframe)
);
"""

_PRODUCTS = """
CREATE TABLE IF NOT EXISTS products (
    id {id_col},
    niche TEXT NOT NULL,
    subcategory TEXT NOT NULL DEFAULT '',
    product_group TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    selling_price_min REAL,
    selling_price_max REAL,
    selling_price_avg REAL,
    selling_platform TEXT,
    sale_date TEXT,
    source_price_min REAL,
    source_price_max REAL,
    source_platform TEXT,
    source_verified_date TEXT,
    source_in_stock INTEGER,
    margin_pct REAL,
    margin_tier TEXT,
    trend_24h TEXT,
    trend_7d TEXT,
    trend_30d TEXT,
    trend_updated TEXT,
    opportunity_score REAL,
    created_at TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    product_url TEXT NOT NULL DEFAULT ''
);
"""

_NICHE_QUEUE = """
CREATE TABLE IF NOT EXISTS niche_queue (
    id {id_col},
    niche TEXT NOT NULL UNIQUE,
    last_scraped TEXT,
    scrape_count INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 5,
    added_by TEXT NOT NULL DEFAULT 'system',
    needs_sourcing_refresh INTEGER NOT NULL DEFAULT 0,
    sourcing_last_refreshed TEXT
);
"""

_SCRAPE_LOG = """
CREATE TABLE IF NOT EXISTS scrape_log (
    id {id_col},
    niche TEXT NOT NULL,
    scrape_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    products_added INTEGER NOT NULL DEFAULT 0,
    products_updated INTEGER NOT NULL DEFAULT 0,
    credits_used INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    error_message TEXT NOT NULL DEFAULT '',
    progress_message TEXT NOT NULL DEFAULT ''
);
"""

_CREDIT_EVENTS = """
CREATE TABLE IF NOT EXISTS credit_events (
    id {id_col},
    used_at TEXT NOT NULL,
    billing_month TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    credits INTEGER NOT NULL DEFAULT 0
);
"""

_APP_META = """
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

_PRODUCT_FILL_FAILURES = """
CREATE TABLE IF NOT EXISTS product_fill_failures (
    product_id INTEGER NOT NULL,
    job_type TEXT NOT NULL DEFAULT 'fill_missing_sources',
    error_message TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 1,
    last_attempt TEXT NOT NULL,
    PRIMARY KEY (product_id, job_type)
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
    tier = user.get("tier", "none")
    if tier == "cancelling":
        tier = user.get("cancelled_from_tier") or "subscriber"
    if tier in PAID_TIERS or tier == "subscriber":
        return "subscriber"
    if tier == "trial":
        return "none"
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


async def _migrate_scrape_log() -> None:
    db = get_database()
    if uses_postgres():
        await db.execute(
            "ALTER TABLE scrape_log ADD COLUMN IF NOT EXISTS progress_message TEXT NOT NULL DEFAULT ''"
        )
    else:
        rows = await db.fetch_all("PRAGMA table_info(scrape_log)")
        cols = {row["name"] for row in rows} if rows else set()
        if "progress_message" not in cols:
            await db.execute(
                "ALTER TABLE scrape_log ADD COLUMN progress_message TEXT NOT NULL DEFAULT ''"
            )


async def _migrate_products() -> None:
    db = get_database()
    if uses_postgres():
        await db.execute(
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS source_in_stock INTEGER"
        )
        await db.execute(
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS platform_prices TEXT NOT NULL DEFAULT ''"
        )
    else:
        rows = await db.fetch_all("PRAGMA table_info(products)")
        cols = {row["name"] for row in rows} if rows else set()
        if "source_in_stock" not in cols:
            await db.execute("ALTER TABLE products ADD COLUMN source_in_stock INTEGER")
        if "platform_prices" not in cols:
            await db.execute(
                "ALTER TABLE products ADD COLUMN platform_prices TEXT NOT NULL DEFAULT ''"
            )


async def init_db() -> None:
    await connect_db()
    db = get_database()
    id_col = "SERIAL PRIMARY KEY" if uses_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    if uses_postgres():
        await db.execute(_POSTGRES_USERS)
    else:
        await db.execute(_SQLITE_USERS)
    for template in (
        _SEARCH_HISTORY,
        _WATCHLIST,
        _PRICE_HISTORY,
        _QUICK_START_JOBS,
        _STAGE1_RESULT_CACHE,
        _DRILLDOWN_JOBS,
        _TRENDS_CACHE,
        _PRODUCTS,
        _NICHE_QUEUE,
        _SCRAPE_LOG,
        _CREDIT_EVENTS,
        _APP_META,
        _PRODUCT_FILL_FAILURES,
    ):
        await db.execute(template.format(id_col=id_col))
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_product_fill_failures_retry "
        "ON product_fill_failures (job_type, last_attempt)"
    )
    if uses_postgres():
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_niche_url "
            "ON products (niche, product_url)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_credit_events_month "
            "ON credit_events (billing_month)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_credit_events_used_at "
            "ON credit_events (used_at)"
        )
    else:
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_niche_url "
            "ON products (niche, product_url)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_credit_events_month "
            "ON credit_events (billing_month)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_credit_events_used_at "
            "ON credit_events (used_at)"
        )
    await _migrate_products()
    await _migrate_scrape_log()
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
    return await create_user_with_tier(email, password, "none")


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
    if limits_tier == "subscriber" and user.get("own_scrapingbee_key"):
        limits["stage2"] = PRO_OWN_KEY_STAGE2_LIMIT
    return limits


def is_pro_user(user: dict) -> bool:
    """True when the user has an active paid subscription."""
    tier = user.get("tier", "none")
    if tier == "cancelling":
        return True
    return _effective_tier(user) == "subscriber"


def is_subscribed_user(user: dict) -> bool:
    return is_pro_user(user)


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


async def user_has_completed_search(user_id: int) -> bool:
    """True if the user has run any Stage 1/2 search or completed Quick Start."""
    user = await get_user_by_id(user_id)
    if not user:
        return False
    if int(user.get("stage1_used", 0)) + int(user.get("stage2_used", 0)) > 0:
        return True
    db = get_database()
    row = await db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM quick_start_jobs
        WHERE user_id = :user_id AND status = 'completed' AND completed > 0
        """,
        {"user_id": user_id},
    )
    if row and int(row["c"]) > 0:
        return True
    row = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM search_history WHERE user_id = :user_id",
        {"user_id": user_id},
    )
    return bool(row and int(row["c"]) > 0)


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
    if is_test_account_email(user.get("email")):
        return {
            "tier": user.get("tier", "trial"),
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
    if is_test_account_email(user.get("email")):
        return True
    return get_remaining_for_user(user)["stage1_remaining"] >= count


def can_user_stage2(user: dict) -> bool:
    if is_test_account_email(user.get("email")):
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


async def get_public_stats() -> dict:
    """Stats for dashboard — only real counts from the database."""
    db = get_database()
    product_count = 0
    try:
        product_count = int(await db.fetch_val("SELECT COUNT(*) FROM products") or 0)
    except Exception:
        product_count = 0
    niche_count = 0
    try:
        niche_count = int(await db.fetch_val("SELECT COUNT(DISTINCT niche) FROM products") or 0)
    except Exception:
        niche_count = 0
    if niche_count == 0:
        try:
            niche_count = int(await db.fetch_val("SELECT COUNT(*) FROM niche_queue") or 0)
        except Exception:
            niche_count = 0
    hours_ago = None
    last_updated = await db.fetch_val("SELECT MAX(last_updated) FROM products")
    if not last_updated:
        last_updated = await db.fetch_val("SELECT MAX(completed_at) FROM scrape_log WHERE status = 'completed'")
    if last_updated:
        try:
            updated = datetime.fromisoformat(str(last_updated).replace("Z", "+00:00"))
            delta = datetime.utcnow() - updated.replace(tzinfo=None)
            hours_ago = max(1, int(delta.total_seconds() // 3600) or 1)
        except (TypeError, ValueError):
            hours_ago = None
    return {
        "product_count": product_count,
        "niche_count": niche_count,
        "hours_ago": hours_ago,
    }


async def save_stage1_result(user_id: int, category: str, result: dict) -> int:
    now = datetime.utcnow().isoformat()
    row = await get_database().fetch_one(
        """
        INSERT INTO stage1_result_cache (user_id, category, result_json, created_at)
        VALUES (:user_id, :category, :result_json, :created_at)
        RETURNING id
        """,
        {
            "user_id": user_id,
            "category": category,
            "result_json": dumps_json_safe(result),
            "created_at": now,
        },
    )
    return int(row["id"])


async def get_stage1_result(user_id: int, result_id: int) -> dict | None:
    row = await get_database().fetch_one(
        """
        SELECT result_json FROM stage1_result_cache
        WHERE id = :id AND user_id = :user_id
        """,
        {"id": result_id, "user_id": user_id},
    )
    if not row:
        return None
    try:
        data = json.loads(row["result_json"] or "{}")
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


async def ensure_niche_in_queue(niche: str, *, added_by: str = "system", priority: int = 5) -> None:
    niche = niche.strip()
    if not niche:
        return
    db = get_database()
    existing = await db.fetch_one(
        "SELECT id FROM niche_queue WHERE LOWER(niche) = LOWER(:niche)",
        {"niche": niche},
    )
    if existing:
        return
    await db.execute(
        """
        INSERT INTO niche_queue (niche, priority, added_by)
        VALUES (:niche, :priority, :added_by)
        """,
        {"niche": niche, "priority": priority, "added_by": added_by},
    )


async def create_scrape_log(niche: str, scrape_type: str) -> int:
    now = _now_iso()
    row = await get_database().fetch_one(
        """
        INSERT INTO scrape_log (niche, scrape_type, started_at, status)
        VALUES (:niche, :scrape_type, :started_at, 'running')
        RETURNING id
        """,
        {"niche": niche, "scrape_type": scrape_type, "started_at": now},
    )
    return int(row["id"])


async def finish_scrape_log(
    log_id: int,
    *,
    status: str,
    products_added: int = 0,
    products_updated: int = 0,
    credits_used: int = 0,
    error_message: str = "",
) -> None:
    await get_database().execute(
        """
        UPDATE scrape_log
        SET status = :status,
            completed_at = :completed_at,
            products_added = :products_added,
            products_updated = :products_updated,
            credits_used = :credits_used,
            error_message = :error_message
        WHERE id = :id
        """,
        {
            "id": log_id,
            "status": status,
            "completed_at": _now_iso(),
            "products_added": products_added,
            "products_updated": products_updated,
            "credits_used": credits_used,
            "error_message": error_message,
        },
    )


async def update_scrape_log_credits(log_id: int, credits_used: int) -> None:
    await get_database().execute(
        "UPDATE scrape_log SET credits_used = :credits_used WHERE id = :id AND status = 'running'",
        {"id": log_id, "credits_used": credits_used},
    )


async def update_scrape_log_progress(log_id: int, progress_message: str) -> None:
    await get_database().execute(
        """
        UPDATE scrape_log
        SET progress_message = :progress_message
        WHERE id = :id AND status = 'running'
        """,
        {"id": log_id, "progress_message": progress_message[:500]},
    )


async def update_scrape_log_stats(
    log_id: int,
    *,
    products_updated: int | None = None,
    credits_used: int | None = None,
) -> None:
    """Persist live fill/scrape counters while a log row is still running."""
    fields = []
    values: dict = {"id": log_id}
    if products_updated is not None:
        fields.append("products_updated = :products_updated")
        values["products_updated"] = products_updated
    if credits_used is not None:
        fields.append("credits_used = :credits_used")
        values["credits_used"] = credits_used
    if not fields:
        return
    await get_database().execute(
        f"""
        UPDATE scrape_log
        SET {", ".join(fields)}
        WHERE id = :id AND status = 'running'
        """,
        values,
    )


async def reap_stale_scrape_logs(
    *,
    scrape_type: str | None = None,
    max_age_minutes: int = 60,
) -> int:
    """
    Mark old 'running' scrape logs as failed when no worker is active.
    Prevents the admin UI from staying locked after a crash.
    """
    cutoff = (datetime.utcnow() - timedelta(minutes=max_age_minutes)).isoformat()
    now = datetime.utcnow().isoformat()
    db = get_database()
    if scrape_type:
        rows = await db.fetch_all(
            """
            SELECT id, products_updated, progress_message
            FROM scrape_log
            WHERE status = 'running'
              AND scrape_type = :scrape_type
              AND started_at < :cutoff
            """,
            {"scrape_type": scrape_type, "cutoff": cutoff},
        )
    else:
        rows = await db.fetch_all(
            """
            SELECT id, products_updated, progress_message
            FROM scrape_log
            WHERE status = 'running' AND started_at < :cutoff
            """,
            {"cutoff": cutoff},
        )
    cleared = 0
    for row in rows:
        updated = int(row.get("products_updated") or 0)
        progress = (row.get("progress_message") or "").strip()
        if updated > 0 or progress.lower().startswith(("filling sources", "completed")):
            status = "completed"
            error_message = (
                "Worker ended without marking complete — progress was saved (auto-closed)"
            )
        else:
            status = "failed"
            error_message = "Stale — worker ended without marking complete (auto-cleared)"
        await db.execute(
            """
            UPDATE scrape_log
            SET status = :status,
                completed_at = :now,
                error_message = :error_message
            WHERE id = :id AND status = 'running'
            """,
            {
                "id": row["id"],
                "now": now,
                "status": status,
                "error_message": error_message,
            },
        )
        cleared += 1
    return cleared


async def get_initial_scrape_stats_today() -> dict:
    """Summary of today's initial-scrape log rows for the admin dashboard."""
    today = date.today().isoformat()
    db = get_database()
    rows = await db.fetch_all(
        """
        SELECT status,
               COUNT(*) AS runs,
               COALESCE(SUM(products_added), 0) AS products_added,
               COALESCE(SUM(credits_used), 0) AS credits_used
        FROM scrape_log
        WHERE scrape_type = 'initial' AND started_at LIKE :prefix
        GROUP BY status
        """,
        {"prefix": f"{today}%"},
    )
    summary = {
        "completed": 0,
        "failed": 0,
        "running": 0,
        "cancelled": 0,
        "products_added": 0,
        "credits_used": 0,
        "total_runs": 0,
        "succeeded": 0,
        "products_in_db": await count_products(),
        "niches_in_db": await count_product_niches(),
    }
    for row in rows:
        status = row["status"]
        runs = int(row["runs"] or 0)
        if status in summary:
            summary[status] = runs
        summary["products_added"] += int(row["products_added"] or 0)
        summary["credits_used"] += int(row["credits_used"] or 0)
        summary["total_runs"] += runs
    succeeded = await db.fetch_val(
        """
        SELECT COUNT(*) FROM scrape_log
        WHERE scrape_type = 'initial'
          AND started_at LIKE :prefix
          AND (status = 'completed' OR products_added > 0)
        """,
        {"prefix": f"{today}%"},
    )
    summary["succeeded"] = int(succeeded or 0)
    return summary


async def had_manual_scrape_today() -> bool:
    """True if admin initial scrape or user live search scrape started today (UTC)."""
    today = date.today().isoformat()
    count = await get_database().fetch_val(
        """
        SELECT COUNT(*) FROM scrape_log
        WHERE scrape_type IN ('initial', 'user_triggered')
          AND started_at LIKE :prefix
        """,
        {"prefix": f"{today}%"},
    )
    return int(count or 0) > 0


async def get_scrape_log(log_id: int) -> dict | None:
    row = await get_database().fetch_one(
        "SELECT * FROM scrape_log WHERE id = :id",
        {"id": log_id},
    )
    return _row_to_dict(row)


async def get_recent_scrape_logs(limit: int = 10) -> list[dict]:
    rows = await get_database().fetch_all(
        """
        SELECT * FROM scrape_log
        ORDER BY started_at DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return [_row_to_dict(row) for row in rows]


async def get_running_scrape_logs(limit: int = 50) -> list[dict]:
    rows = await get_database().fetch_all(
        """
        SELECT * FROM scrape_log
        WHERE status = 'running'
        ORDER BY started_at DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return [_row_to_dict(row) for row in rows]


async def cancel_scrape_log(log_id: int, *, reason: str = "Cancelled by admin") -> bool:
    row = await get_scrape_log(log_id)
    if not row or row.get("status") != "running":
        return False
    await finish_scrape_log(
        log_id,
        status="cancelled",
        error_message=reason,
    )
    return True


async def count_products() -> int:
    try:
        return int(await get_database().fetch_val("SELECT COUNT(*) FROM products") or 0)
    except Exception:
        return 0


async def count_product_niches() -> int:
    try:
        return int(
            await get_database().fetch_val("SELECT COUNT(DISTINCT niche) FROM products") or 0
        )
    except Exception:
        return 0


async def get_last_scrape_info() -> dict | None:
    row = await get_database().fetch_one(
        """
        SELECT niche, scrape_type, completed_at, status, products_added, error_message
        FROM scrape_log
        WHERE status IN ('completed', 'failed', 'cancelled')
          AND completed_at IS NOT NULL
        ORDER BY completed_at DESC
        LIMIT 1
        """
    )
    return _row_to_dict(row)


async def mark_niche_scraped(niche: str) -> None:
    now = _now_iso()
    await get_database().execute(
        """
        UPDATE niche_queue
        SET last_scraped = :now,
            scrape_count = scrape_count + 1,
            needs_sourcing_refresh = 0
        WHERE LOWER(niche) = LOWER(:niche)
        """,
        {"now": now, "niche": niche},
    )


async def mark_niche_sourcing_refreshed(niche: str) -> None:
    now = _now_iso()
    await get_database().execute(
        """
        UPDATE niche_queue
        SET sourcing_last_refreshed = :now,
            needs_sourcing_refresh = 0
        WHERE LOWER(niche) = LOWER(:niche)
        """,
        {"now": now, "niche": niche},
    )


async def set_niche_needs_sourcing_refresh(niche: str) -> None:
    await get_database().execute(
        """
        UPDATE niche_queue
        SET needs_sourcing_refresh = 1
        WHERE LOWER(niche) = LOWER(:niche)
        """,
        {"niche": niche},
    )


async def get_niches_needing_sourcing_refresh(limit: int = 10) -> list[str]:
    rows = await get_database().fetch_all(
        """
        SELECT niche FROM niche_queue
        WHERE needs_sourcing_refresh = 1
        ORDER BY priority DESC, niche ASC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return [row["niche"] for row in rows]


async def get_unscraped_niches(
    limit: int = 10,
    *,
    added_by: str | None = None,
    exclude_added_by: str | None = None,
) -> list[str]:
    clauses = ["(last_scraped IS NULL OR last_scraped = '')"]
    params: dict = {"limit": limit}
    if added_by:
        clauses.append("added_by = :added_by")
        params["added_by"] = added_by
    if exclude_added_by:
        clauses.append("added_by != :exclude_added_by")
        params["exclude_added_by"] = exclude_added_by
    where = " AND ".join(clauses)
    rows = await get_database().fetch_all(
        f"""
        SELECT niche FROM niche_queue
        WHERE {where}
        ORDER BY priority DESC, niche ASC
        LIMIT :limit
        """,
        params,
    )
    return [row["niche"] for row in rows]


async def count_unscraped_niches() -> int:
    return int(
        await get_database().fetch_val(
            """
            SELECT COUNT(*) FROM niche_queue
            WHERE last_scraped IS NULL OR last_scraped = ''
            """
        )
        or 0
    )


async def count_scraped_niches() -> int:
    return int(
        await get_database().fetch_val(
            """
            SELECT COUNT(*) FROM niche_queue
            WHERE last_scraped IS NOT NULL AND last_scraped != ''
            """
        )
        or 0
    )


async def fetch_scraped_niche_names() -> list[str]:
    rows = await get_database().fetch_all(
        """
        SELECT niche FROM niche_queue
        WHERE last_scraped IS NOT NULL AND last_scraped != ''
        ORDER BY niche ASC
        """
    )
    return [row["niche"] for row in rows]


async def get_niches_for_expansion(limit: int = 10) -> list[str]:
    """Niches already scraped — least recently scraped first for another full pass."""
    rows = await get_database().fetch_all(
        """
        SELECT niche FROM niche_queue
        WHERE last_scraped IS NOT NULL AND last_scraped != ''
        ORDER BY last_scraped ASC, scrape_count ASC, priority DESC, niche ASC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return [row["niche"] for row in rows]


async def fetch_products_for_niche(niche: str) -> list[dict]:
    rows = await get_database().fetch_all(
        "SELECT * FROM products WHERE LOWER(niche) = LOWER(:niche)",
        {"niche": niche},
    )
    return [_row_to_dict(row) for row in rows]


_CATALOG_SORT_SQL = {
    "margin": "margin_pct IS NULL, margin_pct DESC, id DESC",
    "price": "selling_price_avg IS NULL, selling_price_avg DESC, id DESC",
    "name": "name ASC, id ASC",
    "trend": (
        "CASE LOWER(COALESCE(trend_30d, 'stable')) "
        "WHEN 'rising' THEN 0 WHEN 'stable' THEN 1 ELSE 2 END, "
        "margin_pct DESC, id DESC"
    ),
}


def _catalog_search_clause(query: str | None) -> tuple[str, dict]:
    text = (query or "").strip()
    if not text:
        return "1=1", {}
    like = f"%{text.lower()}%"
    clause = """
        (LOWER(niche) LIKE :like
         OR LOWER(subcategory) LIKE :like
         OR LOWER(product_group) LIKE :like
         OR LOWER(name) LIKE :like)
    """
    return clause, {"like": like}


async def count_products_catalog(*, query: str | None = None) -> int:
    where, params = _catalog_search_clause(query)
    try:
        return int(
            await get_database().fetch_val(
                f"SELECT COUNT(*) FROM products WHERE {where}",
                params,
            )
            or 0
        )
    except Exception:
        return 0


async def fetch_products_catalog(
    *,
    query: str | None = None,
    sort: str = "margin",
    offset: int = 0,
    limit: int = 50,
) -> list[dict]:
    where, params = _catalog_search_clause(query)
    order = _CATALOG_SORT_SQL.get(sort) or _CATALOG_SORT_SQL["margin"]
    params["limit"] = limit
    params["offset"] = offset
    rows = await get_database().fetch_all(
        f"""
        SELECT * FROM products
        WHERE {where}
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
        """,
        params,
    )
    return [_row_to_dict(row) for row in rows]


async def fetch_all_product_niches() -> list[str]:
    rows = await get_database().fetch_all(
        "SELECT DISTINCT niche FROM products ORDER BY niche"
    )
    return [row["niche"] for row in rows]


async def fetch_all_products() -> list[dict]:
    rows = await get_database().fetch_all(
        "SELECT * FROM products ORDER BY niche, name"
    )
    return [_row_to_dict(row) for row in rows]


async def get_app_meta(key: str) -> str | None:
    row = await get_database().fetch_one(
        "SELECT value FROM app_meta WHERE key = :key",
        {"key": key},
    )
    return row["value"] if row else None


async def set_app_meta(key: str, value: str) -> None:
    if uses_postgres():
        await get_database().execute(
            """
            INSERT INTO app_meta (key, value) VALUES (:key, :value)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            {"key": key, "value": value},
        )
    else:
        await get_database().execute(
            """
            INSERT INTO app_meta (key, value) VALUES (:key, :value)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            {"key": key, "value": value},
        )


FILL_MISSING_JOB_TYPE = "fill_missing_sources"


async def count_product_fill_failures(
    job_type: str = FILL_MISSING_JOB_TYPE,
    max_attempts: int | None = None,
) -> int:
    if max_attempts is None:
        count = await get_database().fetch_val(
            """
            SELECT COUNT(*) FROM product_fill_failures
            WHERE job_type = :job_type
            """,
            {"job_type": job_type},
        )
    else:
        count = await get_database().fetch_val(
            """
            SELECT COUNT(*) FROM product_fill_failures
            WHERE job_type = :job_type AND attempts < :max_attempts
            """,
            {"job_type": job_type, "max_attempts": max_attempts},
        )
    return int(count or 0)


async def record_product_fill_failure(
    product_id: int,
    error_message: str,
    *,
    job_type: str = FILL_MISSING_JOB_TYPE,
) -> None:
    now = datetime.utcnow().isoformat()
    if uses_postgres():
        await get_database().execute(
            """
            INSERT INTO product_fill_failures (
                product_id, job_type, error_message, attempts, last_attempt
            ) VALUES (
                :product_id, :job_type, :error_message, 1, :last_attempt
            )
            ON CONFLICT (product_id, job_type) DO UPDATE SET
                error_message = EXCLUDED.error_message,
                attempts = product_fill_failures.attempts + 1,
                last_attempt = EXCLUDED.last_attempt
            """,
            {
                "product_id": product_id,
                "job_type": job_type,
                "error_message": (error_message or "")[:2000],
                "last_attempt": now,
            },
        )
    else:
        await get_database().execute(
            """
            INSERT INTO product_fill_failures (
                product_id, job_type, error_message, attempts, last_attempt
            ) VALUES (
                :product_id, :job_type, :error_message, 1, :last_attempt
            )
            ON CONFLICT (product_id, job_type) DO UPDATE SET
                error_message = excluded.error_message,
                attempts = attempts + 1,
                last_attempt = excluded.last_attempt
            """,
            {
                "product_id": product_id,
                "job_type": job_type,
                "error_message": (error_message or "")[:2000],
                "last_attempt": now,
            },
        )


async def clear_product_fill_failure(
    product_id: int,
    *,
    job_type: str = FILL_MISSING_JOB_TYPE,
) -> None:
    await get_database().execute(
        """
        DELETE FROM product_fill_failures
        WHERE product_id = :product_id AND job_type = :job_type
        """,
        {"product_id": product_id, "job_type": job_type},
    )


async def fetch_product_fill_retry_batch(
    limit: int,
    *,
    job_type: str = FILL_MISSING_JOB_TYPE,
    max_attempts: int = 5,
) -> list[dict]:
    rows = await get_database().fetch_all(
        """
        SELECT p.*
        FROM products p
        INNER JOIN product_fill_failures f ON f.product_id = p.id
        WHERE f.job_type = :job_type AND f.attempts < :max_attempts
        ORDER BY f.last_attempt ASC
        LIMIT :limit
        """,
        {"job_type": job_type, "max_attempts": max_attempts, "limit": limit},
    )
    return [_row_to_dict(row) for row in rows]


async def fetch_products_batch_after_id(
    last_id: int,
    limit: int,
    *,
    exclude_fill_failures: bool = False,
    job_type: str = FILL_MISSING_JOB_TYPE,
    max_attempts: int = 5,
) -> list[dict]:
    if exclude_fill_failures:
        rows = await get_database().fetch_all(
            """
            SELECT p.*
            FROM products p
            WHERE p.id > :last_id
              AND NOT EXISTS (
                SELECT 1 FROM product_fill_failures f
                WHERE f.product_id = p.id
                  AND f.job_type = :job_type
                  AND f.attempts < :max_attempts
              )
            ORDER BY p.id ASC
            LIMIT :limit
            """,
            {
                "last_id": last_id,
                "limit": limit,
                "job_type": job_type,
                "max_attempts": max_attempts,
            },
        )
    else:
        rows = await get_database().fetch_all(
            """
            SELECT * FROM products
            WHERE id > :last_id
            ORDER BY id ASC
            LIMIT :limit
            """,
            {"last_id": last_id, "limit": limit},
        )
    return [_row_to_dict(row) for row in rows]


async def fetch_max_product_id() -> int:
    value = await get_database().fetch_val("SELECT MAX(id) FROM products")
    return int(value or 0)

