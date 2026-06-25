"""Browser-based page rendering via Playwright with stealth and HTTP fallback."""

import os
import random

from market_spy.config import DEBUG_DIR, RENDER_TIMEOUT, RENDER_WAIT_AFTER, is_debug_mode
from market_spy.utils import safe_get

try:
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth

    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False
    Stealth = None

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

USER_AGENTS = [
    DEFAULT_USER_AGENT,
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) "
        "Gecko/20100101 Firefox/122.0"
    ),
]


def save_debug_html(filename: str, html: str, *, max_chars: int | None = None) -> str | None:
    """Write HTML to output/debug/ only when DEBUG_MODE=true in .env."""
    if not is_debug_mode() or not html:
        return None
    content = html if max_chars is None else html[:max_chars]
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, filename)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"[DEBUG] Saved to: {path}")
    return path


def random_user_agent():
    return random.choice(USER_AGENTS)


def _human_mouse_moves(page, steps=6):
    try:
        width, height = 1920, 1080
        x, y = random.randint(120, 400), random.randint(120, 300)
        page.mouse.move(x, y)
        for _ in range(steps):
            x = max(40, min(width - 40, x + random.randint(-120, 180)))
            y = max(40, min(height - 40, y + random.randint(-80, 140)))
            page.mouse.move(x, y, steps=random.randint(8, 18))
            page.wait_for_timeout(random.randint(120, 320))
    except Exception:
        pass


def fetch_rendered_page(
    url,
    timeout=RENDER_TIMEOUT,
    warmup_url=None,
    wait_after=None,
    user_agent=None,
    scroll_steps=0,
    human_mouse=False,
    use_stealth=True,
):
    """Return rendered page HTML using stealth Playwright when available."""
    if not PLAYWRIGHT_AVAILABLE:
        return safe_get(url)
    wait_after = RENDER_WAIT_AFTER if wait_after is None else wait_after
    user_agent = user_agent or DEFAULT_USER_AGENT
    try:
        stealth_ctx = Stealth().use_sync(sync_playwright()) if use_stealth and Stealth else sync_playwright()
        with stealth_ctx as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            page = context.new_page()
            if warmup_url:
                page.goto(warmup_url, timeout=timeout)
                page.wait_for_timeout(1500)
                if human_mouse:
                    _human_mouse_moves(page)
            page.goto(url, timeout=timeout)
            try:
                page.wait_for_load_state("networkidle", timeout=timeout)
            except Exception:
                pass
            if human_mouse:
                _human_mouse_moves(page)
            for _ in range(scroll_steps):
                page.mouse.wheel(0, random.randint(1200, 2000))
                page.wait_for_timeout(random.randint(600, 1000))
                if human_mouse and random.random() > 0.4:
                    _human_mouse_moves(page, steps=3)
            try:
                page.wait_for_timeout(wait_after)
            except Exception:
                pass
            content = page.content()
            try:
                browser.close()
            except Exception:
                pass
            return content
    except Exception:
        return safe_get(url)
