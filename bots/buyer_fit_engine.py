"""
Buyer-Fit Engine
────────────────────────────────────────────────────────────────
Classifies every product against standard buyer profiles and
produces buyer_fit labels + scores used for ranking and display.

Buyer profiles:
  cheapest_worth_buying  — lowest price that still passes quality thresholds
  best_value             — highest value-for-money score in the sector
  under_budget           — best option below median price
  premium_upgrade        — best quality option above median price
  balanced_pick          — best blend of price, rating, and discount
  deal_hunter_pick       — highest raw discount % with acceptable quality
  beginner_friendly      — entry-level, forgiving, low-risk buy
  power_user_pick        — feature-dense, highly-rated, often premium

All scores are 0–100. All logic is transparent and heuristic-based.
No paid APIs, no external ML services.
"""

import json
import os
import statistics

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "output")

# ─── scoring weights (adjustable) ─────────────────────────────────────────────
WEIGHTS = {
    "buyer_intent":    0.25,
    "value_for_money": 0.25,
    "budget_fit":      0.20,
    "trust":           0.15,
    "commission":      0.15,
}

# ─── quality gate for "cheapest worth buying" ────────────────────────────────
MIN_RATING_FOR_WORTH_BUYING = 3.5
MIN_STOCK_FOR_WORTH_BUYING  = 5


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _safe_int(v, default=0):
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


# ─── derived score functions ─────────────────────────────────────────────────

def _buyer_intent_score(p, median_price):
    """0-100. High intent = good deal + good rating + low stock urgency."""
    rating   = _safe_float(p.get("rating", 0))
    discount = _safe_float(p.get("discount", 0))
    stock    = _safe_int(p.get("stock", 50))
    price    = _safe_int(p.get("price", 0))

    rating_score   = min(rating / 5.0, 1.0) * 35
    discount_score = min(discount / 40.0, 1.0) * 35
    # Mild urgency signal from low stock (max 20 pts)
    urgency_score  = max(0, (20 - stock) / 20.0) * 20 if stock < 20 else 0
    # Below-median price adds buyer pull (max 10 pts)
    price_pull     = 10 if (median_price > 0 and price < median_price) else 0

    return round(rating_score + discount_score + urgency_score + price_pull, 1)


def _value_for_money_score(p, min_price, max_price):
    """0-100. Combines discount strength with rating quality and relative price position."""
    price    = _safe_int(p.get("price", 0))
    rating   = _safe_float(p.get("rating", 0))
    discount = _safe_float(p.get("discount", 0))

    # How cheap is it in the sector? (40 pts)
    price_range = max(max_price - min_price, 1)
    price_pos_score = ((max_price - price) / price_range) * 40

    # Rating contributes to perceived value (35 pts)
    rating_score = min(rating / 5.0, 1.0) * 35

    # Discount adds direct value signal (25 pts)
    discount_score = min(discount / 50.0, 1.0) * 25

    return round(price_pos_score + rating_score + discount_score, 1)


def _budget_fit_score(p, median_price):
    """0-100. Higher if price is well below median."""
    price = _safe_int(p.get("price", 0))
    if median_price <= 0:
        return 50.0
    ratio = price / median_price
    if ratio <= 0.5:
        return 100.0
    if ratio >= 1.5:
        return 0.0
    return round((1.5 - ratio) / 1.0 * 100, 1)


def _trust_score(p):
    """0-100. Based on rating, stock availability, and brand presence."""
    rating = _safe_float(p.get("rating", 0))
    stock  = _safe_int(p.get("stock", 0))
    brand  = p.get("brand", "")

    rating_score = min(rating / 5.0, 1.0) * 60
    # Consistent availability is a trust signal (25 pts)
    stock_score  = min(stock / 50.0, 1.0) * 25
    # Known brand adds trust (15 pts)
    brand_score  = 15 if (brand and brand.lower() not in ("", "generic", "unknown")) else 0

    return round(rating_score + stock_score + brand_score, 1)


def _commission_score(p):
    """0-100. Derived from est_commission_pct or _est_commission_pct."""
    comm = _safe_float(
        p.get("_est_commission_pct") or p.get("est_commission_pct") or
        (p.get("revenue", {}) or {}).get("est_commission_pct") or 0
    )
    return round(min(comm / 15.0, 1.0) * 100, 1)


def _final_rank_score(p, median_price, min_price, max_price):
    """Weighted blend of all 5 dimensions. 0-100."""
    scores = {
        "buyer_intent":    _buyer_intent_score(p, median_price),
        "value_for_money": _value_for_money_score(p, min_price, max_price),
        "budget_fit":      _budget_fit_score(p, median_price),
        "trust":           _trust_score(p),
        "commission":      _commission_score(p),
    }
    weighted = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    return round(weighted, 1), scores


# ─── buyer-fit label assignment ───────────────────────────────────────────────

def _assign_buyer_labels(products, median_price, min_price, max_price):
    """
    Assigns buyer_fit labels to products. A product can carry multiple labels.
    Returns products sorted by final_rank_score.
    """
    # Enrich each product with all derived scores
    for p in products:
        rank_score, breakdown = _final_rank_score(p, median_price, min_price, max_price)
        p["buyer_fit"] = {
            "final_rank_score":    rank_score,
            "buyer_intent_score":  breakdown["buyer_intent"],
            "value_for_money_score": breakdown["value_for_money"],
            "budget_fit_score":    breakdown["budget_fit"],
            "trust_score":         breakdown["trust"],
            "commission_score":    breakdown["commission"],
            "labels":              [],
        }

    # Sort by final_rank_score for label assignment
    scored = sorted(products, key=lambda p: p["buyer_fit"]["final_rank_score"], reverse=True)

    # ── cheapest worth buying ──────────────────────────────────────────────────
    worth_buying = [
        p for p in scored
        if _safe_float(p.get("rating", 0)) >= MIN_RATING_FOR_WORTH_BUYING
        and _safe_int(p.get("stock", 0)) >= MIN_STOCK_FOR_WORTH_BUYING
    ]
    if worth_buying:
        cheapest = min(worth_buying, key=lambda p: _safe_int(p.get("price", 9999999)))
        cheapest["buyer_fit"]["labels"].append("cheapest_worth_buying")
        cheapest["buyer_fit"]["label_display"] = cheapest["buyer_fit"].get("label_display", [])
        cheapest["buyer_fit"]["label_display"].append("Cheapest Worth Buying")

    # ── best value ────────────────────────────────────────────────────────────
    if worth_buying:
        best_val = max(worth_buying, key=lambda p: p["buyer_fit"]["value_for_money_score"])
        best_val["buyer_fit"]["labels"].append("best_value")
        best_val["buyer_fit"].setdefault("label_display", []).append("Best Value")

    # ── under budget (below median, best rank among them) ─────────────────────
    under_median = [p for p in scored if _safe_int(p.get("price", 0)) < median_price]
    if under_median:
        under_median[0]["buyer_fit"]["labels"].append("under_budget")
        under_median[0]["buyer_fit"].setdefault("label_display", []).append("Best Under Budget")

    # ── premium upgrade ───────────────────────────────────────────────────────
    above_median = [p for p in scored if _safe_int(p.get("price", 0)) >= median_price]
    if above_median:
        best_prem = max(above_median, key=lambda p: p["buyer_fit"]["trust_score"])
        best_prem["buyer_fit"]["labels"].append("premium_upgrade")
        best_prem["buyer_fit"].setdefault("label_display", []).append("Premium Pick")

    # ── balanced pick (highest final_rank_score overall) ─────────────────────
    if scored:
        scored[0]["buyer_fit"]["labels"].append("balanced_pick")
        scored[0]["buyer_fit"].setdefault("label_display", []).append("Recommended")

    # ── deal hunter pick (highest discount with acceptable rating) ────────────
    deal_pool = [p for p in scored if _safe_float(p.get("rating", 0)) >= 3.0]
    if deal_pool:
        deal_pick = max(deal_pool, key=lambda p: _safe_float(p.get("discount", 0)))
        if _safe_float(deal_pick.get("discount", 0)) >= 10:
            deal_pick["buyer_fit"]["labels"].append("deal_hunter_pick")
            deal_pick["buyer_fit"].setdefault("label_display", []).append("High Discount Pick")

    # ── beginner friendly (budget price tier + good rating) ───────────────────
    for p in scored:
        segs = p.get("segments", {})
        if (segs.get("price_tier") in ("Budget", "Mid-Range")
                and _safe_float(p.get("rating", 0)) >= 3.8):
            p["buyer_fit"]["labels"].append("beginner_friendly")
            p["buyer_fit"].setdefault("label_display", []).append("Beginner Friendly")
            break

    # ── power user pick (high trust + high rating + typically premium) ────────
    power_candidates = [p for p in scored if p["buyer_fit"]["trust_score"] >= 60]
    if power_candidates:
        power_pick = max(power_candidates, key=lambda p: _safe_float(p.get("rating", 0)))
        if _safe_float(power_pick.get("rating", 0)) >= 4.2:
            power_pick["buyer_fit"]["labels"].append("power_user_pick")
            power_pick["buyer_fit"].setdefault("label_display", []).append("Power User Pick")

    return scored


# ─── public API ───────────────────────────────────────────────────────────────

def enrich_with_buyer_fit(products):
    """
    Main entry point. Takes a list of products (already scraped/ranked),
    enriches each with buyer_fit dict, returns sorted list.
    """
    if not products:
        return products

    prices = [_safe_int(p.get("price", 0)) for p in products if _safe_int(p.get("price", 0)) > 0]
    if not prices:
        return products

    median_price = statistics.median(prices)
    min_price    = min(prices)
    max_price    = max(prices)

    return _assign_buyer_labels(products, median_price, min_price, max_price)


if __name__ == "__main__":
    ranked_path = os.path.join(OUTPUT_DIR, "ranked_products.json")
    if not os.path.exists(ranked_path):
        print("No ranked_products.json found.")
    else:
        with open(ranked_path, "r", encoding="utf-8") as f:
            products = json.load(f)

        enriched = enrich_with_buyer_fit(products)

        out_path = os.path.join(OUTPUT_DIR, "buyer_fit_products.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(enriched, f, indent=4, ensure_ascii=False)

        print(f"Buyer-fit enrichment complete for {len(enriched)} products.")
        for p in enriched[:5]:
            labels = p.get("buyer_fit", {}).get("labels", [])
            print(f"  {p['title'][:35]:35s} score={p['buyer_fit']['final_rank_score']} labels={labels}")
