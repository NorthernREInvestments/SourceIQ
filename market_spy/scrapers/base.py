"""Shared scraping utilities for Playwright-based sources."""

import random
import re
import time

from market_spy.browser import DEFAULT_USER_AGENT, fetch_rendered_page, random_user_agent

REQUEST_DELAY = (2.0, 3.0)

SELLING_SOURCES = {
    "Reddit", "eBay", "Amazon", "Walmart", "Etsy", "TikTok Shop",
    "Product Hunt", "Gumroad", "AppSumo",
}
SOURCING_SOURCES = {
    "AliExpress", "DHgate", "CJDropshipping", "Alibaba", "Made-in-China", "Google Shopping",
}

BLOCK_MARKERS = (
    "captcha", "robot or human", "access denied", "human verification",
    "attention required", "please verify", "datadome", "turnstile",
)


def scrape_delay():
    time.sleep(random.uniform(*REQUEST_DELAY))


def is_blocked(html):
    if not html:
        return True
    lower = html.lower()
    return any(marker in lower for marker in BLOCK_MARKERS)


def fetch_page(url, warmup_url=None, wait_after=3000, scroll_steps=0, human_mouse=False):
    return fetch_rendered_page(
        url,
        warmup_url=warmup_url,
        wait_after=wait_after,
        user_agent=random_user_agent(),
        scroll_steps=scroll_steps,
        human_mouse=human_mouse,
    )


def parse_usd_price(text):
    if not text:
        return None
    match = re.search(r"\$\s*([\d,]+(?:\.\d{2})?)", str(text))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_price_range(text):
    if not text:
        return None, None
    prices = re.findall(r"\$\s*([\d,]+(?:\.\d{2})?)", str(text))
    values = []
    for p in prices:
        try:
            values.append(float(p.replace(",", "")))
        except ValueError:
            continue
    if not values:
        return None, None
    return min(values), max(values)


def parse_moq_text(text):
    if not text:
        return 1
    match = re.search(
        r"(?:MOQ|Min\.?\s*Order)[:\s]*([\d,]+)\s*(?:pieces|pcs|pc|units?|sets?)?",
        str(text),
        re.I,
    )
    if not match:
        match = re.search(r"([\d,]+)\s*\+\s*(?:pieces|pcs|pc)", str(text), re.I)
    if not match:
        return 1
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return 1


def parse_supplier_rating_text(text):
    if not text:
        return None
    match = re.search(r"(\d\.\d)\s*/\s*5(?:\.0)?", str(text))
    if match:
        return float(match.group(1))
    match = re.search(r"(\d\.\d)\s*(?:star|out of)", str(text), re.I)
    if match:
        return float(match.group(1))
    return None


def parse_scraped_usa_shipping(text):
    """Return USA shipping only when explicitly present in scraped page text."""
    if not text:
        return None
    match = re.search(
        r"(?:shipping(?:\s+cost)?(?:\s+to\s+US(?:A)?)?|ship\s+to\s+US(?:A)?)"
        r"[:\s]*\$\s*([\d,.]+)",
        str(text),
        re.I,
    )
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_usa_shipping_text(text, source="", price=None):
    if not text:
        return estimate_usa_shipping(source, price)
    match = re.search(
        r"(?:shipping(?:\s+cost)?(?:\s+to\s+US(?:A)?)?|ship\s+to\s+US(?:A)?)"
        r"[:\s]*\$\s*([\d,.]+)",
        str(text),
        re.I,
    )
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            pass
    return estimate_usa_shipping(source, price)


def parse_int_text(text):
    if not text:
        return 0
    match = re.search(r"([\d,]+)", str(text))
    if not match:
        return 0
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return 0


def estimate_usa_shipping(source, price=None):
    defaults = {
        "AliExpress": 3.50,
        "DHgate": 4.00,
        "CJDropshipping": 5.00,
        "Alibaba": 8.00,
        "Made-in-China": 7.00,
        "Google Shopping": 4.50,
    }
    base = defaults.get(source, 5.00)
    if price is not None and price < 5:
        return round(base * 0.8, 2)
    return base


def landed_cost(item):
    price = item.get("price")
    if price is None:
        return None
    shipping = item.get("shipping_usa")
    if shipping is None:
        shipping = estimate_usa_shipping(item.get("source", ""), price)
    return round(float(price) + float(shipping), 2)


def selling_item(source, name, url, price, **extra):
    item = {
        "source": source,
        "side": "selling",
        "name": name,
        "url": url,
        "price": price,
    }
    item.update(extra)
    return item


def sourcing_item(source, name, url, price, **extra):
    item = {
        "source": source,
        "side": "sourcing",
        "name": name,
        "url": url,
        "price": price,
        "product_family": extra.pop("product_family", None),
    }
    item.update(extra)
    return item


def enrich_sourcing_pricing(item):
    """Populate unit/bulk/sale prices and best landed cost."""
    source = item.get("source", "")
    ship = item.get("shipping_usa")
    if ship is None:
        ship = estimate_usa_shipping(source, item.get("price"))
        item["shipping_usa"] = ship

    unit = item.get("unit_price")
    if unit is None:
        unit = item.get("price")
    item["unit_price"] = unit
    item["moq"] = int(item.get("moq") or 1)

    options = []
    if unit is not None:
        options.append(("unit", float(unit)))
    sale = item.get("sale_price")
    if sale is not None:
        options.append(("sale", float(sale)))
    bulk = item.get("bulk_price")
    if bulk is not None:
        options.append(("bulk", float(bulk)))

    if options:
        best_type, best_price = min(options, key=lambda x: x[1])
        item["best_price"] = best_price
        item["best_price_type"] = best_type
        item["best_landed_cost"] = round(float(best_price) + float(ship), 2)
    item["price_label"] = format_sourcing_price_label(item)
    return item


def format_sourcing_price_label(item):
    parts = []
    unit = item.get("unit_price")
    if unit is not None:
        parts.append(f"unit ${unit:.2f}")
    bulk = item.get("bulk_price")
    if bulk is not None:
        moq = item.get("moq") or 1
        parts.append(f"bulk ${bulk:.2f}@{moq}")
    sale = item.get("sale_price")
    if sale is not None:
        parts.append(f"sale ${sale:.2f}")
    best = item.get("best_landed_cost")
    if best is not None:
        tag = item.get("best_price_type") or "best"
        parts.append(f"best {tag} landed ${best:.2f}")
    return " | ".join(parts) if parts else ""


def niche_matches(text, niche):
    if not text or not niche:
        return True
    words = [w for w in re.split(r"\W+", niche.lower()) if len(w) > 2]
    lower = text.lower()
    return any(w in lower for w in words)
