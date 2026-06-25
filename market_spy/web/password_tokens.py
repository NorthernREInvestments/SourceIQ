"""Secure password-reset tokens using itsdangerous TimestampSigner."""

import os
from urllib.parse import quote

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

RESET_TOKEN_MAX_AGE = 3600  # 1 hour

_signer = TimestampSigner(os.getenv("SECRET_KEY", "sourceiq-reset-key-change-me"))


def generate_reset_token(user_id: int, email: str) -> str:
    payload = f"{user_id}:{email.strip().lower()}"
    return _signer.sign(payload.encode("utf-8")).decode("utf-8")


def verify_reset_token(token: str) -> tuple[int, str] | None:
    """Return (user_id, email) if valid, else None."""
    try:
        raw = _signer.unsign(token.encode("utf-8"), max_age=RESET_TOKEN_MAX_AGE)
        user_id_str, email = raw.decode("utf-8").split(":", 1)
        return int(user_id_str), email
    except (BadSignature, SignatureExpired, ValueError, UnicodeDecodeError):
        return None


def reset_password_url(token: str) -> str:
    base = os.getenv("APP_BASE_URL", "https://sourceiq.up.railway.app").strip().rstrip("/")
    return f"{base}/reset-password/{quote(token, safe='')}"
