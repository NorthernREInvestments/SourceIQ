"""Alibaba wholesale search via ScrapingBee."""

import re
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from market_spy.scrapers.base import (
    enrich_sourcing_pricing,
    is_blocked,
    parse_moq_text,
    parse_price_range,
    parse_scraped_usa_shipping,
    parse_supplier_rating_text,
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
        href = "https://www.alibaba.com" + href
    return href.split("?")[0]


def _title_from_card_text(card_text, href):
    title = re.split(r"\$\s*[\d,.]+", card_text, maxsplit=1)[0].strip()
    title = re.sub(
        r"^(?:certified|sponsored|verified|gold supplier)\s+",
        "",
        title,
        flags=re.I,
    )
    title = re.sub(r"\s+", " ", title).strip()
    if title and len(title) >= 5:
        return title[:200]
    slug = href.rsplit("/", 1)[-1].replace(".html", "")
    slug = re.sub(r"_\d+$", "", slug)
    slug = slug.replace("-", " ").strip()
    return slug[:200] if slug else None


def _parse_html_results(html, limit, niche):
    results = []
    if is_blocked(html):
        return results
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    family = niche.strip().lower()
    for anchor in soup.find_all("a", href=re.compile(r"product-detail/.+\.html")):
        if len(results) >= limit:
            break
        href = _normalize_url(anchor.get("href"))
        if not href or href in seen:
            continue
        card = anchor
        card_text = ""
        for _ in range(14):
            card = card.parent
            if not card:
                break
            card_text = card.get_text(" ", strip=True)
            if "$" in card_text and len(card_text) > 30:
                break
        if "$" not in card_text:
            continue
        price, price_high = parse_price_range(card_text)
        if price is None:
            continue
        title = (anchor.get("title") or anchor.get_text(" ", strip=True) or "").strip()
        if len(title) < 5:
            title = _title_from_card_text(card_text, href)
        if not title:
            continue
        moq = parse_moq_text(card_text)
        rating = parse_supplier_rating_text(card_text)
        shipping = parse_scraped_usa_shipping(card_text)
        seen.add(href)
        bulk_price = price if moq > 1 else None
        item = sourcing_item(
            "Alibaba",
            title[:200],
            href,
            price,
            unit_price=price,
            price_high=price_high,
            bulk_price=bulk_price,
            moq=moq,
            supplier_rating=rating,
            shipping_usa=shipping,
            shipping_scraped=shipping is not None,
            product_family=family,
        )
        enrich_sourcing_pricing(item)
        results.append(item)
    return results


def scrape_alibaba(niche, limit=20):
    q = quote_plus(niche)
    url = f"https://www.alibaba.com/trade/search?SearchText={q}"
    html = fetch_scrapingbee(
        url,
        source="Alibaba",
        render_js=True,
        stealth_proxy=True,
        wait=8000,
    )
    results = _parse_html_results(html, limit, niche) if html else []
    scrape_delay()
    return results
