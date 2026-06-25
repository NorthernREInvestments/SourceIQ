#!/usr/bin/env python3
"""Run SourceIQ from the project root.

Stage 1 (default) — free category overview, unlimited:
    python run.py "phone case"

Quick Start — Stage 1 on 12 preset niches, ranked by opportunity score:
    python run.py --quick-start

Stage 2 — deep margin drill-down (~175 ScrapingBee credits per run):
    python run.py "phone case" --drill-down "iphone 15 pro case"

Beginner mode (default) — simplified scores and top products:
    python run.py "pet supplies"

Advanced mode — full sources, margin pairs, MOQ, ratings, credits:
    python run.py "pet supplies" --advanced

Export Stage 2 to CSV (Pro tier):
    python run.py "pet supplies" --drill-down "dog collar" --export-csv

Credit usage is logged to output/credit_log.txt
"""

from dotenv import load_dotenv

load_dotenv()

from market_spy.cli import main

if __name__ == "__main__":
    main()
