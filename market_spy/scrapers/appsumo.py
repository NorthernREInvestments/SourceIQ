"""AppSumo scraper."""

import json
import re
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from market_spy import config
from market_spy.browser import fetch_rendered_page, save_debug_html


def _parse_deals_from_next_data(soup):
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []
    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        return []
    fallback = data.get("props", {}).get("pageProps", {}).get("fallbackData", [])
    if not fallback:
        return []
    deals = fallback[0].get("deals", []) if isinstance(fallback[0], dict) else []
    return deals if isinstance(deals, list) else []


def scrape_appsumo(niche, limit=10):
    results = []
    q = quote_plus(niche)
    url = f"https://appsumo.com/search/?q={q}"
    html = fetch_rendered_page(url, wait_after=3000)
    save_debug_html("appsumo_debug.html", html or "", max_chars=2000)
    if not html:
        return results

    soup = BeautifulSoup(html, "html.parser")
    deals = _parse_deals_from_next_data(soup)
    seen = set()

    for deal in deals:
        if len(results) >= limit:
            break
        name = (deal.get("public_name") or deal.get("slug") or "").strip()
        path = deal.get("get_absolute_url") or deal.get("slug")
        if not name or not path:
            continue
        href = urljoin("https://appsumo.com", path if path.startswith("/") else f"/products/{path}/")
        if href in seen:
            continue
        seen.add(href)
        price = deal.get("price")
        original_price = deal.get("original_price")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        try:
            original_price = float(original_price) if original_price is not None else None
        except (TypeError, ValueError):
            original_price = None
        if price is None:
            continue
        reviews = deal.get("deal_review") or {}
        review_count = reviews.get("review_count", 0)
        results.append({
            "source": "AppSumo",
            "name": name,
            "url": href,
            "price": price,
            "original_price": original_price,
            "description": deal.get("card_description"),
            "engagement": int(review_count or 0),
            "reviews": review_count,
        })
        if config.DEBUG_APPSUMO:
            print(f"[DEBUG] AppSumo: {name} deal=${price} original=${original_price}")

    return results
