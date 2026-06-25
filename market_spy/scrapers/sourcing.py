"""Sourcing scraper orchestration."""

from market_spy.scrapers.alibaba import scrape_alibaba
from market_spy.scrapers.cjdropshipping import scrape_cjdropshipping
from market_spy.scrapers.made_in_china import scrape_made_in_china

SOURCING_FUNCS = [
    ("CJDropshipping", scrape_cjdropshipping),
    ("Alibaba", scrape_alibaba),
    ("Made-in-China", scrape_made_in_china),
]


def scrape_all_sourcing(niche, limit=20):
    """Run ScrapingBee sourcing scrapers (AliExpress/DHgate wired separately in Stage 2)."""
    all_results = []
    for _label, func in SOURCING_FUNCS:
        try:
            batch = func(niche, limit=limit)
            all_results.extend(batch)
        except Exception:
            continue
    return all_results
