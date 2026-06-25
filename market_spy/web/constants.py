"""Shared copy and helpers for web templates."""

from datetime import date, timedelta

from market_spy.cli import SEARCH_TIP, STAGE1_DISCLAIMER, STAGE2_DISCLAIMER
from market_spy.config import EXPORT_UPGRADE_MESSAGE, UPGRADE_URL

__all__ = [
    "SEARCH_TIP",
    "STAGE1_DISCLAIMER",
    "STAGE2_DISCLAIMER",
    "EXPORT_UPGRADE_MESSAGE",
    "UPGRADE_URL",
    "renewal_date_for_user",
    "can_user_export_csv",
]


def renewal_date_for_user(user: dict) -> str:
    tier = user.get("tier", "trial")
    billing_tier = tier
    if tier == "cancelling":
        billing_tier = user.get("cancelled_from_tier") or "starter"
    start = date.fromisoformat(user["trial_start_date"])
    if billing_tier == "trial":
        return (start + timedelta(days=7)).isoformat()
    created = date.fromisoformat(user["created_at"][:10])
    return (created + timedelta(days=30)).isoformat()


def can_user_export_csv(user: dict) -> tuple[bool, str | None]:
    tier = user.get("tier", "trial")
    if tier == "cancelling":
        tier = user.get("cancelled_from_tier") or "starter"
    if tier == "pro":
        return True, None
    return False, EXPORT_UPGRADE_MESSAGE
