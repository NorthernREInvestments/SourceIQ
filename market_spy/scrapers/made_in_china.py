"""Made-in-China wholesale search via ScrapingBee."""

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from market_spy.scrapers.base import (
    enrich_sourcing_pricing,
    is_blocked,
    parse_moq_text,
    parse_price_range,
    parse_supplier_rating_text,
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
        href = "https://www.made-in-china.com" + href
    return href.split("?")[0]


def _count_star_rating(node):
    if not node:
        return None
    stars = node.select("img[src*='star-light'], img[alt*='star']")
    if stars:
        return float(min(len(stars), 5))
    text = node.get_text(" ", strip=True)
    return parse_supplier_rating_text(text)


def _parse_price_from_node(node):
    if not node:
        return None, None
    price_el = node.select_one("strong.price, .price-info .price, .prod-price .price")
    if price_el:
        return parse_price_range(price_el.get_text(" ", strip=True))
    return parse_price_range(node.get_text(" ", strip=True))


def _parse_moq_from_node(node):
    if not node:
        return 1
    for info in node.select(".info, .product-property .info"):
        info_text = info.get_text(" ", strip=True)
        if "MOQ" in info_text.upper() or "MIN" in info_text.upper():
            return parse_moq_text(info_text)
    return parse_moq_text(node.get_text(" ", strip=True))


def _parse_html_results(html, limit, niche):
    results = []
    if is_blocked(html):
        return results
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    family = niche.strip().lower()

    for node in soup.select("div.list-node"):
        if len(results) >= limit:
            break
        title_el = node.select_one("h2.product-name a, h2.product-name")
        if not title_el:
            continue
        link_el = title_el if title_el.name == "a" else title_el.find("a", href=True)
        href = _normalize_url(link_el.get("href") if link_el else None)
        title = (link_el.get("title") if link_el else None) or title_el.get_text(" ", strip=True)
        title = re.sub(r"\s+", " ", title).strip()
        if not title or not href or href in seen:
            continue
        price, price_high = _parse_price_from_node(node)
        if price is None:
            continue
        moq = _parse_moq_from_node(node)
        rating = _count_star_rating(node.select_one(".icon-star, .auth-icon-item.icon-star"))
        card_text = node.get_text(" ", strip=True)
        shipping = parse_usa_shipping_text(card_text, "Made-in-China", price)
        seen.add(href)
        item = sourcing_item(
            "Made-in-China",
            title[:200],
            href,
            price,
            unit_price=price,
            price_high=price_high,
            bulk_price=price if moq > 1 else None,
            moq=moq,
            supplier_rating=rating,
            shipping_usa=shipping,
            product_family=family,
        )
        enrich_sourcing_pricing(item)
        results.append(item)
    return results


def scrape_made_in_china(niche, limit=20):
    q = quote_plus(niche)
    url = (
        "https://www.made-in-china.com/multi-search/"
        f"{q}/F1--SGS_AS--BT_1/1.html"
    )
    html = fetch_scrapingbee(
        url,
        source="Made-in-China",
        render_js=True,
        wait=8000,
    )
    results = _parse_html_results(html, limit, niche) if html else []
    scrape_delay()
    return results
