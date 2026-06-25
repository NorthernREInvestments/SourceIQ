"""Etsy marketplace search scraper."""

import re
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from market_spy.scrapers.base import (
    fetch_page,
    is_blocked,
    parse_int_text,
    parse_usd_price,
    scrape_delay,
    selling_item,
)


def scrape_etsy(niche, limit=15):
    results = []
    q = quote_plus(niche)
    url = f"https://www.etsy.com/search?q={q}"
    html = fetch_page(url, warmup_url="https://www.etsy.com/", wait_after=4000, scroll_steps=3)
    if is_blocked(html):
        return results

    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    cards = soup.select("li.wt-list-unstyled div.v2-listing-card, div.v2-listing-card, [data-listing-id]")

    for card in cards:
        if len(results) >= limit:
            break
        title_el = card.select_one("h3, h2, [data-listing-card-title]")
        link_el = card.find("a", href=re.compile(r"/listing/"))
        if not title_el and link_el:
            title_el = link_el
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        href = link_el.get("href").split("?")[0] if link_el and link_el.get("href") else None
        if href:
            href = urljoin("https://www.etsy.com", href)
        if not title or not href or href in seen:
            continue
        price_el = card.select_one("span.currency-value, p.wt-text-title-01, [class*='currency']")
        card_text = card.get_text(" ", strip=True)
        price = parse_usd_price(price_el.get_text() if price_el else card_text)
        if price is None:
            continue
        sales_match = re.search(r"([\d,]+)\s+sales?", card_text, re.I)
        sales = int(sales_match.group(1).replace(",", "")) if sales_match else 0
        seen.add(href)
        results.append(selling_item(
            "Etsy", title[:200], href, price,
            sales=sales, engagement=sales,
        ))
    scrape_delay()
    return results
