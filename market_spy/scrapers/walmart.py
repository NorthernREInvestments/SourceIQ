"""Walmart marketplace search via ScrapingBee."""

import concurrent.futures
import json
import re
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from market_spy.scrapers.base import (
    is_blocked,
    parse_usd_price,
    scrape_delay,
    selling_item,
)
from market_spy.scrapers.scrapingbee_client import fetch_scrapingbee

# Walmart pages often hang on ScrapingBee; cap wait so margin analysis can continue.
_WALMART_SCRAPE_TIMEOUT = 60


def _price_from_info(price_info):
    if not isinstance(price_info, dict):
        return None
    min_price = price_info.get("minPrice")
    if min_price is not None:
        try:
            return float(min_price)
        except (TypeError, ValueError):
            pass
    for key in ("linePrice", "itemPrice"):
        price = parse_usd_price(price_info.get(key))
        if price is not None:
            return price
    return None


def _parse_next_data(html, limit):
    results = []
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return results
    try:
        payload = json.loads(script.string)
    except json.JSONDecodeError:
        return results
    stacks = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("initialData", {})
        .get("searchResult", {})
        .get("itemStacks", [])
    )
    seen = set()
    for stack in stacks:
        for item in stack.get("items") or []:
            if len(results) >= limit:
                return results
            if not isinstance(item, dict):
                continue
            title = item.get("name")
            us_item_id = item.get("usItemId")
            path = item.get("canonicalUrl")
            if not title or not us_item_id:
                continue
            if path:
                href = urljoin("https://www.walmart.com", path.split("?")[0])
            else:
                href = f"https://www.walmart.com/ip/{us_item_id}"
            if href in seen:
                continue
            price = _price_from_info(item.get("priceInfo"))
            if price is None or price <= 0:
                continue
            reviews = item.get("numberOfReviews") or 0
            try:
                reviews = int(reviews)
            except (TypeError, ValueError):
                reviews = 0
            rating = item.get("averageRating")
            try:
                rating = float(rating) if rating is not None else None
            except (TypeError, ValueError):
                rating = None
            seen.add(href)
            results.append(selling_item(
                "Walmart", title[:200], href, price,
                reviews=reviews, rating=rating, engagement=reviews,
            ))
    return results


def _parse_html_fallback(html, limit):
    results = []
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    cards = soup.select("[data-item-id], div[role='group']")
    for card in cards:
        if len(results) >= limit:
            break
        title_el = card.select_one(
            "[data-automation-id='product-title'], h3, span.w_iUH7"
        )
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        link_el = card.find("a", href=re.compile(r"/ip/"))
        if not link_el:
            continue
        href = urljoin("https://www.walmart.com", link_el["href"].split("?")[0])
        if href in seen:
            continue
        price = parse_usd_price(card.get_text(" ", strip=True))
        if price is None:
            continue
        seen.add(href)
        results.append(selling_item(
            "Walmart", title[:200], href, price,
        ))
    return results


def scrape_walmart(niche, limit=15):
    q = quote_plus(niche)
    url = f"https://www.walmart.com/search?q={q}"

    def _fetch_html():
        return fetch_scrapingbee(
            url,
            source="Walmart",
            render_js=True,
            wait=5000,
            timeout=_WALMART_SCRAPE_TIMEOUT,
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_fetch_html)
            html = future.result(timeout=_WALMART_SCRAPE_TIMEOUT + 5)
    except concurrent.futures.TimeoutError:
        print(
            f"[walmart] scrape cancelled after {_WALMART_SCRAPE_TIMEOUT}s "
            f"niche={niche!r}",
            flush=True,
        )
        return []
    except Exception as exc:
        print(
            f"[walmart] scrape failed niche={niche!r}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return []

    if not html:
        return []
    results = _parse_next_data(html, limit)
    if not results and not is_blocked(html):
        results = _parse_html_fallback(html, limit)
    scrape_delay()
    return results
