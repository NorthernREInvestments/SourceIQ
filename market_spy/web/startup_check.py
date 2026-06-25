"""Startup environment variable checks for the SourceIQ web app."""

import os

REQUIRED_ENV_VARS = (
    ("SECRET_KEY", "secure sessions and password-reset tokens"),
    ("SCRAPINGBEE_API_KEY", "Stage 2 profit margin scraping"),
    ("STRIPE_SECRET_KEY", "subscription checkout and billing"),
    ("SENDGRID_API_KEY", "transactional email (reset, trial expiry, receipts)"),
)


def check_required_env_vars() -> list[str]:
    """Print warnings for missing vars. Returns list of missing variable names."""
    missing = [name for name, _ in REQUIRED_ENV_VARS if not os.getenv(name, "").strip()]
    if not missing:
        print("[startup] All core environment variables are set.", flush=True)
        return missing

    purposes = {name: purpose for name, purpose in REQUIRED_ENV_VARS}
    print("\n" + "=" * 68, flush=True)
    print("SourceIQ startup warning — missing environment variables", flush=True)
    print("The app will start with reduced functionality for local development.", flush=True)
    print("-" * 68, flush=True)
    for name in missing:
        print(f"  • {name:<22} ({purposes[name]})", flush=True)
    print("=" * 68 + "\n", flush=True)
    return missing
