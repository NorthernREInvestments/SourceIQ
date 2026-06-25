"""SendGrid transactional email helpers for SourceIQ."""

import os

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@sourceiq.app").strip()
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "").strip()
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://sourceiq.up.railway.app").strip().rstrip("/")


def _send(to_email: str, subject: str, html_body: str) -> bool:
    """Send an email via SendGrid. Returns True on success, False if misconfigured."""
    if not SENDGRID_API_KEY or not FROM_EMAIL:
        print(f"[email] skipped (no SendGrid config): {subject} -> {to_email}", flush=True)
        return False

    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=to_email,
        subject=subject,
        html_content=html_body,
    )
    try:
        SendGridAPIClient(SENDGRID_API_KEY).send(message)
        return True
    except Exception as exc:
        print(f"[email] failed: {subject} -> {to_email}: {exc}", flush=True)
        return False


def send_verification_email(email: str, token: str) -> bool:
    link = f"{APP_BASE_URL}/verify?token={token}"
    html = (
        "<h2>Verify your SourceIQ account</h2>"
        f"<p>Click the link below to verify <strong>{email}</strong>:</p>"
        f'<p><a href="{link}">Verify email</a></p>'
        "<p>This link expires in 24 hours.</p>"
    )
    return _send(email, "Verify your SourceIQ email", html)


def send_trial_expiry_reminder(email: str, days_remaining: int) -> bool:
    html = (
        "<h2>Your SourceIQ trial is ending soon</h2>"
        f"<p>You have <strong>{days_remaining}</strong> day"
        f"{'s' if days_remaining != 1 else ''} left on your free trial.</p>"
        f'<p><a href="{APP_BASE_URL}/">Upgrade to keep scanning niches</a></p>'
    )
    return _send(email, f"SourceIQ trial — {days_remaining} days remaining", html)


def send_search_limit_warning(email: str, tier: str, remaining: int) -> bool:
    html = (
        "<h2>SourceIQ search limit warning</h2>"
        f"<p>Your <strong>{tier}</strong> plan has "
        f"<strong>{remaining}</strong> Stage 1 searches remaining this month.</p>"
        f'<p><a href="{APP_BASE_URL}/account">View usage</a> or '
        f'<a href="{APP_BASE_URL}/">upgrade your plan</a>.</p>'
    )
    return _send(email, "SourceIQ — search limit running low", html)


def send_trial_expired_email(email: str) -> bool:
    html = (
        "<h2>Your SourceIQ free trial has ended</h2>"
        "<p>Your 7-day trial has expired. Upgrade to Starter or Pro to continue "
        "researching niches and running margin analysis.</p>"
        f'<p><a href="{APP_BASE_URL}/">View plans and upgrade</a></p>'
    )
    return _send(email, "Your SourceIQ trial has expired", html)


def send_password_reset(email: str, token: str) -> bool:
    from market_spy.web.password_tokens import reset_password_url

    link = reset_password_url(token)
    html = (
        "<h2>Reset your SourceIQ password</h2>"
        "<p>Click the link below to choose a new password:</p>"
        f'<p><a href="{link}">Reset password</a></p>'
        "<p>If you did not request this, you can ignore this email.</p>"
    )
    return _send(email, "Reset your SourceIQ password", html)


def send_subscription_receipt(email: str, tier: str, amount: str) -> bool:
    html = (
        "<h2>SourceIQ subscription receipt</h2>"
        f"<p>Thank you for subscribing to the <strong>{tier}</strong> plan.</p>"
        f"<p>Amount charged: <strong>{amount}</strong></p>"
        f'<p><a href="{APP_BASE_URL}/dashboard">Go to dashboard</a></p>'
    )
    return _send(email, f"SourceIQ {tier} subscription receipt", html)
