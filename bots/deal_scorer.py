"""
Deal-quality scorer — the free engine that turns raw sourced products into
ranked, badge-worthy deals using ONLY our own recorded price observations.

No paid price-history APIs: every sourcing sweep records what it sees into
``price_history`` and this module derives:

    * ``mrp``            — list price implied by discount % (fallback estimate)
    * ``discount_pct``   — verified drop vs MRP
    * ``is_lowest_ever`` — current price <= historical minimum seen
    * ``drop_vs_avg_pct``— current price vs average of all past sightings
    * ``deal_score``     — 0-100 composite feeding the EV ranker and UI badges
    * ``badge``          — "LOWEST EVER" / "HOT DEAL" / "GOOD DEAL" / ""

This is what separates a deals channel people forward from a link dump:
only genuinely hot deals get posted.
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.path.join(PROJECT_ROOT, 'bots') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'bots'))


def _record(product_id, price, mrp=None):
    try:
        import db_manager
        db_manager.record_price(product_id, price, mrp)
    except Exception as exc:
        print(f"[DEAL_SCORER] price record failed: {exc}")


def _stats(product_id):
    try:
        import db_manager
        return db_manager.get_price_stats(product_id)
    except Exception:
        return None


def score_deal(product, record=True):
    """
    Score one sourced product dict (mutates and returns it).

    Adds/updates: mrp, discount_pct, is_lowest_ever, drop_vs_avg_pct,
    deal_score, badge.
    """
    product_id = product.get("id") or product.get("product_id") or ""
    price = float(product.get("price", 0) or 0)
    discount_field = float(product.get("discount", 0) or 0)

    # MRP implied by the source's own discount claim; sanity-clamped.
    mrp = None
    if price > 0 and discount_field > 0:
        implied = price / (1.0 - min(discount_field, 90.0) / 100.0)
        if implied <= price * 3.0:  # reject absurd estimates
            mrp = round(implied, 2)

    if record and product_id and price > 0:
        _record(product_id, price, mrp)

    stats = _stats(product_id) if product_id else None

    # Verified discount vs our MRP estimate.
    discount_pct = discount_field
    if mrp and mrp > price:
        discount_pct = round((mrp - price) / mrp * 100.0, 1)

    is_lowest_ever = False
    drop_vs_avg_pct = 0.0
    if stats and price > 0:
        prior_min = stats["min"]
        if stats["last"] is not None and abs(stats["last"] - price) < 0.01:
            prior_min = stats["min"]  # includes today's sighting
        is_lowest_ever = price <= prior_min + 0.01 and stats["samples"] >= 1
        if stats["avg"] > 0:
            drop_vs_avg_pct = round((stats["avg"] - price) / stats["avg"] * 100.0, 1)

    # Composite 0-100: discount depth + lowest-ever bonus + below-average bonus.
    score = 0.0
    if price > 0:
        depth_points = min(discount_pct, 80.0) * 0.9          # up to 72 pts
        history_bonus = 15.0 if is_lowest_ever else 0.0        # scarcity signal
        avg_bonus = max(min(drop_vs_avg_pct, 30.0), 0.0) * 0.4 # up to 12 pts
        score = round(min(depth_points + history_bonus + avg_bonus, 100.0), 1)

    if is_lowest_ever and discount_pct >= 20:
        badge = "LOWEST EVER"
    elif score >= 55:
        badge = "HOT DEAL"
    elif score >= 35:
        badge = "GOOD DEAL"
    else:
        badge = ""

    product.update({
        "mrp": mrp,
        "discount_pct": discount_pct,
        "is_lowest_ever": bool(is_lowest_ever),
        "drop_vs_avg_pct": drop_vs_avg_pct,
        "deal_score": score,
        "badge": badge,
    })
    return product


def rank_deals(products):
    """Sort sourced products by deal_score descending (best first)."""
    return sorted(products, key=lambda p: p.get("deal_score", 0), reverse=True)
