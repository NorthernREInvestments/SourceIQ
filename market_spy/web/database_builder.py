"""Product database builder — scheduled scrapes, live search, and storage."""

from __future__ import annotations

import asyncio
import json
import uuid
import traceback
from dataclasses import dataclass
from datetime import date, datetime

from market_spy.analysis import (
    _extract_keywords,
    _keyword_overlap,
    compute_market_opportunity,
    enforce_recency_and_timestamps,
    resolve_broad_category,
)
from market_spy.scrapers import (
    scrape_alibaba,
    scrape_aliexpress,
    scrape_amazon,
    scrape_bing_shopping,
    scrape_dhgate,
    scrape_ebay,
    scrape_made_in_china,
    scrape_walmart,
)
from market_spy.scrapers.base import scrape_delay
from market_spy.product_groups import (
    _group_signature,
    _is_selling,
    _is_sourcing,
    _margin_tier,
    build_product_groups,
)
from market_spy.trends import fetch_trends, fetch_trends_windows
from market_spy.scrapers.scrapingbee_client import get_session_credit_total
from market_spy.web.credit_util import credits_used_between_async
from market_spy.web.database import (
    cancel_scrape_log as db_cancel_scrape_log,
    count_product_niches,
    count_products,
    create_scrape_log,
    ensure_niche_in_queue,
    fetch_all_products,
    fetch_all_product_niches,
    fetch_products_for_niche,
    finish_scrape_log,
    get_app_meta,
    get_database,
    get_running_scrape_logs,
    get_niches_for_expansion,
    get_unscraped_niches,
    had_manual_scrape_today,
    mark_niche_scraped,
    mark_niche_sourcing_refreshed,
    set_app_meta,
    set_niche_needs_sourcing_refresh,
    update_scrape_log_credits,
    update_scrape_log_progress,
)
from market_spy.web.logger import log_error, log_event
from market_spy.web.search_service import (
    _build_trends_payload,
    _enrich_product_groups_async,
)

INITIAL_36_NICHES = [
    "Sports",
    "Health and Wellness",
    "Electronics",
    "Home and Garden",
    "Pet Supplies",
    "Beauty and Personal Care",
    "Kitchen and Cooking",
    "Outdoor and Camping",
    "Baby and Kids",
    "Automotive",
    "Fashion and Accessories",
    "Gaming",
    "Yoga Equipment",
    "Fitness Equipment",
    "Dog Supplies",
    "Cat Supplies",
    "Kitchen Gadgets",
    "Coffee Accessories",
    "Home Decor",
    "Bedroom Accessories",
    "Phone Cases",
    "Laptop Accessories",
    "Headphones",
    "Gaming Chairs",
    "Camping Gear",
    "Hiking Gear",
    "Fishing Gear",
    "Baby Clothing",
    "Baby Toys",
    "Car Accessories",
    "Truck Accessories",
    "Jewelry",
    "Watches",
    "Sunglasses",
    "Skincare",
    "Hair Care",
]

NICHE_SCRAPE_EXCEPTION_DATE = date(2026, 6, 24)
SEARCH_RESULT_THRESHOLD = 10
SOURCING_REFRESH_LIMIT = 5
NIGHTLY_SCRAPE_TOTAL = 10
NIGHTLY_EXPAND_SLOTS = 5
NIGHTLY_NEW_SLOTS = 5

SELL_PRICE_MIN = 1.0
SELL_PRICE_MAX = 2000.0
SOURCE_PRICE_MIN = 0.50
SOURCE_PRICE_MAX = 2000.0

DATABASE_SELL_SCRAPERS = [
    ("eBay", scrape_ebay, {"limit": 45}),
    ("Amazon", scrape_amazon, {"limit": 15}),
    ("Walmart", scrape_walmart, {"limit": 15}),
    ("Bing Shopping", scrape_bing_shopping, {"limit": 30}),
]

DATABASE_SOURCE_SCRAPERS = [
    ("AliExpress", scrape_aliexpress, {"limit": 15}),
    ("DHgate", scrape_dhgate, {"limit": 15}),
    ("Alibaba", scrape_alibaba, {"limit": 15}),
    ("Made-in-China", scrape_made_in_china, {"limit": 15}),
]

FILL_SOURCE_PLATFORMS: dict[str, tuple] = {
    "Amazon": (scrape_amazon, "selling", {"limit": 5}),
    "Walmart": (scrape_walmart, "selling", {"limit": 5}),
    "AliExpress": (scrape_aliexpress, "sourcing", {"limit": 5}),
    "DHgate": (scrape_dhgate, "sourcing", {"limit": 5}),
}

PRICE_CLEANUP_META_KEY = "price_cleanup_run"

_trend_direction_values = {"rising": 1.0, "stable": 0.5, "falling": 0.0}

_live_scrape_tasks: dict[int, asyncio.Task] = {}
_cancelled_log_ids: set[int] = set()


def _format_scrape_error(exc: BaseException) -> str:
    message = str(exc).strip()
    name = type(exc).__name__
    head = f"{name}: {message}" if message else name
    trace = traceback.format_exc()
    if not trace or trace.strip() == "NoneType: None":
        return head[:2000]
    lines = [line.strip() for line in trace.splitlines() if line.strip()]
    tail = lines[-1] if lines else ""
    if tail and tail not in head:
        return f"{head} | {tail}"[:2000]
    return head[:2000]


def _valid_sell_price(price: float | None) -> bool:
    if price is None:
        return False
    try:
        value = float(price)
    except (TypeError, ValueError):
        return False
    return SELL_PRICE_MIN <= value <= SELL_PRICE_MAX


def _valid_source_price(price: float | None, sell_avg: float | None = None) -> bool:
    if price is None:
        return False
    try:
        value = float(price)
    except (TypeError, ValueError):
        return False
    if not (SOURCE_PRICE_MIN <= value <= SOURCE_PRICE_MAX):
        return False
    if sell_avg is not None and value > float(sell_avg):
        return False
    return True


def _parse_platform_prices(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _platform_prices_from_row(row: dict) -> dict:
    prices = _parse_platform_prices(row.get("platform_prices"))
    sell_platform = (row.get("selling_platform") or "").strip()
    sell_price = row.get("selling_price_avg") or row.get("selling_price_min")
    if sell_platform and sell_price is not None and sell_platform not in prices:
        try:
            prices[sell_platform] = {
                "price": float(sell_price),
                "url": row.get("product_url") or "",
                "side": "selling",
            }
        except (TypeError, ValueError):
            pass
    src_platform = (row.get("source_platform") or "").strip()
    src_price = row.get("source_price_min")
    if src_platform and src_price is not None and src_platform not in prices:
        try:
            prices[src_platform] = {
                "price": float(src_price),
                "url": "",
                "side": "sourcing",
            }
        except (TypeError, ValueError):
            pass
    return prices


def _platform_entry_valid(
    platform: str,
    entry: dict | None,
    sell_avg: float | None = None,
) -> bool:
    if not entry or not isinstance(entry, dict):
        return False
    price = entry.get("price")
    side = entry.get("side") or FILL_SOURCE_PLATFORMS.get(platform, (None, "selling", {}))[1]
    if side == "selling":
        return _valid_sell_price(price)
    return _valid_source_price(price, sell_avg)


def _clean_platform_prices(platform_prices: dict) -> dict:
    """Drop junk prices (e.g. $0.02) from stored platform data."""
    cleaned: dict = {}
    sell_prices = []
    for _platform, entry in (platform_prices or {}).items():
        if not isinstance(entry, dict):
            continue
        if (entry.get("side") or "selling") == "selling" and _valid_sell_price(entry.get("price")):
            sell_prices.append(float(entry["price"]))
    sell_avg = round(sum(sell_prices) / len(sell_prices), 2) if sell_prices else None
    for platform, entry in (platform_prices or {}).items():
        if _platform_entry_valid(platform, entry, sell_avg):
            cleaned[platform] = entry
        elif isinstance(entry, dict) and entry.get("price") is not None:
            log_event(
                f"fill_missing: dropped invalid stored price "
                f"platform={platform} price={entry.get('price')}"
            )
    return cleaned


def _missing_fill_platforms(row: dict) -> list[str]:
    prices = _clean_platform_prices(_platform_prices_from_row(row))
    missing = []
    for platform in FILL_SOURCE_PLATFORMS:
        entry = prices.get(platform)
        if not entry or entry.get("price") is None:
            missing.append(platform)
    return missing


def _fill_platform_plan(row: dict) -> tuple[list[str], list[str], dict]:
    """
    Plan fill job work per product.
    Returns (missing, refresh, cleaned_platform_prices).
    Missing = no data yet; refresh = soft re-scrape to verify existing data.
    """
    platform_prices = _clean_platform_prices(_platform_prices_from_row(row))
    missing: list[str] = []
    refresh: list[str] = []
    for platform in FILL_SOURCE_PLATFORMS:
        entry = platform_prices.get(platform)
        if not entry or entry.get("price") is None:
            missing.append(platform)
        else:
            refresh.append(platform)
    return missing, refresh, platform_prices


def _merge_platform_entry(
    prices: dict,
    platform: str,
    price: float,
    url: str,
    side: str,
) -> dict:
    merged = dict(prices or {})
    merged[platform] = {
        "price": round(float(price), 2),
        "url": (url or "")[:500],
        "side": side,
    }
    return merged


def _aggregates_from_platform_prices(platform_prices: dict) -> dict:
    sell_prices = []
    source_prices = []
    sell_platforms = []
    source_platforms = []
    for platform, entry in (platform_prices or {}).items():
        if not isinstance(entry, dict):
            continue
        price = entry.get("price")
        side = entry.get("side")
        if side == "selling" and _valid_sell_price(price):
            sell_prices.append(float(price))
            sell_platforms.append(platform)
        elif side == "sourcing":
            sell_avg = (
                round(sum(sell_prices) / len(sell_prices), 2) if sell_prices else None
            )
            if _valid_source_price(price, sell_avg):
                source_prices.append(float(price))
                source_platforms.append(platform)
    sell_avg = None
    if sell_prices:
        sell_min, sell_max = min(sell_prices), max(sell_prices)
        sell_avg = round((sell_min + sell_max) / 2, 2)
    else:
        sell_min = sell_max = None
    src_min = min(source_prices) if source_prices else None
    src_max = max(source_prices) if source_prices else None
    best_source = None
    if source_prices:
        best_source = source_platforms[source_prices.index(min(source_prices))]
    primary_sell = sell_platforms[0] if sell_platforms else None
    return {
        "sell_min": sell_min,
        "sell_max": sell_max,
        "sell_avg": sell_avg,
        "sell_platform": primary_sell,
        "source_min": src_min,
        "source_max": src_max,
        "source_platform": best_source,
    }


async def cleanup_bad_product_prices() -> dict[str, int]:
    """Delete products with invalid sell/source prices. Returns per-reason counts."""
    db = get_database()
    rules = [
        ("null_or_zero_sell", "selling_price_avg IS NULL OR selling_price_avg = 0"),
        ("sell_under_1", "selling_price_avg IS NOT NULL AND selling_price_avg < 1.0"),
        (
            "source_under_50c",
            "source_price_min IS NOT NULL AND source_price_min < 0.50",
        ),
        (
            "negative_margin",
            "source_price_min IS NOT NULL AND selling_price_avg IS NOT NULL "
            "AND source_price_min > selling_price_avg",
        ),
    ]
    counts: dict[str, int] = {}
    for key, clause in rules:
        count = await db.fetch_val(f"SELECT COUNT(*) FROM products WHERE {clause}")
        counts[key] = int(count or 0)
    for _key, clause in rules:
        await db.execute(f"DELETE FROM products WHERE {clause}")
    total = sum(counts.values())
    log_event(
        "product price cleanup: "
        f"deleted={total} "
        f"null_or_zero_sell={counts['null_or_zero_sell']} "
        f"sell_under_1={counts['sell_under_1']} "
        f"source_under_50c={counts['source_under_50c']} "
        f"negative_margin={counts['negative_margin']}"
    )
    return counts


async def run_startup_product_cleanup_if_needed() -> dict[str, int] | None:
    if await get_app_meta(PRICE_CLEANUP_META_KEY) == "1":
        return None
    counts = await cleanup_bad_product_prices()
    await set_app_meta(PRICE_CLEANUP_META_KEY, "1")
    return counts


class ScrapeCancelled(Exception):
    """Raised when a scrape is cancelled before or after completion."""


@dataclass
class BatchJob:
    batch_id: str
    job_type: str
    cancel_event: asyncio.Event
    task: asyncio.Task
    started_at: str
    niches_done: int = 0
    niches_total: int = 0
    niches_index: int = 0
    current_niche: str = ""


_batch_jobs: dict[str, BatchJob] = {}
_scrape_credit_baseline: dict[int, int] = {}


def _is_log_cancelled(log_id: int | None) -> bool:
    return log_id is not None and log_id in _cancelled_log_ids


def _check_cancelled(*, log_id: int | None = None, cancel_event: asyncio.Event | None = None) -> None:
    if cancel_event and cancel_event.is_set():
        raise ScrapeCancelled("Batch scrape cancelled")
    if _is_log_cancelled(log_id):
        raise ScrapeCancelled("Scrape cancelled")


def is_initial_scrape_running() -> bool:
    return any(
        job.job_type == "initial" and not job.task.done()
        for job in _batch_jobs.values()
    )


def get_active_batch_jobs() -> list[dict]:
    rows = []
    for job in _batch_jobs.values():
        if job.task.done():
            continue
        rows.append({
            "batch_id": job.batch_id,
            "job_type": job.job_type,
            "started_at": job.started_at,
            "niches_done": job.niches_done,
            "niches_total": job.niches_total,
            "niches_index": job.niches_index,
            "current_niche": job.current_niche,
        })
    return rows


async def is_any_scrape_active() -> bool:
    if get_active_batch_jobs():
        return True
    count = await get_database().fetch_val(
        "SELECT COUNT(*) FROM scrape_log WHERE status = 'running'"
    )
    return int(count or 0) > 0


async def is_initial_scrape_active() -> bool:
    if is_initial_scrape_running():
        return True
    count = await get_database().fetch_val(
        """
        SELECT COUNT(*) FROM scrape_log
        WHERE status = 'running' AND scrape_type = 'initial'
        """
    )
    return int(count or 0) > 0


async def is_fill_missing_active() -> bool:
    if any(job.job_type == "fill_missing" for job in _batch_jobs.values()):
        return True
    count = await get_database().fetch_val(
        """
        SELECT COUNT(*) FROM scrape_log
        WHERE status = 'running' AND scrape_type = 'fill_missing_sources'
        """
    )
    return int(count or 0) > 0


async def try_start_fill_missing_sources() -> tuple[str | None, str | None]:
    """Start fill-missing-sources job. Returns (batch_id, error_message)."""
    if await is_fill_missing_active():
        return None, "Fill missing sources is already running."
    batch_id = uuid.uuid4().hex[:12]
    cancel_event = asyncio.Event()

    async def _runner():
        try:
            await run_fill_missing_sources(batch_id=batch_id, cancel_event=cancel_event)
        finally:
            _batch_jobs.pop(batch_id, None)

    task = asyncio.create_task(_runner())
    _batch_jobs[batch_id] = BatchJob(
        batch_id=batch_id,
        job_type="fill_missing",
        cancel_event=cancel_event,
        task=task,
        started_at=_now_iso(),
        niches_total=0,
    )
    log_event(f"fill missing sources batch started: batch_id={batch_id}")
    return batch_id, None


async def try_start_initial_scrape() -> tuple[str | None, str | None]:
    """Start initial scrape if none is running. Returns (batch_id, error_message)."""
    if await is_initial_scrape_active():
        return None, "Initial scrape is already running."
    batch_id = uuid.uuid4().hex[:12]
    cancel_event = asyncio.Event()

    async def _runner():
        try:
            await run_initial_scrape(batch_id=batch_id, cancel_event=cancel_event)
        finally:
            _batch_jobs.pop(batch_id, None)

    task = asyncio.create_task(_runner())
    _batch_jobs[batch_id] = BatchJob(
        batch_id=batch_id,
        job_type="initial",
        cancel_event=cancel_event,
        task=task,
        started_at=_now_iso(),
        niches_total=len(INITIAL_36_NICHES),
    )
    log_event(f"initial scrape batch started: batch_id={batch_id}")
    return batch_id, None


async def cancel_batch_job(batch_id: str) -> bool:
    job = _batch_jobs.get(batch_id)
    if not job or job.task.done():
        return False
    job.cancel_event.set()
    log_event(f"batch scrape cancel requested: batch_id={batch_id} type={job.job_type}")
    return True


async def cancel_all_running_scrapes() -> dict:
    """Cancel every in-flight batch job and every running scrape log."""
    batches = 0
    for batch_id, job in list(_batch_jobs.items()):
        if job.task.done():
            continue
        job.cancel_event.set()
        batches += 1
        log_event(f"cancel all: batch_id={batch_id} type={job.job_type}")

    logs = 0
    for row in await get_running_scrape_logs(100):
        log_id = int(row["id"])
        _cancelled_log_ids.add(log_id)
        if await db_cancel_scrape_log(log_id, reason="Cancelled entire scrape"):
            logs += 1

    log_event(f"cancel all scrapes complete: batches={batches} logs={logs}")
    return {"batches": batches, "logs": logs}


async def cancel_scrape_log_by_id(log_id: int) -> bool:
    _cancelled_log_ids.add(log_id)
    return await db_cancel_scrape_log(log_id)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


async def _refresh_scrape_credits(log_id: int, started_at: str) -> int:
    credits = await credits_for_scrape_async(log_id, started_at)
    await update_scrape_log_credits(log_id, credits)
    return credits


def _run_scraper_batch_logged(scrapers, niche: str, stage: str) -> list[dict]:
    """Run scrapers sequentially with per-source logging; continue on individual failures."""
    items: list[dict] = []
    errors: list[str] = []
    for label, func, kwargs in scrapers:
        if label in STAGE2_COMING_SOON:
            continue
        log_event(f"scraper start: stage={stage} source={label} niche={niche!r}")
        try:
            batch = func(niche, **kwargs) or []
            items.extend(batch)
            log_event(
                f"scraper done: stage={stage} source={label} niche={niche!r} "
                f"items={len(batch)}"
            )
        except Exception as exc:
            detail = _format_scrape_error(exc)
            log_error(f"scraper:{stage}:{label}", exc)
            log_event(
                f"scraper failed: stage={stage} source={label} niche={niche!r} "
                f"error={detail}"
            )
            errors.append(f"{label}: {detail}")
    if errors:
        log_event(
            f"scraper stage {stage} niche={niche!r} "
            f"{len(errors)} source(s) failed: {'; '.join(errors)[:500]}"
        )
    return items


async def credits_for_scrape_async(log_id: int, started_at: str, completed_at: str | None = None) -> int:
    db_credits = 0
    if completed_at:
        db_credits = await credits_used_between_async(started_at, completed_at)
    baseline = _scrape_credit_baseline.get(log_id)
    if baseline is None:
        return db_credits
    session_delta = max(0, get_session_credit_total() - baseline)
    return max(db_credits, session_delta)


def _clear_scrape_credit_baseline(log_id: int) -> None:
    _scrape_credit_baseline.pop(log_id, None)


def credits_for_log_row(log_id: int, started_at: str) -> int:
    """Sync shim — prefer credits_for_scrape_async from async code."""
    import asyncio

    try:
        asyncio.get_running_loop()
        return 0
    except RuntimeError:
        return asyncio.run(credits_for_scrape_async(log_id, started_at))


def _sale_date_iso(item: dict) -> str | None:
    raw = item.get("date") or item.get("sale_date")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.isoformat()
    return str(raw)


def _stage2_scrapers():
    return list(DATABASE_SOURCE_SCRAPERS)


def _full_scrape_items(niche: str) -> list[dict]:
    items = _run_scraper_batch_logged(DATABASE_SELL_SCRAPERS, niche, "sell")
    items.extend(_run_scraper_batch_logged(DATABASE_SOURCE_SCRAPERS, niche, "source"))
    return enforce_recency_and_timestamps(items)


def _sourcing_scrape_items(niche: str) -> list[dict]:
    return enforce_recency_and_timestamps(
        _run_scraper_batch_logged(_stage2_scrapers(), niche, "sourcing")
    )


def _light_sourcing_scrape_items(niche: str) -> list[dict]:
    """Minimal sourcing pull for price/stock refresh — low limits, sourcing platforms only."""
    items = []
    for _label, func, kwargs in _stage2_scrapers():
        refresh_kwargs = dict(kwargs)
        refresh_kwargs["limit"] = min(int(refresh_kwargs.get("limit") or 15), SOURCING_REFRESH_LIMIT)
        try:
            items.extend(func(niche, **refresh_kwargs))
        except Exception as exc:
            log_error(f"scraper:light_sourcing:{_label}", exc)
            log_event(
                f"scraper failed: stage=light_sourcing source={_label} niche={niche!r} "
                f"error={_format_scrape_error(exc)}"
            )
            continue
    return [i for i in enforce_recency_and_timestamps(items) if _is_sourcing(i)]


def _sourcing_matches_product(product: dict, sourcing_items: list[dict]) -> list[dict]:
    product_kw = _extract_keywords(
        " ".join(
            str(product.get(key) or "")
            for key in ("name", "product_group", "subcategory")
        )
    )
    if not product_kw:
        return []
    matched = []
    for item in sourcing_items:
        item_kw = _extract_keywords(item.get("name", ""))
        if _keyword_overlap(product_kw, item_kw) > 0 or (product_kw & item_kw):
            matched.append(item)
    return matched


async def update_sourcing_prices_only(niche: str, sourcing_items: list[dict]) -> tuple[int, int]:
    """Update source price and in-stock on existing rows — never insert or re-scrape sell data."""
    rows = await fetch_products_for_niche(niche)
    if not rows:
        return 0, 0
    now = _now_iso()
    updated = 0
    for row in rows:
        matches = _sourcing_matches_product(row, sourcing_items)
        if not matches:
            await get_database().execute(
                """
                UPDATE products
                SET source_in_stock = 0, last_updated = :now
                WHERE id = :id
                """,
                {"id": row["id"], "now": now},
            )
            updated += 1
            continue
        prices = [p for p in (_scraped_unit(i) for i in matches) if p is not None]
        if not prices:
            continue
        src_min, src_max = min(prices), max(prices)
        platform = matches[0].get("source")
        sell = row.get("selling_price_avg") or row.get("selling_price_min")
        margin_pct = row.get("margin_pct")
        margin_tier = row.get("margin_tier")
        if sell and src_min:
            margin_pct = round((float(sell) - src_min) / float(sell) * 100, 1)
            margin_tier = _margin_tier(margin_pct)
        await get_database().execute(
            """
            UPDATE products SET
                source_price_min = :src_min,
                source_price_max = :src_max,
                source_platform = :platform,
                source_verified_date = :verified,
                source_in_stock = 1,
                margin_pct = :margin_pct,
                margin_tier = :margin_tier,
                last_updated = :verified
            WHERE id = :id
            """,
            {
                "id": row["id"],
                "src_min": src_min,
                "src_max": src_max,
                "platform": platform,
                "verified": now,
                "margin_pct": margin_pct,
                "margin_tier": margin_tier,
            },
        )
        updated += 1
    return 0, updated


def _assign_group_names(items: list[dict], niche: str) -> dict[int, dict]:
    """Map id(item) -> product group row from build_product_groups."""
    groups = build_product_groups(items, niche)
    selling = [i for i in items if _is_selling(i) and i.get("price") is not None]
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for item in selling:
        sig = _group_signature(item.get("name", ""))
        buckets.setdefault(sig, []).append(item)

    sig_to_group: dict[tuple[str, str, str], dict] = {}
    used: set[int] = set()
    for group in groups:
        for sig, bucket_items in buckets.items():
            if len(bucket_items) < 2:
                continue
            if id(bucket_items[0]) in used:
                continue
            if group.get("product_count") == len(bucket_items):
                sig_to_group[sig] = group
                used.update(id(i) for i in bucket_items)
                break

    assignments: dict[int, dict] = {}
    for item in selling:
        sig = _group_signature(item.get("name", ""))
        group = sig_to_group.get(sig)
        if not group:
            for g in groups:
                if g.get("product_count") == 1:
                    assignments[id(item)] = g
                    break
        else:
            assignments[id(item)] = group
    return assignments


def _best_source_for_group(group: dict | None) -> tuple[float | None, float | None, str | None]:
    if not group:
        return None, None, None
    suppliers = group.get("suppliers") or []
    prices = [s.get("unit_price") for s in suppliers if s.get("unit_price") is not None]
    if not prices:
        return None, None, None
    platform = suppliers[0].get("platform") if suppliers else None
    return min(prices), max(prices), platform


async def _upsert_product_row(
    *,
    niche: str,
    subcategory: str,
    product_group: str,
    name: str,
    product_url: str,
    selling_platform: str,
    sell_price: float,
    sale_date: str | None,
    source_min: float | None,
    source_max: float | None,
    source_platform: str | None,
    source_verified_date: str | None,
    margin_pct: float | None,
    margin_tier: str,
    trend_24h: str,
    trend_7d: str,
    trend_30d: str,
    trend_updated: str,
    opportunity_score: float,
    update_sourcing_only: bool = False,
    insert_only: bool = False,
    extra_platform_prices: dict | None = None,
) -> tuple[bool, bool]:
    """Insert or update a product row. Returns (added, updated)."""
    if not _valid_sell_price(sell_price):
        return False, False
    if source_min is not None and not _valid_source_price(source_min, sell_price):
        source_min = source_max = None
        source_platform = None

    db = get_database()
    now = _now_iso()
    existing_row = await db.fetch_one(
        """
        SELECT * FROM products
        WHERE LOWER(niche) = LOWER(:niche)
          AND product_url = :product_url
        """,
        {"niche": niche, "product_url": product_url or ""},
    )
    existing = dict(existing_row) if existing_row else None
    platform_prices = _platform_prices_from_row(existing) if existing else {}
    if selling_platform:
        platform_prices = _merge_platform_entry(
            platform_prices,
            selling_platform,
            sell_price,
            product_url,
            "selling",
        )
    if source_platform and source_min is not None:
        platform_prices = _merge_platform_entry(
            platform_prices,
            source_platform,
            source_min,
            "",
            "sourcing",
        )
    for platform, entry in (extra_platform_prices or {}).items():
        if not isinstance(entry, dict) or entry.get("price") is None:
            continue
        platform_prices = _merge_platform_entry(
            platform_prices,
            platform,
            entry["price"],
            entry.get("url") or "",
            entry.get("side") or "sourcing",
        )
    agg = _aggregates_from_platform_prices(platform_prices)
    if agg["sell_avg"] is None:
        return False, False
    sell_min = agg["sell_min"]
    sell_max = agg["sell_max"]
    sell_avg = agg["sell_avg"]
    selling_platform = agg["sell_platform"] or selling_platform
    if agg["source_min"] is not None:
        source_min = agg["source_min"]
        source_max = agg["source_max"]
        source_platform = agg["source_platform"]
    platform_json = json.dumps(platform_prices)

    if existing:
        if insert_only:
            return False, False
        sale_date_keep = existing.get("sale_date") or sale_date
        if update_sourcing_only:
            await db.execute(
                """
                UPDATE products SET
                    source_price_min = :source_min,
                    source_price_max = :source_max,
                    source_platform = :source_platform,
                    source_verified_date = :source_verified_date,
                    platform_prices = :platform_prices,
                    last_updated = :last_updated
                WHERE id = :id
                """,
                {
                    "id": existing["id"],
                    "source_min": source_min,
                    "source_max": source_max,
                    "source_platform": source_platform,
                    "source_verified_date": source_verified_date or now,
                    "platform_prices": platform_json,
                    "last_updated": now,
                },
            )
            return False, True

        await db.execute(
            """
            UPDATE products SET
                subcategory = :subcategory,
                product_group = :product_group,
                name = :name,
                selling_price_min = :sell_min,
                selling_price_max = :sell_max,
                selling_price_avg = :sell_avg,
                selling_platform = :selling_platform,
                sale_date = :sale_date,
                source_price_min = COALESCE(:source_min, source_price_min),
                source_price_max = COALESCE(:source_max, source_price_max),
                source_platform = COALESCE(:source_platform, source_platform),
                source_verified_date = COALESCE(:source_verified_date, source_verified_date),
                margin_pct = :margin_pct,
                margin_tier = :margin_tier,
                trend_24h = :trend_24h,
                trend_7d = :trend_7d,
                trend_30d = :trend_30d,
                trend_updated = :trend_updated,
                opportunity_score = :opportunity_score,
                platform_prices = :platform_prices,
                last_updated = :last_updated
            WHERE id = :id
            """,
            {
                "id": existing["id"],
                "subcategory": subcategory,
                "product_group": product_group,
                "name": name,
                "sell_min": sell_min,
                "sell_max": sell_max,
                "sell_avg": sell_avg,
                "selling_platform": selling_platform,
                "sale_date": sale_date_keep,
                "source_min": source_min,
                "source_max": source_max,
                "source_platform": source_platform,
                "source_verified_date": source_verified_date,
                "margin_pct": margin_pct,
                "margin_tier": margin_tier,
                "trend_24h": trend_24h,
                "trend_7d": trend_7d,
                "trend_30d": trend_30d,
                "trend_updated": trend_updated,
                "opportunity_score": opportunity_score,
                "platform_prices": platform_json,
                "last_updated": now,
            },
        )
        return False, True

    await db.execute(
        """
        INSERT INTO products (
            niche, subcategory, product_group, name,
            selling_price_min, selling_price_max, selling_price_avg,
            selling_platform, sale_date,
            source_price_min, source_price_max, source_platform, source_verified_date,
            margin_pct, margin_tier,
            trend_24h, trend_7d, trend_30d, trend_updated,
            opportunity_score, created_at, last_updated, product_url, platform_prices
        ) VALUES (
            :niche, :subcategory, :product_group, :name,
            :sell_min, :sell_max, :sell_avg,
            :selling_platform, :sale_date,
            :source_min, :source_max, :source_platform, :source_verified_date,
            :margin_pct, :margin_tier,
            :trend_24h, :trend_7d, :trend_30d, :trend_updated,
            :opportunity_score, :created_at, :last_updated, :product_url, :platform_prices
        )
        """,
        {
            "niche": niche,
            "subcategory": subcategory,
            "product_group": product_group,
            "name": name,
            "sell_min": sell_min,
            "sell_max": sell_max,
            "sell_avg": sell_avg,
            "selling_platform": selling_platform,
            "sale_date": sale_date,
            "source_min": source_min,
            "source_max": source_max,
            "source_platform": source_platform,
            "source_verified_date": source_verified_date or now,
            "margin_pct": margin_pct,
            "margin_tier": margin_tier,
            "trend_24h": trend_24h,
            "trend_7d": trend_7d,
            "trend_30d": trend_30d,
            "trend_updated": trend_updated,
            "opportunity_score": opportunity_score,
            "created_at": now,
            "last_updated": now,
            "product_url": product_url or "",
            "platform_prices": platform_json,
        },
    )
    return True, False


def _window_direction(windows: dict, key: str) -> str:
    window = windows.get(key)
    if isinstance(window, dict):
        return str(window.get("direction") or "stable")
    return "stable"


async def auto_create_product_cards(
    niche: str,
    items: list[dict],
    *,
    trends_windows: dict | None = None,
    opportunity_score: float | None = None,
    update_sourcing_only: bool = False,
    insert_only: bool = False,
) -> tuple[int, int]:
    """Persist scraped items as product rows grouped by product type."""
    items = enforce_recency_and_timestamps(items)
    if trends_windows is None:
        trends_windows = fetch_trends_windows(niche)
    trends = fetch_trends(niche)
    if opportunity_score is None:
        opportunity_score = round(float(compute_market_opportunity(items, trends)), 1)

    windows = trends_windows or {}
    trend_24h = _window_direction(windows, "24h")
    trend_7d = _window_direction(windows, "7d")
    trend_30d = _window_direction(windows, "30d")
    trend_updated = _now_iso()

    broad = resolve_broad_category(niche) or niche
    assignments = _assign_group_names(items, niche)
    added = updated = 0

    if update_sourcing_only:
        sourcing_items = [i for i in items if _is_sourcing(i) and _scraped_unit(i) is not None]
        if not sourcing_items:
            return 0, 0
        prices = [_scraped_unit(i) for i in sourcing_items]
        src_min, src_max = min(prices), max(prices)
        platform = sourcing_items[0].get("source")
        now = _now_iso()
        result = await get_database().execute(
            """
            UPDATE products SET
                source_price_min = :src_min,
                source_price_max = :src_max,
                source_platform = :platform,
                source_verified_date = :verified,
                last_updated = :verified
            WHERE LOWER(niche) = LOWER(:niche)
            """,
            {
                "niche": niche,
                "src_min": src_min,
                "src_max": src_max,
                "platform": platform,
                "verified": now,
            },
        )
        return 0, int(result or 0)

    for item in items:
        if not _is_selling(item):
            continue
        price = item.get("price")
        if price is None:
            continue
        group = assignments.get(id(item), {})
        product_group = group.get("name") or niche
        src_min, src_max, src_platform = _best_source_for_group(group)
        margin_mid = group.get("margin_mid")
        margin_tier = group.get("margin_tier") or _margin_tier(margin_mid)
        extra_platform_prices = {}
        for supplier in group.get("suppliers") or []:
            unit = supplier.get("unit_price")
            platform = supplier.get("platform")
            if unit is None or not platform:
                continue
            extra_platform_prices[platform] = {
                "price": unit,
                "url": supplier.get("url") or "",
                "side": "sourcing",
            }
        row_added, row_updated = await _upsert_product_row(
            niche=niche,
            subcategory=broad,
            product_group=product_group,
            name=(item.get("name") or "")[:200],
            product_url=(item.get("url") or "")[:500],
            selling_platform=item.get("source", ""),
            sell_price=float(price),
            sale_date=_sale_date_iso(item),
            source_min=src_min,
            source_max=src_max,
            source_platform=src_platform,
            source_verified_date=_now_iso() if src_min is not None else None,
            margin_pct=margin_mid,
            margin_tier=margin_tier or "LOW",
            trend_24h=trend_24h,
            trend_7d=trend_7d,
            trend_30d=trend_30d,
            trend_updated=trend_updated,
            opportunity_score=float(opportunity_score),
            insert_only=insert_only,
            extra_platform_prices=extra_platform_prices,
        )
        if row_added:
            added += 1
        if row_updated:
            updated += 1
    return added, updated


def _scraped_unit(item: dict) -> float | None:
    raw = item.get("unit_price") if item.get("unit_price") is not None else item.get("price")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _best_scrape_match(product_name: str, items: list[dict], side: str) -> dict | None:
    product_kw = _extract_keywords(product_name)
    best_item = None
    best_score = 0.0
    for item in items:
        if side == "selling" and not _is_selling(item):
            continue
        if side == "sourcing" and not _is_sourcing(item):
            continue
        price = _scraped_unit(item)
        if price is None:
            continue
        if side == "selling" and not _valid_sell_price(price):
            continue
        if side == "sourcing" and not _valid_source_price(price):
            continue
        score = _keyword_overlap(product_kw, _extract_keywords(item.get("name", "")))
        if score > best_score:
            best_score = score
            best_item = item
    return best_item if best_score > 0 else None


def _scrape_platform_for_product(
    platform: str,
    product_name: str,
    side: str,
    sell_avg: float | None,
) -> dict | None:
    func, scrape_side, kwargs = FILL_SOURCE_PLATFORMS[platform]
    try:
        items = func(product_name, **kwargs) or []
    except Exception as exc:
        log_error(f"fill_missing:{platform}", exc)
        log_event(
            f"fill missing scrape failed platform={platform} "
            f"product={product_name[:40]!r} error={_format_scrape_error(exc)}"
        )
        return None
    scrape_delay()
    match = _best_scrape_match(product_name, items, scrape_side)
    if not match:
        return None
    price = _scraped_unit(match)
    if scrape_side == "selling":
        if not _valid_sell_price(price):
            log_event(
                f"fill_missing: rejected sell price platform={platform} "
                f"product={product_name[:40]!r} price={price}"
            )
            return None
    elif not _valid_source_price(price, sell_avg):
        log_event(
            f"fill_missing: rejected source price platform={platform} "
            f"product={product_name[:40]!r} price={price} sell_avg={sell_avg}"
        )
        return None
    log_event(
        f"fill_missing: accepted platform={platform} product={product_name[:40]!r} "
        f"price=${float(price):.2f} side={scrape_side}"
    )
    return {
        "price": price,
        "url": match.get("url") or "",
        "side": scrape_side,
    }


async def _update_product_platform_prices(
    product_id: int,
    platform_prices: dict,
) -> bool:
    agg = _aggregates_from_platform_prices(platform_prices)
    if agg["sell_avg"] is None:
        return False
    margin_pct = None
    margin_tier = "LOW"
    if agg["source_min"] is not None and agg["sell_avg"]:
        margin_pct = round(
            (agg["sell_avg"] - agg["source_min"]) / agg["sell_avg"] * 100,
            1,
        )
        margin_tier = _margin_tier(margin_pct)
    now = _now_iso()
    await get_database().execute(
        """
        UPDATE products SET
            selling_price_min = :sell_min,
            selling_price_max = :sell_max,
            selling_price_avg = :sell_avg,
            selling_platform = COALESCE(:sell_platform, selling_platform),
            source_price_min = :src_min,
            source_price_max = :src_max,
            source_platform = :src_platform,
            source_verified_date = CASE WHEN :src_min IS NOT NULL THEN :now ELSE source_verified_date END,
            platform_prices = :platform_prices,
            margin_pct = COALESCE(:margin_pct, margin_pct),
            margin_tier = COALESCE(:margin_tier, margin_tier),
            last_updated = :now
        WHERE id = :id
        """,
        {
            "id": product_id,
            "sell_min": agg["sell_min"],
            "sell_max": agg["sell_max"],
            "sell_avg": agg["sell_avg"],
            "sell_platform": agg["sell_platform"],
            "src_min": agg["source_min"],
            "src_max": agg["source_max"],
            "src_platform": agg["source_platform"],
            "platform_prices": json.dumps(platform_prices),
            "margin_pct": margin_pct,
            "margin_tier": margin_tier,
            "now": now,
        },
    )
    return True


async def run_fill_missing_sources(
    *,
    batch_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
    log_id: int | None = None,
) -> dict:
    """
    Fill missing Amazon/Walmart/AliExpress/DHgate data and soft-refresh existing
    platform prices so junk like $0.02 is replaced or removed.
    """
    started = _now_iso()
    if log_id is None:
        log_id = await create_scrape_log("all products", "fill_missing_sources")
    _scrape_credit_baseline[log_id] = get_session_credit_total()
    updated = 0
    refreshed = 0
    products_checked = 0
    try:
        products = await fetch_all_products()
        await update_scrape_log_progress(
            log_id,
            f"Checking {len(products)} products — fill missing + soft refresh…",
        )
        for index, row in enumerate(products):
            _check_cancelled(log_id=log_id, cancel_event=cancel_event)
            products_checked += 1
            missing, to_refresh, platform_prices = _fill_platform_plan(row)
            to_scrape = missing + to_refresh
            if not to_scrape:
                continue
            name = (row.get("name") or row.get("product_group") or row.get("niche") or "product")
            parts = []
            if missing:
                parts.append(f"adding {', '.join(missing)}")
            if to_refresh:
                parts.append(f"refreshing {', '.join(to_refresh)}")
            progress = f"filling sources for {name[:50]} — {'; '.join(parts)}"
            await update_scrape_log_progress(log_id, progress)
            log_event(progress)
            sell_avg = row.get("selling_price_avg")
            agg = _aggregates_from_platform_prices(platform_prices)
            sell_avg = agg.get("sell_avg") or sell_avg
            changed = platform_prices != _clean_platform_prices(_platform_prices_from_row(row))
            for platform in to_scrape:
                is_refresh = platform in to_refresh
                old_entry = platform_prices.get(platform)
                result = await asyncio.to_thread(
                    _scrape_platform_for_product,
                    platform,
                    name,
                    FILL_SOURCE_PLATFORMS[platform][1],
                    sell_avg,
                )
                if result:
                    platform_prices = _merge_platform_entry(
                        platform_prices,
                        platform,
                        result["price"],
                        result["url"],
                        result["side"],
                    )
                    changed = True
                    if is_refresh:
                        refreshed += 1
                elif is_refresh and old_entry:
                    if _platform_entry_valid(platform, old_entry, sell_avg):
                        log_event(
                            f"fill_missing: soft refresh kept existing "
                            f"platform={platform} product={name[:40]!r} "
                            f"price={old_entry.get('price')}"
                        )
                    else:
                        platform_prices.pop(platform, None)
                        changed = True
                        log_event(
                            f"fill_missing: removed invalid stale price "
                            f"platform={platform} product={name[:40]!r}"
                        )
                agg = _aggregates_from_platform_prices(platform_prices)
                sell_avg = agg.get("sell_avg") or sell_avg
            if changed and await _update_product_platform_prices(row["id"], platform_prices):
                updated += 1
            if index % 3 == 0:
                await _refresh_scrape_credits(log_id, started)
        completed_at = _now_iso()
        credits = await credits_for_scrape_async(log_id, started, completed_at)
        await finish_scrape_log(
            log_id,
            status="completed",
            products_updated=updated,
            credits_used=credits,
        )
        log_event(
            f"fill missing sources complete: checked={products_checked} updated={updated} "
            f"soft_refreshed={refreshed} credits={credits}"
        )
        _clear_scrape_credit_baseline(log_id)
        return {
            "log_id": log_id,
            "batch_id": batch_id,
            "products_checked": products_checked,
            "updated": updated,
        }
    except ScrapeCancelled as exc:
        completed_at = _now_iso()
        credits = await credits_for_scrape_async(log_id, started, completed_at)
        await finish_scrape_log(
            log_id,
            status="cancelled",
            products_updated=updated,
            credits_used=credits,
            error_message=str(exc),
        )
        _clear_scrape_credit_baseline(log_id)
        raise
    except Exception as exc:
        log_error("fill_missing_sources", exc)
        completed_at = _now_iso()
        credits = await credits_for_scrape_async(log_id, started, completed_at)
        err_text = _format_scrape_error(exc)
        await update_scrape_log_progress(log_id, f"Failed — {err_text[:480]}")
        await finish_scrape_log(
            log_id,
            status="failed",
            products_updated=updated,
            credits_used=credits,
            error_message=err_text[:2000],
        )
        _clear_scrape_credit_baseline(log_id)
        raise


async def _scrape_and_store(
    niche: str,
    scrape_type: str,
    *,
    sourcing_only: bool = False,
    light_refresh: bool = False,
    insert_only: bool = False,
    log_id: int | None = None,
    cancel_event: asyncio.Event | None = None,
) -> dict:
    started = _now_iso()
    if log_id is None:
        log_id = await create_scrape_log(niche, scrape_type)
    _scrape_credit_baseline[log_id] = get_session_credit_total()
    _check_cancelled(log_id=log_id, cancel_event=cancel_event)
    await ensure_niche_in_queue(niche, added_by=scrape_type)
    await update_scrape_log_progress(log_id, "Starting scrape…")
    log_event(
        f"database scrape start: type={scrape_type} niche={niche!r} log_id={log_id} "
        f"light_refresh={light_refresh} insert_only={insert_only}"
    )
    added = updated = 0
    items: list[dict] = []
    try:
        if sourcing_only:
            if light_refresh:
                items = await asyncio.to_thread(_light_sourcing_scrape_items, niche)
            else:
                items = await asyncio.to_thread(_sourcing_scrape_items, niche)
            await _refresh_scrape_credits(log_id, started)
            _check_cancelled(log_id=log_id, cancel_event=cancel_event)
            if light_refresh:
                added, updated = await update_sourcing_prices_only(niche, items)
            else:
                trends_windows = fetch_trends_windows(niche)
                added, updated = await auto_create_product_cards(
                    niche,
                    items,
                    trends_windows=trends_windows,
                    update_sourcing_only=True,
                )
            await mark_niche_sourcing_refreshed(niche)
        else:
            await update_scrape_log_progress(
                log_id,
                "Sell-side: eBay, Amazon, Walmart, Bing Shopping…",
            )
            items = await asyncio.to_thread(
                _run_scraper_batch_logged, DATABASE_SELL_SCRAPERS, niche, "sell"
            )
            await _refresh_scrape_credits(log_id, started)
            _check_cancelled(log_id=log_id, cancel_event=cancel_event)
            await update_scrape_log_progress(
                log_id,
                f"Sell-side done ({len(items)} listings). "
                "Sourcing: AliExpress, DHgate, Alibaba, Made-in-China…",
            )
            source_items = await asyncio.to_thread(
                _run_scraper_batch_logged, DATABASE_SOURCE_SCRAPERS, niche, "source"
            )
            items.extend(source_items)
            items = enforce_recency_and_timestamps(items)
            await _refresh_scrape_credits(log_id, started)
            _check_cancelled(log_id=log_id, cancel_event=cancel_event)
            await update_scrape_log_progress(log_id, f"Fetched {len(items)} total listings. Loading trends…")
            try:
                trends_windows = fetch_trends_windows(niche)
                trends = fetch_trends(niche)
            except Exception as exc:
                await update_scrape_log_progress(
                    log_id, f"Trends failed — {_format_scrape_error(exc)}"
                )
                raise
            score = round(float(compute_market_opportunity(items, trends)), 1)
            await update_scrape_log_progress(log_id, f"Saving products (score {score})…")
            try:
                added, updated = await auto_create_product_cards(
                    niche,
                    items,
                    trends_windows=trends_windows,
                    opportunity_score=score,
                    insert_only=insert_only,
                )
            except Exception as exc:
                await update_scrape_log_progress(
                    log_id, f"Save failed — {_format_scrape_error(exc)}"
                )
                raise
            try:
                await mark_niche_scraped(niche)
            except Exception as mark_exc:
                log_error(f"mark_niche_scraped:{niche}", mark_exc)
        completed_at = _now_iso()
        credits = await credits_for_scrape_async(log_id, started, completed_at)
        await finish_scrape_log(
            log_id,
            status="completed",
            products_added=added,
            products_updated=updated,
            credits_used=credits,
        )
        log_event(
            f"database scrape complete: niche={niche!r} added={added} updated={updated} "
            f"credits={credits}"
        )
        _clear_scrape_credit_baseline(log_id)
        return {
            "log_id": log_id,
            "niche": niche,
            "added": added,
            "updated": updated,
            "items": len(items),
        }
    except ScrapeCancelled as exc:
        completed_at = _now_iso()
        credits = await credits_for_scrape_async(log_id, started, completed_at)
        await finish_scrape_log(
            log_id,
            status="cancelled",
            products_added=added,
            products_updated=updated,
            credits_used=credits,
            error_message=str(exc),
        )
        _clear_scrape_credit_baseline(log_id)
        raise
    except Exception as exc:
        log_error(f"database scrape:{scrape_type}", exc)
        completed_at = _now_iso()
        credits = await credits_for_scrape_async(log_id, started, completed_at)
        saved = (added + updated) > 0
        err_text = _format_scrape_error(exc)
        await update_scrape_log_progress(log_id, f"Failed — {err_text[:480]}")
        await finish_scrape_log(
            log_id,
            status="completed" if saved else "failed",
            products_added=added,
            products_updated=updated,
            credits_used=credits,
            error_message="" if saved else err_text[:2000],
        )
        _clear_scrape_credit_baseline(log_id)
        if not saved:
            raise


async def run_initial_scrape(
    *,
    batch_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> dict:
    """Scrape all 36 initial niches with full Stage 1 + Stage 2."""
    job = _batch_jobs.get(batch_id) if batch_id else None
    if job:
        job.niches_total = len(INITIAL_36_NICHES)
    summary = {
        "batch_id": batch_id,
        "niches": [],
        "total_added": 0,
        "total_updated": 0,
        "errors": [],
        "cancelled": False,
    }
    for idx, niche in enumerate(INITIAL_36_NICHES):
        if cancel_event and cancel_event.is_set():
            summary["cancelled"] = True
            log_event(f"initial scrape batch {batch_id} stopped after {len(summary['niches'])} niches")
            break
        if job:
            job.niches_index = idx + 1
            job.current_niche = niche
        await ensure_niche_in_queue(niche, added_by="initial", priority=10)
        try:
            result = await _scrape_and_store(
                niche,
                "initial",
                cancel_event=cancel_event,
            )
            summary["niches"].append(niche)
            summary["total_added"] += result["added"]
            summary["total_updated"] += result["updated"]
            if job:
                job.niches_done = len(summary["niches"])
                job.current_niche = ""
        except ScrapeCancelled:
            summary["cancelled"] = True
            break
        except Exception as exc:
            summary["errors"].append({"niche": niche, "error": str(exc)})
    log_event(
        f"initial scrape finished: batch={batch_id} niches={len(summary['niches'])} "
        f"added={summary['total_added']} errors={len(summary['errors'])} "
        f"cancelled={summary['cancelled']}"
    )
    return summary


async def _pick_nightly_niche_slots() -> tuple[list[str], list[str]]:
    """Split nightly capacity: new products in existing niches vs brand-new niches."""
    total = NIGHTLY_SCRAPE_TOTAL
    expand_target = NIGHTLY_EXPAND_SLOTS
    new_target = NIGHTLY_NEW_SLOTS

    expand_candidates = await get_niches_for_expansion(limit=total)
    new_candidates = await get_unscraped_niches(limit=total)

    expand = expand_candidates[:expand_target]
    new = new_candidates[:new_target]

    remaining = total - len(expand) - len(new)
    expand_extra = expand_candidates[len(expand) :]
    new_extra = new_candidates[len(new) :]

    while remaining > 0:
        if len(new) < new_target and new_extra:
            new.append(new_extra.pop(0))
        elif expand_extra:
            expand.append(expand_extra.pop(0))
        elif new_extra:
            new.append(new_extra.pop(0))
        else:
            break
        remaining -= 1

    return expand, new


async def run_nightly_new_niches() -> dict:
    """Nightly job — full scrapes that insert new products only (no listing updates)."""
    if await had_manual_scrape_today():
        log_event("nightly scrape skipped: manual scrape already ran today")
        return {
            "skipped": True,
            "reason": "manual_scrape_today",
            "expanded": [],
            "new_scraped": [],
            "errors": [],
        }

    expand_niches, new_niches = await _pick_nightly_niche_slots()
    summary = {"expanded": [], "new_scraped": [], "errors": []}

    for niche in expand_niches:
        try:
            await _scrape_and_store(niche, "nightly_expand", insert_only=True)
            summary["expanded"].append(niche)
        except Exception as exc:
            summary["errors"].append({"niche": niche, "error": str(exc), "type": "expand"})

    for niche in new_niches:
        try:
            await _scrape_and_store(niche, "nightly_new", insert_only=True)
            summary["new_scraped"].append(niche)
        except Exception as exc:
            summary["errors"].append({"niche": niche, "error": str(exc), "type": "nightly"})

    log_event(
        f"nightly scrape finished: expanded={len(summary['expanded'])} "
        f"new={len(summary['new_scraped'])}"
    )
    return summary


async def run_weekly_sourcing_refresh() -> dict:
    """Refresh sourcing prices and stock for existing products — light scrape only."""
    summary = {"refreshed": [], "errors": []}
    niches = await fetch_all_product_niches()
    for niche in niches:
        try:
            await _scrape_and_store(
                niche, "weekly_sourcing", sourcing_only=True, light_refresh=True,
            )
            summary["refreshed"].append(niche)
        except Exception as exc:
            summary["errors"].append({"niche": niche, "error": str(exc)})
    log_event(f"weekly sourcing refresh finished: count={len(summary['refreshed'])}")
    return summary


async def run_trend_refresh() -> dict:
    """Update trend columns for all products — pytrends only, no ScrapingBee."""
    summary = {"niches": 0, "products": 0, "boosted": []}
    niches = await fetch_all_product_niches()
    now = _now_iso()
    for niche in niches:
        windows = await asyncio.to_thread(fetch_trends_windows, niche)
        trend_24h = (windows.get("24h") or {}).get("direction", "stable")
        trend_7d = (windows.get("7d") or {}).get("direction", "stable")
        trend_30d = (windows.get("30d") or {}).get("direction", "stable")
        await get_database().execute(
            """
            UPDATE products SET
                trend_24h = :t24,
                trend_7d = :t7,
                trend_30d = :t30,
                trend_updated = :updated
            WHERE LOWER(niche) = LOWER(:niche)
            """,
            {
                "niche": niche,
                "t24": trend_24h,
                "t7": trend_7d,
                "t30": trend_30d,
                "updated": now,
            },
        )
        summary["niches"] += 1
        count = await get_database().fetch_val(
            "SELECT COUNT(*) FROM products WHERE LOWER(niche) = LOWER(:niche)",
            {"niche": niche},
        )
        summary["products"] += int(count or 0)
        if trend_24h == "rising" and trend_7d == "rising":
            await set_niche_needs_sourcing_refresh(niche)
            summary["boosted"].append(niche)
    log_event(
        f"trend refresh finished: niches={summary['niches']} boosted={len(summary['boosted'])}"
    )
    return summary


def _normalize_query(query: str) -> str:
    return " ".join((query or "").strip().split())


def _query_keywords(query: str) -> set[str]:
    return _extract_keywords(_normalize_query(query))


def _product_match_score(query: str, query_kw: set[str], row: dict) -> float:
    if not query_kw:
        return 0.0
    fields = " ".join(
        str(row.get(key) or "")
        for key in ("niche", "subcategory", "product_group", "name")
    ).lower()
    field_kw = _extract_keywords(fields)
    overlap = _keyword_overlap(query_kw, field_kw)
    if overlap > 0:
        return overlap
    for token in query_kw:
        if token in fields:
            return 0.35
    broad = resolve_broad_category(query)
    if broad and broad.lower() in fields:
        return 0.5
    return 0.0


def _rank_score(row: dict) -> float:
    margin = float(row.get("margin_pct") or 0)
    opportunity = float(row.get("opportunity_score") or 0)
    trend = _trend_direction_values.get((row.get("trend_30d") or "stable").lower(), 0.5)
    price = float(row.get("selling_price_avg") or 0)
    price_component = min(price / 100.0, 1.0) if price > 0 else 0.0
    return (
        margin * 0.40
        + opportunity * 0.30
        + trend * 100.0 * 0.20
        + price_component * 100.0 * 0.10
    )


async def _fetch_matching_products(query: str, limit: int = 200) -> list[dict]:
    query = _normalize_query(query)
    if not query:
        return []
    db = get_database()
    like = f"%{query.lower()}%"
    rows = await db.fetch_all(
        """
        SELECT * FROM products
        WHERE LOWER(niche) LIKE :like
           OR LOWER(subcategory) LIKE :like
           OR LOWER(product_group) LIKE :like
           OR LOWER(name) LIKE :like
        LIMIT :limit
        """,
        {"like": like, "limit": limit},
    )
    products = [_row_dict(row) for row in rows]
    query_kw = _query_keywords(query)
    scored = []
    for row in products:
        match = _product_match_score(query, query_kw, row)
        if match <= 0:
            continue
        scored.append((match + _rank_score(row), row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _score, row in scored]


def _row_dict(row) -> dict:
    return dict(row)


def _products_to_scrape_items(rows: list[dict]) -> list[dict]:
    items = []
    for row in rows:
        platform_prices = _platform_prices_from_row(row)
        if platform_prices:
            for platform, entry in platform_prices.items():
                if not isinstance(entry, dict):
                    continue
                price = entry.get("price")
                side = entry.get("side") or "selling"
                url = entry.get("url") or row.get("product_url") or ""
                if side == "selling" and _valid_sell_price(price):
                    items.append({
                        "source": platform,
                        "side": "selling",
                        "name": row.get("name") or "",
                        "url": url,
                        "price": float(price),
                    })
                elif side == "sourcing" and _valid_source_price(
                    price, row.get("selling_price_avg")
                ):
                    items.append({
                        "source": platform,
                        "side": "sourcing",
                        "name": row.get("name") or "",
                        "url": url,
                        "price": float(price),
                        "unit_price": float(price),
                    })
            continue
        price = row.get("selling_price_avg") or row.get("selling_price_min")
        if price is None:
            continue
        items.append({
            "source": row.get("selling_platform") or "eBay",
            "side": "selling",
            "name": row.get("name") or "",
            "url": row.get("product_url") or "",
            "price": float(price),
        })
        src = row.get("source_price_min")
        if src is not None:
            items.append({
                "source": row.get("source_platform") or "Supplier",
                "side": "sourcing",
                "name": row.get("name") or "",
                "url": "",
                "price": float(src),
                "unit_price": float(src),
            })
    return items


async def _build_result_from_products(query: str, rows: list[dict]) -> dict:
    items = _products_to_scrape_items(rows)
    trends_windows = fetch_trends_windows(query)
    trends_payload = _build_trends_payload(trends_windows)
    trends = fetch_trends(query)
    score = round(float(compute_market_opportunity(items, trends)), 1) if items else 0.0
    groups = build_product_groups(items, query)
    groups = await _enrich_product_groups_async(groups)
    return {
        "category": query,
        "view_mode": "product_groups",
        "score": score,
        "total_listings": len(rows),
        "groups": groups,
        "from_database": True,
        "data_updated_at": _now_iso(),
        **trends_payload,
    }


async def search_database(query: str, limit: int = 100) -> dict:
    """
    Search stored products. Returns result payload and metadata.
    Triggers live scrape when fewer than 10 matches.
    """
    query = _normalize_query(query)
    await ensure_niche_in_queue(query, added_by="search")
    rows = await _fetch_matching_products(query, limit=limit)
    needs_live = len(rows) < SEARCH_RESULT_THRESHOLD
    result = None
    if rows:
        result = await _build_result_from_products(query, rows[:limit])
    return {
        "query": query,
        "count": len(rows),
        "needs_live_scrape": needs_live,
        "result": result,
    }


async def trigger_live_scrape(niche: str) -> int:
    """Queue a full live scrape for a niche. Returns scrape_log id."""
    niche = _normalize_query(niche)
    await ensure_niche_in_queue(niche, added_by="user", priority=8)
    log_id = await create_scrape_log(niche, "user_triggered")
    started = _now_iso()

    async def _run():
        try:
            _check_cancelled(log_id=log_id)
            await _scrape_and_store(
                niche,
                "user_triggered",
                log_id=log_id,
            )
        except ScrapeCancelled:
            pass
        except Exception as exc:
            log_error("trigger_live_scrape", exc)
            await finish_scrape_log(
                log_id,
                status="failed",
                credits_used=await credits_for_scrape_async(log_id, started),
                error_message=str(exc),
            )
        finally:
            _live_scrape_tasks.pop(log_id, None)

    task = asyncio.create_task(_run())
    _live_scrape_tasks[log_id] = task
    return log_id


async def complete_live_scrape_if_ready(log_id: int, query: str) -> dict | None:
    """If live scrape finished, return results payload for the session."""
    from market_spy.web.database import get_scrape_log

    log = await get_scrape_log(log_id)
    if not log:
        return None
    if log.get("status") == "running":
        return None
    if log.get("status") != "completed":
        return {"failed": True, "error": log.get("error_message") or "Scrape failed"}
    rows = await _fetch_matching_products(query, limit=100)
    if not rows:
        rows = await fetch_products_for_niche(log.get("niche", query))
    if not rows:
        return None
    return await _build_result_from_products(query, rows)


def scheduled_nightly_hour() -> int:
    """Return scrape hour — 5am on exception date, 2am otherwise (UTC)."""
    if date.today() == NICHE_SCRAPE_EXCEPTION_DATE:
        return 5
    return 2


async def seed_initial_niche_queue() -> None:
    for niche in INITIAL_36_NICHES:
        await ensure_niche_in_queue(niche, added_by="initial", priority=10)
