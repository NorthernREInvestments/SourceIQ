"""Ensure default test accounts exist after deploy (Railway ephemeral filesystem)."""

import os

from market_spy.web.database import create_user_with_tier, get_user_by_email

DEFAULT_TEST_EMAIL = "test@sourceiq.app"
DEFAULT_TEST_PASSWORD = "Test1234"
DEFAULT_ADMIN_EMAIL = "admin@sourceiq.app"


def ensure_default_accounts() -> None:
    """Create default test accounts if they are missing from the database."""
    _ensure_account(DEFAULT_TEST_EMAIL, DEFAULT_TEST_PASSWORD, "pro")

    admin_password = os.getenv("ADMIN_PASSWORD", "").strip()
    if admin_password:
        _ensure_account(DEFAULT_ADMIN_EMAIL, admin_password, "pro")
    else:
        print(
            "[startup] Skipping admin@sourceiq.app seed — ADMIN_PASSWORD not set.",
            flush=True,
        )


def _ensure_account(email: str, password: str, tier: str) -> None:
    if get_user_by_email(email):
        return
    user, error = create_user_with_tier(email, password, tier)
    if user:
        print(f"[startup] Created default account: {email} (tier={tier})", flush=True)
        return
    print(f"[startup] Could not create {email}: {error}", flush=True)
