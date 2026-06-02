"""
Product & Campaign Segmentation Engine
─────────────────────────────────────────────────────
Replicates:
  • CleverTap  "User Segments" + "Past Behavior Segments"
  • WebEngage  "Segment Builder"

Classifies each product into 5 rule-based segment dimensions:

  1. Price Tier    — Premium / Mid-Range / Budget
  2. Rating Tier   — Star / Good / Risky
  3. Deal Tier     — Hot Deal / Deal / Full Price
  4. Stock Tier    — Scarce / Limited / Available
  5. Performance Tier — Viral / Engaging / Low  (requires analytics data)

Also computes a composite label (e.g. "Hot Deal + Star") and provides
filtering utilities for India-first channel selection:
  • get_whatsapp_worthy()  — returns only Hot Deal + Star products
  • filter_by_segment()    — generic multi-dimension filter

Input:  data/trending_products.json  +  data/output/market_analysis.json
Output: data/output/segmented_products.json  (enriched copy)
        Also writes back segments into trending_products.json
"""

import json
import os
import sys
import statistics
from datetime import datetime


# ═══════════════════════════════════════════════════════════════════════
# SEGMENT RULES — editable thresholds
# ═══════════════════════════════════════════════════════════════════════

RULES = {
    "rating": {
        "Star":  {"min": 4.5},           # ≥ 4.5
        "Good":  {"min": 3.5, "max": 4.5},  # 3.5 – 4.49
        "Risky": {"max": 3.5},           # < 3.5
    },
    "deal": {
        "Hot Deal":   {"min": 20},       # ≥ 20% off
        "Deal":       {"min": 10, "max": 20},  # 10–19.99% off
        "Full Price": {"max": 10},       # < 10% off
    },
    "stock": {
        "Scarce":    {"max": 10},        # < 10 units
        "Limited":   {"min": 10, "max": 30},  # 10–29 units
        "Available": {"min": 30},        # ≥ 30 units
    },
    "performance": {
        "Viral":    {"min": 4.0},        # CTR ≥ 4%
        "Engaging": {"min": 2.0, "max": 4.0},  # 2–3.99%
        "Low":      {"max": 2.0},        # < 2%
    },
}


# ═══════════════════════════════════════════════════════════════════════
# CORE SEGMENTATION
# ═══════════════════════════════════════════════════════════════════════

def segment_products(products, market_analysis=None):
    """
    Main entry point. Takes a list of product dicts and an optional
    market_analysis dict. Returns the same list with a 'segments' dict
    added to each product.

    Args:
        products: list of product dicts (from trending_products.json)
        market_analysis: dict from market_analyzer (for median price)

    Returns:
        list of product dicts, each enriched with 'segments' key
    """
    if not products:
        return products

    # Determine median price for this sector
    median_price = _get_median_price(products, market_analysis)

    for product in products:
        price = _safe_int(product.get("price", "0"))
        rating = _safe_float(product.get("rating", "0"))
        discount = product.get("discount", 0)
        stock = product.get("stock", 0)

        segments = {
            "price_tier": _compute_price_tier(price, median_price),
            "rating_tier": _compute_tier(rating, RULES["rating"]),
            "deal_tier": _compute_tier(discount, RULES["deal"]),
            "stock_tier": _compute_tier(stock, RULES["stock"]),
            "performance_tier": "Pending",  # computed post-analytics
        }

        # Composite label
        segments["composite_label"] = _compute_composite_label(segments)

        # WhatsApp eligibility (India-first: only Hot Deal + Star)
        segments["whatsapp_priority"] = (
            segments["deal_tier"] == "Hot Deal" and
            segments["rating_tier"] == "Star"
        )

        product["segments"] = segments

    return products


def enrich_with_performance(products, analytics_data):
    """
    Post-analytics enrichment: adds Performance Tier based on CTR.
    Call this after analytics_engine has run.

    Args:
        products: list of product dicts with 'segments' already present
        analytics_data: list of analytics log entries (from analytics_log.json)
    """
    for idx, product in enumerate(products):
        if "segments" not in product:
            continue

        ctr = 0.0
        if idx < len(analytics_data):
            metrics = analytics_data[idx].get("total_metrics", {})
            ctr_str = metrics.get("overall_ctr", "0%")
            ctr = _safe_float(ctr_str.replace("%", ""))

        product["segments"]["performance_tier"] = _compute_tier(
            ctr, RULES["performance"]
        )

        # Recompute composite label with performance included
        product["segments"]["composite_label"] = _compute_composite_label(
            product["segments"]
        )

    return products


# ═══════════════════════════════════════════════════════════════════════
# FILTERING UTILITIES
# ═══════════════════════════════════════════════════════════════════════

def get_whatsapp_worthy(products):
    """
    India-first filter: returns only products that qualify for
    WhatsApp Business API broadcast (Hot Deal + Star rated).
    Mirrors WebEngage's segment-based channel targeting.
    """
    return [
        p for p in products
        if p.get("segments", {}).get("whatsapp_priority", False)
    ]


def filter_by_segment(products, **criteria):
    """
    Generic multi-dimension filter.

    Usage:
        filter_by_segment(products, deal_tier="Hot Deal", rating_tier="Star")
        filter_by_segment(products, stock_tier="Scarce")
        filter_by_segment(products, price_tier="Budget", deal_tier="Deal")

    Returns only products matching ALL specified criteria.
    """
    result = []
    for p in products:
        segs = p.get("segments", {})
        match = all(
            segs.get(dim) == value
            for dim, value in criteria.items()
        )
        if match:
            result.append(p)
    return result


def get_segment_summary(products):
    """
    Produces a summary dict of segment distribution across all products.
    Useful for the dashboard stats panel.
    """
    summary = {
        "total_products": len(products),
        "price_tiers": {},
        "rating_tiers": {},
        "deal_tiers": {},
        "stock_tiers": {},
        "performance_tiers": {},
        "whatsapp_eligible": 0,
        "composite_labels": {},
    }

    for p in products:
        segs = p.get("segments", {})

        for dim, key in [
            ("price_tier", "price_tiers"),
            ("rating_tier", "rating_tiers"),
            ("deal_tier", "deal_tiers"),
            ("stock_tier", "stock_tiers"),
            ("performance_tier", "performance_tiers"),
        ]:
            val = segs.get(dim, "Unknown")
            summary[key][val] = summary[key].get(val, 0) + 1

        if segs.get("whatsapp_priority"):
            summary["whatsapp_eligible"] += 1

        label = segs.get("composite_label", "Unknown")
        summary["composite_labels"][label] = summary["composite_labels"].get(label, 0) + 1

    return summary


# ═══════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _get_median_price(products, market_analysis):
    """Get median price from market analysis or compute from products."""
    if market_analysis:
        pa = market_analysis.get("price_analysis", {})
        if "median" in pa:
            return pa["median"]

    prices = [_safe_int(p.get("price", "0")) for p in products]
    return statistics.median(prices) if prices else 0


def _compute_price_tier(price, median_price):
    """Price tier relative to the sector's median price."""
    if median_price <= 0:
        return "Unknown"
    ratio = price / median_price
    if ratio >= 1.5:
        return "Premium"
    elif ratio >= 0.7:
        return "Mid-Range"
    else:
        return "Budget"


def _compute_tier(value, rule_dict):
    """
    Generic tier computation using the RULES dict.
    Checks each tier's min/max bounds.
    """
    for tier_name, bounds in rule_dict.items():
        lo = bounds.get("min", float("-inf"))
        hi = bounds.get("max", float("inf"))
        if lo <= value < hi:
            return tier_name
        # Handle the top tier (only min, no max)
        if "max" not in bounds and value >= lo:
            return tier_name
    return "Unknown"


def _compute_composite_label(segments):
    """
    Generates a human-readable composite label combining the most
    interesting segment dimensions.
    """
    parts = []

    deal = segments.get("deal_tier", "")
    rating = segments.get("rating_tier", "")
    stock = segments.get("stock_tier", "")
    perf = segments.get("performance_tier", "")

    if deal == "Hot Deal":
        parts.append("Hot Deal")
    if rating == "Star":
        parts.append("Star")
    if stock == "Scarce":
        parts.append("Scarce")
    if perf == "Viral":
        parts.append("Viral")

    if parts:
        return " + ".join(parts)

    # Fallback labels
    if rating == "Good" and deal == "Deal":
        return "Good Value"
    if segments.get("price_tier") == "Budget":
        return "Budget Pick"
    if segments.get("price_tier") == "Premium":
        return "Premium"

    return "Standard"


def _safe_int(val):
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _safe_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


# ═══════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Starting Segmentation Engine...")

    from config import PROJECT_ROOT, OUTPUT_DIR
    products_path = os.path.join(PROJECT_ROOT, "data", "trending_products.json")
    market_path = os.path.join(OUTPUT_DIR, "market_analysis.json")
    output_path = os.path.join(OUTPUT_DIR, "segmented_products.json")

    if not os.path.exists(products_path):
        print(f"Error: Product data not found at {products_path}")
        sys.exit(1)

    with open(products_path, "r", encoding="utf-8") as f:
        products = json.load(f)

    # Load market analysis if available
    market_analysis = None
    if os.path.exists(market_path):
        with open(market_path, "r", encoding="utf-8") as f:
            market_analysis = json.load(f)

    # Run segmentation
    segmented = segment_products(products, market_analysis)

    # Write enriched products back
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(segmented, f, indent=4, ensure_ascii=False)

    # Also write back to trending_products.json so downstream steps get segments
    with open(products_path, "w", encoding="utf-8") as f:
        json.dump(segmented, f, indent=4, ensure_ascii=False)

    # Print summary
    summary = get_segment_summary(segmented)
    print(f"Segmented {summary['total_products']} products")
    print(f"  Price tiers:  {summary['price_tiers']}")
    print(f"  Rating tiers: {summary['rating_tiers']}")
    print(f"  Deal tiers:   {summary['deal_tiers']}")
    print(f"  Stock tiers:  {summary['stock_tiers']}")
    print(f"  WhatsApp eligible: {summary['whatsapp_eligible']}")
    print(f"  Labels: {summary['composite_labels']}")
