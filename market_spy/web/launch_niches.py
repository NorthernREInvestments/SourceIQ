"""Niche seeds for automated catalog builds — combines fixed lists and generated combos."""

from __future__ import annotations

# Core launch niches (hand-picked).
LAUNCH_EXTRA_NICHES = [
    "Bluetooth Speakers",
    "Wireless Earbuds",
    "Smart Watches",
    "Tablet Accessories",
    "Camera Accessories",
    "Drone Accessories",
    "LED Strip Lights",
    "Office Chairs",
    "Standing Desks",
    "Desk Organizers",
    "Wall Art",
    "Throw Pillows",
    "Area Rugs",
    "Kitchen Knives",
    "Air Fryer Accessories",
    "Baking Supplies",
    "Water Bottles",
    "Protein Shakers",
    "Resistance Bands",
    "Dumbbells",
    "Exercise Bikes",
    "Treadmill Accessories",
    "Running Shoes",
    "Athletic Socks",
    "Compression Sleeves",
    "Tennis Equipment",
    "Golf Accessories",
    "Soccer Gear",
    "Basketball Accessories",
    "Swimming Goggles",
    "Surfing Gear",
    "Snowboard Accessories",
    "Ski Goggles",
    "Motorcycle Accessories",
    "Bike Lights",
    "Bike Helmets",
    "RV Accessories",
    "Boat Accessories",
    "Garden Tools",
    "Planters",
    "Grill Accessories",
    "Patio Furniture",
    "Hammocks",
    "Coolers",
    "Tents",
    "Sleeping Bags",
    "Camping Stoves",
    "Flashlights",
    "Power Banks",
    "Phone Chargers",
    "Laptop Bags",
    "Backpacks",
    "Travel Accessories",
    "Luggage",
    "Passport Holders",
    "Baby Strollers",
    "Baby Monitors",
    "Diaper Bags",
    "Nursing Pillows",
    "Dog Beds",
    "Dog Toys",
    "Cat Trees",
    "Aquarium Supplies",
    "Bird Cages",
    "Makeup Brushes",
    "Nail Art",
    "Beard Grooming",
    "Electric Toothbrushes",
    "Massage Guns",
    "Essential Oil Diffusers",
    "Candles",
    "Craft Supplies",
    "Sewing Accessories",
    "Party Supplies",
    "Halloween Decorations",
    "Christmas Decorations",
    "Wedding Accessories",
    "Musical Instruments",
    "Guitar Accessories",
    "Piano Accessories",
    "Art Supplies",
    "Board Games",
    "Puzzle Games",
    "RC Cars",
    "Model Kits",
    "Collectible Figures",
    "Vintage Clothing",
    "Handbags",
    "Belts",
    "Scarves",
    "Hats",
    "Work Boots",
    "Safety Glasses",
    "Tool Sets",
    "Power Drill Accessories",
    "Storage Bins",
    "Garage Organization",
    "Cleaning Supplies",
    "Robot Vacuums",
    "Air Purifiers",
    "Humidifiers",
    "Space Heaters",
    "Electric Blankets",
    "Mattress Toppers",
    "Weighted Blankets",
    "Pillows",
    "Shower Curtains",
    "Bath Mats",
    "Towel Sets",
]

_NICHE_MODIFIERS = (
    "Wireless",
    "Portable",
    "Electric",
    "Smart",
    "Mini",
    "Professional",
    "Outdoor",
    "Indoor",
    "Travel",
    "Kids",
    "Baby",
    "Pet",
    "Car",
    "Home",
    "Kitchen",
    "Office",
    "Gaming",
    "Fitness",
    "Yoga",
    "Camping",
)

_NICHE_PRODUCTS = (
    "Accessories",
    "Supplies",
    "Equipment",
    "Tools",
    "Organizers",
    "Storage",
    "Lights",
    "Chargers",
    "Cases",
    "Covers",
    "Bags",
    "Bottles",
    "Mats",
    "Chairs",
    "Desks",
    "Shelves",
    "Decor",
    "Toys",
    "Grooming",
    "Safety",
    "Monitors",
    "Cameras",
    "Speakers",
    "Headphones",
    "Watches",
    "Gloves",
    "Masks",
    "Filters",
    "Brushes",
    "Kits",
)

_CATEGORY_ROOTS = (
    "Dog",
    "Cat",
    "Baby",
    "Kitchen",
    "Bathroom",
    "Bedroom",
    "Living Room",
    "Garage",
    "Garden",
    "Patio",
    "Office",
    "Gym",
    "Sports",
    "Fishing",
    "Hunting",
    "Cycling",
    "Running",
    "Swimming",
    "Golf",
    "Tennis",
    "Soccer",
    "Basketball",
    "Baseball",
    "Skincare",
    "Hair",
    "Makeup",
    "Nail",
    "Jewelry",
    "Watch",
    "Phone",
    "Laptop",
    "Tablet",
    "Camera",
    "Drone",
    "RV",
    "Boat",
    "Motorcycle",
    "Truck",
    "Car",
    "Wedding",
    "Party",
    "Craft",
    "Art",
    "Music",
    "Guitar",
    "Piano",
    "RC",
    "Collectible",
)


def _generated_combo_niches() -> list[str]:
    out: list[str] = []
    for mod in _NICHE_MODIFIERS:
        for product in _NICHE_PRODUCTS:
            out.append(f"{mod} {product}")
    for root in _CATEGORY_ROOTS:
        out.append(f"{root} Supplies")
        out.append(f"{root} Accessories")
        out.append(f"{root} Equipment")
    return out


def _dedupe_niches(niches: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for niche in niches:
        key = " ".join((niche or "").strip().split()).lower()
        if len(key) < 3 or key in seen:
            continue
        seen.add(key)
        ordered.append(" ".join((niche or "").strip().split()))
    return ordered


def all_catalog_seed_niches(*, include_initial_36: list[str] | None = None) -> list[str]:
    """Deduped niche list for seeding the catalog build queue."""
    sources = list(include_initial_36 or []) + LAUNCH_EXTRA_NICHES + _generated_combo_niches()
    return _dedupe_niches(sources)


# High-volume parents: after the parent niche is scraped once, depth sub-queries are queued.
# device_fmt / style_fmt / cross_fmt use {device}, {style}, {parent} placeholders.
_HIGH_VOLUME_DEPTH: dict[str, dict] = {
    "phone cases": {
        "devices": [
            "iPhone 15 Pro Max", "iPhone 15 Pro", "iPhone 15", "iPhone 14 Pro", "iPhone 14",
            "iPhone 13", "Samsung Galaxy S24 Ultra", "Samsung Galaxy S24", "Samsung Galaxy S23",
            "Samsung Galaxy S22", "Google Pixel 8 Pro", "Google Pixel 8", "Google Pixel 7",
            "OnePlus 12", "Motorola Edge",
        ],
        "styles": [
            "Rugged", "Clear", "Wallet", "Leather", "Silicone", "MagSafe", "Shockproof",
            "Waterproof", "Card Holder", "Slim", "Heavy Duty", "Cute",
        ],
        "device_fmt": "{device} case",
        "style_fmt": "{style} phone case",
        "cross_fmt": "{device} {style} case",
    },
    "laptop accessories": {
        "devices": [
            "MacBook Pro 16", "MacBook Pro 14", "MacBook Air", "Dell XPS 15", "Dell XPS 13",
            "HP Spectre", "Lenovo ThinkPad", "ASUS ROG", "Surface Pro", "iPad Pro",
        ],
        "styles": [
            "Sleeve", "Hard Shell", "Leather", "Waterproof", "Anti Theft", "Rolling",
            "Backpack", "Docking", "USB Hub", "Stand",
        ],
        "device_fmt": "{device} laptop sleeve",
        "style_fmt": "{style} laptop accessories",
        "cross_fmt": "{device} {style} laptop bag",
    },
    "headphones": {
        "devices": [],
        "styles": [
            "Wireless", "Noise Cancelling", "Gaming", "Bluetooth", "Over Ear", "In Ear",
            "Sports", "Kids", "Studio", "Bone Conduction",
        ],
        "style_fmt": "{style} headphones",
    },
    "dog supplies": {
        "devices": [],
        "styles": [
            "Large Breed", "Small Breed", "Puppy", "Chew Proof", "Waterproof", "Orthopedic",
            "Travel", "Outdoor", "Grooming", "Training",
        ],
        "style_fmt": "{style} dog supplies",
    },
    "skincare": {
        "devices": [],
        "styles": [
            "Anti Aging", "Acne", "Vitamin C", "Retinol", "Hyaluronic Acid", "Sunscreen",
            "Moisturizer", "Serum", "Eye Cream", "Sensitive Skin",
        ],
        "style_fmt": "{style} skincare",
    },
    "kitchen gadgets": {
        "devices": [],
        "styles": [
            "Electric", "Stainless Steel", "Silicone", "Manual", "Compact", "Professional",
            "Vegetable", "Coffee", "Baking", "Air Fryer",
        ],
        "style_fmt": "{style} kitchen gadgets",
    },
    "jewelry": {
        "devices": [],
        "styles": [
            "Gold", "Silver", "Sterling Silver", "Pearl", "Diamond", "Vintage",
            "Minimalist", "Wedding", "Statement", "Handmade",
        ],
        "style_fmt": "{style} jewelry",
    },
    "sunglasses": {
        "devices": [],
        "styles": [
            "Polarized", "Aviator", "Cat Eye", "Oversized", "Sport", "Kids", "Designer",
            "Blue Light", "Driving", "Fishing",
        ],
        "style_fmt": "{style} sunglasses",
    },
    "baby clothing": {
        "devices": [],
        "styles": [
            "Newborn", "Organic Cotton", "Winter", "Summer", "Onesie", "Pajama", "Bodysuit",
            "Toddler", "Gender Neutral", "Holiday",
        ],
        "style_fmt": "{style} baby clothing",
    },
    "home decor": {
        "devices": [],
        "styles": [
            "Farmhouse", "Modern", "Boho", "Minimalist", "Rustic", "Wall", "Tabletop",
            "Seasonal", "LED", "Vintage",
        ],
        "style_fmt": "{style} home decor",
    },
}


def high_volume_parent_keys() -> list[str]:
    return sorted(_HIGH_VOLUME_DEPTH.keys())


def depth_niches_for_parent(parent: str) -> list[str]:
    """Sub-queries for drilling into a high-volume parent niche."""
    profile = _HIGH_VOLUME_DEPTH.get((parent or "").strip().lower())
    if not profile:
        return []
    out: list[str] = []
    parent_title = " ".join((parent or "").strip().split())
    devices = profile.get("devices") or []
    styles = profile.get("styles") or []
    device_fmt = profile.get("device_fmt")
    style_fmt = profile.get("style_fmt")
    cross_fmt = profile.get("cross_fmt")
    if device_fmt:
        for device in devices:
            out.append(device_fmt.format(device=device, style="", parent=parent_title))
    if style_fmt:
        for style in styles:
            out.append(style_fmt.format(device="", style=style, parent=parent_title))
    if cross_fmt and devices and styles:
        for device in devices[:10]:
            for style in styles[:6]:
                out.append(
                    cross_fmt.format(device=device, style=style, parent=parent_title)
                )
    return _dedupe_niches(out)
