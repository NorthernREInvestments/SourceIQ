#!/usr/bin/env python3
"""Create a SourceIQ web user directly in the database."""

import argparse
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from market_spy.config import VALID_TIERS
from market_spy.web.database import create_user_with_tier, init_db


async def _run(email: str, password: str, tier: str) -> None:
    await init_db()
    user, error = await create_user_with_tier(email, password, tier)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    print(f"Created user id={user['id']} email={user['email']} tier={user['tier']}")


def main():
    parser = argparse.ArgumentParser(
        description="Create a SourceIQ user in the database (no Stripe required).",
    )
    parser.add_argument("email", help="User email address")
    parser.add_argument("password", help="Password (min 8 characters)")
    parser.add_argument(
        "tier",
        choices=VALID_TIERS,
        help="Account tier: none, trial, starter, or pro",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.email, args.password, args.tier))


if __name__ == "__main__":
    main()
