"""24-hour Google Trends cache backed by the app database."""

import json
from datetime import datetime, timedelta

from market_spy.web.database import get_database

CACHE_TTL = timedelta(hours=24)


def _normalize_term(term: str) -> str:
    return (term or "").strip().lower()


def _is_fresh(cached_at: str) -> bool:
    try:
        cached = datetime.fromisoformat(cached_at)
    except ValueError:
        return False
    return datetime.utcnow() - cached < CACHE_TTL


async def get_cached_window(term: str, timeframe: str) -> dict | None:
    row = await get_database().fetch_one(
        """
        SELECT found, direction, change_val, series_json, cached_at
        FROM trends_cache
        WHERE search_term = :term AND timeframe = :timeframe
        """,
        {"term": _normalize_term(term), "timeframe": timeframe},
    )
    if not row or not _is_fresh(row["cached_at"]):
        return None
    series = []
    if row["series_json"]:
        try:
            series = json.loads(row["series_json"])
        except json.JSONDecodeError:
            series = []
    return {
        "found": bool(row["found"]),
        "direction": row["direction"],
        "change": float(row["change_val"]),
        "series": series,
    }


async def store_window(term: str, timeframe: str, window: dict, series=None) -> None:
    term = _normalize_term(term)
    now = datetime.utcnow().isoformat()
    series_json = json.dumps(series or [])
    db = get_database()
    await db.execute(
        "DELETE FROM trends_cache WHERE search_term = :term AND timeframe = :timeframe",
        {"term": term, "timeframe": timeframe},
    )
    await db.execute(
        """
        INSERT INTO trends_cache (
            search_term, timeframe, found, direction, change_val, series_json, cached_at
        ) VALUES (
            :term, :timeframe, :found, :direction, :change_val, :series_json, :cached_at
        )
        """,
        {
            "term": term,
            "timeframe": timeframe,
            "found": 1 if window.get("found") else 0,
            "direction": window.get("direction", "stable"),
            "change_val": float(window.get("change", 0)),
            "series_json": series_json,
            "cached_at": now,
        },
    )
