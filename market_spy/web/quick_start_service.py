"""Quick Start background job tracking and execution."""

import asyncio
import json
from datetime import datetime

from market_spy.cli import QUICK_START_NICHES
from market_spy.web.database import get_database, increment_user_stage1
from market_spy.web.logger import log_error
from market_spy.web.search_service import run_stage1_search

ACTIVE_STATUSES = ("pending", "running")


def _now() -> str:
    return datetime.utcnow().isoformat()


def _summary_row(result: dict, niche: str) -> dict:
    return {
        "category": niche,
        "score": result.get("score"),
        "total_listings": result.get("total_listings", 0),
        "trends_direction": result.get("trends_direction", "stable"),
        "trends_change": result.get("trends_change", 0),
        "trends_found": bool(result.get("trends_found")),
    }


def _parse_results(raw: str) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


async def create_quick_start_job(user_id: int, total: int | None = None) -> dict:
    total = total or len(QUICK_START_NICHES)
    now = _now()
    row = await get_database().fetch_one(
        """
        INSERT INTO quick_start_jobs (
            user_id, status, total, completed, current_niche,
            results_json, error_message, stage1_credited, created_at, updated_at
        ) VALUES (
            :user_id, 'pending', :total, 0, '', '[]', '', 0, :created_at, :updated_at
        )
        RETURNING *
        """,
        {"user_id": user_id, "total": total, "created_at": now, "updated_at": now},
    )
    return dict(row)


async def get_quick_start_job(job_id: int, user_id: int) -> dict | None:
    row = await get_database().fetch_one(
        "SELECT * FROM quick_start_jobs WHERE id = :id AND user_id = :user_id",
        {"id": job_id, "user_id": user_id},
    )
    return dict(row) if row else None


async def get_active_quick_start_job(user_id: int) -> dict | None:
    row = await get_database().fetch_one(
        """
        SELECT * FROM quick_start_jobs
        WHERE user_id = :user_id AND status IN ('pending', 'running')
        ORDER BY id DESC
        LIMIT 1
        """,
        {"user_id": user_id},
    )
    return dict(row) if row else None


async def get_latest_quick_start_job(user_id: int) -> dict | None:
    row = await get_database().fetch_one(
        """
        SELECT * FROM quick_start_jobs
        WHERE user_id = :user_id
        ORDER BY id DESC
        LIMIT 1
        """,
        {"user_id": user_id},
    )
    return dict(row) if row else None


async def mark_job_running(job_id: int) -> bool:
    now = _now()
    row = await get_database().fetch_one(
        """
        UPDATE quick_start_jobs
        SET status = 'running', updated_at = :updated_at
        WHERE id = :id AND status = 'pending'
        RETURNING id
        """,
        {"id": job_id, "updated_at": now},
    )
    return row is not None


async def cancel_quick_start_job(job_id: int, user_id: int) -> bool:
    now = _now()
    row = await get_database().fetch_one(
        """
        UPDATE quick_start_jobs
        SET status = 'cancelled', current_niche = '', updated_at = :updated_at
        WHERE id = :id AND user_id = :user_id AND status IN ('pending', 'running')
        RETURNING id
        """,
        {"id": job_id, "user_id": user_id, "updated_at": now},
    )
    return row is not None


async def _save_job_progress(
    job_id: int,
    *,
    completed: int,
    current_niche: str,
    results: list[dict],
    status: str | None = None,
    error_message: str = "",
) -> None:
    now = _now()
    values = {
        "id": job_id,
        "completed": completed,
        "current_niche": current_niche,
        "results_json": json.dumps(results),
        "updated_at": now,
        "error_message": error_message,
    }
    if status:
        await get_database().execute(
            """
            UPDATE quick_start_jobs
            SET completed = :completed, current_niche = :current_niche,
                results_json = :results_json, updated_at = :updated_at,
                status = :status, error_message = :error_message
            WHERE id = :id
            """,
            {**values, "status": status},
        )
    else:
        await get_database().execute(
            """
            UPDATE quick_start_jobs
            SET completed = :completed, current_niche = :current_niche,
                results_json = :results_json, updated_at = :updated_at,
                error_message = :error_message
            WHERE id = :id
            """,
            values,
        )


async def _credit_stage1_if_needed(job: dict) -> None:
    if job.get("stage1_credited"):
        return
    completed = int(job.get("completed", 0))
    if completed <= 0:
        return
    await increment_user_stage1(job["user_id"], completed)
    await get_database().execute(
        "UPDATE quick_start_jobs SET stage1_credited = 1 WHERE id = :id",
        {"id": job["id"]},
    )


async def job_status_payload(job: dict) -> dict:
    results = _parse_results(job.get("results_json", "[]"))
    results.sort(key=lambda row: row.get("score") or 0, reverse=True)
    status = job.get("status", "pending")
    completed = int(job.get("completed", 0))
    total = int(job.get("total", len(QUICK_START_NICHES)))
    done = status in ("completed", "cancelled", "failed")
    return {
        "job_id": job["id"],
        "total": total,
        "completed": completed,
        "current_niche": job.get("current_niche") or "",
        "status": status,
        "results": results,
        "error_message": job.get("error_message") or "",
        "done": done,
    }


async def run_quick_start_job(job_id: int, user_id: int) -> None:
    """Background task: run Stage 1 for each preset niche, updating job progress."""
    job = await get_quick_start_job(job_id, user_id)
    if not job or job["status"] not in ACTIVE_STATUSES:
        return

    results = _parse_results(job.get("results_json", "[]"))
    completed = int(job.get("completed", 0))
    niches = QUICK_START_NICHES[completed:]

    try:
        for niche in niches:
            fresh = await get_quick_start_job(job_id, user_id)
            if not fresh or fresh["status"] == "cancelled":
                await _credit_stage1_if_needed(fresh or job)
                return

            await _save_job_progress(
                job_id,
                completed=completed,
                current_niche=niche,
                results=results,
            )

            try:
                raw = await asyncio.to_thread(run_stage1_search, niche)
                row = _summary_row(raw, niche)
            except Exception as exc:
                log_error(f"quick_start:{niche}", exc)
                row = {
                    "category": niche,
                    "score": 0,
                    "total_listings": 0,
                    "trends_direction": "stable",
                    "trends_change": 0,
                    "trends_found": False,
                    "error": str(exc),
                }

            results.append(row)
            completed += 1
            await _save_job_progress(
                job_id,
                completed=completed,
                current_niche="",
                results=results,
            )

        await _save_job_progress(
            job_id,
            completed=completed,
            current_niche="",
            results=results,
            status="completed",
        )
        final_job = await get_quick_start_job(job_id, user_id)
        if final_job:
            await _credit_stage1_if_needed(final_job)
    except Exception as exc:
        log_error("quick_start_job", exc)
        await _save_job_progress(
            job_id,
            completed=completed,
            current_niche="",
            results=results,
            status="failed",
            error_message=str(exc),
        )
        final_job = await get_quick_start_job(job_id, user_id)
        if final_job:
            await _credit_stage1_if_needed(final_job)


def ranked_results(job: dict) -> list[dict]:
    results = _parse_results(job.get("results_json", "[]"))
    results.sort(key=lambda row: row.get("score") or 0, reverse=True)
    return results


def quick_start_opportunity_label(score) -> dict:
    value = float(score or 0)
    if value > 40:
        return {"label": "GOOD", "css_class": "opp-good"}
    if value >= 35:
        return {"label": "MODERATE", "css_class": "opp-moderate"}
    return {"label": "LOW", "css_class": "opp-low"}


def display_category_name(category: str) -> str:
    return " ".join(word.capitalize() for word in (category or "").split())


def enrich_result_row(row: dict) -> dict:
    enriched = dict(row)
    opp = quick_start_opportunity_label(row.get("score"))
    enriched["display_name"] = display_category_name(row.get("category", ""))
    enriched["opportunity_label"] = opp["label"]
    enriched["opportunity_class"] = opp["css_class"]
    return enriched


def enrich_results(results: list[dict]) -> list[dict]:
    return [enrich_result_row(row) for row in results]


def _recommendation_line(row: dict, top_score: float, rank: int, tied_at_top: bool) -> str:
    name = display_category_name(row.get("category", ""))
    direction = row.get("trends_direction", "stable")
    score = float(row.get("score") or 0)

    if direction == "rising":
        if score >= top_score and tied_at_top:
            return f"{name} — Rising trend, tied for top score. Strong demand."
        if rank == 0:
            return f"{name} — Rising trend, highest score. Good starting point."
        return f"{name} — Rising trend. Worth a closer look."

    if direction == "falling" and score >= 35:
        return f"{name} — High score but falling trend. Research competition first."

    if direction == "stable" and score >= top_score * 0.9:
        return f"{name} — Steady demand with a strong score. Explore subcategories."

    if score < 35:
        return f"{name} — Lower score. Compare carefully before committing."

    return f"{name} — Moderate opportunity. Run Stage 1 for more detail."


def build_recommendations(results: list[dict], limit: int = 3) -> list[dict]:
    ranked = sorted(results, key=lambda row: row.get("score") or 0, reverse=True)
    if not ranked:
        return []

    top_score = float(ranked[0].get("score") or 0)
    tied_at_top = sum(
        1 for row in ranked if float(row.get("score") or 0) >= top_score
    ) > 1

    recommendations = []
    for index, row in enumerate(ranked[:limit]):
        recommendations.append({
            "category": row.get("category", ""),
            "display_name": display_category_name(row.get("category", "")),
            "explanation": _recommendation_line(row, top_score, index, tied_at_top),
        })
    return recommendations
