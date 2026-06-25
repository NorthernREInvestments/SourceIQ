"""DHgate wholesale search via ScrapingBee."""

import json
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from market_spy.scrapers.base import (
    enrich_sourcing_pricing,
    parse_price_range,
    parse_usd_price,
    scrape_delay,
    sourcing_item,
)
from market_spy.scrapers.scrapingbee_client import fetch_scrapingbee


def _parse_next_data(html, limit, niche):
    results = []
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return results
    try:
        payload = json.loads(script.string)
    except json.JSONDecodeError:
        return results
    products = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("data", {})
        .get("totalProducts", [])
    )
    seen = set()
    family = niche.strip().lower()
    for product in products:
        if len(results) >= limit:
            break
        if not isinstance(product, dict):
            continue
        title = product.get("productname") or product.get("internationalProductname")
        href = product.get("productDetailUrl") or product.get("productDurl")
        product_id = str(product.get("productid") or product.get("itemcode") or "")
        dedupe_key = product_id or href
        if not title or not href or dedupe_key in seen:
            continue
        price_text = product.get("price") or product.get("pricebeforerate") or ""
        price, price_high = parse_price_range(price_text)
        if price is None:
            price = parse_usd_price(product.get("simHighPrice"))
        if price_high is None and product.get("simHighPrice"):
            price_high = parse_usd_price(product.get("simHighPrice"))
        if price is None:
            continue
        moq = product.get("minOrderNum") or 1
        try:
            moq = int(moq)
        except (TypeError, ValueError):
            moq = 1
        original_price = None
        sale_price = None
        before_text = product.get("pricebeforerate") or product.get("simHighPriceBefore") or ""
        before_low, _ = parse_price_range(before_text)
        if before_low and before_low > price + 0.01:
            original_price = before_low
            sale_price = price
        promo = product.get("pinfallPrice") or product.get("futurePromoPrice")
        if promo:
            promo_val = parse_usd_price(str(promo))
            if promo_val and promo_val < price:
                sale_price = promo_val
        bulk_price = price if moq > 1 else None
        sold = product.get("recentlysold") or product.get("reviewCount") or 0
        try:
            sold = int(sold or 0)
        except (TypeError, ValueError):
            sold = 0
        seen.add(dedupe_key)
        item = sourcing_item(
            "DHgate", title[:200], href, price,
            unit_price=price,
            price_high=price_high,
            sale_price=sale_price,
            original_price=original_price,
            bulk_price=bulk_price,
            moq=moq,
            orders=sold,
            engagement=sold,
            shipping_usa=4.00,
            product_family=family,
        )
        enrich_sourcing_pricing(item)
        results.append(item)
    return results


def scrape_dhgate(niche, limit=20):
    q = quote_plus(niche)
    url = f"https://www.dhgate.com/wholesale/search.do?searchkey={q}"
    html = fetch_scrapingbee(url, source="DHgate", render_js=True, wait=5000)
    if not html:
        return []
    results = _parse_next_data(html, limit, niche)
    scrape_delay()
    return results
