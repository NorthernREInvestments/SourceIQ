"""ScrapingBee connectivity test for Railway diagnostics."""

from market_spy.config import SCRAPINGBEE_REQUEST_TIMEOUT, get_scrapingbee_api_key


def scrapingbee_key_prefix() -> str | None:
    key = get_scrapingbee_api_key()
    if not key:
        return None
    return key[:4]


def run_scrapingbee_test(
    url: str = "https://www.amazon.com/s?k=dog+collar",
) -> dict:
    """Make one ScrapingBee request and return structured success/error details."""
    key = get_scrapingbee_api_key()
    prefix = scrapingbee_key_prefix()
    if not key:
        return {
            "ok": False,
            "key_prefix": prefix,
            "key_loaded": False,
            "error": "SCRAPINGBEE_API_KEY is not set in the environment",
        }

    try:
        from scrapingbee import ScrapingBeeClient
    except ImportError as exc:
        return {
            "ok": False,
            "key_prefix": prefix,
            "key_loaded": True,
            "error": f"scrapingbee package not installed: {exc}",
        }

    params = {
        "render_js": True,
        "premium_proxy": True,
        "wait": 3000,
    }
    try:
        client = ScrapingBeeClient(api_key=key)
        response = client.get(url, params=params, timeout=SCRAPINGBEE_REQUEST_TIMEOUT)
    except Exception as exc:
        return {
            "ok": False,
            "key_prefix": prefix,
            "key_loaded": True,
            "url": url,
            "timeout_seconds": SCRAPINGBEE_REQUEST_TIMEOUT,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }

    cost = response.headers.get("Spb-cost") or response.headers.get("spb-cost")
    body = response.content.decode("utf-8", errors="replace") if response.content else ""
    return {
        "ok": response.status_code == 200 and bool(body),
        "key_prefix": prefix,
        "key_loaded": True,
        "url": url,
        "status_code": response.status_code,
        "html_length": len(body),
        "spb_cost": cost,
        "timeout_seconds": SCRAPINGBEE_REQUEST_TIMEOUT,
        "preview": body[:400] if body else "",
        "error": None if response.status_code == 200 and body else (
            f"HTTP {response.status_code}" if response.status_code != 200 else "Empty response body"
        ),
    }
