#!/usr/bin/env python3
"""Run margin analysis across multiple niches."""

from dotenv import load_dotenv

load_dotenv()

from market_spy.analysis import compute_margin_analysis
from market_spy.cli import _print_margin_analysis, _print_source_summary
from market_spy.scrapers import (
    scrape_aliexpress,
    scrape_amazon,
    scrape_dhgate,
    scrape_ebay,
    scrape_walmart,
)
from rich.console import Console

console = Console()

NICHES = ["phone case", "yoga mat", "kitchen gadgets"]

SCRAPERS = [
    ("eBay", scrape_ebay, {"limit": 20}),
    ("Amazon", scrape_amazon, {"limit": 15}),
    ("Walmart", scrape_walmart, {"limit": 15}),
    ("AliExpress", scrape_aliexpress, {"limit": 15}),
    ("DHgate", scrape_dhgate, {"limit": 15}),
]


def run_niche(niche):
    items = []
    console.print(f"\n[bold green]Scraping:[/bold green] {niche}")
    for label, func, kwargs in SCRAPERS:
        console.print(f"  {label}...", end=" ")
        try:
            batch = func(niche, **kwargs)
            console.print(f"{len(batch)} items")
            items.extend(batch)
        except Exception as exc:
            console.print(f"failed ({exc})")
    _print_source_summary(items)
    margin = compute_margin_analysis(items, niche=niche)
    _print_margin_analysis(margin, niche=niche)
    return items, margin


def main():
    console.print("[bold]Multi-niche margin analysis[/bold]")
    console.print(f"Niches: {', '.join(NICHES)}")
    for niche in NICHES:
        run_niche(niche)
    console.print("\n[bold green]All niches complete.[/bold green]")


if __name__ == "__main__":
    main()
