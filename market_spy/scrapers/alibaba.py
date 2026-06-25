"""Alibaba wholesale search via ScrapingBee."""

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from market_spy.scrapers.base import (
    enrich_sourcing_pricing,
    is_blocked,
    parse_moq_text,
    parse_scraped_usa_shipping,
    parse_supplier_rating_text,
    scrape_delay,
    sourcing_item,
)
from market_spy.scrapers.scrapingbee_client import fetch_scrapingbee

MIN_VALID_UNIT_PRICE = 0.50
PREFERRED_MIN_UNIT_PRICE = 1.00

# Alibaba search result cards — price-specific elements first.
_PRICE_SELECTORS = (
    "[class*='search-card-e-price']",
    "[class*='elements-offer-price']",
    "[class*='price-item']",
    "[class*='price']",
    "[data-price]",
)


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


def _dollar_amounts_in_text(text: str) -> list[float]:
    if not text:
        return []
    values = []
    for match in re.findall(r"\$\s*([\d,]+(?:\.\d{2})?)", str(text)):
        try:
            values.append(float(match.replace(",", "")))
        except ValueError:
            continue
    return values


def _pick_unit_price(values: list[float]) -> tuple[float | None, float | None]:
    """Ignore junk under $0.50; prefer real unit prices at or above $1."""
    valid = [v for v in values if v >= MIN_VALID_UNIT_PRICE]
    if not valid:
        return None, None
    preferred = [v for v in valid if v >= PREFERRED_MIN_UNIT_PRICE]
    pool = preferred if preferred else valid
    return min(pool), max(pool)


def _log_price_source(title: str, tag: str, classes: str, text: str, unit: float, high: float | None):
    cls = classes[:100] if classes else "(no class)"
    snippet = re.sub(r"\s+", " ", text)[:120]
    high_part = f" high=${high:.2f}" if high is not None else ""
    print(
        f"[alibaba] price extracted title={title[:50]!r} "
        f"element=<{tag} class={cls!r}> text={snippet!r} "
        f"unit=${unit:.2f}{high_part}",
        flush=True,
    )


def _extract_prices_from_card(card, title: str) -> tuple[float | None, float | None]:
    """Extract wholesale unit price from card HTML; log the winning element."""
    best_source = None
    best_unit = None
    best_high = None

    for selector in _PRICE_SELECTORS:
        for el in card.select(selector):
            text = el.get_text(" ", strip=True)
            if "$" not in text:
                data_price = el.get("data-price")
                if data_price:
                    text = f"${data_price} {text}"
                else:
                    continue
            amounts = _dollar_amounts_in_text(text)
            unit, high = _pick_unit_price(amounts)
            if unit is None:
                continue
            classes = " ".join(el.get("class") or [])
            tag = el.name or "node"
            if best_unit is None or unit < best_unit:
                best_unit = unit
                best_high = high
                best_source = (tag, classes, text)

    if best_unit is not None and best_source:
        tag, classes, text = best_source
        _log_price_source(title, tag, classes, text, best_unit, best_high)
        return best_unit, best_high

    card_text = card.get_text(" ", strip=True)
    amounts = _dollar_amounts_in_text(card_text)
    unit, high = _pick_unit_price(amounts)
    if unit is not None:
        print(
            f"[alibaba] price extracted (card text fallback) title={title[:50]!r} "
            f"unit=${unit:.2f}"
            f"{f' high=${high:.2f}' if high is not None else ''} "
            f"amounts_found={amounts[:8]}",
            flush=True,
        )
    return unit, high


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
        title = (anchor.get("title") or anchor.get_text(" ", strip=True) or "").strip()
        if len(title) < 5:
            title = _title_from_card_text(card_text, href)
        if not title:
            continue
        price, price_high = _extract_prices_from_card(card, title)
        if price is None:
            print(
                f"[alibaba] skipped (no valid price >= ${MIN_VALID_UNIT_PRICE}) "
                f"title={title[:50]!r}",
                flush=True,
            )
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
