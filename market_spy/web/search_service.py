"""Search orchestration for the SourceIQ web API."""

import re

from market_spy.analysis import (
    TIER_LABELS,
    compute_margin_analysis,
    compute_market_opportunity,
    enforce_recency_and_timestamps,
)
from market_spy.cli import QUICK_START_NICHES, STAGE1_SCRAPERS, STAGE2_COMING_SOON, STAGE2_SCRAPERS
from market_spy.trends import fetch_trends

_TITLE_JUNK = re.compile(
    r"\b(hot sale|new arrival|wholesale|free shipping|high quality|best seller|"
    r"top rated|on sale|clearance|\d+\s*colors?|pcs|pack of|set of|with|for|the|and)\b",
    re.I,
)
_STOPWORDS = {
    "new", "hot", "sale", "best", "top", "free", "shipping", "high", "quality",
    "women", "men", "kids", "adult", "size", "color", "colors", "piece", "pieces",
    "set", "pack", "pcs", "item", "items", "product", "products", "style", "fashion",
    "premium", "professional", "portable", "adjustable", "durable", "multi",
}


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


def _trends_plain_english(direction: str, trends_found: bool) -> str:
    if not trends_found:
        return "Google Trends data is unavailable for this niche right now."
    if direction == "rising":
        return (
            "Search interest is rising — more people are searching for this. "
            "Good time to enter this market."
        )
    if direction == "falling":
        return (
            "Search interest is declining — fewer people are searching for this right now. "
            "Consider a subcategory instead."
        )
    return (
        "Search interest is steady — demand looks stable with no strong up or down swing."
    )


def _extract_subcategory_phrase(title: str) -> str | None:
    cleaned = _TITLE_JUNK.sub(" ", title)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    tokens = [
        t for t in cleaned.split()
        if len(t) > 2 and t.lower() not in _STOPWORDS and not t.isdigit()
    ]
    if len(tokens) < 2:
        return None
    # Product type is usually in the last meaningful words (e.g. "Yoga Pants")
    phrase_tokens = tokens[-3:] if len(tokens) >= 3 else tokens[-2:]
    phrase = " ".join(phrase_tokens).strip().title()
    if len(phrase) < 4 or len(phrase) > 40:
        return None
    return phrase


def _suggest_drill_downs(items, parent_category: str = "", limit: int = 5):
    """Build clean subcategory names from product title keywords."""
    category_tokens = {t.lower() for t in parent_category.split() if len(t) > 2}
    scores: dict[str, int] = {}
    labels: dict[str, str] = {}

    for item in items:
        name = (item.get("name") or "").strip()
        phrase = _extract_subcategory_phrase(name)
        if not phrase:
            continue
        key = phrase.lower()
        score = 1
        for word in key.split():
            if word in category_tokens:
                score += 3
        scores[key] = scores.get(key, 0) + score
        labels.setdefault(key, phrase)

    if parent_category and len(scores) < limit:
        base = parent_category.strip().title()
        key = base.lower()
        scores.setdefault(key, 5)
        labels.setdefault(key, base)

    ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    return [labels[key] for key, _ in ranked[:limit]]


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
    rounded_score = round(float(score), 1)

    return {
        "category": category,
        "score": rounded_score,
        "score_insight": _score_insight(rounded_score),
        "total_listings": len(items),
        "trends_found": bool(trends),
        "trends_direction": direction,
        "trends_change": trend_change,
        "trends_plain": _trends_plain_english(direction, bool(trends)),
        "trends_series": [
            {"date": d, "value": int(v)} for d, v in (trends or [])
        ],
        "sources": sources,
        "source_details": source_details,
        "drill_down_suggestions": _suggest_drill_downs(items, parent_category=category),
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
