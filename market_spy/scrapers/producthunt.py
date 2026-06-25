"""Product Hunt scraper."""

import re
from datetime import datetime
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from market_spy import config
from market_spy.browser import fetch_rendered_page, save_debug_html
from market_spy.utils import extract_price, parse_date_from_text, safe_niche_slug, sleep_random


def scrape_producthunt(niche, limit=12):
    results = []
    q = quote_plus(niche)
    url = f"https://www.producthunt.com/search?q={q}"
    html = fetch_rendered_page(url)
    sleep_random()
    slug = safe_niche_slug(niche)
    if config.DEBUG_PH and html:
        save_debug_html(f"producthunt_search_{slug}.html", html)
        print("[DEBUG] Search page HTML snippet (first 4k chars):")
        print(html[:4000])
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=re.compile(r"^/posts/"), limit=limit * 6)
    if not anchors:
        posts_containers = soup.find_all(attrs={"class": re.compile(r"post|hunt|search")})
        for pc in posts_containers:
            anchors.extend(pc.find_all("a", href=re.compile(r"^/posts/")))
    seen = set()
    for a in anchors:
        title = a.get_text(strip=True)
        if not title or title in seen:
            continue
        seen.add(title)
        href = a.get("href")
        if href and href.startswith("/"):
            href = urljoin("https://www.producthunt.com", href)
        sleep_random()
        ph = fetch_rendered_page(href)
        if config.DEBUG_PH and ph:
            save_debug_html(f"producthunt_post_{slug}_{len(results)}.html", ph)
            print("[DEBUG] Post page HTML snippet (first 2k chars):")
            print(ph[:2000])
        if not ph:
            continue
        psoup = BeautifulSoup(ph, "html.parser")
        upvotes = 0
        reviews = 0
        launch_date = None
        m = psoup.find(text=re.compile(r"\d+[\,\d]*\s+upvote", re.I)) or psoup.find(
            text=re.compile(r"votes?", re.I)
        )
        if m:
            try:
                upvotes = int(re.sub(r"[^0-9]", "", m))
            except Exception:
                upvotes = 0
        rev = psoup.find("a", href=re.compile(r"comments"))
        if rev:
            s = rev.get_text()
            reviews = int(re.sub(r"[^0-9]", "", s) or 0)
        time_el = psoup.find("time")
        if time_el and time_el.get("datetime"):
            try:
                launch_date = datetime.fromisoformat(time_el.get("datetime").replace("Z", "+00:00"))
            except Exception:
                launch_date = parse_date_from_text(time_el.get_text())
        price = None
        ptext = psoup.find(text=re.compile(r"\$|USD|Free|Paid|Subscription|pricing", re.I))
        if ptext:
            price = extract_price(ptext)
        engagement = upvotes + reviews
        results.append({
            "source": "Product Hunt",
            "name": title,
            "url": href,
            "price": price,
            "engagement": engagement,
            "launch_date": launch_date,
            "last_activity": None,
        })
        if len(results) >= limit:
            break
    return results
