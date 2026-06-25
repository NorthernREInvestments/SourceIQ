"""TikTok Creative Center trending products scraper."""

import json
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from market_spy.scrapers.base import (
    fetch_page,
    is_blocked,
    niche_matches,
    parse_int_text,
    scrape_delay,
    selling_item,
)

TIKTOK_URL = (
    "https://ads.tiktok.com/business/creativecenter/inspiration/popular/product/pc/en"
)
TIKTOK_SEARCH_URL = (
    "https://ads.tiktok.com/business/creativecenter/inspiration/popular/pc/en"
)

SKIP_TIKTOK_TITLES = (
    "united states of america",
    "browse what's trending",
    "find what performs",
    "tiktok for business",
    "creative center",
)


def _is_valid_tiktok_title(name):
    if not name or len(name) < 10:
        return False
    lower = name.lower()
    return not any(skip in lower for skip in SKIP_TIKTOK_TITLES)


def _extract_from_scripts(soup, niche):
    results = []
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if len(text) < 100 or "product" not in text.lower():
            continue
        for match in re.finditer(r"\{[^{}]*\"(?:name|title|product_name)\"[^{}]*\}", text):
            try:
                obj = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
            name = obj.get("name") or obj.get("title") or obj.get("product_name")
            if not name or not niche_matches(name, niche) or not _is_valid_tiktok_title(name):
                continue
            url = obj.get("url") or obj.get("link") or TIKTOK_URL
            engagement = parse_int_text(str(obj.get("like", obj.get("ctr", obj.get("impression", 0)))))
            results.append(selling_item(
                "TikTok Shop", name[:200], url, None,
                engagement=engagement,
            ))
    return results


def scrape_tiktok(niche, limit=15):
    results = []
    q = quote_plus(niche)
    urls = [
        f"{TIKTOK_URL}?keyword={q}",
        f"{TIKTOK_SEARCH_URL}?keyword={q}",
        TIKTOK_URL,
    ]
    seen = set()
    for url in urls:
        html = fetch_page(url, wait_after=5000, scroll_steps=4)
        if is_blocked(html):
            continue
        soup = BeautifulSoup(html, "html.parser")
        for row in soup.select(
            "[class*='product'], [class*='Product'], [class*='trend'], "
            "[class*='card'], tr, li"
        ):
            if len(results) >= limit:
                break
            text = row.get_text(" ", strip=True)
            if not text or len(text) < 8:
                continue
            if not niche_matches(text, niche) and url == TIKTOK_URL:
                continue
            link = row.find("a", href=True)
            href = link["href"] if link else url
            if not href.startswith("http"):
                href = "https://ads.tiktok.com" + href
            name = text[:120]
            if not _is_valid_tiktok_title(name):
                continue
            if href in seen:
                continue
            metrics = {}
            for label, pat in [
                ("likes", r"([\d,.]+[KMB]?)\s*likes?"),
                ("ctr", r"([\d.]+%)\s*CTR"),
                ("impressions", r"([\d,.]+[KMB]?)\s*impressions?"),
            ]:
                m = re.search(pat, text, re.I)
                if m:
                    metrics[label] = m.group(1)
            engagement = parse_int_text(metrics.get("likes", "0"))
            seen.add(href)
            results.append(selling_item(
                "TikTok Shop", name, href, None,
                engagement=engagement, metrics=metrics,
            ))
        results.extend(_extract_from_scripts(soup, niche))
        if results:
            break
    scrape_delay()
    return results[:limit]
