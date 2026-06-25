"""SourceIQ FastAPI web application (Railway-hosted)."""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import os
import secrets
from datetime import datetime, timezone
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from market_spy.cli import QUICK_START_NICHES
from market_spy.config import STAGE1_UPGRADE_MESSAGE, STAGE2_UPGRADE_MESSAGE
from market_spy.web.admin_service import get_admin_stats
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
    check_all_trial_expiries,
    check_database_connected,
    check_trial_expiry,
    create_user,
    create_user_with_tier,
    get_price_history,
    get_remaining_for_user,
    get_search_history,
    get_search_history_entry,
    get_user_by_email,
    get_user_by_id,
    get_watchlist,
    increment_user_stage1,
    increment_user_stage2,
    init_db,
    is_pro_user,
    margin_meta_from_stage2,
    remove_watchlist_item,
    save_price_history,
    update_user_password,
    update_user_scrapingbee_key,
    update_user_tier_and_password,
    update_watchlist_after_search,
)
from market_spy.web.email_service import send_password_reset, send_trial_expired_email
from market_spy.web.export_web import export_stage2_csv_web
from market_spy.web.health import check_scrapingbee_connected
from market_spy.web.logger import log_error, log_request
from market_spy.web.password_tokens import generate_reset_token, verify_reset_token
from market_spy.web.search_service import (
    items_from_serializable,
    run_quick_start,
    run_stage1_search,
    run_stage2_drilldown,
)
from market_spy.web.stripe_service import (
    construct_webhook_event,
    create_checkout_session,
    handle_checkout_success,
    handle_webhook_event,
)
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
            expired = check_all_trial_expiries(send_expiry_email=send_trial_expired_email)
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
    init_db()
    asyncio.create_task(_trial_expiry_loop())


def _ensure_trial_valid(user: dict) -> dict:
    """Refresh user after trial expiry check."""
    if user:
        check_trial_expiry(user["id"], send_expiry_email=send_trial_expired_email)
        return get_user_by_id(user["id"])
    return user


def _record_stage1(user_id: int, category: str, result: dict):
    add_search_history(user_id, category, 1, opportunity_score=result.get("score"))
    update_watchlist_after_search(user_id, category, result.get("score", 0))


def _record_stage2(user_id: int, subcategory: str, by_tier: dict, user: dict):
    tier_key, summary = margin_meta_from_stage2(by_tier or {})
    add_search_history(
        user_id,
        subcategory,
        2,
        margin_tier=tier_key,
        margin_summary=summary,
    )
    if is_pro_user(user):
        save_price_history(
            user_id,
            subcategory,
            (by_tier.get("budget") or {}).get("tier_margin_percent"),
            (by_tier.get("mid") or {}).get("tier_margin_percent"),
            (by_tier.get("premium") or {}).get("tier_margin_percent"),
        )


def _apply_stage2_session(request: Request, result: dict, subcategory: str):
    request.session["stage2_result"] = {
        "subcategory": result["subcategory"],
        "parent_category": result.get("parent_category", ""),
        "product_family": result.get("product_family"),
        "total_listings": result.get("total_listings"),
        "sources": result.get("sources"),
        "by_tier": result.get("by_tier"),
    }
    request.session["stage2_export"] = {
        "subcategory": subcategory,
        "items": result.get("items_serializable", []),
        "margin": result.get("margin_raw"),
    }


def _current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(int(user_id))


def _require_user(request: Request):
    user = _current_user(request)
    if not user:
        return None
    return _ensure_trial_valid(user)


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
        {"request": request, "user": _current_user(request)},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _current_user(request):
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
    user, error = authenticate_user(email, password)
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
    if _current_user(request):
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
    user, error = create_user(email, password)
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
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user)
    ctx["flash"] = _pop_flash(request)
    ctx["quick_start_count"] = len(QUICK_START_NICHES)
    ctx["quick_start_results"] = request.session.pop("quick_start_results", None)
    return templates.TemplateResponse("dashboard.html", ctx)


@app.post("/search")
async def search(request: Request, category: str = Form(...)):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    user = get_user_by_id(user["id"])
    if not can_user_stage1(user):
        _flash(request, STAGE1_UPGRADE_MESSAGE, "error")
        return RedirectResponse("/dashboard", status_code=303)
    category = category.strip()
    if not category:
        _flash(request, "Please enter a niche to research.", "error")
        return RedirectResponse("/dashboard", status_code=303)
    try:
        result = run_stage1_search(category)
        increment_user_stage1(user["id"], 1)
        _record_stage1(user["id"], category, result)
        request.session["stage1_result"] = result
        request.session["stage1_parent_category"] = category
    except Exception as exc:
        _flash(request, f"Search failed: {exc}", "error")
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/results/stage1", status_code=303)


@app.post("/quick-start")
async def quick_start(request: Request):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    user = get_user_by_id(user["id"])
    needed = len(QUICK_START_NICHES)
    if not can_user_stage1(user, needed):
        _flash(
            request,
            f"Quick Start needs {needed} Stage 1 searches. {STAGE1_UPGRADE_MESSAGE}",
            "error",
        )
        return RedirectResponse("/dashboard", status_code=303)
    try:
        results = run_quick_start()
        increment_user_stage1(user["id"], needed)
        request.session["quick_start_results"] = results
        _flash(request, "Quick Start complete — 12 niches scanned.", "success")
    except Exception as exc:
        _flash(request, f"Quick Start failed: {exc}", "error")
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/results/stage1", response_class=HTMLResponse)
async def results_stage1(request: Request, advanced: int = 0):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    result = request.session.get("stage1_result")
    if not result:
        return RedirectResponse("/dashboard", status_code=303)
    ctx = _nav_context(request, user)
    ctx.update({
        "result": result,
        "parent_category": request.session.get("stage1_parent_category", result.get("category", "")),
        "advanced": bool(advanced),
        "flash": _pop_flash(request),
    })
    return templates.TemplateResponse("results_stage1.html", ctx)


@app.post("/drilldown")
async def drilldown(
    request: Request,
    category: str = Form(default=""),
    subcategory: str = Form(...),
):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    user = get_user_by_id(user["id"])
    if not can_user_stage2(user):
        _flash(request, STAGE2_UPGRADE_MESSAGE, "error")
        return RedirectResponse("/dashboard", status_code=303)
    subcategory = subcategory.strip()
    if not subcategory:
        _flash(request, "Please enter a subcategory for drill-down.", "error")
        return RedirectResponse("/dashboard", status_code=303)
    parent = category.strip() or request.session.get("stage1_parent_category", "")
    try:
        result = run_stage2_drilldown(subcategory)
        result["parent_category"] = parent
        increment_user_stage2(user["id"], 1)
        _record_stage2(user["id"], subcategory, result.get("by_tier"), user)
        _apply_stage2_session(request, result, subcategory)
    except Exception as exc:
        _flash(request, f"Drill-down failed: {exc}", "error")
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/results/stage2", status_code=303)


@app.get("/results/stage2", response_class=HTMLResponse)
async def results_stage2(request: Request):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    result = request.session.get("stage2_result")
    if not result:
        return RedirectResponse("/dashboard", status_code=303)
    can_export, export_message = can_user_export_csv(user)
    ctx = _nav_context(request, user)
    ctx.update({
        "result": result,
        "can_export": can_export,
        "export_message": export_message,
        "flash": _pop_flash(request),
    })
    return templates.TemplateResponse("results_stage2.html", ctx)


@app.post("/results/stage2/export")
async def results_stage2_export(request: Request):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    bundle = request.session.get("stage2_export")
    if not bundle:
        _flash(request, "No drill-down data to export. Run Stage 2 first.", "error")
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
    user = _require_user(request)
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


@app.post("/account")
async def account_update(
    request: Request,
    scrapingbee_key: str = Form(default=""),
):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    update_user_scrapingbee_key(user["id"], scrapingbee_key)
    _flash(request, "Account settings saved.", "success")
    return RedirectResponse("/account", status_code=303)


@app.post("/account")
async def account_update(
    request: Request,
    scrapingbee_key: str = Form(default=""),
):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    update_user_scrapingbee_key(user["id"], scrapingbee_key)
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
    user = get_user_by_email(email)
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
        update_user_password(user_id, password)
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
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ctx = _nav_context(request, user)
    ctx.update({
        "history": get_search_history(user["id"], 50),
        "is_pro": is_pro_user(user),
        "flash": _pop_flash(request),
    })
    return templates.TemplateResponse("history.html", ctx)


@app.post("/history/rerun")
async def history_rerun(request: Request, history_id: int = Form(...)):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    user = get_user_by_id(user["id"])
    entry = get_search_history_entry(user["id"], history_id)
    if not entry:
        _flash(request, "Search not found.", "error")
        return RedirectResponse("/history", status_code=303)

    if entry["stage"] == 1:
        if not can_user_stage1(user):
            _flash(request, STAGE1_UPGRADE_MESSAGE, "error")
            return RedirectResponse("/history", status_code=303)
        try:
            result = run_stage1_search(entry["niche"])
            increment_user_stage1(user["id"], 1)
            _record_stage1(user["id"], entry["niche"], result)
            request.session["stage1_result"] = result
            request.session["stage1_parent_category"] = entry["niche"]
        except Exception as exc:
            _flash(request, f"Rerun failed: {exc}", "error")
            return RedirectResponse("/history", status_code=303)
        return RedirectResponse("/results/stage1", status_code=303)

    if not can_user_stage2(user):
        _flash(request, STAGE2_UPGRADE_MESSAGE, "error")
        return RedirectResponse("/history", status_code=303)
    try:
        result = run_stage2_drilldown(entry["niche"])
        result["parent_category"] = ""
        increment_user_stage2(user["id"], 1)
        _record_stage2(user["id"], entry["niche"], result.get("by_tier"), user)
        _apply_stage2_session(request, result, entry["niche"])
    except Exception as exc:
        _flash(request, f"Rerun failed: {exc}", "error")
        return RedirectResponse("/history", status_code=303)
    return RedirectResponse("/results/stage2", status_code=303)


@app.get("/history/{niche:path}", response_class=HTMLResponse)
async def history_niche_chart(request: Request, niche: str):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    niche = unquote(niche)
    is_pro = is_pro_user(user)
    records = get_price_history(user["id"], niche) if is_pro else []
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
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    is_pro = is_pro_user(user)
    ctx = _nav_context(request, user)
    ctx.update({
        "is_pro": is_pro,
        "items": get_watchlist(user["id"]) if is_pro else [],
        "flash": _pop_flash(request),
    })
    return templates.TemplateResponse("watchlist.html", ctx)


@app.post("/watchlist/add")
async def watchlist_add(request: Request, niche: str = Form(...)):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not is_pro_user(user):
        _flash(request, "Watchlist is a Pro feature. Upgrade to add niches.", "error")
        return RedirectResponse("/watchlist", status_code=303)
    niche = niche.strip()
    if not niche:
        _flash(request, "Please enter a niche to watch.", "error")
        return RedirectResponse("/watchlist", status_code=303)
    add_watchlist_item(user["id"], niche)
    _flash(request, f"Added “{niche}” to your watchlist.", "success")
    return RedirectResponse("/watchlist", status_code=303)


@app.post("/watchlist/remove")
async def watchlist_remove(request: Request, niche: str = Form(...)):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not is_pro_user(user):
        _flash(request, "Watchlist is a Pro feature.", "error")
        return RedirectResponse("/watchlist", status_code=303)
    remove_watchlist_item(user["id"], niche)
    _flash(request, "Removed from watchlist.", "success")
    return RedirectResponse("/watchlist", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, _admin: bool = Depends(_require_admin)):
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "stats": get_admin_stats()},
    )


@app.get("/admin/create-test-user", response_class=HTMLResponse)
async def admin_create_test_user(_admin: bool = Depends(_require_admin)):
    """Create or reset the standard dev test account (admin auth required)."""
    email = "test@sourceiq.app"
    password = "Test1234"
    tier = "pro"

    user, error = create_user_with_tier(email, password, tier)
    if error:
        existing = get_user_by_email(email)
        if not existing:
            raise HTTPException(status_code=400, detail=error)
        user, error = update_user_tier_and_password(existing["id"], password, tier)
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
        "database_connected": check_database_connected(),
        "scrapingbee_connected": check_scrapingbee_connected(),
    }


@app.post("/create-checkout-session")
async def stripe_create_checkout(request: Request, plan: str = Form(...)):
    user = _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    try:
        session = create_checkout_session(request, user, plan)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(session.url, status_code=303)


@app.get("/success", response_class=HTMLResponse)
async def stripe_success(request: Request, session_id: str = ""):
    user = _require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if session_id:
        try:
            handle_checkout_success(session_id)
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
        handle_webhook_event(event)
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
