"""
Epsilon-Greedy Multi-Armed Bandit for creative optimisation.

Rather than gaming conversions, the engine mathematically optimises which
creative variant (A/B) to serve for each product based on the *real*
click-to-conversion ratios accumulated in ``affiliate_clicks`` and
``affiliate_conversions``.

Selection rule (ε = 0.20):
    * With probability ε  → explore: pick a random variant.
    * With probability 1-ε → exploit: serve the variant with the highest
      observed conversion ratio (only once it has enough samples).

This guarantees continued exploration of under-tested creatives while
converging on the highest-yielding hook for each product.
"""

import os
import sys
import random
import sqlite3

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH, AB_EPSILON, AB_MIN_SAMPLES  # noqa: E402

# Creative hook styles assigned to each variant.
VARIANT_STYLES = {
    "A": {
        "name": "urgency",
        "hook": "Limited stock — grab the deal before it's gone!",
    },
    "B": {
        "name": "value",
        "hook": "Top-rated pick loved by buyers — best value at this price!",
    },
}


def _variant_conversion_stats(product_id, variant):
    """
    Return (clicks, conversions) for a product/variant.

    Conversions are attributed to a variant via the session that produced the
    click (an organic attribution model; no fake IDs).
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        cursor = conn.cursor()
        # All human clicks for this product + variant.
        cursor.execute(
            "SELECT session_id FROM affiliate_clicks "
            "WHERE product_id = ? AND variant = ? AND is_bot = 0 AND session_id IS NOT NULL AND session_id != ''",
            (product_id, variant),
        )
        sessions = [r[0] for r in cursor.fetchall() if r[0]]
        clicks = len(sessions)

        conversions = 0
        if sessions:
            placeholders = ",".join("?" * len(sessions))
            cursor.execute(
                f"SELECT COUNT(*) FROM affiliate_conversions "
                f"WHERE session_id IN ({placeholders}) AND status = 'converted'",
                sessions,
            )
            conversions = cursor.fetchone()[0]
        return clicks, conversions
    finally:
        conn.close()


def select_variant(product_id, epsilon=None):
    """
    Choose which creative variant to serve for a product.

    Returns the variant letter ('A' | 'B') along with diagnostic info.
    """
    epsilon = AB_EPSILON if epsilon is None else float(epsilon)
    min_samples = AB_MIN_SAMPLES

    stats = {}
    for variant in ("A", "B"):
        stats[variant] = _variant_conversion_stats(product_id, variant)

    total_clicks = sum(s[0] for s in stats.values())

    if total_clicks < min_samples:
        # Not enough data yet: pure exploration (50/50).
        chosen = random.choice(["A", "B"])
        return _build_result(product_id, chosen, stats, epsilon, explored=True, exploit=False)

    if random.random() < epsilon:
        chosen = random.choice(["A", "B"])
        return _build_result(product_id, chosen, stats, epsilon, explored=True, exploit=False)

    # Exploit the empirically better variant.
    rate_a = _safe_rate(stats["A"])
    rate_b = _safe_rate(stats["B"])
    chosen = "A" if rate_a >= rate_b else "B"
    return _build_result(product_id, chosen, stats, epsilon, explored=True, exploit=True)


def _safe_rate(stats):
    clicks, conversions = stats
    return conversions / clicks if clicks > 0 else 0.0


def _build_result(product_id, chosen, stats, epsilon, explored, exploit):
    return {
        "product_id": product_id,
        "variant": chosen,
        "hook": VARIANT_STYLES[chosen]["hook"],
        "style": VARIANT_STYLES[chosen]["name"],
        "epsilon": epsilon,
        "explored": explored,
        "exploited": exploit,
        "stats": {
            variant: {
                "clicks": s[0],
                "conversions": s[1],
                "conversion_rate": round(_safe_rate(s), 6),
            }
            for variant, s in stats.items()
        },
    }


def get_variant_hook(variant):
    """Return the display hook for a variant letter."""
    return VARIANT_STYLES.get(variant, VARIANT_STYLES["A"])


def get_experiment_results(limit=100):
    """
    Aggregate per-product variant performance for the dashboard.

    Returns a dict with ``results`` (per product/variant rows) and summary
    counters.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT product_id, variant,
                   COUNT(*) AS clicks
            FROM affiliate_clicks
            WHERE is_bot = 0 AND variant IN ('A', 'B')
            GROUP BY product_id, variant
            ORDER BY clicks DESC
            LIMIT ?
            """,
            (limit,),
        )
        click_rows = [dict(r) for r in cursor.fetchall()]

        results = []
        for row in click_rows:
            clicks, conversions = _variant_conversion_stats(row["product_id"], row["variant"])
            results.append({
                "product_id": row["product_id"],
                "variant": row["variant"],
                "style": VARIANT_STYLES.get(row["variant"], {}).get("name", ""),
                "clicks": clicks,
                "conversions": conversions,
                "conversion_rate": round(_safe_rate((clicks, conversions)), 6),
            })

        cursor.execute(
            "SELECT COUNT(DISTINCT product_id) AS total FROM affiliate_clicks WHERE is_bot = 0 AND variant IN ('A', 'B')"
        )
        total = cursor.fetchone()["total"]

        return {"total_experiments": total, "results": results}
    finally:
        conn.close()
