"""Product database builder — scheduled scrapes, live search, and storage."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from market_spy.analysis import (
    _extract_keywords,
    _keyword_overlap,
    compute_market_opportunity,
    enforce_recency_and_timestamps,
    resolve_broad_category,
)
from market_spy.cli import STAGE1_SCRAPERS, STAGE2_COMING_SOON, STAGE2_SCRAPERS
from market_spy.config import CREDIT_LOG_FILE
from market_spy.product_groups import (
    _group_signature,
    _is_selling,
    _is_sourcing,
    _margin_tier,
    build_product_groups,
)
from market_spy.trends import fetch_trends, fetch_trends_windows
from market_spy.web.database import (
    cancel_scrape_log as db_cancel_scrape_log,
    count_product_niches,
    count_products,
    create_scrape_log,
    ensure_niche_in_queue,
    fetch_all_product_niches,
    fetch_products_for_niche,
    finish_scrape_log,
    get_database,
    get_niches_needing_sourcing_refresh,
    get_running_scrape_logs,
    get_unscraped_niches,
    mark_niche_scraped,
    mark_niche_sourcing_refreshed,
    set_niche_needs_sourcing_refresh,
)
from market_spy.web.logger import log_error, log_event
from market_spy.web.search_service import (
    _build_trends_payload,
    _enrich_product_groups_async,
    _run_scrapers,
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

_trend_direction_values = {"rising": 1.0, "stable": 0.5, "falling": 0.0}

_live_scrape_tasks: dict[int, asyncio.Task] = {}
_cancelled_log_ids: set[int] = set()


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
    current_niche: str = ""


_batch_jobs: dict[str, BatchJob] = {}


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


def _sale_date_iso(item: dict) -> str | None:
    raw = item.get("date") or item.get("sale_date")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.isoformat()
    return str(raw)


def _credits_used_since(started_at: str) -> int:
    if not CREDIT_LOG_FILE:
        return 0
    try:
        with open(CREDIT_LOG_FILE, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return 0
    total = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("timestamp"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        ts, _source, _url, credits = parts[0], parts[1], parts[2], parts[3]
        if ts < started_at:
            continue
        try:
            total += int(credits)
        except ValueError:
            continue
    return total


def _stage2_scrapers():
    return [
        (label, func, kwargs)
        for label, func, kwargs in STAGE2_SCRAPERS
        if label not in STAGE2_COMING_SOON
    ]


def _full_scrape_items(niche: str) -> list[dict]:
    items = _run_scrapers(STAGE1_SCRAPERS, niche)
    items.extend(_run_scrapers(_stage2_scrapers(), niche))
    return enforce_recency_and_timestamps(items)


def _sourcing_scrape_items(niche: str) -> list[dict]:
    return enforce_recency_and_timestamps(_run_scrapers(_stage2_scrapers(), niche))


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
) -> tuple[bool, bool]:
    """Insert or update a product row. Returns (added, updated)."""
    db = get_database()
    now = _now_iso()
    existing = await db.fetch_one(
        """
        SELECT * FROM products
        WHERE LOWER(niche) = LOWER(:niche)
          AND product_url = :product_url
        """,
        {"niche": niche, "product_url": product_url or ""},
    )
    if existing:
        sale_date_keep = existing.get("sale_date") or sale_date
        if update_sourcing_only:
            await db.execute(
                """
                UPDATE products SET
                    source_price_min = :source_min,
                    source_price_max = :source_max,
                    source_platform = :source_platform,
                    source_verified_date = :source_verified_date,
                    last_updated = :last_updated
                WHERE id = :id
                """,
                {
                    "id": existing["id"],
                    "source_min": source_min,
                    "source_max": source_max,
                    "source_platform": source_platform,
                    "source_verified_date": source_verified_date or now,
                    "last_updated": now,
                },
            )
            return False, True

        sell_min = sell_price
        sell_max = sell_price
        if existing.get("selling_price_min") is not None:
            sell_min = min(float(existing["selling_price_min"]), sell_price)
        if existing.get("selling_price_max") is not None:
            sell_max = max(float(existing["selling_price_max"]), sell_price)
        sell_avg = round((sell_min + sell_max) / 2, 2)

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
            opportunity_score, created_at, last_updated, product_url
        ) VALUES (
            :niche, :subcategory, :product_group, :name,
            :sell_price, :sell_price, :sell_price,
            :selling_platform, :sale_date,
            :source_min, :source_max, :source_platform, :source_verified_date,
            :margin_pct, :margin_tier,
            :trend_24h, :trend_7d, :trend_30d, :trend_updated,
            :opportunity_score, :created_at, :last_updated, :product_url
        )
        """,
        {
            "niche": niche,
            "subcategory": subcategory,
            "product_group": product_group,
            "name": name,
            "sell_price": sell_price,
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
        },
    )
    return True, False


async def auto_create_product_cards(
    niche: str,
    items: list[dict],
    *,
    trends_windows: dict | None = None,
    opportunity_score: float | None = None,
    update_sourcing_only: bool = False,
) -> tuple[int, int]:
    """Persist scraped items as product rows grouped by product type."""
    items = enforce_recency_and_timestamps(items)
    if trends_windows is None:
        trends_windows = fetch_trends_windows(niche)
    trends = fetch_trends(niche)
    if opportunity_score is None:
        opportunity_score = round(float(compute_market_opportunity(items, trends)), 1)

    windows = trends_windows or {}
    trend_24h = (windows.get("24h") or {}).get("direction", "stable")
    trend_7d = (windows.get("7d") or {}).get("direction", "stable")
    trend_30d = (windows.get("30d") or {}).get("direction", "stable")
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


async def _scrape_and_store(
    niche: str,
    scrape_type: str,
    *,
    sourcing_only: bool = False,
    log_id: int | None = None,
    cancel_event: asyncio.Event | None = None,
) -> dict:
    started = _now_iso()
    if log_id is None:
        log_id = await create_scrape_log(niche, scrape_type)
    _check_cancelled(log_id=log_id, cancel_event=cancel_event)
    await ensure_niche_in_queue(niche, added_by=scrape_type)
    log_event(f"database scrape start: type={scrape_type} niche={niche!r} log_id={log_id}")
    try:
        if sourcing_only:
            items = await asyncio.to_thread(_sourcing_scrape_items, niche)
            _check_cancelled(log_id=log_id, cancel_event=cancel_event)
            trends_windows = fetch_trends_windows(niche)
            added, updated = await auto_create_product_cards(
                niche,
                items,
                trends_windows=trends_windows,
                update_sourcing_only=True,
            )
            await mark_niche_sourcing_refreshed(niche)
        else:
            items = await asyncio.to_thread(_full_scrape_items, niche)
            _check_cancelled(log_id=log_id, cancel_event=cancel_event)
            trends_windows = fetch_trends_windows(niche)
            trends = fetch_trends(niche)
            score = round(float(compute_market_opportunity(items, trends)), 1)
            added, updated = await auto_create_product_cards(
                niche,
                items,
                trends_windows=trends_windows,
                opportunity_score=score,
            )
            await mark_niche_scraped(niche)
        credits = _credits_used_since(started)
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
        return {
            "log_id": log_id,
            "niche": niche,
            "added": added,
            "updated": updated,
            "items": len(items),
        }
    except ScrapeCancelled as exc:
        await finish_scrape_log(
            log_id,
            status="cancelled",
            credits_used=_credits_used_since(started),
            error_message=str(exc),
        )
        raise
    except Exception as exc:
        log_error(f"database scrape:{scrape_type}", exc)
        await finish_scrape_log(
            log_id,
            status="failed",
            credits_used=_credits_used_since(started),
            error_message=str(exc),
        )
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
    for niche in INITIAL_36_NICHES:
        if cancel_event and cancel_event.is_set():
            summary["cancelled"] = True
            log_event(f"initial scrape batch {batch_id} stopped after {len(summary['niches'])} niches")
            break
        if job:
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


async def run_nightly_new_niches() -> dict:
    """Nightly job — sourcing refresh for trending niches first, then 10 new niches."""
    summary = {"sourcing_refreshed": [], "new_scraped": [], "errors": []}

    refresh_niches = await get_niches_needing_sourcing_refresh(limit=10)
    for niche in refresh_niches:
        try:
            await _scrape_and_store(niche, "weekly_sourcing", sourcing_only=True)
            summary["sourcing_refreshed"].append(niche)
        except Exception as exc:
            summary["errors"].append({"niche": niche, "error": str(exc), "type": "sourcing"})

    remaining = max(0, 10 - len(summary["sourcing_refreshed"]))
    if remaining:
        new_niches = await get_unscraped_niches(limit=remaining)
        for niche in new_niches:
            try:
                await _scrape_and_store(niche, "nightly_new")
                summary["new_scraped"].append(niche)
            except Exception as exc:
                summary["errors"].append({"niche": niche, "error": str(exc), "type": "nightly"})

    log_event(
        f"nightly scrape finished: sourcing={len(summary['sourcing_refreshed'])} "
        f"new={len(summary['new_scraped'])}"
    )
    return summary


async def run_weekly_sourcing_refresh() -> dict:
    """Refresh sourcing prices for all niches already in the database."""
    summary = {"refreshed": [], "errors": []}
    niches = await fetch_all_product_niches()
    for niche in niches:
        try:
            await _scrape_and_store(niche, "weekly_sourcing", sourcing_only=True)
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
                credits_used=_credits_used_since(started),
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
