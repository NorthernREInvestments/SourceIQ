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
    tier = user.get("tier", "none")
    billing_tier = tier
    if tier == "cancelling":
        billing_tier = user.get("cancelled_from_tier") or "subscriber"
    created = date.fromisoformat(user["created_at"][:10])
    if billing_tier in ("none", "trial"):
        return created.isoformat()
    return (created + timedelta(days=30)).isoformat()


def can_user_export_csv(user: dict) -> tuple[bool, str | None]:
    from market_spy.web.database import is_subscribed_user

    if is_subscribed_user(user):
        return True, None
    return False, EXPORT_UPGRADE_MESSAGE
