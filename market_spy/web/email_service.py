"""Resend transactional email helpers for SourceIQ."""

import os

import resend

FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@sourceiq.app").strip()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://sourceiq.up.railway.app").strip().rstrip("/")

if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY


def _send(to_email: str, subject: str, html_body: str) -> bool:
    """Send an email via Resend. Returns True on success, False if misconfigured."""
    if not RESEND_API_KEY or not FROM_EMAIL:
        print(f"[email] skipped (no Resend config): {subject} -> {to_email}", flush=True)
        return False

    try:
        resend.Emails.send(
            {
                "from": FROM_EMAIL,
                "to": [to_email],
                "subject": subject,
                "html": html_body,
            }
        )
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
        "<h2>Subscribe to keep using SourceIQ</h2>"
        f"<p>Your access ends in <strong>{days_remaining}</strong> day"
        f"{'s' if days_remaining != 1 else ''}.</p>"
        f'<p><a href="{APP_BASE_URL}/subscribe">Subscribe to SourceIQ</a></p>'
    )
    return _send(email, f"SourceIQ — {days_remaining} days to subscribe", html)


def send_search_limit_warning(email: str, tier: str, remaining: int) -> bool:
    html = (
        "<h2>SourceIQ search limit warning</h2>"
        f"<p>You have <strong>{remaining}</strong> Stage 1 searches remaining this month.</p>"
        f'<p><a href="{APP_BASE_URL}/account">View usage</a> in your account.</p>'
    )
    return _send(email, "SourceIQ — search limit running low", html)


def send_trial_expired_email(email: str) -> bool:
    html = (
        "<h2>Your SourceIQ access has ended</h2>"
        "<p>Subscribe to continue researching niches and running margin analysis.</p>"
        f'<p><a href="{APP_BASE_URL}/subscribe">Subscribe to SourceIQ</a></p>'
    )
    return _send(email, "Subscribe to continue using SourceIQ", html)


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
        "<p>Thank you for subscribing to SourceIQ.</p>"
        f"<p>Amount charged: <strong>{amount}</strong></p>"
        f'<p><a href="{APP_BASE_URL}/dashboard">Go to dashboard</a></p>'
    )
    return _send(email, "SourceIQ subscription receipt", html)
