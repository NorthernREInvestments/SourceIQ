"""Background drill-down job tracking and execution."""

import asyncio
import json
from datetime import datetime

from market_spy.cli import STAGE2_COMING_SOON, STAGE2_SCRAPERS
from market_spy.config import SCRAPINGBEE_REQUEST_TIMEOUT
from market_spy.web.database import (
    get_database,
    get_user_by_id,
    increment_user_stage2,
    is_pro_user,
    margin_meta_from_stage2,
    save_price_history,
    add_search_history,
)
from market_spy.web.json_util import dumps_json_safe
from market_spy.web.logger import log_error, log_event
from market_spy.web.search_service import build_stage2_result

ACTIVE_STATUSES = ("pending", "running")


def _now() -> str:
    return datetime.utcnow().isoformat()


def _active_stage2_scrapers():
    return [
        (label, func, kwargs)
        for label, func, kwargs in STAGE2_SCRAPERS
        if label not in STAGE2_COMING_SOON
    ]


def _progress_payload(sources: list[dict], current: str | None = None) -> dict:
    return {"_progress": True, "sources": sources, "current": current}


def format_sources_progress_line(sources: list[dict]) -> str:
    """Plain-text line like: Amazon ✓ Walmart ✓ AliExpress waiting..."""
    parts = []
    for row in sources:
        label = row.get("label", "")
        status = row.get("status", "waiting")
        if status == "done":
            parts.append(f"{label} ✓")
        elif status == "running":
            parts.append(f"{label}...")
        elif status == "timeout":
            parts.append(f"{label} timed out")
        elif status == "error":
            parts.append(f"{label} failed")
        else:
            parts.append(f"{label} waiting...")
    return " ".join(parts)


async def create_drilldown_job(
    user_id: int,
    niche: str,
    *,
    parent_category: str = "",
    return_to: str = "/dashboard",
) -> dict:
    now = _now()
    row = await get_database().fetch_one(
        """
        INSERT INTO drilldown_jobs (
            user_id, niche, parent_category, return_to, status,
            result_json, error_message, stage2_credited, created_at, updated_at
        ) VALUES (
            :user_id, :niche, :parent_category, :return_to, 'pending',
            '', '', 0, :created_at, :updated_at
        )
        RETURNING *
        """,
        {
            "user_id": user_id,
            "niche": niche,
            "parent_category": parent_category or "",
            "return_to": return_to or "/dashboard",
            "created_at": now,
            "updated_at": now,
        },
    )
    return dict(row)


async def get_drilldown_job(job_id: int, user_id: int) -> dict | None:
    row = await get_database().fetch_one(
        "SELECT * FROM drilldown_jobs WHERE id = :id AND user_id = :user_id",
        {"id": job_id, "user_id": user_id},
    )
    return dict(row) if row else None


async def get_active_drilldown_job(user_id: int) -> dict | None:
    row = await get_database().fetch_one(
        """
        SELECT * FROM drilldown_jobs
        WHERE user_id = :user_id AND status IN ('pending', 'running')
        ORDER BY id DESC
        LIMIT 1
        """,
        {"user_id": user_id},
    )
    return dict(row) if row else None


async def mark_drilldown_running(job_id: int) -> bool:
    now = _now()
    row = await get_database().fetch_one(
        """
        UPDATE drilldown_jobs
        SET status = 'running', updated_at = :updated_at
        WHERE id = :id AND status = 'pending'
        RETURNING id
        """,
        {"id": job_id, "updated_at": now},
    )
    return row is not None


async def _save_drilldown_job(
    job_id: int,
    *,
    status: str | None = None,
    result_json: str = "",
    error_message: str = "",
) -> None:
    now = _now()
    if status:
        await get_database().execute(
            """
            UPDATE drilldown_jobs
            SET status = :status, result_json = :result_json,
                error_message = :error_message, updated_at = :updated_at
            WHERE id = :id
            """,
            {
                "id": job_id,
                "status": status,
                "result_json": result_json,
                "error_message": error_message,
                "updated_at": now,
            },
        )
    else:
        await get_database().execute(
            """
            UPDATE drilldown_jobs
            SET result_json = :result_json, error_message = :error_message,
                updated_at = :updated_at
            WHERE id = :id
            """,
            {
                "id": job_id,
                "result_json": result_json,
                "error_message": error_message,
                "updated_at": now,
            },
        )


async def _save_drilldown_progress(
    job_id: int,
    sources: list[dict],
    *,
    current: str | None = None,
) -> None:
    await _save_drilldown_job(
        job_id,
        result_json=dumps_json_safe(_progress_payload(sources, current)),
    )


async def _run_scraper_with_timeout(
    label: str, func, niche: str, kwargs: dict
) -> tuple[list, str]:
    log_event(f"drilldown scraper start: {label} niche={niche!r}")
    try:
        batch = await asyncio.wait_for(
            asyncio.to_thread(func, niche, **kwargs),
            timeout=SCRAPINGBEE_REQUEST_TIMEOUT,
        )
        items = batch or []
        log_event(f"drilldown scraper complete: {label} items={len(items)}")
        return items, "done"
    except asyncio.TimeoutError:
        log_event(
            f"drilldown scraper timeout: {label} niche={niche!r} "
            f"after {SCRAPINGBEE_REQUEST_TIMEOUT}s"
        )
        return [], "timeout"
    except Exception as exc:
        log_error(f"drilldown scraper:{label}", exc)
        log_event(f"drilldown scraper error: {label} niche={niche!r} error={exc}")
        return [], "error"


async def _credit_stage2_if_needed(job: dict, result: dict) -> None:
    if job.get("stage2_credited"):
        return
    user = await get_user_by_id(job["user_id"])
    if not user:
        return
    niche = job["niche"]
    by_tier = result.get("by_tier") or {}
    await increment_user_stage2(job["user_id"], 1)
    tier_key, summary = margin_meta_from_stage2(by_tier)
    await add_search_history(
        job["user_id"],
        niche,
        2,
        margin_tier=tier_key,
        margin_summary=summary,
    )
    if is_pro_user(user):
        await save_price_history(
            job["user_id"],
            niche,
            (by_tier.get("budget") or {}).get("tier_margin_percent"),
            (by_tier.get("mid") or {}).get("tier_margin_percent"),
            (by_tier.get("premium") or {}).get("tier_margin_percent"),
        )
    await get_database().execute(
        "UPDATE drilldown_jobs SET stage2_credited = 1 WHERE id = :id",
        {"id": job["id"]},
    )


def parse_drilldown_progress(job: dict) -> dict | None:
    raw = job.get("result_json") or ""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("_progress"):
            return data
    except json.JSONDecodeError:
        pass
    return None


def parse_drilldown_result(job: dict) -> dict | None:
    raw = job.get("result_json") or ""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and not data.get("_progress"):
            return data
    except json.JSONDecodeError:
        return None
    return None


async def job_status_payload(job: dict) -> dict:
    status = job.get("status", "pending")
    done = status in ("completed", "failed")
    progress = parse_drilldown_progress(job)
    sources = (progress or {}).get("sources") or []
    sources_line = format_sources_progress_line(sources) if sources else ""
    return {
        "job_id": job["id"],
        "niche": job.get("niche") or "",
        "status": status,
        "error_message": job.get("error_message") or "",
        "done": done,
        "return_to": job.get("return_to") or "/dashboard",
        "sources": sources,
        "sources_line": sources_line,
        "current_source": (progress or {}).get("current") or "",
    }


async def run_drilldown_job(job_id: int, user_id: int) -> None:
    job = await get_drilldown_job(job_id, user_id)
    if not job or job["status"] not in ACTIVE_STATUSES:
        return

    niche = job["niche"]
    parent = job.get("parent_category") or ""
    scrapers = _active_stage2_scrapers()
    sources_status = [
        {"label": label, "status": "waiting", "count": 0}
        for label, _, _ in scrapers
    ]

    try:
        log_event(f"drilldown job start: job_id={job_id} niche={niche!r}")
        await _save_drilldown_progress(job_id, sources_status)

        items = []
        for index, (label, func, kwargs) in enumerate(scrapers):
            sources_status[index]["status"] = "running"
            await _save_drilldown_progress(job_id, sources_status, current=label)

            batch, outcome = await _run_scraper_with_timeout(label, func, niche, kwargs)
            sources_status[index]["status"] = outcome
            sources_status[index]["count"] = len(batch)
            items.extend(batch)
            await _save_drilldown_progress(job_id, sources_status)

        if not items:
            message = (
                "No listings found from any source. "
                "Try a more specific product or check back later."
            )
            log_event(f"drilldown job no results: job_id={job_id} niche={niche!r}")
            await _save_drilldown_job(
                job_id,
                status="failed",
                error_message=message,
            )
            return

        result = await asyncio.to_thread(build_stage2_result, niche, items)
        result["parent_category"] = parent
        await _save_drilldown_job(
            job_id,
            status="completed",
            result_json=dumps_json_safe(result),
        )
        log_event(
            f"drilldown job complete: job_id={job_id} niche={niche!r} "
            f"items={len(items)}"
        )
        final_job = await get_drilldown_job(job_id, user_id)
        if final_job:
            await _credit_stage2_if_needed(final_job, result)
    except Exception as exc:
        log_error(f"drilldown:{niche}", exc)
        log_event(f"drilldown job failed: job_id={job_id} error={exc}")
        await _save_drilldown_job(
            job_id,
            status="failed",
            error_message=str(exc),
        )
