"""Persist ScrapingBee credit usage to the app database (survives redeploys)."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from datetime import datetime, timezone

_CREDIT_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS credit_events (
    id {id_col},
    used_at TEXT NOT NULL,
    billing_month TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    credits INTEGER NOT NULL DEFAULT 0
);
"""

_table_ready = False
_table_lock = threading.Lock()
_pg_write_lock = threading.Lock()


def current_billing_month(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m")


def _used_at_iso(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.replace(tzinfo=None).isoformat()


def _postgres_dsn() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return ""
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def _sqlite_path() -> str:
    from market_spy.web.database import DB_PATH

    return DB_PATH


def ensure_credit_events_table_sync() -> None:
    """Create credit_events if missing (safe from scraper worker threads)."""
    global _table_ready
    if _table_ready:
        return
    with _table_lock:
        if _table_ready:
            return
        dsn = _postgres_dsn()
        if dsn.startswith("postgresql"):
            with _pg_write_lock:
                asyncio.run(_ensure_postgres_table(dsn))
        else:
            path = _sqlite_path()
            id_col = "INTEGER PRIMARY KEY AUTOINCREMENT"
            with sqlite3.connect(path, timeout=30) as conn:
                conn.execute(_CREDIT_EVENTS_DDL.format(id_col=id_col))
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_credit_events_month "
                    "ON credit_events(billing_month)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_credit_events_used_at "
                    "ON credit_events(used_at)"
                )
                conn.commit()
        _table_ready = True


async def _ensure_postgres_table(dsn: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS credit_events (
                id SERIAL PRIMARY KEY,
                used_at TEXT NOT NULL,
                billing_month TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                credits INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_credit_events_month ON credit_events(billing_month)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_credit_events_used_at ON credit_events(used_at)"
        )
    finally:
        await conn.close()


def record_credit_event(source: str, url: str, credits: int, used_at: str | None = None) -> None:
    """Write one credit event synchronously (called from scraper threads)."""
    used_at = used_at or _used_at_iso()
    billing_month = used_at[:7]
    cost = int(credits or 0)
    safe_source = (source or "unknown")[:200]
    safe_url = (url or "")[:500]

    try:
        ensure_credit_events_table_sync()
        dsn = _postgres_dsn()
        if dsn.startswith("postgresql"):
            with _pg_write_lock:
                asyncio.run(
                    _insert_postgres(dsn, used_at, billing_month, safe_source, safe_url, cost)
                )
            return

        with sqlite3.connect(_sqlite_path(), timeout=30) as conn:
            conn.execute(
                """
                INSERT INTO credit_events (used_at, billing_month, source, url, credits)
                VALUES (?, ?, ?, ?, ?)
                """,
                (used_at, billing_month, safe_source, safe_url, cost),
            )
            conn.commit()
    except Exception as exc:
        print(
            f"[credit_store] failed to persist credit event source={safe_source} "
            f"credits={cost}: {type(exc).__name__}: {exc}",
            flush=True,
        )


async def _insert_postgres(
    dsn: str,
    used_at: str,
    billing_month: str,
    source: str,
    url: str,
    credits: int,
) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO credit_events (used_at, billing_month, source, url, credits)
            VALUES ($1, $2, $3, $4, $5)
            """,
            used_at,
            billing_month,
            source,
            url,
            credits,
        )
    finally:
        await conn.close()
