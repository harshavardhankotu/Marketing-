"""
Automated Campaign Journey Engine
─────────────────────────────────────────────────────
Replicates:
  • CleverTap  "Journeys" (multi-step conditional campaigns)
  • WebEngage  "Journey Designer" (visual workflows)

This engine makes conditional decisions about distribution strategy 
based on the segments assigned to each product.

Input:  data/output/segmented_products.json
Output: data/output/journey_plans.json

Journey Logic:
  1. Flash Deal Blast (Hot Deal + Scarce) 
     → Priority: High | Channels: WhatsApp, Telegram | Copy: Urgency
  2. Premium Showcase (Premium + Star)
     → Priority: Normal | Channels: Instagram, Twitter | Copy: Aspirational
  3. Budget Picks (Budget + Deal)
     → Priority: Normal | Channels: Telegram, WhatsApp | Copy: Value
  4. Daily Digest (Fallback)
     → Priority: Normal | Channels: All | Copy: Standard
"""

import json
import os
import sys

# ═══════════════════════════════════════════════════════════════════════
# JOURNEY DEFINITIONS (The "Spec")
# ═══════════════════════════════════════════════════════════════════════

JOURNEY_RULES = [
    {
        "name": "Flash Deal Blast",
        "description": "High-discount, low-stock urgency campaign",
        "condition": lambda s: s.get("deal_tier") == "Hot Deal" and s.get("stock_tier") == "Scarce",
        "strategy": {
            "channels": ["whatsapp", "telegram"],
            "priority": "high",
            "copy_style": "urgency",
            "overlay_text": "FLASHSALE"
        }
    },
    {
        "name": "Premium Showcase",
        "description": "High-rated, high-price aspirational campaign",
        "condition": lambda s: s.get("price_tier") == "Premium" and s.get("rating_tier") == "Star",
        "strategy": {
            "channels": ["instagram", "twitter"],
            "priority": "normal",
            "copy_style": "aspirational",
            "overlay_text": "PREMIUM"
        }
    },
    {
        "name": "Budget Picks",
        "description": "Value-focused mid-range campaign",
        "condition": lambda s: s.get("price_tier") == "Budget" and s.get("deal_tier") != "Full Price",
        "strategy": {
            "channels": ["telegram", "whatsapp"],
            "priority": "normal",
            "copy_style": "value",
            "overlay_text": "BUDGET"
        }
    }
]

DEFAULT_STRATEGY = {
    "name": "Daily Digest",
    "channels": ["twitter", "instagram", "telegram", "whatsapp"],
    "priority": "normal",
    "copy_style": "standard",
    "overlay_text": None
}

# ═══════════════════════════════════════════════════════════════════════
# EXECUTION LOGIC
# ═══════════════════════════════════════════════════════════════════════

def process_journeys(products):
    """
    Assigns a journey strategy to each product based on its segments.
    """
    journey_plans = []

    for product in products:
        segments = product.get("segments", {})
        assigned_journey = None

        # Check rules in order
        for rule in JOURNEY_RULES:
            if rule["condition"](segments):
                assigned_journey = {
                    "product_id": product.get("id"),
                    "journey_name": rule["name"],
                    "strategy": rule["strategy"]
                }
                break
        
        # Fallback to default
        if not assigned_journey:
            assigned_journey = {
                "product_id": product.get("id"),
                "journey_name": DEFAULT_STRATEGY["name"],
                "strategy": DEFAULT_STRATEGY
            }
        
        journey_plans.append(assigned_journey)
    
    return journey_plans

if __name__ == "__main__":
    print("Starting Journey Engine...")

    from config import OUTPUT_DIR
    products_path = os.path.join(OUTPUT_DIR, "segmented_products.json")
    output_path = os.path.join(OUTPUT_DIR, "journey_plans.json")

    if not os.path.exists(products_path):
        print(f"Error: Segmented data not found at {products_path}")
        sys.exit(1)

    with open(products_path, "r", encoding="utf-8") as f:
        products = json.load(f)

    # Execute
    plans = process_journeys(products)

    # Persist
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(plans, f, indent=4, ensure_ascii=False)

    # Print summary
    counts = {}
    for p in plans:
        name = p["journey_name"]
        counts[name] = counts.get(name, 0) + 1
    
    print(f"Processed journeys for {len(plans)} products:")
    for name, count in counts.items():
        print(f"  - {name}: {count}")
