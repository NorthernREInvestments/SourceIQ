"""Google/Bing Shopping fallback for wholesale sourcing prices."""

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from market_spy.browser import fetch_rendered_page, random_user_agent
from market_spy.config import get_scrapingbee_api_key
from market_spy.scrapers.base import is_blocked, parse_usd_price, sourcing_item
from market_spy.scrapers.scrapingbee_client import fetch_scrapingbee


def _infer_source(name, url, card_text=""):
    text = f"{name} {url} {card_text}".lower()
    if "aliexpress" in text:
        return "AliExpress"
    if "dhgate" in text:
        return "DHgate"
    if "alibaba" in text:
        return "Alibaba"
    if "cj" in text and "dropship" in text:
        return "CJDropshipping"
    return "Google Shopping"


def _clean_title(raw):
    title = re.sub(r"^Saved Save to wishlist\s*(SALE\s*)?", "", raw, flags=re.I)
    title = title.split("$")[0].strip()
    title = re.sub(r"\s+A\s+(Aliexpress|DHgate|Alibaba).*$", "", title, flags=re.I)
    title = re.sub(r"[^\x00-\x7F]+", "", title)
    return re.sub(r"\s+", " ", title).strip()


def _parse_bing_shopping(html, limit):
    results = []
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    for card in soup.select(".br-item"):
        if len(results) >= limit:
            break
        text = card.get_text(" ", strip=True)
        if "$" not in text:
            continue
        link_el = card.find("a", href=True)
        href = link_el.get("href") if link_el else ""
        prices = re.findall(r"\$\s*([\d,]+(?:\.\d{2})?)", text)
        if not prices:
            continue
        try:
            price = float(prices[0].replace(",", ""))
            price_high = float(prices[1].replace(",", "")) if len(prices) > 1 else None
        except ValueError:
            continue
        title = _clean_title(text)
        if not title or len(title) < 5:
            continue
        source = _infer_source(title, href, text)
        key = (title[:80], price)
        if key in seen:
            continue
        seen.add(key)
        price_label = f"${price:.2f}"
        if price_high and price_high > price:
            price_label = f"${price:.2f}–${price_high:.2f}"
        results.append(sourcing_item(
            source,
            title[:200],
            href,
            price,
            price_high=price_high,
            price_label=price_label,
            fallback=True,
        ))
    return results


def _parse_google_shopping(html, limit):
    results = []
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    cards = soup.select(
        ".sh-dgr__grid-result, .i0Xnmd, .Ez5pwe, div[data-docid], li[data-attrid]"
    )
    for card in cards:
        if len(results) >= limit:
            break
        title_el = card.select_one("h3, h4, .tAxDx")
        link_el = card.find("a", href=True)
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        href = link_el.get("href") if link_el else ""
        price = parse_usd_price(card.get_text(" ", strip=True))
        if not title or price is None:
            continue
        key = (title[:80], price)
        if key in seen:
            continue
        seen.add(key)
        source = _infer_source(title, href)
        results.append(sourcing_item(
            source,
            title[:200],
            href,
            price,
            price_label=f"${price:.2f}",
            fallback=True,
        ))
    return results


def scrape_bing_shopping(niche, limit=20):
    """Bing Shopping sourcing prices (Stage 1 free source)."""
    bing_url = f"https://www.bing.com/shop?q={quote_plus(niche + ' wholesale aliexpress')}"
    if get_scrapingbee_api_key():
        bing_html = fetch_scrapingbee(
            bing_url,
            source="Bing Shopping",
            render_js=False,
            wait=0,
        )
    else:
        bing_html = fetch_rendered_page(
            bing_url,
            wait_after=5000,
            user_agent=random_user_agent(),
            scroll_steps=4,
            human_mouse=True,
        )
    if bing_html and not is_blocked(bing_html):
        return _parse_bing_shopping(bing_html, limit)
    return []


def scrape_google_shopping(niche, limit=20):
    """
    Fallback sourcing via Google Shopping, then Bing Shopping if Google is blocked.
  """
    q = quote_plus(f"{niche} wholesale price aliexpress")
    google_url = f"https://www.google.com/search?q={q}&tbm=shop"
    html = fetch_rendered_page(
        google_url,
        wait_after=5000,
        user_agent=random_user_agent(),
        scroll_steps=3,
        human_mouse=True,
    )
    if html and not is_blocked(html) and "unusual traffic" not in html.lower():
        results = _parse_google_shopping(html, limit)
        if results:
            return results

    bing_url = f"https://www.bing.com/shop?q={quote_plus(niche + ' wholesale aliexpress')}"
    bing_html = fetch_rendered_page(
        bing_url,
        wait_after=5000,
        user_agent=random_user_agent(),
        scroll_steps=4,
        human_mouse=True,
    )
    if bing_html and not is_blocked(bing_html):
        return _parse_bing_shopping(bing_html, limit)
    return []
