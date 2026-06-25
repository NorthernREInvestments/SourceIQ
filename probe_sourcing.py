#!/usr/bin/env python3
"""One-off probe for ScrapingBee sourcing HTML."""

import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()

from market_spy.scrapers.scrapingbee_client import fetch_scrapingbee

os.makedirs("output/debug", exist_ok=True)
q = quote_plus("baby products")
sites = [
    ("cj", "CJDropshipping", f"https://cjdropshipping.com/products.html?searchkey={q}"),
    ("alibaba", "Alibaba", f"https://www.alibaba.com/trade/search?SearchText={q}"),
    (
        "mic",
        "Made-in-China",
        "https://www.made-in-china.com/productdirectory.do?"
        f"subaction=hunt&style=b&mode=and&code=0&comProvince=nolimit&order=0"
        f"&isOpenCorrection=1&org=top&keyword={q}",
    ),
]
for slug, name, url in sites:
    html = fetch_scrapingbee(url, source=name, render_js=True, wait=5000)
    path = f"output/debug/probe_{slug}.html"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html or "")
    blocked = html and any(
        m in html.lower() for m in ("captcha", "verification", "access denied")
    )
    print(name, "len", len(html or ""), "blocked" if blocked else "ok", path)
