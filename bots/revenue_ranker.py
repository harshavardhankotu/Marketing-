"""
Revenue-Aware Product Ranker
─────────────────────────────────────────────────────
Calculates estimated affiliate commission and baseline 
revenue potential for each product.

NOTE: This is now a supporting signal. The final 
display ranking is handled by buyer_fit_engine.py.
"""

import json, os, sys

# ═══════════════════════════════════════════════════════════════════════
# COMMISSION RATES  (mock — swap with real affiliate dashboard data)
# ═══════════════════════════════════════════════════════════════════════

COMMISSION_RATES = {
    # platform/network → vertical → rate (%)
    "cpa_lead_net_9876": {
        "default": 35,
        "auto_insurance": 45,
        "health_insurance": 40,
        "debt_relief": 50,
        "solar_energy": 45,
        "home_security": 40,
        "live_links": 35
    }
}

# ═══════════════════════════════════════════════════════════════════════
# SCORING WEIGHTS  (tunable — no ML, just transparent weights)
# ═══════════════════════════════════════════════════════════════════════

WEIGHTS = {
    "deal":       0.25,   # discount attractiveness
    "rating":     0.20,   # user trust / product quality
    "commission": 0.25,   # affiliate revenue potential
    "urgency":    0.15,   # stock scarcity → FOMO
    "ctr":        0.15,   # historical click-through performance
}

# ═══════════════════════════════════════════════════════════════════════
# SCORING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def _deal_score(product):
    """0-100 based on discount %.  20%+ = 100."""
    disc = float(product.get("discount", 0))
    return min(disc * 5, 100)

def _rating_score(product):
    """0-100 based on rating out of 5."""
    try:
        r = float(product.get("rating", 0))
    except (ValueError, TypeError):
        r = 0
    return min(r * 20, 100)

def _commission_score(product):
    """0-100 based on estimated commission rate."""
    platform = "cpa_lead_net_9876"
    sector = product.get("sector", "default").lower()
    
    # Load commission rates dynamically from SQLite operator_settings
    rates = COMMISSION_RATES
    try:
        from config import DB_PATH
        import sqlite3
        import json
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        c.execute("SELECT value FROM operator_settings WHERE key = 'commission_rates'")
        row = c.fetchone()
        conn.close()
        if row:
            rates = json.loads(row[0])
    except Exception as exc:
        print(f"[REVENUE_RANKER] Error loading dynamic commission rates, using defaults: {exc}")
        rates = COMMISSION_RATES

    platform_rates = rates.get(platform, rates.get("cpa_lead_net_9876", COMMISSION_RATES["cpa_lead_net_9876"]))
    rate = platform_rates.get(sector, platform_rates.get("default", 35))

    product["_est_commission_pct"] = rate
    product["_est_platform"] = platform
    # Normalise: 60% max = 100
    return min(rate / 60 * 100, 100)

def _urgency_score(product):
    """0-100 based on stock scarcity.  stock<5 = 100."""
    stock = int(product.get("stock", 50))
    if stock <= 0:
        return 100
    if stock >= 50:
        return 0
    return max(0, 100 - stock * 2)

def _ctr_score(product):
    """0-100 based on segments performance tier."""
    segs = product.get("segments", {})
    tier = segs.get("performance_tier", "")
    if tier == "Viral":
        return 100
    if tier == "Engaging":
        return 60
    return 20

# ═══════════════════════════════════════════════════════════════════════
# MAIN RANKING
# ═══════════════════════════════════════════════════════════════════════

def rank_products(products):
    """Score and sort products by revenue potential."""
    for p in products:
        scores = {
            "deal":       _deal_score(p),
            "rating":     _rating_score(p),
            "commission": _commission_score(p),
            "urgency":    _urgency_score(p),
            "ctr":        _ctr_score(p),
        }

        weighted = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
        p["revenue_score"] = round(weighted, 1)
        p["revenue_breakdown"] = {k: round(v, 1) for k, v in scores.items()}

        # Human-readable reason
        reasons = []
        if scores["commission"] >= 60:
            reasons.append("High Commission")
        if scores["deal"] >= 60:
            reasons.append("Great Deal")
        if scores["rating"] >= 80:
            reasons.append("Star Rated")
        if scores["urgency"] >= 60:
            reasons.append("Low Stock")
        if scores["ctr"] >= 60:
            reasons.append("Trending")
        if not reasons:
            reasons.append("Solid Pick")
        p["revenue_reason"] = " + ".join(reasons)

    return products


# ═══════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Starting Revenue Ranker...")

    from config import OUTPUT_DIR
    seg_path = os.path.join(OUTPUT_DIR, "segmented_products.json")
    out_path = os.path.join(OUTPUT_DIR, "ranked_products.json")

    if not os.path.exists(seg_path):
        print("Error: segmented_products.json not found.")
        sys.exit(1)

    with open(seg_path, "r", encoding="utf-8") as f:
        products = json.load(f)

    ranked = rank_products(products)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ranked, f, indent=4, ensure_ascii=False)

    print(f"Ranked {len(ranked)} products by revenue score.")
    for p in ranked[:3]:
        pid = (p.get("id") or p.get("title", "?"))[:20]
        print(f"  {pid}  rev_score={p['revenue_score']}  reason={p['revenue_reason']}")
