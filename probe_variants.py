#!/usr/bin/env python3
import os
from urllib.parse import quote_plus
from dotenv import load_dotenv
load_dotenv()
from market_spy.scrapers.scrapingbee_client import fetch_scrapingbee

q = quote_plus("baby products")
urls = [
    ("cj1", "CJDropshipping", f"https://cjdropshipping.com/products.html?searchkey={q}"),
    ("cj2", "CJDropshipping", f"https://www.cjdropshipping.com/search/{q.replace('+', '-')}.html"),
    ("ali1", "Alibaba", f"https://www.alibaba.com/trade/search?SearchText={q}"),
    ("ali2", "Alibaba", f"https://us.alibaba.com/trade/search?SearchText={q}"),
    ("mic1", "Made-in-China", f"https://www.made-in-china.com/multi-search/{q.replace('+', '%20')}/F1--SGS_AS--BT_1/1.html"),
    ("mic2", "Made-in-China", f"https://www.made-in-china.com/productdirectory.do?word={q}&subaction=hunt&style=b"),
]
os.makedirs("output/debug", exist_ok=True)
for slug, src, url in urls:
    html = fetch_scrapingbee(url, source=src, render_js=True, wait=8000)
    path = f"output/debug/variant_{slug}.html"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html or "")
    has_products = html and any(
        m in html.lower()
        for m in ("product-detail", "/product/", "moq", "min.order", "us $", "usd")
    )
    blocked = html and any(m in html.lower() for m in ("captcha", "verification", "nocaptcha"))
    print(slug, len(html or ""), "products" if has_products else "no-prod", "blocked" if blocked else "ok")
