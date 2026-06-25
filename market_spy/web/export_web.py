"""CSV export for subscribed web users."""

import csv
import os
from datetime import datetime

from market_spy.config import EXPORTS_DIR, EXPORT_UPGRADE_MESSAGE
from market_spy.utils import safe_niche_slug
from market_spy.web.constants import can_user_export_csv


def export_stage2_csv_web(user, niche, items, margin, out_dir=None):
    allowed, message = can_user_export_csv(user)
    if not allowed:
        return None, message

    out_dir = out_dir or EXPORTS_DIR
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = safe_niche_slug(niche)
    filename = f"sourceiq_stage2_{slug}_{ts}.csv"
    out_path = os.path.join(out_dir, filename)

    rows = []
    for item in items:
        rows.append({
            "record_type": "listing",
            "niche": niche,
            "source": item.get("source", ""),
            "name": item.get("name", ""),
            "url": item.get("url", ""),
            "price": item.get("price"),
            "price_label": item.get("price_label", ""),
            "moq": item.get("moq"),
            "supplier_rating": item.get("supplier_rating"),
            "shipping_usa": item.get("shipping_usa"),
            "bulk_price": item.get("bulk_price"),
            "unit_price": item.get("unit_price"),
            "best_landed_cost": item.get("best_landed_cost"),
        })

    by_tier = margin.get("by_tier") or {}
    for tier_key, tier_data in by_tier.items():
        for match in tier_data.get("matches") or []:
            rows.append({
                "record_type": "margin_match",
                "niche": niche,
                "source": match.get("source_platform", ""),
                "name": match.get("display_name") or match.get("sourcing_name", ""),
                "unit_price": match.get("unit_price"),
                "bulk_price": match.get("bulk_price"),
                "moq": match.get("moq"),
                "best_landed_cost": match.get("source_landed"),
                "selling_platform": match.get("selling_platform", ""),
                "selling_price": match.get("selling_price"),
                "margin_percent": match.get("margin_percent"),
                "margin_label": match.get("margin_label", ""),
                "tier": tier_key,
                "confidence": match.get("confidence"),
            })

    fieldnames = [
        "record_type", "niche", "source", "name", "url", "price", "price_label",
        "unit_price", "bulk_price", "moq", "supplier_rating", "shipping_usa",
        "best_landed_cost", "selling_platform", "selling_price",
        "margin_percent", "margin_label", "tier", "confidence",
    ]

    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return out_path, None
