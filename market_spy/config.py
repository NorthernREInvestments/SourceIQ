"""Shared configuration, API keys, tier limits, and user session."""

import json
import os
from datetime import date, datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
REPORTS_DIR = os.path.join(OUTPUT_DIR, "reports")
EXPORTS_DIR = os.path.join(OUTPUT_DIR, "exports")
DEBUG_DIR = os.path.join(OUTPUT_DIR, "debug")
CREDIT_LOG_FILE = os.path.join(OUTPUT_DIR, "credit_log.txt")
USER_SESSION_FILE = os.path.join(PROJECT_ROOT, "user_session.json")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SourceIQ/2.0"}
REQUEST_DELAY = (0.6, 1.2)
RENDER_TIMEOUT = 120_000  # ms
RENDER_WAIT_AFTER = 2000  # ms

REDDIT_SUBREDDITS = [
    "entrepreneur",
    "SideProject",
    "passive_income",
    "dropshipping",
    "ecommerce",
    "smallbusiness",
]

USER_KEY_FIELDS = (
    "REDDIT_USER_CLIENT_ID",
    "REDDIT_USER_SECRET",
    "EBAY_USER_APP_ID",
    "ETSY_USER_API_KEY",
)

VALID_TIERS = ("none", "trial", "starter", "pro")

TIER_LIMITS = {
    "none": {"stage1": 0, "stage2": 0},
    "trial": {"stage1": 10, "stage2": 3},
    "starter": {"stage1": 30, "stage2": 5},
    "pro": {"stage1": 100, "stage2": 25},
}

PRO_OWN_KEY_STAGE2_LIMIT = 50

UPGRADE_URL = "sourceiq.up.railway.app"

STAGE2_UPGRADE_MESSAGE = (
    "You have used all your Stage 2 drill downs this month. "
    "Upgrade to Pro for 25 drill downs and full margin analysis at "
    f"{UPGRADE_URL}"
)

STAGE1_UPGRADE_MESSAGE = (
    "You have used all your Stage 1 searches this month. "
    "Upgrade to Starter or Pro for more category scans at "
    f"{UPGRADE_URL}"
)

EXPORT_UPGRADE_MESSAGE = (
    "CSV export is available on the Pro plan. "
    f"Upgrade at {UPGRADE_URL} to export full margin analysis."
)

STAGE2_CREDITS_PER_DRILLDOWN = 175

DEBUG_PH = False
DEBUG_GUM = False
DEBUG_APPSUMO = False

SCRAPINGBEE_API_KEY = os.getenv("SCRAPINGBEE_API_KEY", "").strip()
USER_SCRAPINGBEE_API_KEY = os.getenv("USER_SCRAPINGBEE_API_KEY", "").strip()


def is_debug_mode() -> bool:
    """True when DEBUG_MODE=true in .env (local debugging only)."""
    return os.getenv("DEBUG_MODE", "").strip().lower() in ("true", "1", "yes")


def has_user_keys():
    """True only if all four user API key fields are set in .env."""
    return all(os.getenv(field, "").strip() for field in USER_KEY_FIELDS)


def has_own_scrapingbee_key():
    """True when the user supplies their own ScrapingBee API key."""
    return bool(USER_SCRAPINGBEE_API_KEY)


def _current_month_key():
    return date.today().strftime("%Y-%m")


def _default_session():
    today = date.today().isoformat()
    tier = os.getenv("SOURCEIQ_TIER", "trial").strip().lower()
    if tier not in VALID_TIERS:
        tier = "trial"
    return {
        "tier": tier,
        "stage1_used_this_month": 0,
        "stage2_used_this_month": 0,
        "trial_start_date": today,
        "own_scrapingbee_key": has_own_scrapingbee_key(),
        "month_key": _current_month_key(),
    }


def _read_user_session_file():
    if not os.path.exists(USER_SESSION_FILE):
        return None
    try:
        with open(USER_SESSION_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _write_user_session_file(data):
    with open(USER_SESSION_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def _reset_monthly_counts_if_needed(session):
    if session.get("month_key") != _current_month_key():
        session["month_key"] = _current_month_key()
        session["stage1_used_this_month"] = 0
        session["stage2_used_this_month"] = 0
    return session


def get_user_session():
    """Load user session from user_session.json, creating defaults if missing."""
    session = _read_user_session_file() or _default_session()
    env_tier = os.getenv("SOURCEIQ_TIER", "").strip().lower()
    if env_tier in VALID_TIERS:
        session["tier"] = env_tier
    session["own_scrapingbee_key"] = has_own_scrapingbee_key()
    session = _reset_monthly_counts_if_needed(session)
    _write_user_session_file(session)
    return session


def save_user_session(session):
    """Persist user session to user_session.json."""
    _write_user_session_file(session)


def get_tier_limits(session=None):
    """Return stage1/stage2 monthly limits for the current tier."""
    session = session or get_user_session()
    tier = session.get("tier", "trial")
    limits = dict(TIER_LIMITS.get(tier, TIER_LIMITS["trial"]))
    if tier == "pro" and session.get("own_scrapingbee_key"):
        limits["stage2"] = PRO_OWN_KEY_STAGE2_LIMIT
    return limits


def get_remaining_searches(session=None):
    """Return Stage 1 and Stage 2 remaining searches for the current tier."""
    session = session or get_user_session()
    limits = get_tier_limits(session)
    stage1_used = int(session.get("stage1_used_this_month", 0))
    stage2_used = int(session.get("stage2_used_this_month", 0))
    return {
        "tier": session.get("tier", "trial"),
        "stage1_limit": limits["stage1"],
        "stage2_limit": limits["stage2"],
        "stage1_used": stage1_used,
        "stage2_used": stage2_used,
        "stage1_remaining": max(0, limits["stage1"] - stage1_used),
        "stage2_remaining": max(0, limits["stage2"] - stage2_used),
        "own_scrapingbee_key": bool(session.get("own_scrapingbee_key")),
        "trial_start_date": session.get("trial_start_date"),
    }


def can_stage1_search(count=1, session=None):
    """Return True if the user has enough Stage 1 searches remaining this month."""
    remaining = get_remaining_searches(session)
    return remaining["stage1_remaining"] >= count


def can_stage2_drilldown(session=None):
    """Return True if the user has Stage 2 drill-downs remaining this month."""
    remaining = get_remaining_searches(session)
    return remaining["stage2_remaining"] > 0


def increment_stage1_count(count=1):
    """Record Stage 1 search usage against the monthly tier limit."""
    session = get_user_session()
    session["stage1_used_this_month"] = int(session.get("stage1_used_this_month", 0)) + count
    save_user_session(session)


def increment_stage2_count(count=1):
    """Record Stage 2 drill-down usage against the monthly tier limit."""
    session = get_user_session()
    session["stage2_used_this_month"] = int(session.get("stage2_used_this_month", 0)) + count
    save_user_session(session)


def is_pro_tier(session=None):
    session = session or get_user_session()
    return session.get("tier") == "pro"


def can_export_csv(session=None):
    """Return (allowed, message). CSV export is Pro tier only."""
    session = session or get_user_session()
    if is_pro_tier(session):
        return True, None
    return False, EXPORT_UPGRADE_MESSAGE


def can_stage2_search():
    """Return (allowed, message) for Stage 2 drill-down searches."""
    if can_stage2_drilldown():
        return True, None
    return False, STAGE2_UPGRADE_MESSAGE


def can_search():
    """Alias for Stage 2 drill-down limit checks."""
    return can_stage2_search()


# Legacy daily counter helpers (deprecated; kept for compatibility)
SEARCH_COUNT_FILE = os.path.join(PROJECT_ROOT, "search_count.json")
STAGE2_DAILY_LIMIT = TIER_LIMITS["pro"]["stage2"]
SEARCH_LIMIT_MESSAGE = STAGE2_UPGRADE_MESSAGE


def get_daily_search_count():
    return get_remaining_searches()["stage2_used"]


def increment_search_count():
    increment_stage2_count(1)
