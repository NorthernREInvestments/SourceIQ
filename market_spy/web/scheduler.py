"""APScheduler jobs for database maintenance scrapes."""

from __future__ import annotations

from datetime import date, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from market_spy.web.database_builder import (
    NICHE_SCRAPE_EXCEPTION_DATE,
    run_nightly_new_niches,
    run_trend_refresh,
    run_weekly_sourcing_refresh,
    seed_initial_niche_queue,
)
from market_spy.web.logger import log_error, log_event

_scheduler: AsyncIOScheduler | None = None


async def _safe_job(name: str, coro_fn):
    try:
        log_event(f"scheduler job start: {name}")
        await coro_fn()
        log_event(f"scheduler job complete: {name}")
    except Exception as exc:
        log_error(f"scheduler:{name}", exc)


async def _trend_refresh_job():
    await _safe_job("trend_refresh", run_trend_refresh)


async def _nightly_new_niches_job():
    now = datetime.utcnow()
    if date.today() == NICHE_SCRAPE_EXCEPTION_DATE:
        if now.hour != 5:
            return
    elif now.hour != 2:
        return
    await _safe_job("nightly_new_niches", run_nightly_new_niches)


async def _weekly_sourcing_job():
    await _safe_job("weekly_sourcing_refresh", run_weekly_sourcing_refresh)


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _trend_refresh_job,
        CronTrigger(hour=1, minute=0),
        id="trend_refresh",
        replace_existing=True,
    )
    scheduler.add_job(
        _nightly_new_niches_job,
        CronTrigger(hour="2,5", minute=0),
        id="nightly_new_niches",
        replace_existing=True,
    )
    scheduler.add_job(
        _weekly_sourcing_job,
        CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="weekly_sourcing_refresh",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    log_event(
        "scheduler started: trend=01:00 UTC nightly=02:00 UTC (5 expand + 5 new) "
        f"(exception {NICHE_SCRAPE_EXCEPTION_DATE} at 05:00 UTC) sourcing=Sun 03:00 UTC"
    )
    return scheduler


async def bootstrap_database_queue() -> None:
    await seed_initial_niche_queue()
