"""Background drill-down job tracking and execution."""

import asyncio
import json
from datetime import datetime

from market_spy.web.database import (
    get_database,
    get_user_by_id,
    increment_user_stage2,
    is_pro_user,
    margin_meta_from_stage2,
    save_price_history,
    add_search_history,
)
from market_spy.web.logger import log_error
from market_spy.web.search_service import run_stage2_drilldown

ACTIVE_STATUSES = ("pending", "running")


def _now() -> str:
    return datetime.utcnow().isoformat()


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


def parse_drilldown_result(job: dict) -> dict | None:
    raw = job.get("result_json") or ""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


async def job_status_payload(job: dict) -> dict:
    status = job.get("status", "pending")
    done = status in ("completed", "failed")
    return {
        "job_id": job["id"],
        "niche": job.get("niche") or "",
        "status": status,
        "error_message": job.get("error_message") or "",
        "done": done,
        "return_to": job.get("return_to") or "/dashboard",
    }


async def run_drilldown_job(job_id: int, user_id: int) -> None:
    job = await get_drilldown_job(job_id, user_id)
    if not job or job["status"] not in ACTIVE_STATUSES:
        return

    niche = job["niche"]
    parent = job.get("parent_category") or ""

    try:
        result = await asyncio.to_thread(run_stage2_drilldown, niche)
        result["parent_category"] = parent
        await _save_drilldown_job(
            job_id,
            status="completed",
            result_json=json.dumps(result),
        )
        final_job = await get_drilldown_job(job_id, user_id)
        if final_job:
            await _credit_stage2_if_needed(final_job, result)
    except Exception as exc:
        log_error(f"drilldown:{niche}", exc)
        await _save_drilldown_job(
            job_id,
            status="failed",
            error_message=str(exc),
        )
