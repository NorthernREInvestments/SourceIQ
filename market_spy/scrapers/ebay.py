"""eBay completed/sold listings scraper."""

import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from market_spy.browser import fetch_rendered_page
from market_spy.config import get_scrapingbee_api_key
from market_spy.scrapers.scrapingbee_client import fetch_scrapingbee

EBAY_CUTOFF_DAYS = 90


def _parse_sold_date(text):
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^Sold\s*", "", text, flags=re.I).strip()
    for fmt in ("%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_price(text):
    if not text:
        return None
    match = re.search(r"\$\s*([\d,]+(?:\.\d{2})?)", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_bids(text):
    if not text:
        return 0
    match = re.search(r"(\d+)\s+bids?", text, re.I)
    return int(match.group(1)) if match else 0


def _is_error_page(html):
    if not html:
        return True
    sample = html[:4000].lower()
    return "error-header" in sample or "error page | ebay" in sample


def _fetch_search_html(niche):
    q = quote_plus(niche)
    url = f"https://www.ebay.com/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1"
    if get_scrapingbee_api_key():
        for attempt in range(2):
            html = fetch_scrapingbee(
                url,
                source="eBay",
                render_js=True,
                wait=3000,
            )
            if html and not _is_error_page(html):
                return html
            if attempt == 0:
                time.sleep(2)
        return html
    for attempt in range(2):
        html = fetch_rendered_page(
            url,
            warmup_url="https://www.ebay.com/",
            wait_after=5000 if attempt else 4000,
        )
        if html and not _is_error_page(html):
            return html
        if attempt == 0:
            time.sleep(2)
    return html


def _parse_s_card(card, cutoff, seen, results, limit):
    if len(results) >= limit:
        return
    title_el = card.select_one(".s-card__title")
    if not title_el:
        return
    title = title_el.get_text(" ", strip=True)
    title = re.sub(r"Opens in a new window.*$", "", title, flags=re.I).strip()
    title = re.sub(r"^New Listing\s*", "", title, flags=re.I).strip()
    if not title or "Shop on eBay" in title:
        return
    link_el = card.find("a", href=re.compile(r"ebay\.com/itm/"))
    if not link_el:
        link_el = card.select_one("a.s-card__link")
    href = link_el.get("href").split("?")[0] if link_el and link_el.get("href") else None
    if not href or href in seen:
        return
    seen.add(href)
    price_el = card.select_one(".s-card__price")
    sold_price = _parse_price(price_el.get_text(" ", strip=True) if price_el else "")
    sold_date = None
    bids = 0
    for row in card.select(
        ".s-card__attribute-row, .su-styled-text.positive, .s-item__caption"
    ):
        row_text = row.get_text(" ", strip=True)
        if sold_date is None and re.search(r"Sold\s+", row_text, re.I):
            sold_date = _parse_sold_date(row_text)
        if not bids:
            bids = _parse_bids(row_text)
    if sold_date and sold_date > datetime.utcnow():
        sold_date = sold_date.replace(year=sold_date.year - 1)
    if sold_date and sold_date < cutoff:
        return
    if sold_price is None:
        return
    results.append({
        "source": "eBay",
        "side": "selling",
        "name": title,
        "url": href,
        "price": sold_price,
        "date": sold_date,
        "bids": bids,
        "engagement": bids,
    })


def _parse_s_item(card, cutoff, seen, results, limit):
    if len(results) >= limit:
        return
    title_el = card.select_one(".s-item__title, h3.s-item__title")
    if not title_el:
        return
    title = title_el.get_text(" ", strip=True)
    title = re.sub(r"^New Listing\s*", "", title, flags=re.I).strip()
    if not title or "Shop on eBay" in title:
        return
    link_el = card.select_one("a.s-item__link, a[href*='ebay.com/itm/']")
    href = link_el.get("href").split("?")[0] if link_el and link_el.get("href") else None
    if not href or href in seen:
        return
    seen.add(href)
    price_el = card.select_one(".s-item__price, .s-card__price")
    sold_price = _parse_price(price_el.get_text(" ", strip=True) if price_el else "")
    sold_date = None
    for row in card.select(".s-item__title--tag, .s-item__caption, .s-item__ended-date"):
        row_text = row.get_text(" ", strip=True)
        if sold_date is None and re.search(r"Sold\s+", row_text, re.I):
            sold_date = _parse_sold_date(row_text)
    if sold_date and sold_date > datetime.utcnow():
        sold_date = sold_date.replace(year=sold_date.year - 1)
    if sold_date and sold_date < cutoff:
        return
    if sold_price is None:
        return
    results.append({
        "source": "eBay",
        "side": "selling",
        "name": title,
        "url": href,
        "price": sold_price,
        "date": sold_date,
        "bids": 0,
        "engagement": 0,
    })


def scrape_ebay(niche, limit=20):
    """Scrape eBay sold/completed listings for actual transaction prices."""
    results = []
    html = _fetch_search_html(niche)
    if not html or _is_error_page(html):
        return results

    soup = BeautifulSoup(html, "html.parser")
    cutoff = datetime.utcnow() - timedelta(days=EBAY_CUTOFF_DAYS)
    seen = set()

    for card in soup.select(".srp-results li.s-card"):
        _parse_s_card(card, cutoff, seen, results, limit)

    if len(results) < limit:
        for card in soup.select(".srp-results li.s-item, li.s-item"):
            _parse_s_item(card, cutoff, seen, results, limit)

    return results
