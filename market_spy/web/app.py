"""SourceIQ FastAPI web application (Railway-hosted)."""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import os
import secrets
from datetime import datetime, timezone
from urllib.parse import quote, unquote

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from market_spy.cli import QUICK_START_NICHES
from market_spy.config import (
    STAGE1_UPGRADE_MESSAGE,
    STAGE2_UPGRADE_MESSAGE,
    TEST_ACCOUNT_EMAIL,
    scrapingbee_key_prefix,
)
from market_spy.web.admin_service import get_admin_stats, get_scrape_status_payload
from market_spy.web.constants import (
    SEARCH_TIP,
    STAGE1_DISCLAIMER,
    STAGE2_DISCLAIMER,
    UPGRADE_URL,
    can_user_export_csv,
    renewal_date_for_user,
)
from market_spy.web.database import (
    add_search_history,
    add_watchlist_item,
    authenticate_user,
    can_user_stage1,
    can_user_stage2,
    cancel_user_subscription,
    check_all_trial_expiries,
    check_database_connected,
    check_trial_expiry,
    create_user,
    create_user_with_tier,
    get_price_history,
    get_remaining_for_user,
    get_search_history,
    get_search_history_entry,
    get_public_stats,
    get_stage1_result,
    ensure_niche_in_queue,
    get_user_by_email,
    get_user_by_id,
    get_watchlist,
    increment_user_stage1,
    increment_user_stage2,
    disconnect_db,
    init_db,
    is_pro_user,
    margin_meta_from_stage2,
    remove_watchlist_item,
    save_price_history,
    save_stage1_result,
    update_user_password,
    update_user_scrapingbee_key,
    update_user_tier_and_password,
    update_watchlist_after_search,
    user_has_completed_search,
    uses_postgres,
)
from market_spy.web.email_service import send_password_reset, send_trial_expired_email
from market_spy.web.export_web import export_stage2_csv_web
from market_spy.web.health import check_scrapingbee_connected
from market_spy.web.json_util import json_safe
from market_spy.web.logger import log_request, log_error
from market_spy.web.messages import EMPTY_SEARCH_MESSAGE, SEARCH_PENDING_MESSAGE
from market_spy.web.password_tokens import generate_reset_token, verify_reset_token
from market_spy.web.drilldown_service import (
    create_drilldown_job,
    get_drilldown_job,
    job_status_payload as drilldown_status_payload,
    mark_drilldown_running,
    parse_drilldown_result,
    run_drilldown_job,
)
from market_spy.web.quick_start_service import (
    cancel_quick_start_job,
    create_quick_start_job,
    display_category_name,
    enrich_results,
    get_active_quick_start_job,
    get_latest_quick_start_job,
    get_quick_start_job,
    job_status_payload,
    mark_job_running,
    ranked_results,
    run_quick_start_job,
)
from market_spy.web.search_service import (
    build_stage2_summary,
    is_broad_category,
    items_from_serializable,
    run_stage1_search_async,
    _subcategory_insight_line,
)
from market_spy.web.stripe_service import (
    construct_webhook_event,
    create_checkout_session,
    handle_checkout_success,
    handle_webhook_event,
)
from market_spy.web.scheduler import bootstrap_database_queue, start_scheduler
from market_spy.web.database_builder import (
    cancel_all_running_scrapes,
    cancel_batch_job,
    cancel_scrape_log_by_id,
    run_trend_refresh,
    search_database,
    trigger_live_scrape,
    complete_live_scrape_if_ready,
    try_start_initial_scrape,
)
from market_spy.web.seed_accounts import ensure_default_accounts
from market_spy.web.startup_check import check_required_env_vars

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(WEB_DIR, "templates")
APP_VERSION = "1.0.0"

app = FastAPI(title="SourceIQ", description="Find winning dropshipping products")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", secrets.token_hex(32)),
    session_cookie="sourceiq_session",
    max_age=14 * 24 * 60 * 60,
    same_site="lax",
    https_only=os.getenv("RAILWAY_ENVIRONMENT") == "production",
)

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["urlencode_path"] = lambda value: quote(str(value), safe="")


TRIAL_CHECK_INTERVAL_SECONDS = 3600
_admin_security = HTTPBasic(auto_error=False)


def _require_admin(credentials: HTTPBasicCredentials = Depends(_admin_security)):
    expected_user = (os.getenv("ADMIN_USERNAME", "admin").strip() or "admin")
    expected_password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not expected_password:
        raise HTTPException(
            status_code=503,
            detail="Admin dashboard is not configured (set ADMIN_PASSWORD).",
            headers={"WWW-Authenticate": "Basic"},
        )
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Admin authentication required.",
            headers={"WWW-Authenticate": "Basic"},
        )
    username_ok = secrets.compare_digest(credentials.username or "", expected_user)
    password_ok = secrets.compare_digest(credentials.password or "", expected_password)
    if not username_ok or not password_ok:
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


async def _trial_expiry_loop():
    while True:
        try:
            expired = await check_all_trial_expiries(send_expiry_email=send_trial_expired_email)
            if expired:
                print(f"[trial] downgraded {expired} expired trial user(s)", flush=True)
        except Exception as exc:
            log_error("trial_expiry_loop", exc)
        await asyncio.sleep(TRIAL_CHECK_INTERVAL_SECONDS)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    response = await call_next(request)
    log_request(request.method, request.url.path, response.status_code)
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    log_error(request.url.path, exc)
    accept = request.headers.get("accept", "")
    if "application/json" in accept or request.url.path.startswith("/health"):
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )
    return HTMLResponse(
        status_code=500,
        content=(
            "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem;'>"
            "<h1>Something went wrong</h1>"
            "<p>We've logged the error. Please try again or contact support.</p>"
            "<a href='/dashboard'>Back to dashboard</a>"
            "</body></html>"
        ),
    )


@app.on_event("startup")
async def on_startup():
    check_required_env_vars()
    backend = "PostgreSQL" if uses_postgres() else "SQLite (local)"
    print(f"[startup] Database backend: {backend}", flush=True)
    key_prefix = scrapingbee_key_prefix() or "(not set)"
    print(f"[startup] SCRAPINGBEE_API_KEY prefix: {key_prefix}", flush=True)
    try:
        from market_spy.browser import PLAYWRIGHT_AVAILABLE
    except Exception:
        PLAYWRIGHT_AVAILABLE = False
    print(
        f"[startup] Playwright available: {PLAYWRIGHT_AVAILABLE} "
        "(Stage 1 only; Stage 2 uses ScrapingBee, not local browsers)",
        flush=True,
    )
    await init_db()
    await ensure_default_accounts()
    await bootstrap_database_queue()
    start_scheduler()
    asyncio.create_task(_trial_expiry_loop())


@app.on_event("shutdown")
async def on_shutdown():
    await disconnect_db()


async def _ensure_trial_valid(user: dict) -> dict:
    """Refresh user after trial expiry check."""
    if user:
        await check_trial_expiry(user["id"], send_expiry_email=send_trial_expired_email)
        return await get_user_by_id(user["id"])
    return user


async def _record_stage1(user_id: int, category: str, result: dict):
    await add_search_history(user_id, category, 1, opportunity_score=result.get("score"))
    await update_watchlist_after_search(user_id, category, result.get("score", 0))


async def _record_stage2(user_id: int, subcategory: str, by_tier: dict, user: dict):
    tier_key, summary = margin_meta_from_stage2(by_tier or {})
    await add_search_history(
        user_id,
        subcategory,
        2,
        margin_tier=tier_key,
        margin_summary=summary,
    )
    if is_pro_user(user):
        await save_price_history(
            user_id,
            subcategory,
            (by_tier.get("budget") or {}).get("tier_margin_percent"),
            (by_tier.get("mid") or {}).get("tier_margin_percent"),
            (by_tier.get("premium") or {}).get("tier_margin_percent"),
        )


def _apply_stage2_session(request: Request, result: dict, subcategory: str):
    summary = build_stage2_summary(result)
    request.session["stage2_result"] = {
        "subcategory": result["subcategory"],
        "parent_category": result.get("parent_category", ""),
        "product_family": result.get("product_family"),
        "total_listings": result.get("total_listings"),
        "sources": result.get("sources"),
        "by_tier": result.get("by_tier"),
        "summary": summary,
        "has_matches": summary.get("has_matches", False),
    }
    request.session["stage2_export"] = {
        "subcategory": subcategory,
        "items": result.get("items_serializable", []),
        "margin": json_safe(result.get("margin_raw")),
    }


async def _store_stage1_result(request: Request, user_id: int, category: str, result: dict):
    result_id = await save_stage1_result(user_id, category, result)
    request.session["stage1_result_id"] = result_id
    request.session.pop("stage1_result", None)


def _normalize_stage1_result(result: dict) -> dict:
    """Ensure cached search payloads expose product groups."""
    normalized = dict(result)
    if normalized.get("view_mode") == "product_groups":
        normalized.setdefault("groups", [])
        return normalized
    if normalized.get("groups"):
        normalized["view_mode"] = "product_groups"
        return normalized
    category = normalized.get("category", "")
    for sub in normalized.get("subcategories") or []:
        if not sub.get("insight_line"):
            sub["insight_line"] = _subcategory_insight_line(sub)
    for product in normalized.get("products") or []:
        if not product.get("drill_term"):
            product["drill_term"] = ((product.get("name") or "").strip() or category)[:100]
    if normalized.get("view_mode"):
        if normalized["view_mode"] == "products" and not normalized.get("products"):
            legacy = normalized.get("top_products") or normalized.get("all_products") or []
            normalized["products"] = legacy
        return normalized
    if normalized.get("subcategories"):
        normalized["view_mode"] = "subcategories"
    else:
        normalized["view_mode"] = "products"
        normalized["products"] = (
            normalized.get("products")
            or normalized.get("top_products")
            or normalized.get("all_products")
            or []
        )
    return normalized


def _freshness_hours(result: dict) -> int | None:
    raw = result.get("data_updated_at") or ""
    if not raw:
        return None
    try:
        updated = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - updated
        return max(1, int(delta.total_seconds() // 3600) or 1)
    except (TypeError, ValueError):
        return None


async def _load_stage1_result(request: Request, user_id: int) -> dict | None:
    raw_id = request.session.get("stage1_result_id")
    if raw_id is not None:
        try:
            cached = await get_stage1_result(user_id, int(raw_id))
            if cached:
                return _normalize_stage1_result(cached)
        except (TypeError, ValueError):
            pass
    legacy = request.session.get("stage1_result")
    return _normalize_stage1_result(legacy) if isinstance(legacy, dict) else None


async def _hydrate_stage2_from_job(request: Request, user_id: int) -> bool:
    raw_id = request.session.get("drilldown_job_id")
    if raw_id is None:
        return False
    try:
        job_id = int(raw_id)
    except (TypeError, ValueError):
        return False
    job = await get_drilldown_job(job_id, user_id)
    if not job or job.get("status") != "completed":
        return False
    result = parse_drilldown_result(job)
    if not result:
        return False
    _apply_stage2_session(request, result, result.get("subcategory", job["niche"]))
    return True


def _drilldown_job_id(request: Request) -> int | None:
    raw = request.session.get("drilldown_job_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


async def _current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return await get_user_by_id(int(user_id))


async def _require_user(request: Request):
    user = await _current_user(request)
    if not user:
        return None
    return await _ensure_trial_valid(user)


def _flash(request: Request, message: str, level: str = "info"):
    request.session["flash"] = {"message": message, "level": level}


def _pop_flash(request: Request):
    return request.session.pop("flash", None)


def _nav_context(request: Request, user):
    return {
        "request": request,
        "user": user,
        "remaining": get_remaining_for_user(user),
        "search_tip": SEARCH_TIP,
        "stage1_disclaimer": STAGE1_DISCLAIMER,
        "stage2_disclaimer": STAGE2_DISCLAIMER,
        "upgrade_url": UPGRADE_URL,
    }


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse(
        "landing.html",
        {"request": request, "user": await _current_user(request)},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if await _current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None},
    )


@app.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    remember_me: str = Form(default=""),
):
    user, error = await authenticate_user(email, password)
    if error:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": error},
            status_code=400,
        )
    request.session["user_id"] = user["id"]
    if remember_me:
        request.session["remember"] = True
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if await _current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "error": None},
    )


@app.post("/register")
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    accept_terms: str = Form(default=""),
    accept_billing: str = Form(default=""),
):
    if not accept_terms:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "You must accept the terms to register."},
            status_code=400,
        )
    if not accept_billing:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": "You must acknowledge the trial billing terms before signing up.",
            },
            status_code=400,
        )
    user, error = await create_user(email, password)
    if error:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": error},
            status_code=400,
        )
    request.session["user_id"] = user["id"]
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user)
    ctx["flash"] = _pop_flash(request)
    ctx["quick_start_count"] = len(QUICK_START_NICHES)
    ctx["show_welcome_banner"] = not await user_has_completed_search(user["id"])
    ctx["db_stats"] = await get_public_stats()
    ctx["is_pro"] = is_pro_user(user)
    return templates.TemplateResponse("dashboard.html", ctx)


@app.post("/search")
async def search(
    request: Request,
    category: str = Form(...),
    return_to: str = Form(default="/dashboard"),
    product_view: str = Form(default=""),
):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    user = await get_user_by_id(user["id"])
    back = _safe_return_path(return_to)
    if not can_user_stage1(user):
        _flash(request, STAGE1_UPGRADE_MESSAGE, "error")
        return RedirectResponse(back, status_code=303)
    category = category.strip()
    if not category:
        _flash(request, "Please enter a niche to research.", "error")
        return RedirectResponse(back, status_code=303)

    await ensure_niche_in_queue(category, added_by="user")

    db_search = await search_database(category)
    if db_search.get("count", 0) >= 10 and db_search.get("result"):
        result = db_search["result"]
        await _record_stage1(user["id"], category, result)
        await _store_stage1_result(request, user["id"], category, result)
        return RedirectResponse("/results", status_code=303)

    try:
        log_id = await trigger_live_scrape(category)
        request.session["live_scrape_log_id"] = log_id
        request.session["live_scrape_query"] = category
        request.session.pop("stage1_result_id", None)
        _flash(request, SEARCH_PENDING_MESSAGE, "info")
        return RedirectResponse("/results", status_code=303)
    except Exception as exc:
        _flash(request, f"Search failed: {exc}", "error")
        return RedirectResponse(back, status_code=303)


@app.post("/quick-start")
async def quick_start_begin(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    user = await get_user_by_id(user["id"])
    needed = len(QUICK_START_NICHES)
    if not can_user_stage1(user, needed):
        _flash(
            request,
            f"Quick Start needs {needed} Stage 1 searches. {STAGE1_UPGRADE_MESSAGE}",
            "error",
        )
        return RedirectResponse("/dashboard", status_code=303)

    active = await get_active_quick_start_job(user["id"])
    if active:
        request.session["quick_start_job_id"] = active["id"]
        return RedirectResponse("/quick-start/progress", status_code=303)

    job = await create_quick_start_job(user["id"], needed)
    request.session["quick_start_job_id"] = job["id"]
    return RedirectResponse("/quick-start/progress", status_code=303)


def _quick_start_job_id(request: Request) -> int | None:
    raw = request.session.get("quick_start_job_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


@app.get("/quick-start/progress", response_class=HTMLResponse)
async def quick_start_progress(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    job_id = _quick_start_job_id(request)
    job = None
    if job_id:
        job = await get_quick_start_job(job_id, user["id"])
    if not job:
        job = await get_active_quick_start_job(user["id"])
    if not job:
        _flash(request, "No Quick Start scan in progress.", "error")
        return RedirectResponse("/dashboard", status_code=303)
    request.session["quick_start_job_id"] = job["id"]
    return templates.TemplateResponse(
        "quick_start_progress.html",
        {
            "request": request,
            "job_id": job["id"],
            "total": job["total"],
        },
    )


@app.post("/quick-start/run")
async def quick_start_run(request: Request, background_tasks: BackgroundTasks):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    job_id = _quick_start_job_id(request)
    if not job_id:
        raise HTTPException(status_code=404, detail="No Quick Start job found")
    job = await get_quick_start_job(job_id, user["id"])
    if not job:
        raise HTTPException(status_code=404, detail="Quick Start job not found")
    if job["status"] in ("completed", "cancelled", "failed"):
        return {"ok": True, "already_done": True}
    if job["status"] == "pending":
        started = await mark_job_running(job_id)
        if started:
            background_tasks.add_task(run_quick_start_job, job_id, user["id"])
    return {"ok": True}


@app.get("/quick-start/status")
async def quick_start_status(request: Request):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    job_id = _quick_start_job_id(request)
    job = None
    if job_id:
        job = await get_quick_start_job(job_id, user["id"])
    if not job:
        job = await get_active_quick_start_job(user["id"])
    if not job:
        job = await get_latest_quick_start_job(user["id"])
    if not job:
        raise HTTPException(status_code=404, detail="No Quick Start job found")
    return await job_status_payload(job)


@app.post("/quick-start/cancel")
async def quick_start_cancel(request: Request):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    job_id = _quick_start_job_id(request)
    if not job_id:
        raise HTTPException(status_code=404, detail="No Quick Start job found")
    await cancel_quick_start_job(job_id, user["id"])
    return {"ok": True}


def _safe_return_path(path: str, default: str = "/dashboard") -> str:
    path = (path or "").strip()
    if path.startswith("/") and not path.startswith("//"):
        return path
    return default


@app.get("/quick-start/results", response_class=HTMLResponse)
async def quick_start_results_page(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    job_id = _quick_start_job_id(request)
    job = None
    if job_id:
        job = await get_quick_start_job(job_id, user["id"])
    if not job:
        job = await get_latest_quick_start_job(user["id"])
    if not job or job["status"] not in ("completed", "cancelled"):
        _flash(request, "Complete a Quick Start scan to see results.", "info")
        return RedirectResponse("/dashboard", status_code=303)
    results = ranked_results(job)
    ctx = _nav_context(request, user)
    ctx.update({
        "results": enrich_results(results),
        "empty_search_message": EMPTY_SEARCH_MESSAGE,
        "flash": _pop_flash(request),
        "is_pro": is_pro_user(user),
    })
    return templates.TemplateResponse("quick_start_results.html", ctx)


@app.get("/results", response_class=HTMLResponse)
async def results_page(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    live_log_id = request.session.get("live_scrape_log_id")
    live_query = request.session.get("live_scrape_query") or ""
    search_pending = False

    if live_log_id:
        try:
            log_id = int(live_log_id)
        except (TypeError, ValueError):
            log_id = None
        if log_id:
            pending_result = await complete_live_scrape_if_ready(log_id, live_query)
            if pending_result and pending_result.get("failed"):
                request.session.pop("live_scrape_log_id", None)
                request.session.pop("live_scrape_query", None)
                _flash(request, pending_result.get("error") or "Live scrape failed.", "error")
            elif pending_result:
                await increment_user_stage1(user["id"], 1)
                await _record_stage1(user["id"], live_query, pending_result)
                await _store_stage1_result(request, user["id"], live_query, pending_result)
                request.session.pop("live_scrape_log_id", None)
                request.session.pop("live_scrape_query", None)
            else:
                search_pending = True

    result = await _load_stage1_result(request, user["id"])
    if not result and search_pending:
        ctx = _nav_context(request, user)
        ctx.update({
            "result": {"category": live_query},
            "groups": [],
            "has_groups": False,
            "search_pending": True,
            "pending_message": SEARCH_PENDING_MESSAGE,
            "empty_search_message": SEARCH_PENDING_MESSAGE,
            "freshness_hours": None,
            "is_pro": is_pro_user(user),
            "flash": _pop_flash(request),
        })
        return templates.TemplateResponse("results.html", ctx)
    if not result:
        return RedirectResponse("/dashboard", status_code=303)
    groups = result.get("groups") or []
    ctx = _nav_context(request, user)
    ctx.update({
        "result": result,
        "groups": groups,
        "has_groups": bool(groups),
        "search_pending": search_pending,
        "pending_message": SEARCH_PENDING_MESSAGE if search_pending else "",
        "empty_search_message": EMPTY_SEARCH_MESSAGE,
        "freshness_hours": _freshness_hours(result) if groups else None,
        "is_pro": is_pro_user(user),
        "flash": _pop_flash(request),
    })
    return templates.TemplateResponse("results.html", ctx)


@app.get("/search/live-status")
async def search_live_status(request: Request):
    user = await _current_user(request)
    if not user:
        return JSONResponse({"status": "unauthorized"}, status_code=401)
    raw_id = request.session.get("live_scrape_log_id")
    query = request.session.get("live_scrape_query") or ""
    if raw_id is None:
        return JSONResponse({"status": "idle"})
    try:
        log_id = int(raw_id)
    except (TypeError, ValueError):
        return JSONResponse({"status": "idle"})
    payload = await complete_live_scrape_if_ready(log_id, query)
    if payload and payload.get("failed"):
        return JSONResponse({"status": "failed", "error": payload.get("error")})
    if payload:
        return JSONResponse({"status": "complete", "redirect": "/results"})
    return JSONResponse({"status": "running"})


@app.get("/results/stage1", response_class=HTMLResponse)
async def results_stage1(request: Request):
    return RedirectResponse("/results", status_code=303)


@app.get("/disclaimer", response_class=HTMLResponse)
async def disclaimer_page(request: Request):
    user = await _current_user(request)
    ctx = {
        "request": request,
        "stage1_disclaimer": STAGE1_DISCLAIMER,
        "stage2_disclaimer": STAGE2_DISCLAIMER,
    }
    if user:
        ctx.update(_nav_context(request, user))
    return templates.TemplateResponse("disclaimer.html", ctx)


@app.post("/drilldown")
async def drilldown_begin(
    request: Request,
    category: str = Form(default=""),
    subcategory: str = Form(...),
    return_to: str = Form(default="/dashboard"),
):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    user = await get_user_by_id(user["id"])
    back = _safe_return_path(return_to)
    if not can_user_stage2(user):
        _flash(request, STAGE2_UPGRADE_MESSAGE, "error")
        return RedirectResponse(back, status_code=303)
    niche = subcategory.strip()
    if not niche:
        _flash(request, "Please enter a subcategory to check profit margins.", "error")
        return RedirectResponse(back, status_code=303)
    parent = category.strip() or request.session.get("stage1_parent_category", "")
    params = f"niche={quote(niche)}&return_to={quote(back)}"
    if parent:
        params += f"&parent={quote(parent)}"
    return RedirectResponse(f"/drilldown/progress?{params}", status_code=303)


@app.get("/drilldown/progress", response_class=HTMLResponse)
async def drilldown_progress(
    request: Request,
    niche: str = "",
    parent: str = "",
    return_to: str = "/dashboard",
):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    user = await get_user_by_id(user["id"])
    back = _safe_return_path(return_to)
    niche = unquote(niche).strip()
    if not niche:
        _flash(request, "Please select a niche to check profit margins.", "error")
        return RedirectResponse(back, status_code=303)
    if not can_user_stage2(user):
        _flash(request, STAGE2_UPGRADE_MESSAGE, "error")
        return RedirectResponse(back, status_code=303)

    job_id = _drilldown_job_id(request)
    job = None
    if job_id:
        job = await get_drilldown_job(job_id, user["id"])
    if not job or job["niche"] != niche or job["status"] in ("completed", "failed", "cancelled"):
        job = await create_drilldown_job(
            user["id"],
            niche,
            parent_category=unquote(parent).strip(),
            return_to=back,
        )
    request.session["drilldown_job_id"] = job["id"]
    return templates.TemplateResponse(
        "drilldown_progress.html",
        {
            "request": request,
            "niche": niche,
            "display_name": display_category_name(niche),
            "return_to": back,
            "job_id": job["id"],
        },
    )


@app.post("/drilldown/run")
async def drilldown_run(request: Request, background_tasks: BackgroundTasks):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    job_id = _drilldown_job_id(request)
    if not job_id:
        raise HTTPException(status_code=404, detail="No profit margin analysis job found")
    job = await get_drilldown_job(job_id, user["id"])
    if not job:
        raise HTTPException(status_code=404, detail="Profit margin analysis job not found")
    if job["status"] in ("completed", "failed"):
        return {"ok": True, "already_done": True}
    if job["status"] == "pending":
        started = await mark_drilldown_running(job_id)
        if started:
            background_tasks.add_task(run_drilldown_job, job_id, user["id"])
    return {"ok": True}


@app.get("/drilldown/status")
async def drilldown_status(request: Request):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    job_id = _drilldown_job_id(request)
    if not job_id:
        raise HTTPException(status_code=404, detail="No profit margin analysis job found")
    job = await get_drilldown_job(job_id, user["id"])
    if not job:
        raise HTTPException(status_code=404, detail="Profit margin analysis job not found")
    return await drilldown_status_payload(job)


@app.get("/results/stage2", response_class=HTMLResponse)
async def results_stage2(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    result = request.session.get("stage2_result")
    if not result:
        hydrated = await _hydrate_stage2_from_job(request, user["id"])
        if not hydrated:
            return RedirectResponse("/dashboard", status_code=303)
        result = request.session.get("stage2_result")
    can_export, export_message = can_user_export_csv(user)
    summary = result.get("summary") or build_stage2_summary(result)
    ctx = _nav_context(request, user)
    ctx.update({
        "result": result,
        "stage2_summary": summary,
        "has_margin_matches": summary.get("has_matches", False),
        "can_export": can_export,
        "export_message": export_message,
        "flash": _pop_flash(request),
    })
    return templates.TemplateResponse("results_stage2.html", ctx)


@app.post("/results/stage2/export")
async def results_stage2_export(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    bundle = request.session.get("stage2_export")
    if not bundle:
        _flash(request, "No profit margin data to export. Check profit margins first.", "error")
        return RedirectResponse("/results/stage2", status_code=303)
    items = items_from_serializable(bundle.get("items", []))
    margin = bundle.get("margin") or {}
    path, err = export_stage2_csv_web(user, bundle["subcategory"], items, margin)
    if err:
        _flash(request, err, "error")
        return RedirectResponse("/results/stage2", status_code=303)
    return FileResponse(
        path,
        media_type="text/csv",
        filename=os.path.basename(path),
    )


@app.get("/account", response_class=HTMLResponse)
async def account(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    remaining = get_remaining_for_user(user)
    ctx = _nav_context(request, user)
    ctx.update({
        "remaining": remaining,
        "renewal_date": renewal_date_for_user(user),
        "flash": _pop_flash(request),
    })
    return templates.TemplateResponse("account.html", ctx)


@app.get("/account/cancel-subscription", response_class=HTMLResponse)
async def cancel_subscription_confirm(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("tier") == "cancelling":
        _flash(request, "Your subscription is already cancelled.", "info")
        return RedirectResponse("/account", status_code=303)
    if user.get("tier") == "none":
        _flash(request, "You do not have an active subscription to cancel.", "error")
        return RedirectResponse("/account", status_code=303)
    return templates.TemplateResponse(
        "cancel_subscription.html",
        {
            "request": request,
            "renewal_date": renewal_date_for_user(user),
        },
    )


@app.post("/cancel-subscription")
async def cancel_subscription_submit(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    access_until = renewal_date_for_user(user)
    _user, error = await cancel_user_subscription(user["id"])
    if error:
        _flash(request, error, "error")
        return RedirectResponse("/account", status_code=303)
    return templates.TemplateResponse(
        "cancel_subscription_done.html",
        {"request": request, "access_until": access_until},
    )


@app.post("/account")
async def account_update(
    request: Request,
    scrapingbee_key: str = Form(default=""),
):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    await update_user_scrapingbee_key(user["id"], scrapingbee_key)
    _flash(request, "Account settings saved.", "success")
    return RedirectResponse("/account", status_code=303)


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        "forgot_password.html",
        {"request": request, "message": None, "message_level": "info"},
    )


@app.post("/forgot-password")
async def forgot_password_submit(request: Request, email: str = Form(...)):
    user = await get_user_by_email(email)
    if user:
        token = generate_reset_token(user["id"], user["email"])
        send_password_reset(user["email"], token)
    return templates.TemplateResponse(
        "forgot_password.html",
        {
            "request": request,
            "message": "If that email is registered, a reset link has been sent.",
            "message_level": "success",
        },
    )


@app.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str):
    if not verify_reset_token(token):
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error": "This reset link is invalid or has expired."},
            status_code=400,
        )
    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "token": token, "error": None},
    )


@app.post("/reset-password/{token}")
async def reset_password_submit(
    request: Request,
    token: str,
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    verified = verify_reset_token(token)
    if not verified:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error": "This reset link is invalid or has expired."},
            status_code=400,
        )
    if password != password_confirm:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error": "Passwords do not match."},
            status_code=400,
        )
    user_id, _email = verified
    try:
        await update_user_password(user_id, password)
    except ValueError as exc:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error": str(exc)},
            status_code=400,
        )
    _flash(request, "Password updated. You can log in with your new password.", "success")
    return RedirectResponse("/login", status_code=303)


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user)
    ctx.update({
        "history": await get_search_history(user["id"], 50),
        "is_pro": is_pro_user(user),
        "flash": _pop_flash(request),
    })
    return templates.TemplateResponse("history.html", ctx)


@app.post("/history/rerun")
async def history_rerun(request: Request, history_id: int = Form(...)):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    user = await get_user_by_id(user["id"])
    entry = await get_search_history_entry(user["id"], history_id)
    if not entry:
        _flash(request, "Search not found.", "error")
        return RedirectResponse("/history", status_code=303)

    if entry["stage"] == 1:
        if not can_user_stage1(user):
            _flash(request, STAGE1_UPGRADE_MESSAGE, "error")
            return RedirectResponse("/history", status_code=303)
        try:
            result = run_stage1_search(entry["niche"])
            await increment_user_stage1(user["id"], 1)
            await _record_stage1(user["id"], entry["niche"], result)
            request.session["stage1_parent_category"] = entry["niche"]
            await _store_stage1_result(request, user["id"], entry["niche"], result)
        except Exception as exc:
            _flash(request, f"Rerun failed: {exc}", "error")
            return RedirectResponse("/history", status_code=303)
        return RedirectResponse("/results", status_code=303)

    if not can_user_stage2(user):
        _flash(request, STAGE2_UPGRADE_MESSAGE, "error")
        return RedirectResponse("/history", status_code=303)
    params = f"niche={quote(entry['niche'])}&return_to={quote('/history')}"
    return RedirectResponse(f"/drilldown/progress?{params}", status_code=303)


@app.get("/history/{niche:path}", response_class=HTMLResponse)
async def history_niche_chart(request: Request, niche: str):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    niche = unquote(niche)
    is_pro = is_pro_user(user)
    records = await get_price_history(user["id"], niche) if is_pro else []
    ctx = _nav_context(request, user)
    ctx.update({
        "niche": niche,
        "is_pro": is_pro,
        "records": records,
        "chart_labels": [r["recorded_at"][:10] for r in records],
        "chart_budget": [r.get("budget_margin") for r in records],
        "chart_mid": [r.get("mid_margin") for r in records],
        "chart_premium": [r.get("premium_margin") for r in records],
    })
    return templates.TemplateResponse("history_niche.html", ctx)


@app.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    is_pro = is_pro_user(user)
    ctx = _nav_context(request, user)
    ctx.update({
        "is_pro": is_pro,
        "items": await get_watchlist(user["id"]) if is_pro else [],
        "flash": _pop_flash(request),
    })
    return templates.TemplateResponse("watchlist.html", ctx)


@app.post("/watchlist/add")
async def watchlist_add(
    request: Request,
    niche: str = Form(...),
    return_to: str = Form(default="/watchlist"),
):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    back = _safe_return_path(return_to)
    if not is_pro_user(user):
        _flash(request, "Watchlist is a Pro feature. Upgrade to add niches.", "error")
        return RedirectResponse(back, status_code=303)
    niche = niche.strip()
    if not niche:
        _flash(request, "Please enter a niche to watch.", "error")
        return RedirectResponse(back, status_code=303)
    await add_watchlist_item(user["id"], niche)
    _flash(request, f"Added “{niche}” to your watchlist.", "success")
    return RedirectResponse(back, status_code=303)


@app.post("/watchlist/remove")
async def watchlist_remove(request: Request, niche: str = Form(...)):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not is_pro_user(user):
        _flash(request, "Watchlist is a Pro feature.", "error")
        return RedirectResponse("/watchlist", status_code=303)
    await remove_watchlist_item(user["id"], niche)
    _flash(request, "Removed from watchlist.", "success")
    return RedirectResponse("/watchlist", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, _admin: bool = Depends(_require_admin)):
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "stats": await get_admin_stats()},
    )


@app.get("/admin/api/scrape-status")
async def admin_scrape_status(_admin: bool = Depends(_require_admin)):
    return JSONResponse(await get_scrape_status_payload())


@app.post("/admin/cancel-all-scrapes")
async def admin_cancel_all_scrapes(_admin: bool = Depends(_require_admin)):
    result = await cancel_all_running_scrapes()
    total = result.get("batches", 0) + result.get("logs", 0)
    flash = "all_scrapes_cancelled" if total else "all_scrapes_cancel_failed"
    return RedirectResponse(f"/admin?flash={flash}", status_code=303)


@app.post("/admin/run-initial-scrape")
async def admin_run_initial_scrape(_admin: bool = Depends(_require_admin)):
    batch_id, error = await try_start_initial_scrape()
    if error:
        return RedirectResponse(
            "/admin?flash=initial_scrape_already_running",
            status_code=303,
        )
    return RedirectResponse(
        f"/admin?flash=initial_scrape_started&batch_id={batch_id}",
        status_code=303,
    )


@app.post("/admin/cancel-batch/{batch_id}")
async def admin_cancel_batch(batch_id: str, _admin: bool = Depends(_require_admin)):
    cancelled = await cancel_batch_job(batch_id)
    flash = "batch_cancelled" if cancelled else "batch_cancel_failed"
    return RedirectResponse(f"/admin?flash={flash}", status_code=303)


@app.post("/admin/cancel-scrape/{log_id}")
async def admin_cancel_scrape(log_id: int, _admin: bool = Depends(_require_admin)):
    cancelled = await cancel_scrape_log_by_id(log_id)
    flash = "scrape_cancelled" if cancelled else "scrape_cancel_failed"
    return RedirectResponse(f"/admin?flash={flash}", status_code=303)


@app.post("/admin/run-trend-refresh")
async def admin_run_trend_refresh(_admin: bool = Depends(_require_admin)):
    asyncio.create_task(run_trend_refresh())
    return RedirectResponse("/admin?flash=trend_refresh_started", status_code=303)


@app.post("/admin/add-niche-queue")
async def admin_add_niche_queue(
    request: Request,
    niche: str = Form(...),
    _admin: bool = Depends(_require_admin),
):
    niche = niche.strip()
    if niche:
        await ensure_niche_in_queue(niche, added_by="admin", priority=7)
    return RedirectResponse("/admin?flash=niche_queued", status_code=303)


@app.get("/admin/create-test-user", response_class=HTMLResponse)
async def admin_create_test_user(_admin: bool = Depends(_require_admin)):
    """Create or reset the standard dev test account (admin auth required)."""
    email = TEST_ACCOUNT_EMAIL
    password = "Test1234"
    tier = "pro"

    user, error = await create_user_with_tier(email, password, tier)
    if error:
        existing = await get_user_by_email(email)
        if not existing:
            raise HTTPException(status_code=400, detail=error)
        user, error = await update_user_tier_and_password(existing["id"], password, tier)
        if error:
            raise HTTPException(status_code=400, detail=error)
        action = "updated"
    else:
        action = "created"

    return HTMLResponse(
        content=(
            "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem;'>"
            f"<h1>Test user {action}</h1>"
            f"<p><strong>Email:</strong> {user['email']}<br>"
            f"<strong>Password:</strong> {password}<br>"
            f"<strong>Tier:</strong> {user['tier']}</p>"
            "<p><a href='/admin'>Back to admin</a> · "
            "<a href='/login'>Log in as test user</a></p>"
            "</body></html>"
        ),
    )


@app.get("/health")
async def health_check():
    return {
        "status": "OK",
        "version": APP_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database_connected": await check_database_connected(),
        "scrapingbee_connected": check_scrapingbee_connected(),
    }


@app.post("/create-checkout-session")
async def stripe_create_checkout(request: Request, plan: str = Form(...)):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    try:
        session = create_checkout_session(request, user, plan)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(session.url, status_code=303)


@app.get("/success", response_class=HTMLResponse)
async def stripe_success(request: Request, session_id: str = ""):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if session_id:
        try:
            await handle_checkout_success(session_id)
            _flash(request, "Subscription activated — welcome aboard!", "success")
        except Exception as exc:
            log_error("/success", exc)
            _flash(request, "Payment received but activation failed. Contact support.", "error")
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/cancel", response_class=HTMLResponse)
async def stripe_cancel(request: Request):
    _flash(request, "Checkout cancelled. You can upgrade anytime from the pricing page.", "info")
    return RedirectResponse("/", status_code=303)


@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = construct_webhook_event(payload, signature)
        await handle_webhook_event(event)
    except ValueError as exc:
        log_error("/webhook", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log_error("/webhook", exc)
        raise HTTPException(status_code=500, detail="Webhook handler failed") from exc
    return {"received": True}


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)
