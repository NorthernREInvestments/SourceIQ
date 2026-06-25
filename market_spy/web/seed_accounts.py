"""Ensure default test accounts exist after deploy (Railway ephemeral filesystem)."""

import os

from market_spy.config import TEST_ACCOUNT_EMAIL
from market_spy.web.database import create_user_with_tier, get_user_by_email

DEFAULT_TEST_PASSWORD = "Test1234"
DEFAULT_ADMIN_EMAIL = "admin@sourceiq.app"


async def ensure_default_accounts() -> None:
    """Create default test accounts if they are missing from the database."""
    await _ensure_account(TEST_ACCOUNT_EMAIL, DEFAULT_TEST_PASSWORD, "subscriber")

    admin_password = os.getenv("ADMIN_PASSWORD", "").strip()
    if admin_password:
        await _ensure_account(DEFAULT_ADMIN_EMAIL, admin_password, "subscriber")
    else:
        print(
            "[startup] Skipping admin@sourceiq.app seed — ADMIN_PASSWORD not set.",
            flush=True,
        )


async def _ensure_account(email: str, password: str, tier: str) -> None:
    if await get_user_by_email(email):
        return
    user, error = await create_user_with_tier(email, password, tier)
    if user:
        print(f"[startup] Created default account: {email} (tier={tier})", flush=True)
        return
    print(f"[startup] Could not create {email}: {error}", flush=True)
