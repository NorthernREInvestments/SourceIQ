"""Command-line interface for SourceIQ."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console
from rich.table import Table

from market_spy import config
from market_spy.analysis import (
    compute_margin_analysis,
    compute_market_opportunity,
    enforce_recency_and_timestamps,
)
from market_spy.config import (
    CREDIT_LOG_FILE,
    REPORTS_DIR,
    STAGE1_UPGRADE_MESSAGE,
    STAGE2_CREDITS_PER_DRILLDOWN,
    can_stage1_search,
    can_stage2_search,
    get_remaining_searches,
    increment_stage1_count,
    increment_stage2_count,
)
from market_spy.export import export_stage2_csv
from market_spy.report import generate_report
from market_spy.scrapers import (
    scrape_aliexpress,
    scrape_alibaba,
    scrape_amazon,
    scrape_appsumo,
    scrape_bing_shopping,
    scrape_cjdropshipping,
    scrape_dhgate,
    scrape_ebay,
    scrape_gumroad,
    scrape_made_in_china,
    scrape_walmart,
)
from market_spy.scrapers.scrapingbee_client import get_session_credit_total
from market_spy.trends import fetch_trends

console = Console()

SEARCH_TIP = (
    "Tip: Start broad to find categories, then drill down on specific products for "
    "margin analysis. Specific searches return more accurate margin data than broad terms."
)

STAGE1_DISCLAIMER = (
    "SourceIQ helps you identify opportunities worth investigating. Always order samples, "
    "verify supplier quality, and research competition before investing in inventory. "
    "This tool assists your research — it does not guarantee profit."
)

QUICK_START_NICHES = [
    "home decor",
    "pet supplies",
    "fitness gear",
    "kitchen gadgets",
    "beauty tools",
    "phone accessories",
    "outdoor gear",
    "baby products",
    "car accessories",
    "gaming accessories",
    "jewelry",
    "yoga equipment",
]

STAGE1_SCRAPERS = [
    ("eBay", scrape_ebay, {"limit": 20}),
    ("Bing Shopping", scrape_bing_shopping, {"limit": 15}),
    ("Gumroad", scrape_gumroad, {"limit": 10}),
    ("AppSumo", scrape_appsumo, {"limit": 10}),
]

STAGE2_DISCLAIMER = (
    "IMPORTANT: Margin estimates are based on product cost only and do not include "
    "shipping to FBA, Amazon referral fees, fulfillment fees, advertising spend, or "
    "returns. Always verify your true costs and order samples before purchasing inventory. "
    "SourceIQ is a research tool, not a guarantee of profit."
)

STAGE2_COMING_SOON = {"CJDropshipping"}

STAGE2_SCRAPERS = [
    ("Amazon", scrape_amazon, {"limit": 15}),
    ("Walmart", scrape_walmart, {"limit": 15}),
    ("AliExpress", scrape_aliexpress, {"limit": 15}),
    ("DHgate", scrape_dhgate, {"limit": 15}),
    ("CJDropshipping", scrape_cjdropshipping, {"limit": 15}),
    ("Alibaba", scrape_alibaba, {"limit": 15}),
    ("Made-in-China", scrape_made_in_china, {"limit": 15}),
]


def _print_search_tip():
    console.print(f"\n[dim italic]{SEARCH_TIP}[/dim italic]")


def _print_usage_footer():
    remaining = get_remaining_searches()
    console.print(
        f"\n  Plan: [bold]{remaining['tier'].title()}[/bold] — "
        f"Stage 1: {remaining['stage1_remaining']}/{remaining['stage1_limit']} left, "
        f"Stage 2: {remaining['stage2_remaining']}/{remaining['stage2_limit']} left this month "
        f"(~{STAGE2_CREDITS_PER_DRILLDOWN} ScrapingBee credits per drill-down)"
    )


def _run_scraper(label, func, niche, kwargs):
    try:
        results = func(niche, **kwargs)
        console.print(f"  {label}: {len(results)} items")
        return results
    except Exception as exc:
        console.print(f"  {label}: failed ({exc})")
        return []


def _run_scraper_batch(scrapers, niche, quiet=False):
    items = []
    for label, func, kwargs in scrapers:
        if label in STAGE2_COMING_SOON:
            if not quiet:
                console.print(f"Scraping {label}...")
                console.print(f"  {label}: coming soon (skipped)")
            continue
        if not quiet:
            console.print(f"Scraping {label}...")
            items.extend(_run_scraper(label, func, niche, kwargs))
        else:
            items.extend(_run_scraper_quiet(label, func, niche, kwargs))
    return items


def _run_scraper_quiet(label, func, niche, kwargs):
    try:
        return func(niche, **kwargs)
    except Exception as exc:
        return []


def _collect_stage1(category):
    items = []
    for label, func, kwargs in STAGE1_SCRAPERS:
        items.extend(_run_scraper_quiet(label, func, category, kwargs))
    trends = fetch_trends(category)
    items = enforce_recency_and_timestamps(items)
    score = compute_market_opportunity(items, trends)
    return {
        "category": category,
        "items": items,
        "trends": trends,
        "score": score,
        "listings": len(items),
    }


def _top_products(items, limit=3):
    ranked = sorted(items, key=lambda x: x.get("engagement", 0), reverse=True)
    return ranked[:limit]


def _suggest_drill_downs(items, limit=5):
    seen = set()
    suggestions = []
    for item in items:
        name = (item.get("name") or "").strip()
        key = name.lower()
        if len(name) < 8 or key in seen:
            continue
        seen.add(key)
        suggestions.append(name[:70])
        if len(suggestions) >= limit:
            break
    return suggestions


def _margin_label_style(label):
    if label == "HIGH":
        return "bold green"
    if label == "MEDIUM":
        return "bold yellow"
    return "bold red"


def _format_sourcing_pricing_parts(match):
    segments = []
    unit = match.get("unit_price")
    if unit is None:
        unit = match.get("source_price")
    if unit is not None:
        segments.append(f"unit ${unit:.2f}")
    bulk = match.get("bulk_price")
    moq = match.get("moq") or 1
    if bulk is not None:
        if moq > 1:
            segments.append(f"bulk ${bulk:.2f} @ MOQ {moq}")
        else:
            segments.append(f"bulk ${bulk:.2f}")
    sale = match.get("sale_price")
    if sale is not None:
        segments.append(f"sale ${sale:.2f}")
    pricing = " | ".join(segments) if segments else "—"
    best_type = match.get("best_price_type") or "unit"
    landed = match.get("source_landed")
    best_note = f"BEST LANDED ${landed:.2f} ({best_type})" if landed is not None else None
    return pricing, best_note


def _collect_margin_matches(margin):
    matches = []
    by_tier = margin.get("by_tier") or {}
    for tier_data in by_tier.values():
        for match in tier_data.get("matches") or []:
            matches.append(match)
    matches.sort(key=lambda m: m.get("margin_percent") or 0, reverse=True)
    return matches


def _print_margin_simple(margin, niche=None):
    if niche:
        console.print(f"\n[bold magenta]Drill-down: {niche}[/bold magenta]")
    console.print("\n[bold cyan]Margin Summary[/bold cyan]")
    console.print(f"[dim]{STAGE2_DISCLAIMER}[/dim]")

    matches = _collect_margin_matches(margin)
    if not matches:
        console.print("  No margin matches found.")
        return

    for match in matches[:3]:
        label = match.get("margin_label", "LOW")
        style = _margin_label_style(label)
        name = (match.get("display_name") or match.get("sourcing_name") or "Product")[:55]
        console.print(
            f"  {name} — [{style}]{label}[/{style}] "
            f"({match.get('margin_percent', 0):.0f}% margin)"
        )

    best = matches[0]
    overall = best.get("margin_label", "LOW")
    style = _margin_label_style(overall)
    console.print(f"\n  Overall signal: [{style}]{overall}[/{style}]")


def _print_margin_analysis(margin, niche=None):
    from market_spy.analysis import CATEGORY_AVERAGE_LABEL, TIER_LABELS

    if niche:
        console.print(f"\n[bold magenta]{'=' * 56}[/bold magenta]")
        console.print(f"[bold magenta]DRILL-DOWN: {niche}[/bold magenta]")
        console.print(f"[bold magenta]{'=' * 56}[/bold magenta]")

    family = margin.get("product_family") or "Product family"
    console.print("\n[bold cyan]Stage 2 — Margin Analysis[/bold cyan]")
    console.print(f"\n[bold yellow]{STAGE2_DISCLAIMER}[/bold yellow]")
    console.print(
        f"\n  Product family: [bold]{family}[/bold] — tier filter, keyword match, "
        f"category-average fallback."
    )

    by_tier = margin.get("by_tier") or {}
    has_any = any(
        (data.get("matches") or data.get("unmatched"))
        for data in by_tier.values()
    )
    if not has_any:
        console.print("  No matched pairs in comparable tiers.")
        return

    for tier_key, tier_label in TIER_LABELS.items():
        tier_data = by_tier.get(tier_key) or {}
        tier_matches = tier_data.get("matches") or []
        tier_unmatched = tier_data.get("unmatched") or []
        if not tier_matches and not tier_unmatched:
            continue

        console.print(f"\n[bold]{tier_label}[/bold]")

        for match in tier_matches:
            label = match["margin_label"]
            style = _margin_label_style(label)
            pricing, best_note = _format_sourcing_pricing_parts(match)
            platform = match["source_platform"]
            rating = match.get("supplier_rating")
            rating_note = f" | supplier {rating:.1f}/5" if rating else ""
            if match.get("match_type") == "category_average":
                console.print(
                    f"  {match['display_name']} — [dim]{pricing}[/dim] {platform}{rating_note}"
                )
                if best_note:
                    console.print(f"    [bold green]{best_note}[/bold green]")
                console.print(
                    f"    est. sells ${match['selling_price']:.2f} (tier avg) — "
                    f"margin {match['margin_percent']:.0f}% [{style}]{label}[/{style}] "
                    f"[bold yellow]{CATEGORY_AVERAGE_LABEL}[/bold yellow]"
                )
            else:
                console.print(
                    f"  {match['display_name']} — [dim]{pricing}[/dim] {platform}{rating_note}"
                )
                if best_note:
                    console.print(f"    [bold green]{best_note}[/bold green]")
                line = (
                    f"    sells ${match['selling_price']:.2f} {match['selling_platform']} — "
                    f"margin {match['margin_percent']:.0f}% [{style}]{label}[/{style}] "
                    f"({match['confidence']:.0f}% match)"
                )
                if match.get("low_confidence"):
                    line += " [bold red]LOW CONFIDENCE — verify manually[/bold red]"
                console.print(line)

        for item in tier_unmatched:
            pricing, best_note = _format_sourcing_pricing_parts(item)
            console.print(
                f"  {(item.get('display_name') or item.get('sourcing_name') or 'Product')[:55]} — "
                f"[dim]{pricing}[/dim] {item['source_platform']}"
            )
            if best_note:
                console.print(f"    [bold green]{best_note}[/bold green]")
            console.print("    [bold yellow]NO COMPARABLE FOUND[/bold yellow]")

        tier_pct = tier_data.get("tier_margin_percent")
        tier_lbl = tier_data.get("tier_margin_label")
        count = tier_data.get("match_count", 0)
        if tier_pct is not None and tier_lbl:
            style = _margin_label_style(tier_lbl)
            console.print(
                f"  [bold]Tier margin:[/bold] [{style}]{tier_pct:.1f}% {tier_lbl}[/{style}] "
                f"({count} pair{'s' if count != 1 else ''})"
            )
        else:
            console.print("  [bold]Tier margin:[/bold] no matched pairs in this tier")


def _print_source_summary(items):
    counts = {}
    for item in items:
        src = item.get("source", "Unknown")
        counts[src] = counts.get(src, 0) + 1
    console.print("\n[bold]Results by source:[/bold]")
    for src in sorted(counts):
        sample = next((i for i in items if i.get("source") == src), None)
        price_note = _format_price(sample) if sample else "—"
        console.print(f"  {src}: {counts[src]} items (sample price: {price_note})")


def _format_price(item):
    if item.get("price_label"):
        return item["price_label"]
    if item.get("price") is not None:
        currency = item.get("currency", "USD")
        symbol = "$" if currency == "USD" else currency + " "
        base = f"{symbol}{item['price']:.2f}"
        if item.get("original_price") and item["original_price"] > item["price"]:
            return f"{base} (was ${item['original_price']:.2f})"
        return base
    return "—"


def _print_stage1_simple(category, items, score):
    console.print(f"\n[bold green]Stage 1 — {category}[/bold green]")
    console.print(f"\n[bold yellow]{STAGE1_DISCLAIMER}[/bold yellow]")
    console.print(f"\n  [bold]Opportunity score: {score}/100[/bold]")
    console.print("\n[bold]Top products:[/bold]")
    for idx, item in enumerate(_top_products(items, 3), 1):
        price = _format_price(item)
        console.print(f"  {idx}. {(item.get('name') or '')[:60]} — {price}")
    suggestions = _suggest_drill_downs(items, limit=3)
    if suggestions:
        console.print("\n[bold]Try drilling down:[/bold]")
        for name in suggestions:
            console.print(f"  • {name}")
    _print_usage_footer()


def _print_stage1_overview(category, items, trends, score, advanced=False):
    if not advanced:
        _print_stage1_simple(category, items, score)
        return

    console.print(f"\n[bold green]Stage 1 — Category Overview[/bold green] [dim](free, 0 credits)[/dim]")
    console.print(f"\n[bold yellow]{STAGE1_DISCLAIMER}[/bold yellow]")
    console.print(f"\n  Category: [bold]{category}[/bold]")
    console.print(f"  Total listings: {len(items)}")
    console.print(f"  Google Trends: {'found' if trends else 'not available'}")
    console.print(f"  [bold]Opportunity score: {score}/100[/bold]")
    _print_source_summary(items)
    suggestions = _suggest_drill_downs(items)
    if suggestions:
        console.print("\n[bold]Suggested drill-down subcategories:[/bold]")
        for idx, name in enumerate(suggestions, 1):
            console.print(f"  {idx}. {name}")
        console.print(
            f"\n  Run Stage 2: [bold]python run.py \"{category}\" --drill-down \"<subcategory>\"[/bold]"
        )
    _print_usage_footer()


def _print_credit_summary():
    session_credits = get_session_credit_total()
    console.print(
        f"\n[bold]ScrapingBee session credits:[/bold] {session_credits} "
        f"(logged to {CREDIT_LOG_FILE})"
    )


def _run_stage1(category, args):
    _print_search_tip()
    if not can_stage1_search(1):
        console.print(f"[bold red]{STAGE1_UPGRADE_MESSAGE}[/bold red]")
        return

    console.print(
        f"\n[bold green]SourceIQ Stage 1:[/bold green] Category overview for [bold]{category}[/bold]"
    )
    config.DEBUG_GUM = bool(args.debug_gum)
    config.DEBUG_APPSUMO = bool(args.debug_appsumo)

    if args.advanced:
        console.print("[bold]Free sources[/bold] (eBay, Bing Shopping, Gumroad, AppSumo)")
    items = _run_scraper_batch(STAGE1_SCRAPERS, category, quiet=not args.advanced)

    if args.advanced:
        console.print("\nFetching Google Trends...")
    trends = fetch_trends(category)
    if args.advanced:
        console.print("  Trends: " + ("found" if trends else "not available"))
        console.print("Applying recency and timestamp rules...")

    items = enforce_recency_and_timestamps(items)
    score = compute_market_opportunity(items, trends)
    increment_stage1_count(1)
    _print_stage1_overview(category, items, trends, score, advanced=args.advanced)

    if args.scrape_only:
        return

    out_path = generate_report(
        category, items, trends, out_dir=args.output_dir, open_after=not args.no_open
    )
    console.print(f"\nReport saved: {out_path}")


def _run_quick_start(args):
    _print_search_tip()
    needed = len(QUICK_START_NICHES)
    if not can_stage1_search(needed):
        remaining = get_remaining_searches()["stage1_remaining"]
        console.print(
            f"[bold red]Quick Start needs {needed} Stage 1 searches "
            f"({remaining} remaining). {STAGE1_UPGRADE_MESSAGE}[/bold red]"
        )
        return

    console.print(
        f"\n[bold green]SourceIQ Quick Start[/bold green] — "
        f"Stage 1 on {needed} niches (parallel)"
    )
    console.print(f"\n[bold yellow]{STAGE1_DISCLAIMER}[/bold yellow]")
    config.DEBUG_GUM = bool(args.debug_gum)
    config.DEBUG_APPSUMO = bool(args.debug_appsumo)

    results = []
    with ThreadPoolExecutor(max_workers=needed) as executor:
        futures = {
            executor.submit(_collect_stage1, niche): niche
            for niche in QUICK_START_NICHES
        }
        for future in as_completed(futures):
            niche = futures[future]
            try:
                results.append(future.result())
                console.print(f"  [green]✓[/green] {niche}")
            except Exception as exc:
                console.print(f"  [red]✗[/red] {niche}: {exc}")
                results.append({
                    "category": niche,
                    "items": [],
                    "trends": None,
                    "score": 0.0,
                    "listings": 0,
                })

    increment_stage1_count(needed)
    results.sort(key=lambda r: r["score"], reverse=True)

    table = Table(title="Quick Start — Ranked by Opportunity Score")
    table.add_column("Rank", style="bold", justify="right")
    table.add_column("Niche", style="cyan")
    table.add_column("Score", justify="right", style="bold green")
    table.add_column("Listings", justify="right")
    table.add_column("Trends", justify="center")

    for rank, row in enumerate(results, 1):
        table.add_row(
            str(rank),
            row["category"],
            f"{row['score']:.1f}/100",
            str(row["listings"]),
            "✓" if row["trends"] else "—",
        )

    console.print()
    console.print(table)

    if not args.advanced:
        console.print("\n[bold]Top 3 niches:[/bold]")
        for row in results[:3]:
            top = _top_products(row["items"], 1)
            top_name = top[0].get("name", "")[:50] if top else "—"
            console.print(f"  {row['category']}: {row['score']:.1f}/100 — {top_name}")
    else:
        console.print("\n[bold]Top drill-down suggestions:[/bold]")
        for row in results[:3]:
            suggestions = _suggest_drill_downs(row["items"], limit=2)
            if suggestions:
                console.print(f"  [bold]{row['category']}[/bold] ({row['score']:.1f}/100):")
                for name in suggestions:
                    console.print(f"    • {name}")

    _print_usage_footer()

    if args.scrape_only:
        return

    if args.advanced:
        console.print("\n[dim]Generating HTML reports for top niches...[/dim]")
        for row in results[:3]:
            if row["items"]:
                out_path = generate_report(
                    row["category"],
                    row["items"],
                    row["trends"],
                    out_dir=args.output_dir,
                    open_after=False,
                )
                console.print(f"  Report: {out_path}")


def _run_stage2(subcategory, category, args):
    _print_search_tip()
    allowed, limit_message = can_stage2_search()
    if not allowed:
        console.print(f"[bold red]{limit_message}[/bold red]")
        return

    increment_stage2_count(1)

    console.print(
        f"\n[bold green]SourceIQ Stage 2:[/bold green] Drill-down [bold]{subcategory}[/bold]"
    )
    if subcategory.lower() != category.lower():
        console.print(f"  Parent category: {category}")

    if args.advanced:
        console.print(
            "[bold]Paid sources[/bold] (Amazon, Walmart, AliExpress, DHgate, "
            "Alibaba, Made-in-China via ScrapingBee; CJDropshipping coming soon)"
        )

    items = _run_scraper_batch(STAGE2_SCRAPERS, subcategory, quiet=not args.advanced)

    if args.advanced:
        console.print("Applying recency and timestamp rules...")
    items = enforce_recency_and_timestamps(items)

    margin = compute_margin_analysis(items, niche=subcategory)

    if args.advanced:
        _print_source_summary(items)
        _print_margin_analysis(margin, niche=subcategory)
        _print_credit_summary()
    else:
        _print_margin_simple(margin, niche=subcategory)

    _print_usage_footer()

    if args.export_csv:
        path, err = export_stage2_csv(subcategory, items, margin)
        if path:
            console.print(f"\n[bold green]CSV exported:[/bold green] {path}")
        else:
            console.print(f"\n[bold red]{err}[/bold red]")

    if args.scrape_only:
        return

    if args.advanced:
        console.print("\nFetching Google Trends...")
        trends = fetch_trends(subcategory)
        out_path = generate_report(
            subcategory, items, trends, out_dir=args.output_dir, open_after=not args.no_open
        )
        console.print(f"\nReport saved: {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="SourceIQ — two-stage market intelligence",
        epilog=(
            "Stage 1 (default): free category overview.\n"
            "Quick Start (--quick-start): scan 12 preset niches, ranked by score.\n"
            f"Stage 2 (--drill-down): deep margin analysis — ~{STAGE2_CREDITS_PER_DRILLDOWN} "
            "ScrapingBee credits per drill-down.\n"
            "Use --advanced for full source data, matched pairs, and credit usage."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "category",
        nargs="?",
        default="AI automation tools",
        help="Broad category to explore (Stage 1) or parent category (Stage 2)",
    )
    parser.add_argument(
        "--quick-start",
        action="store_true",
        help="Run Stage 1 on 12 preset niches in parallel, ranked by opportunity score",
    )
    parser.add_argument(
        "--drill-down",
        metavar="SUBCATEGORY",
        nargs="?",
        const="__same__",
        help="Stage 2: scrape paid sources for margin analysis (~175 ScrapingBee credits)",
    )
    parser.add_argument(
        "--advanced",
        action="store_true",
        help="Show full data: all sources, matched pairs, bulk/MOQ/ratings, credit usage",
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Export Stage 2 results to CSV in output/exports/ (Pro tier only)",
    )
    parser.add_argument("--output-dir", "-o", default=REPORTS_DIR, help="Directory for HTML reports")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--debug-gum", action="store_true", help="Save rendered Gumroad HTML for debugging")
    parser.add_argument("--debug-appsumo", action="store_true", help="Save rendered AppSumo HTML for debugging")
    parser.add_argument("--scrape-only", action="store_true", help="Print results only, skip HTML report")
    args = parser.parse_args()

    if args.quick_start:
        _run_quick_start(args)
        return

    category = args.category
    if args.drill_down is not None:
        subcategory = category if args.drill_down == "__same__" else args.drill_down
        _run_stage2(subcategory, category, args)
    else:
        _run_stage1(category, args)


if __name__ == "__main__":
    main()
