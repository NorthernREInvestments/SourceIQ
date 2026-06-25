"""Amazon search results via ScrapingBee."""

import re
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from market_spy.scrapers.base import (
    is_blocked,
    parse_int_text,
    parse_usd_price,
    scrape_delay,
    selling_item,
)
from market_spy.scrapers.scrapingbee_client import fetch_scrapingbee


def _parse_amazon_results(html, limit):
    results = []
    if is_blocked(html):
        return results
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    cards = soup.select(
        "div[data-component-type='s-search-result'], "
        "div.s-result-item[data-asin]:not([data-asin=''])"
    )
    for card in cards:
        if len(results) >= limit:
            break
        asin = card.get("data-asin", "").strip()
        if not asin:
            continue
        title_el = card.select_one("h2 a span, h2 span.a-text-normal, .a-link-normal.a-text-normal")
        if not title_el:
            title_el = card.select_one("h2 a")
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        if not title or len(title) < 4:
            continue
        link_el = card.select_one("h2 a, a.a-link-normal.s-no-outline")
        href = None
        if link_el and link_el.get("href"):
            href = urljoin("https://www.amazon.com", link_el["href"].split("?")[0])
        if not href:
            href = f"https://www.amazon.com/dp/{asin}"
        if asin in seen:
            continue
        price_el = card.select_one(
            ".a-price .a-offscreen, span.a-price-whole, .a-color-price"
        )
        price_text = price_el.get_text(" ", strip=True) if price_el else card.get_text(" ", strip=True)
        price = parse_usd_price(price_text)
        if price is None:
            whole = card.select_one("span.a-price-whole")
            frac = card.select_one("span.a-price-fraction")
            if whole:
                try:
                    cents = frac.get_text(strip=True) if frac else "00"
                    price = float(f"{whole.get_text(strip=True).replace(',', '')}.{cents}")
                except ValueError:
                    price = None
        if price is None:
            continue
        reviews_el = card.select_one("span.s-underline-text, a span.a-size-base.s-underline-text")
        reviews_text = reviews_el.get_text(" ", strip=True) if reviews_el else ""
        review_count = parse_int_text(reviews_text)
        rating_el = card.select_one("span.a-icon-alt")
        rating = None
        if rating_el:
            rating_match = re.search(r"(\d\.\d)\s*out of", rating_el.get_text(" ", strip=True), re.I)
            if rating_match:
                rating = float(rating_match.group(1))
        bestseller = bool(
            card.select_one("[aria-label*='Best Seller'], .a-badge-text")
            or re.search(r"best\s*seller", card.get_text(" ", strip=True), re.I)
        )
        seen.add(asin)
        results.append(selling_item(
            "Amazon", title[:200], href, price,
            reviews=review_count, rating=rating, engagement=review_count,
            bestseller=bestseller,
        ))
    return results


def scrape_amazon(niche, limit=15):
    q = quote_plus(niche)
    url = f"https://www.amazon.com/s?k={q}"
    html = fetch_scrapingbee(url, source="Amazon", render_js=True, wait=5000)
    results = _parse_amazon_results(html, limit) if html else []
    scrape_delay()
    return results
