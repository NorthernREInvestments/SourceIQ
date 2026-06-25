"""Web scrapers for market intelligence sources."""

from market_spy.scrapers.alibaba import scrape_alibaba
from market_spy.scrapers.aliexpress import scrape_aliexpress
from market_spy.scrapers.amazon import scrape_amazon
from market_spy.scrapers.appsumo import scrape_appsumo
from market_spy.scrapers.cjdropshipping import scrape_cjdropshipping
from market_spy.scrapers.dhgate import scrape_dhgate
from market_spy.scrapers.ebay import scrape_ebay
from market_spy.scrapers.etsy import scrape_etsy
from market_spy.scrapers.gumroad import scrape_gumroad
from market_spy.scrapers.made_in_china import scrape_made_in_china
from market_spy.scrapers.producthunt import scrape_producthunt
from market_spy.scrapers.reddit import scrape_reddit
from market_spy.scrapers.tiktok import scrape_tiktok
from market_spy.scrapers.walmart import scrape_walmart

from market_spy.scrapers.google_shopping import scrape_bing_shopping, scrape_google_shopping
from market_spy.scrapers.sourcing import scrape_all_sourcing

__all__ = [
    "scrape_alibaba",
    "scrape_aliexpress",
    "scrape_amazon",
    "scrape_appsumo",
    "scrape_cjdropshipping",
    "scrape_dhgate",
    "scrape_ebay",
    "scrape_etsy",
    "scrape_bing_shopping",
    "scrape_google_shopping",
    "scrape_gumroad",
    "scrape_made_in_china",
    "scrape_producthunt",
    "scrape_reddit",
    "scrape_all_sourcing",
    "scrape_tiktok",
    "scrape_walmart",
]
