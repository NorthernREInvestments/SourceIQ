"""AliExpress sourcing via ScrapingBee."""

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from market_spy.scrapers.base import (
    enrich_sourcing_pricing,
    is_blocked,
    scrape_delay,
    sourcing_item,
)
from market_spy.scrapers.scrapingbee_client import fetch_scrapingbee


def _niche_slug(niche):
    return re.sub(r"[^a-z0-9]+", "-", niche.lower()).strip("-")


def _normalize_url(href):
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://www.aliexpress.com" + href
    return href.split("?")[0]


def _parse_card_text(text):
    price = None
    price_high = None
    sale_price = None
    original_price = None
    orders = 0
    rating = None
    moq = 1
    price_match = re.search(
        r"\$\s*([\d\s]+)\s*\.\s*([\d]{2})(?:\s*[-–]\s*\$\s*([\d.]+))?",
        text,
    )
    if price_match:
        whole, cents, high = price_match.groups()
        try:
            price = float(f"{whole.replace(' ', '')}.{cents}")
        except ValueError:
            price = None
        if high:
            try:
                price_high = float(high)
            except ValueError:
                pass
    if price is None:
        m = re.search(r"\$\s*([\d,]+(?:\.\d{2})?)", text)
        if m:
            try:
                price = float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    was_match = re.search(
        r"(?:was|originally|before)\s*:?\s*\$\s*([\d,]+(?:\.\d{2})?)", text, re.I
    )
    if was_match:
        try:
            original_price = float(was_match.group(1).replace(",", ""))
            if price is not None and original_price > price:
                sale_price = price
        except ValueError:
            pass
    moq_match = re.search(r"(\d+)\s*\+\s*(?:pieces|pcs|pc|lot)", text, re.I)
    if moq_match:
        moq = int(moq_match.group(1))
    orders_match = re.search(r"([\d,]+)\+?\s*(?:sold|orders?)", text, re.I)
    if orders_match:
        orders = int(orders_match.group(1).replace(",", ""))
    rating_match = re.search(r"(\d\.\d)\s*(?:star|out of|\|)", text, re.I)
    if not rating_match:
        rating_match = re.search(r"(\d\.\d)\s+[\d,]+\+?\s*sold", text, re.I)
    if rating_match:
        rating = float(rating_match.group(1))
    title = re.split(r"\$\s*\d", text)[0].strip()
    title = re.sub(r"\s+", " ", title)
    bulk_price = price if moq > 1 else None
    return title, price, price_high, sale_price, original_price, bulk_price, moq, orders, rating


def _parse_html_results(html, limit, niche):
    results = []
    if is_blocked(html):
        return results
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    family = niche.strip().lower()
    for anchor in soup.find_all("a", href=re.compile(r"/item/\d+\.html")):
        if len(results) >= limit:
            break
        href = _normalize_url(anchor.get("href"))
        if not href:
            continue
        item_id = re.search(r"/item/(\d+)\.html", href)
        dedupe_key = item_id.group(1) if item_id else href
        if dedupe_key in seen:
            continue
        card = anchor
        card_text = ""
        for _ in range(12):
            card = card.parent
            if not card:
                break
            card_text = card.get_text(" ", strip=True)
            if "$" in card_text and len(card_text) > 20:
                break
        if "$" not in card_text:
            card_text = anchor.get_text(" ", strip=True)
        parsed = _parse_card_text(card_text)
        title, price, price_high, sale_price, original_price, bulk_price, moq, orders, rating = parsed
        if not title or len(title) < 5:
            title = anchor.get_text(" ", strip=True)
        if not title or price is None:
            continue
        seen.add(dedupe_key)
        item = sourcing_item(
            "AliExpress", title[:200], href, price,
            unit_price=price,
            price_high=price_high,
            sale_price=sale_price,
            original_price=original_price,
            bulk_price=bulk_price,
            moq=moq,
            orders=orders,
            store_rating=rating,
            engagement=orders,
            product_family=family,
        )
        enrich_sourcing_pricing(item)
        results.append(item)
    return results


def scrape_aliexpress(niche, limit=20):
    slug = _niche_slug(niche)
    url = f"https://www.aliexpress.com/w/wholesale-{slug}.html"
    html = fetch_scrapingbee(url, source="AliExpress", render_js=True, wait=5000)
    results = _parse_html_results(html, limit, niche) if html else []
    scrape_delay()
    return results
