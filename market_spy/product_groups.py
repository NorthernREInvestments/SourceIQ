"""Product type grouping for search results — material + style + size tier."""



import re

from collections import Counter



from market_spy.analysis import _extract_keywords, _keyword_overlap

from market_spy.scrapers.base import SELLING_SOURCES, SOURCING_SOURCES

from market_spy.web.messages import DATA_UNAVAILABLE



MATERIALS = frozenset({

    "nylon", "leather", "cotton", "plastic", "metal", "silicone", "rubber", "polyester",

    "ceramic", "wood", "stainless", "steel", "aluminum", "aluminium", "glass", "bamboo",

    "canvas", "denim", "fleece", "velvet", "suede", "vinyl", "acrylic", "foam", "mesh",

    "neoprene", "titanium", "brass", "copper", "iron", "resin", "latex", "wool",

})



STYLES = frozenset({

    "basic", "premium", "adjustable", "retractable", "portable", "heavy", "duty",

    "folding", "collapsible", "waterproof", "wireless", "rechargeable", "automatic",

    "manual", "professional", "commercial", "industrial", "compact", "mini", "deluxe",

    "classic", "modern", "vintage", "sport", "athletic", "ergonomic", "insulated",

})



SIZE_TIERS = frozenset({

    "xs", "sm", "md", "lg", "xl", "xxl", "xxxl", "small", "medium", "large", "mini",

    "jumbo", "oversized", "petite", "plus", "size", "inch", "inches", "cm", "mm",

})



NAME_STOP = frozenset({

    "new", "free", "shipping", "sale", "hot", "best", "premium", "sponsored", "pack",

    "set", "pcs", "lot", "wholesale", "bulk", "usa", "us", "the", "for", "with",

})



MARGIN_HIGH = 50.0

MARGIN_MEDIUM = 25.0





def _is_selling(item: dict) -> bool:

    if item.get("side") == "selling":

        return True

    if item.get("side") == "sourcing":

        return False

    return item.get("source") in SELLING_SOURCES





def _is_sourcing(item: dict) -> bool:

    if item.get("side") == "sourcing":

        return True

    if item.get("side") == "selling":

        return False

    return item.get("source") in SOURCING_SOURCES or item.get("fallback")





def _group_signature(name: str) -> tuple[str, str, str]:

    words = _extract_keywords(name or "")

    material = next((w for w in words if w in MATERIALS), "standard")

    style = next((w for w in words if w in STYLES), "standard")

    size = next((w for w in words if w in SIZE_TIERS), "standard")

    return material, style, size





def _display_type_name(items: list[dict], search_term: str) -> str:

    counter: Counter[str] = Counter()

    niche_kw = _extract_keywords(search_term)

    for item in items:

        for kw in _extract_keywords(item.get("name", "")):

            if kw in niche_kw or kw in NAME_STOP or len(kw) < 3:

                continue

            weight = 3 if kw in MATERIALS or kw in STYLES else 1

            if kw in SIZE_TIERS:

                weight = 1

            counter[kw] += weight

    if not counter:

        words = [w for w in re.findall(r"[a-z]+", (search_term or "").lower()) if len(w) > 2]

        label = " ".join(w.capitalize() for w in words[:4]) or "Product Type"

        return label[:48]

    top = [w for w, _ in counter.most_common(6) if w not in SIZE_TIERS][:4]

    if len(top) < 2:

        top = [w for w, _ in counter.most_common(5)]

    label = " ".join(w.capitalize() for w in top[:5])

    return label[:48]





def _format_usd(value: float | None) -> str:

    if value is None:

        return "—"

    return f"${value:,.2f}".rstrip("0").rstrip(".")





def _format_range(low: float | None, high: float | None) -> str:

    if low is None and high is None:

        return DATA_UNAVAILABLE

    if low is None:

        low = high

    if high is None:

        high = low

    if abs(low - high) < 0.01:

        return _format_usd(low)

    return f"{_format_usd(low)} — {_format_usd(high)}"





def _scraped_unit_price(item: dict) -> float | None:

    raw = item.get("unit_price")

    if raw is None:

        raw = item.get("price")

    if raw is None:

        return None

    try:

        return float(raw)

    except (TypeError, ValueError):

        return None





def _margin_pct(sell: float, source: float) -> float | None:

    if sell is None or source is None or sell <= 0:

        return None

    return round((sell - source) / sell * 100, 1)





def _margin_tier(mid: float | None) -> str:

    if mid is None:

        return "LOW"

    if mid >= MARGIN_HIGH:

        return "HIGH"

    if mid >= MARGIN_MEDIUM:

        return "MEDIUM"

    return "LOW"





def _trend_direction(windows: dict | None) -> str:

    if not windows:

        return "stable"

    d30 = (windows.get("30d") or {}).get("direction")

    return d30 or "stable"





def product_group_insight(margin_tier: str, trend_30d: str) -> str:

    tier = (margin_tier or "LOW").upper()

    trend = trend_30d or "stable"

    rising = trend == "rising"

    falling = trend == "falling"

    if tier == "HIGH" and rising:

        return "Strong margin with growing demand — good candidate for testing"

    if tier == "HIGH" and falling:

        return "Strong margin but declining demand — research before committing"

    if tier == "HIGH":

        return "Strong margin with steady demand — reliable opportunity"

    if tier == "MEDIUM" and rising:

        return "Decent margin with growing demand — worth a closer look"

    if tier == "MEDIUM" and falling:

        return "Margin may tighten as demand drops — proceed carefully"

    if tier == "MEDIUM":

        return "Decent margin with steady demand — check competition levels"

    if tier == "LOW" and rising:

        return "Thin margin but rising fast — sourcing costs could improve at scale"

    return "Tight margin — only viable with excellent sourcing deal"





def _supplier_from_item(item: dict) -> dict | None:

    unit = _scraped_unit_price(item)

    if unit is None:

        return None

    shipping = None

    landed = None

    if item.get("shipping_scraped"):

        try:

            shipping = float(item.get("shipping_usa"))

            landed = round(unit + shipping, 2)

        except (TypeError, ValueError):

            shipping = None

    return {

        "platform": item.get("source", "Supplier"),

        "unit_price": unit,

        "unit_price_display": _format_usd(unit),

        "bulk_price": item.get("bulk_price"),

        "bulk_price_display": _format_usd(item.get("bulk_price")),

        "moq": int(item.get("moq") or 1),

        "shipping_usa": shipping,

        "shipping_display": _format_usd(shipping) if shipping is not None else DATA_UNAVAILABLE,

        "landed_cost": landed,

        "landed_display": _format_usd(landed) if landed is not None else DATA_UNAVAILABLE,

        "url": item.get("url", ""),

    }





def _match_sourcing(selling_items: list[dict], sourcing_items: list[dict], limit: int = 3) -> list[dict]:

    if not sourcing_items or not selling_items:

        return []

    sell_kw = set()

    sell_prices = []

    for item in selling_items:

        sell_kw |= _extract_keywords(item.get("name", ""))

        if item.get("price") is not None:

            try:

                sell_prices.append(float(item["price"]))

            except (TypeError, ValueError):

                pass

    sell_floor = min(sell_prices) if sell_prices else None

    scored = []

    for src in sourcing_items:

        unit = _scraped_unit_price(src)

        if unit is None:

            continue

        if sell_floor is not None and sell_floor >= 20 and unit < sell_floor * 0.10:

            continue

        src_kw = _extract_keywords(src.get("name", ""))

        score = _keyword_overlap(src_kw, sell_kw)

        if score <= 0.08:

            continue

        scored.append((score, unit, src))

    scored.sort(key=lambda row: (-row[0], row[1]))

    suppliers = []

    for _score, _unit, item in scored[:limit]:

        row = _supplier_from_item(item)

        if row:

            suppliers.append(row)

    return suppliers





def _serialize_group(

    group_items: list[dict],

    sourcing_pool: list[dict],

    search_term: str,

    *,

    min_products: int = 2,

) -> dict | None:

    selling = [i for i in group_items if _is_selling(i)]

    if not selling:

        return None

    if len(selling) < min_products and min_products > 1:

        pass



    sell_prices = [float(i["price"]) for i in selling if i.get("price") is not None]

    if not sell_prices:

        return None



    suppliers = _match_sourcing(selling, sourcing_pool, limit=10)

    top_suppliers = suppliers[:3]

    source_unit_prices = [s["unit_price"] for s in suppliers if s.get("unit_price") is not None]

    src_min = min(source_unit_prices) if source_unit_prices else None

    src_max = max(source_unit_prices) if source_unit_prices else None

    sell_min, sell_max = min(sell_prices), max(sell_prices)



    margins = []

    for sp in sell_prices:

        for sc in source_unit_prices:

            m = _margin_pct(sp, sc)

            if m is not None:

                margins.append(m)



    margin_min = min(margins) if margins else None

    margin_max = max(margins) if margins else None

    margin_mid = (margin_min + margin_max) / 2 if margins else None

    if margin_mid is not None and margin_mid > 85:

        margin_min = margin_max = margin_mid = None

    has_margin_data = margin_min is not None

    margin_tier = _margin_tier(margin_mid) if has_margin_data else ""



    best_source = None

    if suppliers:

        best = suppliers[0]

        best_source = {

            "platform": best["platform"],

            "unit_price": best["unit_price"],

            "unit_price_display": best["unit_price_display"],

        }



    name = _display_type_name(selling, search_term)

    sell_sources = sorted({i.get("source") for i in selling if i.get("source")})

    sell_source_label = f"({', '.join(sell_sources)})" if sell_sources else ""

    source_platforms_label = ""

    if suppliers:

        best_platform = suppliers[0]["platform"]

        seen_platforms: set[str] = set()

        labels = []

        for supplier in suppliers:

            platform = supplier.get("platform")

            if not platform or platform in seen_platforms:

                continue

            seen_platforms.add(platform)

            if platform == best_platform:

                labels.append(f"{platform} · BEST")

            else:

                labels.append(platform)

        source_platforms_label = f"({', '.join(labels)})" if labels else ""



    if margin_min is not None and margin_max is not None and margin_min != margin_max:

        margin_display = f"{margin_min:.0f}% — {margin_max:.0f}%"

    elif margin_min is not None:

        margin_display = f"{margin_min:.0f}%"

    else:

        margin_display = DATA_UNAVAILABLE



    return {

        "name": name,

        "product_count": len(selling),

        "sell_min": sell_min,

        "sell_max": sell_max,

        "sell_range_display": _format_range(sell_min, sell_max),

        "sell_source_label": sell_source_label,

        "source_min": src_min,

        "source_max": src_max,

        "has_source_data": src_min is not None,

        "source_range_display": _format_range(src_min, src_max) if src_min is not None else DATA_UNAVAILABLE,

        "source_platforms_label": source_platforms_label,

        "best_source": best_source,

        "margin_min": margin_min,

        "margin_max": margin_max,

        "has_margin_data": has_margin_data,

        "margin_range_display": margin_display,

        "margin_mid": margin_mid,

        "margin_tier": margin_tier,

        "margin_tier_lower": margin_tier.lower() if margin_tier else "none",

        "suppliers": top_suppliers,

        "watchlist_term": name,

        "trends_term": name,

        "category_filter": search_term,

        "price_mid": (sell_min + sell_max) / 2,

    }





def build_product_groups(

    items: list[dict],

    search_term: str,

    *,

    min_group_size: int = 2,

) -> list[dict]:

    """Group scraped items into product TYPE cards."""

    selling_items = [i for i in items if _is_selling(i) and i.get("price") is not None]

    sourcing_items = [i for i in items if _is_sourcing(i)]



    buckets: dict[tuple[str, str, str], list[dict]] = {}

    for item in selling_items:

        sig = _group_signature(item.get("name", ""))

        buckets.setdefault(sig, []).append(item)



    groups: list[dict] = []

    used_ids: set[int] = set()



    for _sig, bucket_items in buckets.items():

        if len(bucket_items) >= min_group_size:

            row = _serialize_group(bucket_items, sourcing_items, search_term, min_products=min_group_size)

            if row:

                groups.append(row)

                used_ids.update(id(i) for i in bucket_items)



    for item in selling_items:

        if id(item) in used_ids:

            continue

        row = _serialize_group([item], sourcing_items, search_term, min_products=1)

        if row:

            groups.append(row)



    groups.sort(

        key=lambda g: (-(g.get("margin_mid") or -1), -(g.get("product_count") or 0)),

    )

    return groups

