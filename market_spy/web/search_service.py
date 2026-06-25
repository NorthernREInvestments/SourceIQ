"""Search orchestration for the SourceIQ web API."""

from market_spy.analysis import (
    TIER_LABELS,
    _OPPORTUNITY_RANK,
    _subcategory_opportunity_label,
    compute_margin_analysis,
    compute_market_opportunity,
    enforce_recency_and_timestamps,
    group_into_subcategories,
)
from market_spy.cli import QUICK_START_NICHES, STAGE1_SCRAPERS, STAGE2_COMING_SOON, STAGE2_SCRAPERS
from market_spy.trends import (
    fetch_trends,
    fetch_trends_windows,
    format_trend_window,
    interpret_trend_windows,
    trends_direction,
)


def _score_insight(score: float) -> dict:
    score = round(float(score), 1)
    if score <= 30:
        return {
            "band": "poor",
            "label": "Poor",
            "css_class": "score-poor",
            "summary": f"{score} out of 100 — This is a weak opportunity.",
            "explanation": (
                "Demand looks limited or competition is heavy. "
                "Try a different niche or a more specific subcategory."
            ),
        }
    if score <= 50:
        return {
            "band": "moderate",
            "label": "Moderate",
            "css_class": "score-moderate",
            "summary": f"{score} out of 100 — This is a moderate opportunity.",
            "explanation": (
                "The market exists but competition is present. "
                "Worth investigating specific subcategories."
            ),
        }
    if score <= 70:
        return {
            "band": "good",
            "label": "Good",
            "css_class": "score-good",
            "summary": f"{score} out of 100 — This is a good opportunity.",
            "explanation": (
                "Solid demand signals with room to compete. "
                "Drill down on subcategories to find the best margins."
            ),
        }
    return {
        "band": "excellent",
        "label": "Excellent",
        "css_class": "score-excellent",
        "summary": f"{score} out of 100 — This is an excellent opportunity.",
        "explanation": (
            "Strong demand with promising signals. "
            "Prioritize margin analysis on your top subcategory picks."
        ),
    }


def _trends_plain_english(windows: dict, labels: list[str]) -> str:
    if not any(w.get("found") for w in windows.values()):
        return "Google Trends data is unavailable for this niche right now."
    return (
        f"Search interest across timeframes: {', '.join(labels)}. "
        "Compare windows to spot short-term spikes versus sustained demand."
    )


def _build_trends_payload(windows: dict) -> dict:
    labels = [format_trend_window(key, windows[key]) for key in ("24h", "7d", "30d")]
    found = any(w.get("found") for w in windows.values())
    primary = windows.get("30d", {})
    direction = primary.get("direction", "stable")
    change = primary.get("change", 0)
    interpretation = interpret_trend_windows(windows)
    return {
        "trends_windows": windows,
        "trends_window_labels": labels,
        "trends_windows_line": ", ".join(labels),
        "trends_interpretation": interpretation,
        "trends_found": found,
        "trends_direction": direction,
        "trends_change": change,
        "trends_plain": _trends_plain_english(windows, labels),
    }


def _subcategory_insight_line(sub: dict) -> str:
    """One-line actionable insight for a subcategory card."""
    avg = sub.get("avg_price_display", "—")
    opp = sub.get("opportunity_label", "LOW")
    interpretation = sub.get("trends_interpretation", "")
    windows = sub.get("trends_windows") or {}
    d30 = windows.get("30d", {}).get("direction")
    d24 = windows.get("24h", {}).get("direction")

    if "Short-term spike" in interpretation:
        return "Short-term spike only — wait for sustained trend before investing."

    if opp == "HIGH" and d30 == "rising":
        return f"Strong 30-day trend and {avg} average price — good candidate for margin check."

    if opp == "HIGH":
        return f"Solid price point at {avg} — run a margin check to confirm profit potential."

    if opp == "MEDIUM" and d30 == "rising":
        return f"Moderate opportunity at {avg} with rising demand — worth a margin check."

    if d30 == "falling" or (d24 == "rising" and d30 == "falling"):
        return f"Weaker long-term demand at {avg} — compare other subcategories first."

    if opp == "LOW":
        return f"Lower opportunity at {avg} — only pursue if margins look exceptional."

    return f"Avg price {avg} — check profit margins before ordering inventory."


def _run_scrapers(scrapers, niche):
    items = []
    for label, func, kwargs in scrapers:
        if label in STAGE2_COMING_SOON:
            continue
        try:
            items.extend(func(niche, **kwargs))
        except Exception:
            continue
    return items


def _trends_direction(trends):
    return trends_direction(trends)


_STAGE1_PRODUCT_SKIP = frozenset({"AppSumo", "Gumroad"})


def _serialize_products(items, category: str, limit: int | None = None) -> list[dict]:
    filtered = [
        item for item in items
        if item.get("source") not in _STAGE1_PRODUCT_SKIP
    ]
    ranked = sorted(filtered, key=lambda x: x.get("engagement", 0), reverse=True)
    if limit is not None:
        ranked = ranked[:limit]
    return [
        {
            "name": (p.get("name") or "")[:120],
            "source": p.get("source", ""),
            "price": p.get("price"),
            "price_display": _format_price(p),
            "url": p.get("url", ""),
            "drill_term": ((p.get("name") or "").strip() or category)[:100],
        }
        for p in ranked
    ]


def _enrich_subcategories_with_trends(subcategories: list[dict]) -> list[dict]:
    enriched = []
    for sub in subcategories:
        windows = fetch_trends_windows(sub["drill_term"])
        trends_payload = _build_trends_payload(windows)
        trend_30d = windows.get("30d", {}).get("direction", "stable")
        label = _subcategory_opportunity_label(
            sub.get("avg_price"),
            sub.get("count", 0),
            trend_direction=trend_30d,
        )
        enriched.append({
            **sub,
            **trends_payload,
            "opportunity_label": label,
            "opportunity_rank": _OPPORTUNITY_RANK[label],
            "insight_line": "",
        })
    for row in enriched:
        row["insight_line"] = _subcategory_insight_line(row)
    enriched.sort(
        key=lambda row: (
            -row["opportunity_rank"],
            -(row["avg_price"] or 0),
            row["name"].lower(),
        )
    )
    return enriched


def _format_price(item):
    if item.get("price_label"):
        return item["price_label"]
    if item.get("price") is not None:
        return f"${item['price']:.2f}"
    return "—"


def _serialize_item(item):
    return {
        "source": item.get("source", ""),
        "name": (item.get("name") or "")[:200],
        "url": item.get("url", ""),
        "price": item.get("price"),
        "price_label": item.get("price_label", ""),
        "unit_price": item.get("unit_price"),
        "bulk_price": item.get("bulk_price"),
        "moq": item.get("moq"),
        "supplier_rating": item.get("supplier_rating") or item.get("store_rating"),
        "shipping_usa": item.get("shipping_usa"),
        "best_landed_cost": item.get("best_landed_cost"),
        "engagement": item.get("engagement", 0),
    }


def _format_match(match):
    unit = match.get("unit_price") or match.get("source_price")
    bulk = match.get("bulk_price")
    moq = match.get("moq") or 1
    pricing_parts = []
    if unit is not None:
        pricing_parts.append(f"unit ${unit:.2f}")
    if bulk is not None:
        pricing_parts.append(f"bulk ${bulk:.2f} @ MOQ {moq}")
    sale = match.get("sale_price")
    if sale is not None:
        pricing_parts.append(f"sale ${sale:.2f}")
    landed = match.get("source_landed")
    best_type = match.get("best_price_type") or "unit"
    best_landed = f"${landed:.2f} ({best_type})" if landed is not None else None
    return {
        "name": (match.get("display_name") or match.get("sourcing_name") or "Product")[:80],
        "source_platform": match.get("source_platform", ""),
        "selling_platform": match.get("selling_platform", ""),
        "selling_price": match.get("selling_price"),
        "source_price": match.get("source_price"),
        "source_landed": landed,
        "margin_percent": match.get("margin_percent"),
        "margin_label": match.get("margin_label", "LOW"),
        "pricing_text": " | ".join(pricing_parts) if pricing_parts else match.get("price_summary", "—"),
        "best_landed": best_landed,
        "moq": moq,
        "bulk_price": bulk,
        "unit_price": unit,
        "confidence": match.get("confidence"),
        "match_type": match.get("match_type", "exact"),
    }


def _avg_sold_price_summary(items) -> dict:
    """Prefer eBay completed-sale prices; fall back to active listing prices."""
    ebay_prices = [
        float(item["price"])
        for item in items
        if item.get("source") == "eBay" and item.get("price") is not None
    ]
    listing_prices = [
        float(item["price"])
        for item in items
        if item.get("source") != "eBay" and item.get("price") is not None
    ]
    if ebay_prices:
        avg = sum(ebay_prices) / len(ebay_prices)
        return {
            "avg_price": round(avg, 2),
            "avg_price_display": f"${avg:.0f}",
            "price_basis": "verified",
        }
    if listing_prices:
        avg = sum(listing_prices) / len(listing_prices)
        return {
            "avg_price": round(avg, 2),
            "avg_price_display": f"${avg:.0f}",
            "price_basis": "estimated",
        }
    return {
        "avg_price": None,
        "avg_price_display": "—",
        "price_basis": "none",
    }


_BROAD_CATEGORIES = {name.lower() for name in QUICK_START_NICHES}


def run_stage1_search(
    category: str,
    *,
    product_view: bool = False,
    summary_only: bool = False,
) -> dict:
    items = _run_scrapers(STAGE1_SCRAPERS, category)
    trends = fetch_trends(category)
    trends_windows = fetch_trends_windows(category)
    trends_payload = _build_trends_payload(trends_windows)
    items = enforce_recency_and_timestamps(items)
    score = compute_market_opportunity(items, trends)
    rounded_score = round(float(score), 1)

    if summary_only:
        price_summary = _avg_sold_price_summary(items)
        return {
            "category": category,
            "view_mode": "summary",
            "score": rounded_score,
            "total_listings": len(items),
            **trends_payload,
            **price_summary,
        }

    if not product_view and category.strip().lower() not in _BROAD_CATEGORIES:
        product_view = True

    subcategories = group_into_subcategories(items, category, limit=10)
    show_subcategories = not product_view and bool(subcategories)
    if show_subcategories:
        subcategories = _enrich_subcategories_with_trends(subcategories)
        view_mode = "subcategories"
    else:
        view_mode = "products"

    product_list = _serialize_products(items, category)

    return {
        "category": category,
        "view_mode": view_mode,
        "score": rounded_score,
        "subcategories": subcategories if show_subcategories else [],
        "products": product_list if view_mode == "products" else [],
        **trends_payload,
    }


def run_quick_start() -> list:
    results = []
    for niche in QUICK_START_NICHES:
        row = run_stage1_search(niche)
        row["category"] = niche
        results.append(row)
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def run_stage2_drilldown(subcategory: str) -> dict:
    items = _run_scrapers(STAGE2_SCRAPERS, subcategory)
    items = enforce_recency_and_timestamps(items)
    margin = compute_margin_analysis(items, niche=subcategory)

    sources = {}
    for item in items:
        src = item.get("source", "Unknown")
        sources[src] = sources.get(src, 0) + 1

    by_tier = {}
    raw_by_tier = margin.get("by_tier") or {}
    for tier_key, tier_label in TIER_LABELS.items():
        tier_data = raw_by_tier.get(tier_key) or {}
        tier_matches = [_format_match(m) for m in (tier_data.get("matches") or [])]
        by_tier[tier_key] = {
            "label": tier_label,
            "matches": tier_matches,
            "tier_margin_percent": tier_data.get("tier_margin_percent"),
            "tier_margin_label": tier_data.get("tier_margin_label"),
            "match_count": tier_data.get("match_count", 0),
        }

    return {
        "subcategory": subcategory,
        "product_family": margin.get("product_family"),
        "total_listings": len(items),
        "sources": sources,
        "by_tier": by_tier,
        "items_serializable": [_serialize_item(i) for i in items],
        "margin_raw": margin,
    }


def items_from_serializable(rows):
    """Rehydrate minimal item dicts for CSV export."""
    return [dict(r) for r in rows]
