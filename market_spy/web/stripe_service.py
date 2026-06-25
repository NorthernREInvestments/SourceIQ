"""Stripe checkout and webhook handling for SourceIQ subscriptions."""

import os

import stripe

from market_spy.web.database import (
    get_user_by_id,
    get_user_by_stripe_customer_id,
    update_user_stripe_ids,
    update_user_tier,
)
from market_spy.web.email_service import send_subscription_receipt

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_PRICE_ID = (
    os.getenv("STRIPE_PRICE_ID", "").strip()
    or os.getenv("STARTER_PRICE_ID", "").strip()
    or os.getenv("PRO_PRICE_ID", "").strip()
)
STRIPE_TEST_PRICE_ID = os.getenv("STRIPE_TEST_PRICE_ID", "").strip()
# Backward compatibility for older env files.
STARTER_PRICE_ID = STRIPE_PRICE_ID
PRO_PRICE_ID = os.getenv("PRO_PRICE_ID", "").strip()


def is_stripe_test_mode() -> bool:
    return os.getenv("STRIPE_TEST_MODE", "").strip().lower() in ("true", "1", "yes")


def active_stripe_price_id() -> str:
    if is_stripe_test_mode():
        return STRIPE_TEST_PRICE_ID or STRIPE_PRICE_ID
    return STRIPE_PRICE_ID


def subscription_price_id() -> str:
    return active_stripe_price_id()


def price_id_for_plan(plan: str, *, test_mode: bool | None = None) -> str | None:
    _ = plan
    if test_mode is True or (test_mode is None and is_stripe_test_mode()):
        price_id = STRIPE_TEST_PRICE_ID or STRIPE_PRICE_ID
    else:
        price_id = STRIPE_PRICE_ID
    return price_id or None


def tier_for_price_id(price_id: str) -> str | None:
    if not price_id:
        return None
    if price_id in (STRIPE_PRICE_ID, STRIPE_TEST_PRICE_ID):
        return "subscriber"
    if price_id == PRO_PRICE_ID and PRO_PRICE_ID:
        return "subscriber"
    return None


def _base_url(request) -> str:
    configured = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def create_checkout_session(
    request, user: dict, plan: str, *, test_mode: bool = False
) -> stripe.checkout.Session:
    price_id = price_id_for_plan(plan, test_mode=test_mode)
    if not price_id:
        missing = "STRIPE_TEST_PRICE_ID" if test_mode else "STRIPE_PRICE_ID"
        raise ValueError(f"Stripe is not configured ({missing} missing)")
    if not stripe.api_key:
        raise ValueError("Stripe is not configured (STRIPE_SECRET_KEY missing)")
    if test_mode and stripe.api_key.startswith("sk_live_"):
        raise ValueError(
            "STRIPE_TEST_MODE is enabled but STRIPE_SECRET_KEY is a live key — use sk_test_..."
        )

    base = _base_url(request)
    return stripe.checkout.Session.create(
        mode="subscription",
        customer_email=user["email"],
        client_reference_id=str(user["id"]),
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{base}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}/cancel",
        metadata={
            "user_id": str(user["id"]),
            "plan": "subscriber",
            "stripe_test_mode": "true" if test_mode else "false",
        },
    )


async def handle_checkout_success(session_id: str) -> dict | None:
    """Retrieve a completed checkout session and activate the subscription."""
    if not stripe.api_key or not session_id:
        return None

    session = stripe.checkout.Session.retrieve(
        session_id,
        expand=["line_items", "subscription"],
    )
    if session.payment_status != "paid" and session.status != "complete":
        return None

    user_id = session.client_reference_id or (session.metadata or {}).get("user_id")
    if not user_id:
        return None

    user = await get_user_by_id(int(user_id))
    if not user:
        return None

    tier = (session.metadata or {}).get("plan") or "subscriber"
    if tier not in ("subscriber", "starter", "pro"):
        if session.line_items and session.line_items.data:
            item = session.line_items.data[0]
            price_id = item.price.id if item.price else None
            tier = tier_for_price_id(price_id) or "subscriber"
        else:
            tier = "subscriber"

    customer_id = session.customer or ""
    subscription_id = session.subscription
    if isinstance(subscription_id, stripe.Subscription):
        subscription_id = subscription_id.id

    await update_user_tier(user["id"], "subscriber")
    await update_user_stripe_ids(user["id"], str(customer_id), str(subscription_id or ""))

    amount = "—"
    if session.amount_total is not None:
        amount = f"${session.amount_total / 100:.2f}"

    send_subscription_receipt(user["email"], "SourceIQ", amount)
    return await get_user_by_id(user["id"])


def construct_webhook_event(payload: bytes, signature: str) -> stripe.Event:
    if not STRIPE_WEBHOOK_SECRET:
        raise ValueError("STRIPE_WEBHOOK_SECRET is not configured")
    return stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)


async def handle_webhook_event(event: stripe.Event) -> None:
    """Process Stripe subscription lifecycle events."""
    if event.type == "checkout.session.completed":
        session = event.data.object
        await handle_checkout_success(session.id)
        return

    if event.type == "invoice.paid":
        invoice = event.data.object
        customer_id = invoice.get("customer")
        if not customer_id:
            return
        user = await get_user_by_stripe_customer_id(str(customer_id))
        if not user:
            return
        amount = f"${invoice.get('amount_paid', 0) / 100:.2f}"
        send_subscription_receipt(user["email"], "SourceIQ", amount)
        return

    if event.type == "customer.subscription.updated":
        subscription = event.data.object
        customer_id = subscription.get("customer")
        status = subscription.get("status")
        user = await get_user_by_stripe_customer_id(str(customer_id)) if customer_id else None
        if not user:
            return
        if status in ("active", "trialing"):
            await update_user_tier(user["id"], "subscriber")
            await update_user_stripe_ids(
                user["id"], str(customer_id), subscription.get("id", "")
            )
        elif status in ("canceled", "unpaid", "past_due", "incomplete_expired"):
            await update_user_tier(user["id"], "none")
        return

    if event.type == "customer.subscription.deleted":
        subscription = event.data.object
        customer_id = subscription.get("customer")
        user = await get_user_by_stripe_customer_id(str(customer_id)) if customer_id else None
        if user:
            await update_user_tier(user["id"], "none")
            await update_user_stripe_ids(user["id"], str(customer_id), "")
