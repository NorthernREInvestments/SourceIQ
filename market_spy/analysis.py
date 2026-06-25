"""Recency filtering, market opportunity scoring, and margin analysis."""

import re
from datetime import datetime

import pandas as pd

from market_spy.scrapers.base import (
    SOURCING_SOURCES,
    SELLING_SOURCES,
    enrich_sourcing_pricing,
    estimate_usa_shipping,
    format_sourcing_price_label,
    landed_cost,
)

# Physical marketplaces used for apples-to-apples margin matching.
MARGIN_SELLING_SOURCES = {"eBay", "Amazon", "Walmart"}

STOP_WORDS = {
    "the", "a", "an", "and", "or", "for", "with", "to", "of", "in", "on", "at",
    "by", "from", "is", "are", "was", "were", "be", "been", "new", "free",
    "shipping", "sale", "hot", "best", "premium", "sponsored", "items", "item",
    "pack", "set", "pcs", "pc", "ct", "usd", "us", "your", "our", "all", "per",
    "size", "color", "colors", "style", "adjustable", "large", "small", "medium",
}

NOISE_PREFIXES = (
    "premium sponsored items",
    "sponsored",
    "new listing",
    "new pet",
)

TIER_LABELS = {
    "budget": "Budget (source <$5, sells <$20)",
    "mid": "Mid (source $5–$15, sells $20–$60)",
    "premium": "Premium (source >$15, sells >$60)",
}

MIN_KEYWORD_OVERLAP = 0.3
CATEGORY_AVERAGE_LABEL = "CATEGORY AVERAGE — no exact match found"


def enforce_recency_and_timestamps(all_items):
    kept = []
    now = datetime.utcnow()
    for it in all_items:
        date = None
        if it.get("date"):
            date = it["date"]
        elif it.get("launch_date"):
            date = it["launch_date"]
        elif it.get("last_mentioned"):
            date = it["last_mentioned"]
        elif it.get("price_last_confirmed"):
            date = it["price_last_confirmed"]
        elif it.get("last_activity"):
            date = it["last_activity"]
        it["date"] = date
        keep = True
        if date:
            age = (now - date).days
            if it.get("source") == "Reddit" and age > 90:
                keep = False
            elif it.get("source") == "eBay" and age > 90:
                keep = False
            elif it.get("source") == "Product Hunt" and age > 365:
                keep = False
            elif it.get("source") not in ("Reddit", "Product Hunt") and age > 182:
                keep = False
        it["verified"] = bool(date)
        if it.get("price") is not None and not it.get("price_last_confirmed"):
            it["price_last_confirmed"] = it.get("date")
        if keep:
            kept.append(it)
    return kept


def compute_price_range(items):
    prices = [i["price"] for i in items if i.get("price") is not None]
    if not prices:
        return (None, None)
    return (min(prices), max(prices))


def compute_market_opportunity(items, trends):
    n = len(items)
    avg_eng = sum(i.get("engagement", 0) for i in items) / (n or 1)
    prices = [i["price"] for i in items if i.get("price") is not None]
    spread = (max(prices) - min(prices)) if prices else 0
    trend_score = 0.0
    if trends:
        try:
            df = pd.DataFrame(trends, columns=["date", "value"])
            df["date"] = pd.to_datetime(df["date"])
            if len(df) >= 3:
                coeff = pd.Series(df["value"]).astype(float).reset_index(drop=True).diff().mean()
                trend_score = coeff
        except Exception:
            trend_score = 0.0
    score = (
        (min(n, 100) / 100) * 0.35
        + min(avg_eng / 200.0, 1.0) * 0.35
        + min(spread / 500.0, 1.0) * 0.15
        + max(min((trend_score + 10) / 20.0, 1.0), 0.0) * 0.15
    ) * 100
    return round(score, 1)


MIN_SUBCATEGORY_SIZE = 3
MIN_DYNAMIC_CLUSTER_SIZE = 5
MAX_SUBCATEGORIES = 10

# Predefined subcategory buckets for common broad niches (name, keyword set).
_PREDEFINED_SUBCATEGORY_BUCKETS = {
    "pet supplies": [
        ("Dog Supplies", frozenset({
            "dog", "dogs", "puppy", "puppies", "canine", "leash", "collar", "harness",
            "doggie", "pooch", "retriever", "bulldog", "terrier",
        })),
        ("Cat Supplies", frozenset({
            "cat", "cats", "kitten", "kittens", "feline", "litter", "scratching", "scratcher",
            "litterbox", "catnip",
        })),
        ("Small Animal Supplies", frozenset({
            "hamster", "rabbit", "rabbits", "guinea", "ferret", "chinchilla", "gerbil",
            "small", "rodent", "bunny",
        })),
        ("Pet Health and Grooming", frozenset({
            "grooming", "shampoo", "flea", "tick", "health", "vitamin", "supplement",
            "nail", "brush", "dental", "medicine", "worm", "groom",
        })),
        ("Pet Beds and Furniture", frozenset({
            "bed", "beds", "furniture", "crate", "kennel", "house", "mat", "cushion",
            "petbed", "nest",
        })),
        ("Pet Toys", frozenset({
            "toy", "toys", "chew", "ball", "fetch", "plush", "squeaky", "rope", "puzzle",
        })),
        ("Pet Food and Treats", frozenset({
            "food", "treat", "treats", "snack", "kibble", "nutrition", "feed", "biscuit",
            "jerky", "meal",
        })),
        ("Fish and Aquarium", frozenset({
            "fish", "aquarium", "tank", "filter", "aquatic", "goldfish", "betta", "pond",
            "aqua", "substrate",
        })),
        ("Bird Supplies", frozenset({
            "bird", "birds", "parrot", "cage", "aviary", "cockatiel", "budgie", "feeder",
            "perch",
        })),
        ("Reptile Supplies", frozenset({
            "reptile", "snake", "lizard", "terrarium", "gecko", "turtle", "tortoise",
            "hermit", "iguana", "habitat",
        })),
    ],
    "sports": [
        ("Team Sports", frozenset({
            "team", "teams", "football", "soccer", "basketball", "baseball", "volleyball",
            "hockey", "lacrosse", "rugby", "cricket", "softball", "netball", "handball",
        })),
        ("Fitness Equipment", frozenset({
            "gym", "fitness", "dumbbell", "dumbbells", "kettlebell", "kettlebells",
            "weight", "weights", "bench", "cardio", "treadmill", "elliptical", "exercise",
            "workout", "resistance", "strength", "rowing", "rower", "equipment",
        })),
        ("Outdoor Sports", frozenset({
            "outdoor", "camping", "hiking", "climbing", "fishing", "hunting", "trail",
            "backpack", "trekking", "mountaineering", "archery",
        })),
        ("Water Sports", frozenset({
            "water", "swim", "swimming", "surf", "surfing", "kayak", "kayaking",
            "paddle", "paddling", "diving", "snorkel", "snorkeling", "boat", "boating",
            "wetsuit", "wakeboard", "inflatable",
        })),
        ("Winter Sports", frozenset({
            "winter", "ski", "skiing", "snowboard", "snowboarding", "ice", "skate",
            "skating", "sled", "sledding", "snowshoe", "snowshoeing", "snow",
        })),
        ("Yoga and Pilates", frozenset({
            "yoga", "pilates", "mat", "mats", "block", "blocks", "strap", "straps",
            "meditation", "bolster", "stretch",
        })),
        ("Cycling", frozenset({
            "cycling", "cycle", "bike", "bicycle", "bicycles", "cyclist", "pedal",
            "helmet", "spoke", "mountain",
        })),
        ("Running and Athletics", frozenset({
            "running", "runners", "runner", "jogging", "sneaker", "sneakers", "athletic",
            "athletics", "track", "sprint", "marathon", "trainers",
        })),
        ("Combat Sports", frozenset({
            "boxing", "mma", "martial", "karate", "judo", "wrestling", "combat",
            "punching", "kickboxing", "muay", "grappling",
        })),
        ("Golf", frozenset({
            "golf", "golfer", "putter", "driver", "fairway", "wedge", "tee", "caddy",
            "irons", "chip",
        })),
    ],
    "electronics": [
        ("Phone Accessories", frozenset({
            "phone", "iphone", "android", "mobile", "cellular", "smartphone", "case",
            "charger", "screen", "protector",
        })),
        ("Laptop and Computer", frozenset({
            "laptop", "computer", "pc", "macbook", "keyboard", "mouse", "monitor",
            "desktop", "notebook", "webcam",
        })),
        ("Audio and Headphones", frozenset({
            "audio", "headphone", "headphones", "earbud", "earbuds", "speaker", "speakers",
            "soundbar", "microphone", "amplifier", "bluetooth",
        })),
        ("Smart Home", frozenset({
            "smart", "home", "alexa", "google", "hub", "automation", "thermostat",
            "doorbell", "security", "camera", "sensor", "wifi",
        })),
        ("Gaming", frozenset({
            "gaming", "game", "games", "console", "playstation", "xbox", "nintendo",
            "controller", "joystick", "headset",
        })),
        ("Cameras and Photography", frozenset({
            "camera", "cameras", "photography", "lens", "lenses", "tripod", "dslr",
            "mirrorless", "flash", "gopro",
        })),
        ("TV and Home Theater", frozenset({
            "television", "projector", "theater", "theatre", "hdmi", "receiver",
            "streaming", "roku", "firestick", "antenna",
        })),
        ("Wearables", frozenset({
            "wearable", "wearables", "smartwatch", "watch", "tracker", "fitbit",
            "band", "heart", "step",
        })),
        ("Cables and Adapters", frozenset({
            "cable", "cables", "adapter", "adapters", "usb", "hdmi", "connector",
            "dongle", "converter", "cord",
        })),
        ("Batteries and Power", frozenset({
            "battery", "batteries", "power", "bank", "charger", "charging", "solar",
            "generator", "inverter", "ups",
        })),
    ],
    "home and garden": [
        ("Kitchen and Dining", frozenset({
            "kitchen", "dining", "cookware", "utensil", "utensils", "pan", "pots",
            "knife", "cutlery", "plate", "bowl", "glassware",
        })),
        ("Bedroom and Bath", frozenset({
            "bedroom", "bath", "bathroom", "bedding", "sheet", "sheets", "pillow",
            "towel", "towels", "duvet", "comforter", "shower", "curtain",
        })),
        ("Living Room Decor", frozenset({
            "living", "decor", "decoration", "furniture", "sofa", "couch", "table",
            "rug", "rugs", "curtain", "accent",
        })),
        ("Garden and Outdoor", frozenset({
            "garden", "outdoor", "patio", "lawn", "plant", "plants", "planter",
            "greenhouse", "watering", "hose", "mower", "grill",
        })),
        ("Lighting", frozenset({
            "light", "lighting", "lamp", "lamps", "led", "bulb", "bulbs", "chandelier",
            "fixture", "sconce", "string",
        })),
        ("Storage and Organization", frozenset({
            "storage", "organization", "organizer", "organizers", "shelf", "shelves",
            "bin", "bins", "basket", "container", "closet",
        })),
        ("Cleaning Supplies", frozenset({
            "cleaning", "cleaner", "cleaners", "mop", "vacuum", "broom", "detergent",
            "disinfectant", "sponge", "wipes",
        })),
        ("Tools and Hardware", frozenset({
            "tool", "tools", "hardware", "drill", "screwdriver", "hammer", "wrench",
            "pliers", "saw", "nail", "screw",
        })),
        ("Wall Art", frozenset({
            "wall", "art", "poster", "posters", "frame", "frames", "canvas", "print",
            "painting", "picture", "decor",
        })),
        ("Candles and Fragrance", frozenset({
            "candle", "candles", "fragrance", "scent", "diffuser", "incense",
            "aromatherapy", "wax", "essential", "perfume",
        })),
    ],
}

_CATEGORY_SUFFIXES = ("Equipment", "Accessories", "Supplies", "Products")

_OPPORTUNITY_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _item_price(item):
    price = item.get("price")
    if price is None:
        return None
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def _subcategory_opportunity_label(avg_price, count, trend_direction=None):
    if count <= 1:
        label = "LOW"
    elif avg_price is not None and avg_price < 15:
        label = "LOW"
    elif avg_price is not None and avg_price > 30 and count > 3:
        label = "HIGH"
    elif (avg_price is not None and 15 <= avg_price <= 30) or (2 <= count <= 3):
        label = "MEDIUM"
    elif avg_price is not None and avg_price > 30:
        label = "MEDIUM"
    else:
        label = "LOW"

    if trend_direction == "rising":
        if label == "MEDIUM":
            return "HIGH"
        if label == "LOW" and avg_price is not None and avg_price >= 15:
            return "MEDIUM"
    elif trend_direction == "falling":
        if label == "HIGH":
            return "MEDIUM"
        if label in ("HIGH", "MEDIUM"):
            return "LOW"
    return label


def has_predefined_subcategories(niche: str) -> bool:
    """True when the niche uses fixed subcategory bucket names."""
    return _has_predefined_buckets(niche)


def describe_subcategory_buckets(niche: str, items: list) -> dict:
    """Debug metadata for which bucket mapping Stage 1 subcategory grouping uses."""
    predefined = _has_predefined_buckets(niche)
    if not items:
        return {
            "niche": niche,
            "predefined": predefined,
            "mapping": "predefined" if predefined else "dynamic",
            "bucket_names": [],
            "item_count": 0,
        }
    niche_kw = _extract_keywords(niche)
    keyword_freq = {}
    for item in items:
        kws = _extract_keywords(item.get("name", "")) - niche_kw
        for kw in kws:
            keyword_freq[kw] = keyword_freq.get(kw, 0) + 1
    buckets = _select_buckets(niche, keyword_freq, niche_kw)
    return {
        "niche": niche,
        "predefined": predefined,
        "mapping": "predefined" if predefined else "dynamic",
        "bucket_names": [name for name, _ in buckets],
        "item_count": len(items),
    }


def _normalize_niche_key(niche: str) -> str:
    return (niche or "").strip().lower()


def _has_predefined_buckets(niche: str) -> bool:
    return _normalize_niche_key(niche) in _PREDEFINED_SUBCATEGORY_BUCKETS


def _bucket_overlap(item_kws, bucket_kws):
    return len(item_kws & bucket_kws)


def _select_buckets(niche, keyword_freq, niche_kw):
    key = _normalize_niche_key(niche)
    if key in _PREDEFINED_SUBCATEGORY_BUCKETS:
        return list(_PREDEFINED_SUBCATEGORY_BUCKETS[key])

    themes = [
        kw for kw, freq in sorted(keyword_freq.items(), key=lambda pair: (-pair[1], pair[0]))
        if freq >= MIN_DYNAMIC_CLUSTER_SIZE and kw not in niche_kw and len(kw) >= 3
    ][:10]
    buckets = []
    for idx, theme in enumerate(themes):
        suffix = _CATEGORY_SUFFIXES[idx % len(_CATEGORY_SUFFIXES)]
        buckets.append((f"{theme.title()} {suffix}", frozenset({theme})))
    return buckets


def _catch_all_name(niche, pooled_keywords):
    key = _normalize_niche_key(niche)
    catch_all = {
        "pet supplies": "General Pet Supplies",
        "sports": "General Sports",
        "electronics": "General Electronics",
        "home and garden": "General Home and Garden",
    }
    if key in catch_all:
        return catch_all[key]
    ranked = sorted(pooled_keywords, key=lambda kw: (-len(kw), kw))
    if ranked:
        return f"{ranked[0].title()} Accessories"
    words = [w for w in re.findall(r"[a-z]+", (niche or "").lower()) if w not in STOP_WORDS]
    if words:
        return f"{words[-1].title()} Accessories"
    return "General Accessories"


def _assign_items_to_buckets(item_keywords, buckets, niche_kw):
    assignments = {name: [] for name, _ in buckets}
    unassigned = []
    for idx, kws in enumerate(item_keywords):
        item_kws = kws - niche_kw
        best_name = None
        best_score = 0
        for name, bucket_kws in buckets:
            score = _bucket_overlap(item_kws, bucket_kws)
            if score > best_score:
                best_score = score
                best_name = name
        if best_name and best_score > 0:
            assignments[best_name].append(idx)
        else:
            unassigned.append(idx)
    return assignments, unassigned


def _redistribute_overflow(overflow_idxs, item_keywords, qualifying, bucket_kw_map, niche_kw):
    leftover = []
    for idx in overflow_idxs:
        item_kws = item_keywords[idx] - niche_kw
        best_name = None
        best_score = 0
        for name in qualifying:
            score = _bucket_overlap(item_kws, bucket_kw_map.get(name, frozenset()))
            if score > best_score:
                best_score = score
                best_name = name
        if best_name and best_score > 0:
            qualifying[best_name].append(idx)
        else:
            leftover.append(idx)
    return leftover


def group_into_subcategories(
    items,
    niche,
    limit=MAX_SUBCATEGORIES,
    min_cluster=None,
    min_display=MIN_SUBCATEGORY_SIZE,
):
    """Cluster Stage 1 listings into broad subcategories one level below the niche."""
    if not items:
        return []

    predefined = _has_predefined_buckets(niche)
    if min_cluster is None:
        min_cluster = 2 if predefined else MIN_DYNAMIC_CLUSTER_SIZE

    niche_kw = _extract_keywords(niche)
    keyword_freq = {}
    item_keywords = []
    for item in items:
        kws = _extract_keywords(item.get("name", "")) - niche_kw
        item_keywords.append(kws)
        for kw in kws:
            keyword_freq[kw] = keyword_freq.get(kw, 0) + 1

    buckets = _select_buckets(niche, keyword_freq, niche_kw)
    if not buckets:
        return []

    bucket_kw_map = {name: kws for name, kws in buckets}
    assignments, unassigned = _assign_items_to_buckets(item_keywords, buckets, niche_kw)

    qualifying = {}
    overflow = list(unassigned)
    for name, _ in buckets:
        idxs = assignments.get(name, [])
        if len(idxs) >= min_cluster:
            qualifying[name] = list(idxs)
        else:
            overflow.extend(idxs)

    overflow = _redistribute_overflow(
        overflow, item_keywords, qualifying, bucket_kw_map, niche_kw
    )

    overflow_min = min_cluster if not predefined else min_display
    if len(overflow) >= overflow_min:
        pooled_kws = set()
        for idx in overflow:
            pooled_kws |= item_keywords[idx]
        catch_all = _catch_all_name(niche, pooled_kws - niche_kw)
        qualifying[catch_all] = list(overflow)
    elif overflow and qualifying:
        largest = max(qualifying, key=lambda name: len(qualifying[name]))
        qualifying[largest].extend(overflow)

    subcategories = []
    for name, idxs in qualifying.items():
        unique_idxs = list(dict.fromkeys(idxs))
        if len(unique_idxs) < min_display:
            continue
        cluster_items = [items[i] for i in unique_idxs]
        prices = [p for p in (_item_price(i) for i in cluster_items) if p is not None]
        count = len(cluster_items)
        avg_price = round(sum(prices) / len(prices), 2) if prices else None
        label = _subcategory_opportunity_label(avg_price, count)
        avg_display = f"${avg_price:.0f}" if avg_price is not None else "—"
        subcategories.append({
            "name": name,
            "count": count,
            "avg_price": avg_price,
            "avg_price_display": avg_display,
            "opportunity_label": label,
            "opportunity_rank": _OPPORTUNITY_RANK[label],
            "drill_term": name,
        })

    subcategories.sort(
        key=lambda row: (
            -row["opportunity_rank"],
            -row["count"],
            -(row["avg_price"] or 0),
            row["name"].lower(),
        )
    )
    return subcategories[:limit]


def _is_selling_item(item):
    if item.get("side") == "selling":
        return True
    if item.get("side") == "sourcing":
        return False
    return item.get("source") in SELLING_SOURCES


def _is_sourcing_item(item):
    if item.get("side") == "sourcing":
        return True
    if item.get("side") == "selling":
        return False
    return item.get("source") in SOURCING_SOURCES


def _sourcing_tier(unit_price):
    """Assign sourcing unit price to a margin tier."""
    if unit_price is None:
        return None
    if unit_price < 5:
        return "budget"
    if unit_price <= 15:
        return "mid"
    return "premium"


def _selling_tier(sell_price):
    """Assign selling price to a margin tier (ranges differ from sourcing)."""
    if sell_price is None:
        return None
    if sell_price < 20:
        return "budget"
    if sell_price <= 60:
        return "mid"
    return "premium"


def _extract_keywords(title):
    if not title:
        return set()
    text = title.lower().strip()
    for prefix in NOISE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    words = re.findall(r"[a-z0-9]+", text)
    keywords = set()
    for word in words:
        if word in STOP_WORDS:
            continue
        if len(word) < 3 and word not in ("xs", "sm", "md", "lg", "xl"):
            continue
        keywords.add(word)
    return keywords


def _keyword_overlap(source_kw, selling_kw):
    """Overlap coefficient: shared keywords / smaller keyword set."""
    if not source_kw or not selling_kw:
        return 0.0
    common = source_kw & selling_kw
    if not common:
        return 0.0
    return len(common) / min(len(source_kw), len(selling_kw))


def _display_name(source_kw, selling_kw, fallback_title):
    shared = sorted(source_kw & selling_kw)
    if shared:
        label = " ".join(w.capitalize() for w in shared[:6])
        return label
    title = (fallback_title or "").strip()
    for prefix in NOISE_PREFIXES:
        if title.lower().startswith(prefix):
            title = title[len(prefix):].strip()
    return title[:50] or "Product"


def _normalize_title(title):
    text = (title or "").lower().strip()
    for prefix in NOISE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return re.sub(r"\s+", " ", text)[:100]


def _sourcing_key(item):
    title = _normalize_title(item.get("name"))
    if title:
        return f"{item.get('source')}:{title}:{item.get('price')}"
    url = item.get("url") or ""
    match = re.search(r"/item/(\d+)\.html|/product/[^/]+/(\d+)", url)
    if match:
        return match.group(1) or match.group(2)
    return f"{item.get('source')}:{item.get('price')}"


def _dedupe_items(items, key_fn):
    seen = set()
    unique = []
    for item in items:
        key = key_fn(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _margin_label(pct):
    if pct > 50:
        return "HIGH"
    if pct >= 20:
        return "MEDIUM"
    return "LOW"


def _family_keywords(niche):
    """Core product-family terms from the search niche."""
    if not niche:
        return set()
    return {w for w in re.findall(r"[a-z0-9]+", niche.lower()) if len(w) > 2}


def _in_product_family(title, family_kw):
    """True when the listing belongs to the searched product family."""
    if not family_kw:
        return True
    lower = (title or "").lower()
    return all(word in lower for word in family_kw)


def _family_overlap_score(title_kw, family_kw):
    """How much of the product family is represented in title keywords."""
    if not family_kw:
        return 1.0
    if not title_kw:
        return 0.0
    return len(family_kw & title_kw) / len(family_kw)


def _combined_match_score(source_kw, selling_kw, family_kw):
    """
    Score keyword similarity within the product family.
    Family terms always count as matched for sourcing results from the niche search.
    """
    if not selling_kw:
        return 0.0
    family_in_sell = family_kw & selling_kw if family_kw else set()
    other_src = source_kw - family_kw if family_kw else source_kw
    other_sell = selling_kw - family_kw if family_kw else selling_kw
    if not other_src:
        return 1.0 if family_in_sell else _family_overlap_score(selling_kw, family_kw)
    if not other_sell:
        return len(family_in_sell) / len(family_kw) if family_kw else 0.0
    common_other = other_src & other_sell
    if not common_other and not family_in_sell:
        return 0.0
    overlap_other = len(common_other) / min(len(other_src), len(other_sell)) if other_src and other_sell else 0.0
    family_score = len(family_in_sell) / len(family_kw) if family_kw else 1.0
    return max(overlap_other, family_score * 0.9)


def _tier_category_average(family_selling):
    prices = [s["price"] for s in family_selling if s.get("price") is not None]
    if not prices:
        return None
    return round(sum(prices) / len(prices), 2)


def _sourcing_price_summary(src):
    return format_sourcing_price_label(src) or f"unit ${src['price']:.2f}"


def compute_margin_analysis(items, niche=""):
    """
    Match sourcing to selling within aligned price tiers and product family,
    then keyword similarity. Falls back to tier category average when needed.
    """
    family_kw = _family_keywords(niche)
    family_label = niche.strip().title() if niche else "Product family"

    sourcing = []
    selling = []

    for item in items:
        if _is_sourcing_item(item) and item.get("price") is not None:
            entry = dict(item)
            if not entry.get("product_family"):
                entry["product_family"] = niche.strip().lower()
            enrich_sourcing_pricing(entry)
            entry["landed_cost"] = entry.get("best_landed_cost") or landed_cost(entry)
            sourcing.append(entry)
        elif (
            _is_selling_item(item)
            and item.get("source") in MARGIN_SELLING_SOURCES
            and item.get("price") is not None
        ):
            selling.append(dict(item))

    for entry in sourcing:
        entry["_keywords"] = _extract_keywords(entry.get("name"))
        entry["_tier"] = _sourcing_tier(entry["price"])

    for entry in selling:
        entry["_keywords"] = _extract_keywords(entry.get("name"))
        entry["_tier"] = _selling_tier(entry["price"])
        entry["_in_family"] = _in_product_family(entry.get("name"), family_kw)

    sourcing.sort(key=lambda x: x.get("best_landed_cost") or x["landed_cost"])
    sourcing = _dedupe_items(sourcing, _sourcing_key)

    selling_by_tier = {tier: [] for tier in TIER_LABELS}
    family_selling_by_tier = {tier: [] for tier in TIER_LABELS}
    for sel in selling:
        tier = sel["_tier"]
        if not tier:
            continue
        selling_by_tier[tier].append(sel)
        if sel["_in_family"]:
            family_selling_by_tier[tier].append(sel)

    matches = []
    unmatched = []
    seen_pairs = set()

    for src in sourcing:
        tier = src["_tier"]
        if not tier:
            continue
        src_kw = src["_keywords"]
        tier_family_selling = family_selling_by_tier.get(tier) or []

        best = None
        best_score = 0.0
        best_distance = float("inf")
        landed = src.get("best_landed_cost") or src["landed_cost"]

        for sel in tier_family_selling:
            score = _combined_match_score(src_kw, sel["_keywords"], family_kw)
            distance = abs(landed - sel["price"])
            if score > best_score or (score == best_score and distance < best_distance):
                best_score = score
                best_distance = distance
                best = sel

        price_summary = _sourcing_price_summary(src)
        base = {
            "display_name": _display_name(src_kw, best["_keywords"] if best else set(), src.get("name")),
            "sourcing_name": src.get("name"),
            "source_price": src["price"],
            "source_landed": landed,
            "source_platform": src.get("source", ""),
            "price_summary": price_summary,
            "unit_price": src.get("unit_price"),
            "bulk_price": src.get("bulk_price"),
            "sale_price": src.get("sale_price"),
            "moq": src.get("moq"),
            "best_price_type": src.get("best_price_type"),
            "tier": tier,
            "product_family": family_label,
        }

        if best and best_score >= MIN_KEYWORD_OVERLAP:
            pair_key = (_sourcing_key(src), best.get("url") or best.get("name"))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            sell_price = best["price"]
            margin_dollars = round(sell_price - landed, 2)
            margin_pct = round((margin_dollars / sell_price) * 100, 1) if sell_price else 0.0
            confidence = round(best_score * 100, 1)
            matches.append({
                **base,
                "selling_name": best.get("name"),
                "selling_price": sell_price,
                "selling_platform": best.get("source", ""),
                "margin_dollars": margin_dollars,
                "margin_percent": margin_pct,
                "margin_label": _margin_label(margin_pct),
                "confidence": confidence,
                "low_confidence": confidence < 50,
                "match_type": "exact",
            })
            continue

        category_avg = _tier_category_average(tier_family_selling)
        if category_avg is not None:
            margin_dollars = round(category_avg - landed, 2)
            margin_pct = round((margin_dollars / category_avg) * 100, 1) if category_avg else 0.0
            matches.append({
                **base,
                "display_name": (src.get("name") or "Product")[:50],
                "selling_name": f"{family_label} category average ({len(tier_family_selling)} listings)",
                "selling_price": category_avg,
                "selling_platform": "tier average",
                "margin_dollars": margin_dollars,
                "margin_percent": margin_pct,
                "margin_label": _margin_label(margin_pct),
                "confidence": round(best_score * 100, 1) if best else 0.0,
                "low_confidence": False,
                "match_type": "category_average",
                "category_average_label": CATEGORY_AVERAGE_LABEL,
            })
            continue

        unmatched.append({
            **base,
            "status": "NO COMPARABLE FOUND",
        })

    by_tier = {}
    for tier_key in TIER_LABELS:
        tier_matches = [m for m in matches if m["tier"] == tier_key]
        tier_matches.sort(key=lambda m: -m["margin_percent"])
        tier_unmatched = [u for u in unmatched if u["tier"] == tier_key]
        tier_margin_pct = None
        tier_margin_label = None
        if tier_matches:
            tier_margin_pct = round(
                sum(m["margin_percent"] for m in tier_matches) / len(tier_matches), 1
            )
            tier_margin_label = _margin_label(tier_margin_pct)
        by_tier[tier_key] = {
            "matches": tier_matches,
            "unmatched": tier_unmatched,
            "tier_margin_percent": tier_margin_pct,
            "tier_margin_label": tier_margin_label,
            "match_count": len(tier_matches),
        }

    sourcing.sort(key=lambda x: x.get("best_landed_cost") or x["landed_cost"])
    return {
        "matches": matches,
        "unmatched": unmatched,
        "by_tier": by_tier,
        "top3_sourcing": sourcing[:3],
        "sourcing_count": len(sourcing),
        "selling_count": len(selling),
        "product_family": family_label,
    }
