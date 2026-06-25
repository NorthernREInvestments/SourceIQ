"""Gumroad scraper."""

import json
import re
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup

from market_spy import config
from market_spy.browser import fetch_rendered_page, save_debug_html
from market_spy.utils import extract_price, parse_date_from_text, sleep_random

SKIP_TITLES = {"gumroad", "discover", "log in", "sign up", "start selling"}
ERROR_TITLES = {"error1015", "just a moment", "access denied", "attention required"}


def _normalize_product_url(href):
    if not href:
        return None
    href = href.split("?")[0]
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://gumroad.com" + href
    return href


def _extract_price_from_text(text):
    if not text:
        return None, None
    currency = "USD"
    if "EUR" in text.upper():
        currency = "EUR"
    elif "GBP" in text.upper():
        currency = "GBP"
    match = re.search(r"([€£$])\s*([\d,]+(?:\.\d{2})?)", text)
    if match:
        symbol, amount = match.groups()
        if symbol == "€":
            currency = "EUR"
        elif symbol == "£":
            currency = "GBP"
        try:
            return float(amount.replace(",", "")), currency
        except ValueError:
            pass
    amount = extract_price(text)
    return (amount, currency) if amount is not None else (None, None)


def _extract_card_fields(anchor):
    title = anchor.get_text(" ", strip=True)
    href = _normalize_product_url(anchor.get("href"))
    price = None
    currency = None
    review_date = None
    card = anchor
    for _ in range(8):
        card = card.parent
        if not card:
            break
        text = card.get_text(" ", strip=True)
        if "$" in text or "€" in text or "£" in text:
            price, currency = _extract_price_from_text(text)
            review_match = re.search(r"(\d\.\d)\s*\((\d+)\)", text)
            if review_match and not review_date:
                review_date = parse_date_from_text(text)
            break
    return title, href, price, currency, review_date


def _scrape_product_page(url):
    html = fetch_rendered_page(url)
    if not html:
        return None, None, None
    soup = BeautifulSoup(html, "html.parser")
    title = None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
    price = None
    currency = "USD"
    meta_price = soup.find("meta", attrs={"property": re.compile(r"product:price:amount|og:price:amount", re.I)})
    if meta_price and meta_price.get("content"):
        try:
            price = float(re.sub(r"[^0-9.]", "", meta_price.get("content")))
        except ValueError:
            price = None
    meta_currency = soup.find("meta", attrs={"property": re.compile(r"product:price:currency", re.I)})
    if meta_currency and meta_currency.get("content"):
        currency = meta_currency.get("content").upper()
    if price is None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (TypeError, json.JSONDecodeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                offers = item.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    if offers.get("price"):
                        price = float(str(offers["price"]).replace(",", ""))
                    if offers.get("priceCurrency"):
                        currency = str(offers["priceCurrency"]).upper()
                    if price is not None:
                        break
            if price is not None:
                break
    last_confirm = None
    time_el = soup.find("time")
    if time_el and time_el.get("datetime"):
        last_confirm = parse_date_from_text(time_el.get("datetime"))
    if not last_confirm:
        for node in soup.find_all(string=re.compile(r"\d{4}-\d{2}-\d{2}|ago|yesterday", re.I)):
            last_confirm = parse_date_from_text(str(node))
            if last_confirm:
                break
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else None
    return title, price, currency, last_confirm


def scrape_gumroad(niche, limit=12):
    results = []
    q = quote_plus(niche)
    url = f"https://gumroad.com/discover?query={q}"
    html = fetch_rendered_page(url, wait_after=3000)
    save_debug_html("gumroad_debug.html", html or "", max_chars=2000)
    if config.DEBUG_GUM and html:
        save_debug_html(f"gumroad_search_{niche.replace(' ', '_')}.html", html)
    if not html:
        return results

    soup = BeautifulSoup(html, "html.parser")
    seen_urls = set()
    candidates = []
    for anchor in soup.find_all("a", href=re.compile(r"/l/")):
        href = _normalize_product_url(anchor.get("href"))
        if not href or "/l/" not in href:
            continue
        host = urlparse(href).netloc
        if host == "gumroad.com" and href.rstrip("/").endswith("gumroad.com"):
            continue
        title, link, price, currency, review_date = _extract_card_fields(anchor)
        if not title or title.lower() in SKIP_TITLES:
            continue
        if link in seen_urls:
            continue
        seen_urls.add(link)
        candidates.append({
            "title": title,
            "url": link,
            "price": price,
            "currency": currency,
            "price_last_confirmed": review_date,
        })

    for idx, item in enumerate(candidates):
        if len(results) >= limit:
            break
        name = item["title"]
        price = item["price"]
        currency = item["currency"] or "USD"
        last_confirm = item.get("price_last_confirmed")

        if price is None or not last_confirm:
            sleep_random()
            page_title, page_price, page_currency, page_last = _scrape_product_page(item["url"])
            if page_title and page_title.lower() not in ERROR_TITLES:
                name = page_title
            if page_price is not None:
                price = page_price
            if page_currency:
                currency = page_currency
            if page_last:
                last_confirm = page_last

        if not name or name.lower() in SKIP_TITLES or name.lower() in ERROR_TITLES or price is None:
            continue
        if price <= 0:
            continue
        results.append({
            "source": "Gumroad",
            "name": name,
            "url": item["url"],
            "price": price,
            "currency": currency,
            "price_last_confirmed": last_confirm,
        })

    return results
