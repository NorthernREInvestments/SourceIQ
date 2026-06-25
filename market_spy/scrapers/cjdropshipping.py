"""CJDropshipping product search via ScrapingBee.

Note: Listed in Stage 2 as coming soon — Cloudflare Turnstile currently blocks
automated access. The scraper remains wired but is skipped at runtime.
"""

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from market_spy.scrapers.base import (
    enrich_sourcing_pricing,
    is_blocked,
    parse_moq_text,
    parse_supplier_rating_text,
    parse_usd_price,
    parse_usa_shipping_text,
    scrape_delay,
    sourcing_item,
)
from market_spy.scrapers.scrapingbee_client import fetch_scrapingbee


def _normalize_url(href):
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://cjdropshipping.com" + href
    return href.split("?")[0]


def _parse_html_results(html, limit, niche):
    results = []
    if is_blocked(html):
        return results
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    family = niche.strip().lower()
    cards = soup.select(
        ".product-item, .product-card, [class*='productItem'], "
        "[class*='product-list'] li, .goods-item"
    )
    anchors = []
    if cards:
        for card in cards:
            link = card.find("a", href=re.compile(r"/product/|/products/|/detail/"))
            if link:
                anchors.append((card, link))
    if not anchors:
        for link in soup.find_all("a", href=re.compile(r"/product/|/products/|/detail/")):
            anchors.append((link.parent or link, link))

    for card, link_el in anchors:
        if len(results) >= limit:
            break
        title_el = card.select_one("h3, h4, .title, [class*='title']")
        title = (title_el or link_el).get_text(" ", strip=True)
        href = _normalize_url(link_el.get("href", ""))
        if not title or len(title) < 5 or not href or href in seen:
            continue
        card_text = card.get_text(" ", strip=True)
        price = parse_usd_price(card_text)
        if price is None:
            continue
        moq = parse_moq_text(card_text)
        rating = parse_supplier_rating_text(card_text)
        shipping = parse_usa_shipping_text(card_text, "CJDropshipping", price)
        seen.add(href)
        item = sourcing_item(
            "CJDropshipping",
            title[:200],
            href,
            price,
            unit_price=price,
            bulk_price=price if moq > 1 else None,
            moq=moq,
            supplier_rating=rating,
            shipping_usa=shipping,
            product_family=family,
        )
        enrich_sourcing_pricing(item)
        results.append(item)
    return results


def scrape_cjdropshipping(niche, limit=20):
    q = quote_plus(niche)
    url = f"https://cjdropshipping.com/products.html?searchkey={q}"
    html = fetch_scrapingbee(
        url,
        source="CJDropshipping",
        render_js=True,
        stealth_proxy=True,
        wait=8000,
    )
    results = _parse_html_results(html, limit, niche) if html else []
    scrape_delay()
    return results
