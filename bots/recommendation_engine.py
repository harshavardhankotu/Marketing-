"""
Next-Best-Product Recommendation Engine
─────────────────────────────────────────────────────
Replicates: Amazon "Frequently bought together", Flipkart "Similar products"

For each product, finds 2-3 related picks from the same dataset
using category affinity, price-tier matching, and brand proximity.
Optimises for affiliate AOV/cross-sell, not just relevance.

Input:  data/output/ranked_products.json
Output: data/output/recommendations.json
"""

import json, os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUTPUT_DIR = os.path.join(BASE_DIR, 'data', 'output')

# ═══════════════════════════════════════════════════════════════════════
# CATEGORY ADJACENCY MAP (what goes well together)
# ═══════════════════════════════════════════════════════════════════════

ADJACENT_SECTORS = {
    "auto_insurance": ["health_insurance", "home_security"],
    "health_insurance": ["auto_insurance", "debt_relief"],
    "debt_relief": ["health_insurance", "solar_energy"],
    "solar_energy": ["home_security", "debt_relief"],
    "home_security": ["auto_insurance", "solar_energy"],
    "live_links": ["auto_insurance", "health_insurance", "debt_relief", "solar_energy", "home_security"],
}


# ═══════════════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════════════

def _price_tier(price):
    if price >= 4000:
        return "premium"
    if price >= 3000:
        return "mid"
    return "budget"


def _match_score(product, candidate):
    """Score how well candidate matches as a recommendation for product."""
    if product.get("id") == candidate.get("id"):
        return -1  # never recommend self

    score = 0
    reasons = []

    p_sector = product.get("sector", "")
    c_sector = candidate.get("sector", "")

    # Same sector = moderate match
    if p_sector == c_sector:
        score += 20
        reasons.append("Same category")

    # Adjacent sector = high cross-sell value
    adjacents = ADJACENT_SECTORS.get(p_sector, [])
    if c_sector in adjacents:
        score += 35
        reasons.append("Complementary category")

    # Same brand = brand loyalty
    if product.get("brand") and product.get("brand") == candidate.get("brand"):
        score += 15
        reasons.append("Same brand")

    # Price tier similarity
    p_price = float(product.get("price", 0))
    c_price = float(candidate.get("price", 0))
    if _price_tier(p_price) == _price_tier(c_price):
        score += 10
        reasons.append("Similar price range")

    # High revenue score = good cross-sell target
    c_rev = candidate.get("revenue_score", 0)
    if c_rev >= 50:
        score += 15
        reasons.append("High revenue potential")
    elif c_rev >= 30:
        score += 8

    # High commission = better for affiliate revenue
    c_comm = candidate.get("_est_commission_pct", 0)
    if c_comm >= 8:
        score += 12
        reasons.append("High commission")

    # Good rating = trust signal
    c_rating = float(candidate.get("rating", 0) or 0)
    if c_rating >= 4.5:
        score += 10
        reasons.append("Top rated")

    # Hot deal = cross-sell hook
    c_segs = candidate.get("segments", {})
    if c_segs.get("deal_tier") == "Hot Deal":
        score += 10
        reasons.append("Hot deal")

    return score, reasons


def generate_recommendations(products, max_per_product=3):
    """For each product, find best cross-sell recommendations."""
    results = []

    for product in products:
        candidates = []
        for other in products:
            score_result = _match_score(product, other)
            if isinstance(score_result, tuple):
                score, reasons = score_result
            else:
                continue  # -1 means self

            if score > 0:
                candidates.append({
                    "id": other.get("id", ""),
                    "title": other.get("title", ""),
                    "price": other.get("price", 0),
                    "sector": other.get("sector", ""),
                    "brand": other.get("brand", ""),
                    "rating": other.get("rating", 0),
                    "discount": other.get("discount", 0),
                    "revenue_score": other.get("revenue_score", 0),
                    "match_score": score,
                    "reason": " + ".join(reasons[:3]) if reasons else "Related product",
                })

        # Sort by match score, take top N
        candidates.sort(key=lambda x: x["match_score"], reverse=True)
        top_picks = candidates[:max_per_product]

        rec_entry = {
            "product_id": product.get("id", ""),
            "product_title": product.get("title", ""),
            "sector": product.get("sector", ""),
            "related_products": top_picks,
            "recommendation_count": len(top_picks),
        }
        results.append(rec_entry)

        # Also enrich the product itself
        product["related_products"] = top_picks

    return results


# ═══════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Running Recommendation Engine...")

    ranked_path = os.path.join(OUTPUT_DIR, "ranked_products.json")
    if not os.path.exists(ranked_path):
        print("No ranked products found. Skipping recommendations.")
        with open(os.path.join(OUTPUT_DIR, "recommendations.json"), "w") as f:
            json.dump([], f)
    else:
        with open(ranked_path, "r", encoding="utf-8") as f:
            products = json.load(f)

        recs = generate_recommendations(products)

        out_path = os.path.join(OUTPUT_DIR, "recommendations.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(recs, f, indent=4, ensure_ascii=False)

        print(f"Generated recommendations for {len(recs)} products.")
        for r in recs[:3]:
            top = r["related_products"][0] if r["related_products"] else {}
            print(f"  {r['product_title'][:30]} -> {top.get('title', 'N/A')[:30]} ({top.get('reason', '')})")
