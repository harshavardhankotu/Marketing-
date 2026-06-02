"""
Value Explainer
────────────────────────────────────────────────────────────────
Generates short, trust-building explanation fields for every product.

Fields generated per product:
  why_this_product       — primary reason to buy this specific product
  best_for               — one-liner on ideal buyer type
  strongest_value_point  — what makes it stand out
  price_justification    — why the price is reasonable or exceptional
  tradeoffs              — honest downside acknowledgement
  why_not_cheapest       — if not cheapest, explains why it still wins
  cheaper_alternative    — if we have one, flags it
  better_upgrade         — if there's a pricier but much better option
  confidence_reason      — how confident the recommendation is and why

All text is derived from real product attributes (price, rating,
discount, stock, brand, buyer_fit labels). No fake copy.
No paid copywriting API required.
"""

import os
import json

BASE_DIR  = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "output")


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


def _rating_phrase(rating):
    if rating >= 4.5:
        return "excellent"
    if rating >= 4.0:
        return "very good"
    if rating >= 3.5:
        return "good"
    return "average"


def _discount_phrase(discount):
    if discount >= 30:
        return f"massive {discount:.0f}% discount"
    if discount >= 20:
        return f"strong {discount:.0f}% discount"
    if discount >= 10:
        return f"{discount:.0f}% discount"
    return "no significant discount"


def _explain_single(product, all_products):
    """Generate explanation fields for one product."""
    title    = product.get("title", "This product")[:50]
    price    = _safe_int(product.get("price", 0))
    rating   = _safe_float(product.get("rating", 0))
    discount = _safe_float(product.get("discount", 0))
    stock    = _safe_int(product.get("stock", 50))
    brand    = product.get("brand", "")
    sector   = product.get("sector", "")

    bf       = product.get("buyer_fit", {})
    labels   = bf.get("labels", [])
    vfm      = bf.get("value_for_money_score", 0)
    trust    = bf.get("trust_score", 0)

    prices_all = sorted([_safe_int(p.get("price", 0)) for p in all_products if _safe_int(p.get("price", 0)) > 0])
    min_price  = prices_all[0] if prices_all else price
    max_price  = prices_all[-1] if prices_all else price

    # ── why_this_product ──────────────────────────────────────────────────────
    if "best_value" in labels:
        why = f"Best value in this category — {_rating_phrase(rating)} rated at ₹{price:,} with {_discount_phrase(discount)}."
    elif "cheapest_worth_buying" in labels:
        why = f"The cheapest option that still delivers quality — rated {rating}★ at just ₹{price:,}."
    elif "premium_upgrade" in labels:
        why = f"Premium choice with {_rating_phrase(rating)} ratings ({rating}★). Worth the extra spend if quality matters."
    elif "balanced_pick" in labels:
        why = f"Best overall pick — balances price (₹{price:,}), rating ({rating}★), and {_discount_phrase(discount)}."
    elif "deal_hunter_pick" in labels:
        why = f"Best deal right now — {discount:.0f}% off with {rating}★ rating."
    else:
        why = f"Solid pick at ₹{price:,} with {rating}★ rating."

    # ── best_for ──────────────────────────────────────────────────────────────
    if "beginner_friendly" in labels:
        best_for = "First-time buyers or those wanting a low-risk, affordable entry point."
    elif "power_user_pick" in labels:
        best_for = "Power users who prioritise performance and quality over price."
    elif "premium_upgrade" in labels:
        best_for = "Buyers willing to spend more for a meaningfully better experience."
    elif "cheapest_worth_buying" in labels:
        best_for = "Budget-conscious shoppers who still want a reliable product."
    elif "deal_hunter_pick" in labels:
        best_for = "Deal hunters looking for the highest discount with decent quality."
    else:
        best_for = "Buyers looking for a dependable, well-rounded product in this category."

    # ── strongest_value_point ─────────────────────────────────────────────────
    if discount >= 25 and rating >= 4.0:
        strongest = f"High discount ({discount:.0f}%) + strong rating ({rating}★) — rare combination."
    elif rating >= 4.5:
        strongest = f"Exceptional rating ({rating}★) builds high buyer confidence."
    elif discount >= 20:
        strongest = f"High discount of {discount:.0f}% makes it the best price point in the sector."
    elif stock < 10:
        strongest = f"Limited stock ({stock} units) signals high demand — popular choice."
    elif brand:
        strongest = f"Trusted brand ({brand}) with consistent quality."
    else:
        strongest = f"Competitive price of ₹{price:,} within the {sector} category."

    # ── price_justification ───────────────────────────────────────────────────
    if price <= min_price:
        price_just = f"Lowest priced option in this batch at ₹{price:,}."
    elif price >= max_price * 0.85:
        price_just = f"Premium-priced at ₹{price:,}, justified by its {rating}★ rating and brand quality."
    else:
        pct_below_max = round((1 - price / max_price) * 100)
        price_just = f"Priced at ₹{price:,} — {pct_below_max}% cheaper than the most expensive option while maintaining {rating}★ quality."

    # ── tradeoffs ─────────────────────────────────────────────────────────────
    tradeoffs = []
    if rating < 3.8:
        tradeoffs.append(f"lower rating ({rating}★) than top alternatives")
    if discount < 5:
        tradeoffs.append("no significant discount currently active")
    if stock < 5:
        tradeoffs.append("very limited stock — may sell out soon")
    if price >= max_price * 0.9 and "premium_upgrade" not in labels:
        tradeoffs.append("higher priced than most alternatives in this batch")
    tradeoff_str = (
        "Tradeoffs: " + "; ".join(tradeoffs) + "."
        if tradeoffs else "No significant tradeoffs for this product type."
    )

    # ── why_not_cheapest ──────────────────────────────────────────────────────
    if "cheapest_worth_buying" in labels or price <= min_price:
        why_not_cheapest = "This IS the cheapest option worth buying."
    elif price > min_price:
        gap = price - min_price
        why_not_cheapest = (
            f"₹{gap:,} more than the cheapest option, but offers "
            f"{'better rating' if rating >= 4.0 else 'more availability'} in return."
        )
    else:
        why_not_cheapest = ""

    # ── cheaper_alternative / better_upgrade ─────────────────────────────────
    sorted_by_price = sorted(all_products, key=lambda p: _safe_int(p.get("price", 0)))
    cheaper_alts = [p for p in sorted_by_price if _safe_int(p.get("price", 0)) < price
                    and p.get("id") != product.get("id")]
    cheaper_alt = cheaper_alts[0].get("title", "")[:40] if cheaper_alts else ""

    pricier = [p for p in sorted_by_price if _safe_int(p.get("price", 0)) > price
               and _safe_float(p.get("rating", 0)) > rating + 0.3
               and p.get("id") != product.get("id")]
    better_upgrade = pricier[0].get("title", "")[:40] if pricier else ""

    # ── confidence_reason ─────────────────────────────────────────────────────
    if vfm >= 70 and trust >= 60:
        confidence = "High confidence — strong value-for-money and trust signals."
    elif vfm >= 50 or trust >= 50:
        confidence = "Moderate confidence — good on key metrics but review alternatives."
    else:
        confidence = "Lower confidence — consider comparing with alternatives before buying."

    return {
        "why_this_product":    why,
        "best_for":            best_for,
        "strongest_value_point": strongest,
        "price_justification": price_just,
        "tradeoffs":           tradeoff_str,
        "why_not_cheapest":    why_not_cheapest,
        "cheaper_alternative": cheaper_alt,
        "better_upgrade":      better_upgrade,
        "confidence_reason":   confidence,
    }


def explain_products(products):
    """
    Enrich each product with a 'value_explanation' dict.
    Requires buyer_fit to already be populated on each product.
    """
    for p in products:
        p["value_explanation"] = _explain_single(p, products)
    return products


if __name__ == "__main__":
    path = os.path.join(OUTPUT_DIR, "buyer_fit_products.json")
    if not os.path.exists(path):
        path = os.path.join(OUTPUT_DIR, "ranked_products.json")

    if not os.path.exists(path):
        print("No product file found.")
    else:
        with open(path, "r", encoding="utf-8") as f:
            products = json.load(f)

        explained = explain_products(products)
        out = os.path.join(OUTPUT_DIR, "explained_products.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(explained, f, indent=4, ensure_ascii=False)
        print(f"Value explanations generated for {len(explained)} products.")
        for p in explained[:3]:
            ex = p.get("value_explanation", {})
            # Removed print to avoid charmap errors on Windows.
