"""Search orchestration for the SourceIQ web API."""

from market_spy.analysis import (
    TIER_LABELS,
    compute_margin_analysis,
    compute_market_opportunity,
    enforce_recency_and_timestamps,
)
from market_spy.cli import QUICK_START_NICHES, STAGE1_SCRAPERS, STAGE2_COMING_SOON, STAGE2_SCRAPERS
from market_spy.trends import fetch_trends


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
    if not trends or len(trends) < 2:
        return "stable", 0.0
    values = [float(v) for _, v in trends]
    change = round(values[-1] - values[0], 1)
    if change > 2:
        return "rising", change
    if change < -2:
        return "falling", change
    return "stable", change


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


def run_stage1_search(category: str) -> dict:
    items = _run_scrapers(STAGE1_SCRAPERS, category)
    trends = fetch_trends(category)
    items = enforce_recency_and_timestamps(items)
    score = compute_market_opportunity(items, trends)
    direction, trend_change = _trends_direction(trends)

    sources = {}
    source_details = []
    for item in items:
        src = item.get("source", "Unknown")
        sources[src] = sources.get(src, 0) + 1

    for src, count in sorted(sources.items()):
        sample = next((i for i in items if i.get("source") == src), None)
        source_details.append({
            "source": src,
            "count": count,
            "sample_price": _format_price(sample) if sample else "—",
        })

    top_products = sorted(items, key=lambda x: x.get("engagement", 0), reverse=True)[:8]

    return {
        "category": category,
        "score": round(float(score), 1),
        "total_listings": len(items),
        "trends_found": bool(trends),
        "trends_direction": direction,
        "trends_change": trend_change,
        "sources": sources,
        "source_details": source_details,
        "drill_down_suggestions": _suggest_drill_downs(items),
        "top_products": [
            {
                "name": (p.get("name") or "")[:80],
                "source": p.get("source", ""),
                "price": p.get("price"),
                "price_display": _format_price(p),
                "url": p.get("url", ""),
                "engagement": p.get("engagement", 0),
            }
            for p in top_products
        ],
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
