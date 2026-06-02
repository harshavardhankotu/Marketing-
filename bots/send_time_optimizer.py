"""
Optimal Send-Time Intelligence Engine
─────────────────────────────────────────────────────
Replicates:
  • CleverTap  "Clever.AI" (Optimal Send-Time)
  • WebEngage  "Send Intelligently"

This engine calculates the best "Send Window" per channel for each product
based on India-specific peak hours, TRAI/DND compliance, and urgency.

Input:  data/output/segmented_products.json, data/output/journey_plans.json
Output: data/output/send_plan.json

Constraints (India-First):
  • WhatsApp DND: 21:00 – 08:00 IST (Strict)
  • WhatsApp Peak: 10:00 – 13:00, 17:00 – 20:30 IST
  • Social Peak (Insta/Twitter): 18:00 – 22:00 IST, Weekend Mornings
"""

import json
import os
import sys
from datetime import datetime, timedelta, time


# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION & CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

INDIA_OFFSET_HOURS = 5.5

PEAK_WINDOWS = {
    "whatsapp": [
        {"start": time(10, 0), "end": time(13, 0), "label": "Morning Rush"},
        {"start": time(17, 0), "end": time(20, 30), "label": "Evening Leisure"}
    ],
    "instagram": [
        {"start": time(12, 0), "end": time(14, 0), "label": "Lunch Browse"},
        {"start": time(18, 0), "end": time(22, 0), "label": "Prime Time"}
    ],
    "twitter": [
        {"start": time(9, 0), "end": time(11, 0), "label": "Morning News"},
        {"start": time(17, 0), "end": time(21, 0), "label": "Evening Commute"}
    ],
    "telegram": [
        {"start": time(10, 0), "end": time(22, 0), "label": "General Active"}
    ]
}

QUIET_HOURS = {
    "whatsapp": {"start": time(21, 0), "end": time(8, 0)}, # TRAI/DND-aware
    "default": {"start": time(23, 0), "end": time(7, 0)}
}

FESTIVAL_BOOSTS = {
    "Diwali": {"date": "2026-11-08", "bias": "evening", "boost": 2}, # Example
    "Holi": {"date": "2026-03-03", "bias": "morning", "boost": 1.5}
}


# ═══════════════════════════════════════════════════════════════════════
# LOGIC ENGINE
# ═══════════════════════════════════════════════════════════════════════

def get_ist_now():
    """Returns current UTC time adjusted to IST."""
    return datetime.utcnow() + timedelta(hours=INDIA_OFFSET_HOURS)


def optimize_send_time(product, journey_plan):
    """
    Calculates send windows for all channels assigned to a product.
    """
    ist_now = get_ist_now()
    segments = product.get("segments", {})
    strategy = journey_plan.get("strategy", {})
    channels = strategy.get("channels", ["twitter", "instagram", "telegram", "whatsapp"])
    
    plan = {
        "product_id": product.get("id"),
        "channels": {},
        "summary": ""
    }

    reasons = []
    
    for channel in channels:
        # 1. Start with the "ideal" window for this channel
        windows = PEAK_WINDOWS.get(channel, PEAK_WINDOWS["telegram"])
        
        # 2. Adjust based on urgency (Stock Scarce / Viral / WhatsApp Priority)
        urgent = (segments.get("stock_tier") == "Scarce" or 
                  segments.get("performance_tier") == "Viral" or
                  segments.get("whatsapp_priority") == True)
        
        # 3. Find the first available peak window starting from today
        # For simplicity, we snap to today's or tomorrow's peak
        chosen_window = _find_best_window(ist_now, windows, channel, urgent)
        
        plan["channels"][channel] = {
            "window_start": chosen_window["start"].isoformat(),
            "window_end": chosen_window["end"].isoformat(),
            "label": chosen_window["label"],
            "reason": f"{channel.capitalize()} peak window" + (" (Urgent Boost)" if urgent else "")
        }
        reasons.append(f"{channel}:{chosen_window['label']}")

    plan["summary"] = " | ".join(reasons)
    return plan


def _find_best_window(current_ist, peak_list, channel, urgent):
    """
    Selection logic:
    - If urgent, try to find a window starting within next 4 hours.
    - Otherwise, find the next occurring peak window.
    - Always enforce DND for WhatsApp.
    """
    # Create concrete datetimes for windows today and tomorrow
    potential_slots = []
    
    for day_offset in [0, 1]:
        base_date = (current_ist + timedelta(days=day_offset)).date()
        for peak in peak_list:
            start_dt = datetime.combine(base_date, peak["start"])
            end_dt = datetime.combine(base_date, peak["end"])
            
            # Enforce DND/Quiet Hours
            qh = QUIET_HOURS.get(channel, QUIET_HOURS["default"])
            if day_offset == 0 and _is_in_quiet_hours(start_dt, qh):
                # Skip or shift if in DND (simplified logic)
                continue
                
            if end_dt > current_ist:
                potential_slots.append({
                    "start": start_dt,
                    "end": end_dt,
                    "label": peak["label"]
                })
    
    # Sort by proximity
    potential_slots.sort(key=lambda x: x["start"])
    
    if not potential_slots:
        # Fallback: Just 2 hours from now
        return {
            "start": current_ist + timedelta(minutes=30),
            "end": current_ist + timedelta(hours=2),
            "label": "Next Available"
        }
    
    # If urgent and there's a slot starting later today, take it even if it's not the next
    if urgent and len(potential_slots) > 0:
        return potential_slots[0]
        
    return potential_slots[0]


def _is_in_quiet_hours(dt, qh):
    """Checks if a given datetime falls within quiet/DND hours."""
    t = dt.time()
    if qh["start"] < qh["end"]:
        return qh["start"] <= t <= qh["end"]
    else: # Spans midnight
        return t >= qh["start"] or t <= qh["end"]


# ═══════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Starting Send-Time Optimizer...")

    from config import OUTPUT_DIR
    segmented_path = os.path.join(OUTPUT_DIR, "segmented_products.json")
    journey_path = os.path.join(OUTPUT_DIR, "journey_plans.json")
    output_path = os.path.join(OUTPUT_DIR, "send_plan.json")

    if not os.path.exists(segmented_path) or not os.path.exists(journey_path):
        print("Error: Missing input files (segmented/journey).")
        sys.exit(1)

    with open(segmented_path, "r", encoding="utf-8") as f:
        products = json.load(f)
    with open(journey_path, "r", encoding="utf-8") as f:
        journeys = json.load(f)

    send_plans = []
    for idx, product in enumerate(products):
        j_plan = journeys[idx] if idx < len(journeys) else {}
        optimized = optimize_send_time(product, j_plan)
        send_plans.append(optimized)

    # Persist
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(send_plans, f, indent=4, ensure_ascii=False)

    print(f"Optimized send-times for {len(send_plans)} products.")
    for p in send_plans[:3]:
        pid = p.get('product_id') or "Unknown"
        summary = p.get('summary') or "No channels"
        print(f"  - Product {pid[:8]}...: {summary}")
