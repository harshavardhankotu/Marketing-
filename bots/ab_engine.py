"""
A/B Testing Engine for Product Cards & Copy
─────────────────────────────────────────────────────
Replicates: CleverTap A/B, Optimizely, VWO

Generates 2 copy/CTA variants per product, assigns variant IDs,
tracks impressions and clicks, auto-selects winners.

Input:  data/output/ranked_products.json
Output: data/output/ab_variants.json
DB:     ab_experiments, ab_results tables
"""

import json, os, sqlite3, random, hashlib
from datetime import datetime

from config import DB_PATH, OUTPUT_DIR

# ═══════════════════════════════════════════════════════════════════════
# VARIANT TEMPLATES
# ═══════════════════════════════════════════════════════════════════════

URGENCY_TEMPLATES = [
    "🔥 Only {stock} left — grab it before it's gone!",
    "⏰ Limited time! {discount}% off ends soon",
    "🚨 {title} at ₹{price} — lowest price this week!",
    "⚡ Flash deal alert: {discount}% off {brand}!",
]

ASPIRATIONAL_TEMPLATES = [
    "✨ Upgrade to {title} — rated {rating}★ by buyers",
    "🌟 {brand}'s best-seller at just ₹{price}",
    "💎 Premium pick: {title} — {rating}★ quality at {discount}% off",
    "🏆 Top-rated {brand} — join thousands of happy buyers",
]

CTA_VARIANTS = {
    "A": {"label": "Shop Now →", "style": "urgency", "badge_emphasis": "deal"},
    "B": {"label": "View Details ✨", "style": "aspirational", "badge_emphasis": "rating"},
}


# ═══════════════════════════════════════════════════════════════════════
# SCHEMA
# ═══════════════════════════════════════════════════════════════════════

def setup_tables():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()

    c.execute('''
    CREATE TABLE IF NOT EXISTS ab_experiments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT,
        experiment_name TEXT,
        variant_a TEXT,
        variant_b TEXT,
        winner TEXT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS ab_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        experiment_id INTEGER,
        product_id TEXT,
        variant TEXT,
        impressions INTEGER DEFAULT 0,
        clicks INTEGER DEFAULT 0,
        ctr REAL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (experiment_id) REFERENCES ab_experiments (id)
    )
    ''')

    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# VARIANT GENERATION
# ═══════════════════════════════════════════════════════════════════════

def _fill_template(template, product):
    """Fill a template string with product data."""
    return template.format(
        title=product.get("title", "This product")[:40],
        price=product.get("price", "N/A"),
        brand=product.get("brand", "Top brand"),
        rating=product.get("rating", "4.0"),
        discount=int(product.get("discount", 0)),
        stock=int(product.get("stock", 10)),
    )


def _pick_template(templates, product_id, variant_letter):
    """Deterministic template selection based on product ID."""
    seed = hashlib.md5(f"{product_id}_{variant_letter}".encode()).hexdigest()
    idx = int(seed[:8], 16) % len(templates)
    return templates[idx]


def generate_variants(products):
    """Generate A/B variants for each product using Epsilon-Greedy adaptive allocation."""
    setup_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()

    variants = []

    for p in products:
        pid = p.get("id") or p.get("product_id", "")
        if not pid:
            continue

        # Generate variant A (urgency) and B (aspirational)
        tmpl_a = _pick_template(URGENCY_TEMPLATES, pid, "A")
        tmpl_b = _pick_template(ASPIRATIONAL_TEMPLATES, pid, "B")

        copy_a = _fill_template(tmpl_a, p)
        copy_b = _fill_template(tmpl_b, p)

        # Adaptive Epsilon-Greedy split logic
        epsilon = 0.2
        assigned = None
        
        # Check existing results for this product
        c.execute('''
        SELECT variant, clicks, impressions 
        FROM ab_results 
        WHERE product_id = ?
        ''', (pid,))
        db_results = c.fetchall()
        
        best_variant = None
        if db_results and len(db_results) >= 2:
            ctr_map = {}
            for row in db_results:
                v_name, clicks, impressions = row[0], row[1], row[2]
                ctr = clicks / impressions if impressions > 0 else 0.0
                ctr_map[v_name] = ctr
            
            if ctr_map.get("A", 0) > ctr_map.get("B", 0):
                best_variant = "A"
            elif ctr_map.get("B", 0) > ctr_map.get("A", 0):
                best_variant = "B"
        
        if best_variant and random.random() > epsilon:
            assigned = best_variant
        else:
            # Exploration phase or no prior data: 50/50 split
            assigned = "A" if random.random() < 0.5 else "B"

        variant_data = {
            "product_id": pid,
            "title": p.get("title", ""),
            "variant_a": {
                "id": f"{pid}_A",
                "copy": copy_a,
                "cta": CTA_VARIANTS["A"]["label"],
                "style": CTA_VARIANTS["A"]["style"],
                "badge_emphasis": CTA_VARIANTS["A"]["badge_emphasis"],
            },
            "variant_b": {
                "id": f"{pid}_B",
                "copy": copy_b,
                "cta": CTA_VARIANTS["B"]["label"],
                "style": CTA_VARIANTS["B"]["style"],
                "badge_emphasis": CTA_VARIANTS["B"]["badge_emphasis"],
            },
            "assigned_variant": assigned,
            "active_copy": copy_a if assigned == "A" else copy_b,
            "active_cta": CTA_VARIANTS[assigned]["label"],
            "active_style": CTA_VARIANTS[assigned]["style"],
        }

        variants.append(variant_data)

        # Store/Retrieve experiment in DB
        c.execute("SELECT id FROM ab_experiments WHERE product_id = ?", (pid,))
        existing_exp = c.fetchone()
        if existing_exp:
            exp_id = existing_exp[0]
        else:
            c.execute('''
            INSERT INTO ab_experiments (product_id, experiment_name, variant_a, variant_b)
            VALUES (?, ?, ?, ?)
            ''', (pid, f"copy_{pid[:8]}", copy_a, copy_b))
            exp_id = c.lastrowid

            # Init result rows
            for v in ["A", "B"]:
                c.execute('''
                INSERT INTO ab_results (experiment_id, product_id, variant, impressions, clicks)
                VALUES (?, ?, ?, 0, 0)
                ''', (exp_id, pid, v))

        # Record an impression for the assigned variant
        c.execute('''
        UPDATE ab_results
        SET impressions = impressions + 1,
            ctr = CASE WHEN (impressions + 1) > 0 THEN CAST(clicks AS REAL) / (impressions + 1) ELSE 0 END,
            updated_at = CURRENT_TIMESTAMP
        WHERE product_id = ? AND variant = ?
        ''', (pid, assigned))

        # Enrich the product
        p["ab_variant"] = assigned
        p["ab_copy"] = variant_data["active_copy"]
        p["ab_cta"] = variant_data["active_cta"]
        p["ab_style"] = variant_data["active_style"]
        p["ab_experiment_id"] = exp_id

    conn.commit()
    conn.close()
    return variants


def get_experiment_results():
    """Get A/B test results for dashboard."""
    setup_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute('''
    SELECT e.product_id, e.experiment_name, e.variant_a, e.variant_b, e.winner,
           r.variant, r.impressions, r.clicks, r.ctr
    FROM ab_experiments e
    JOIN ab_results r ON e.id = r.experiment_id
    ORDER BY e.created_at DESC
    LIMIT 100
    ''')
    rows = [dict(r) for r in c.fetchall()]

    # Aggregate
    c.execute('SELECT COUNT(DISTINCT product_id) as total_experiments FROM ab_experiments')
    total = c.fetchone()['total_experiments']

    c.execute('SELECT COUNT(*) as winners FROM ab_experiments WHERE winner IS NOT NULL')
    winners = c.fetchone()['winners']

    conn.close()
    return {
        "total_experiments": total,
        "winners_selected": winners,
        "results": rows,
    }

def record_ab_click(product_id, variant):
    """Increment click count for a variant and update CTR."""
    setup_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    c.execute('''
    UPDATE ab_results 
    SET clicks = clicks + 1,
        ctr = CASE WHEN impressions > 0 THEN CAST((clicks + 1) AS REAL) / impressions ELSE 0 END,
        updated_at = CURRENT_TIMESTAMP
    WHERE product_id = ? AND variant = ?
    ''', (product_id, variant))
    conn.commit()
    conn.close()

def record_ab_impression(product_id, variant):
    """Increment impression count for a variant and update CTR."""
    setup_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    c.execute('''
    UPDATE ab_results 
    SET impressions = impressions + 1,
        ctr = CASE WHEN (impressions + 1) > 0 THEN CAST(clicks AS REAL) / (impressions + 1) ELSE 0 END,
        updated_at = CURRENT_TIMESTAMP
    WHERE product_id = ? AND variant = ?
    ''', (product_id, variant))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Running A/B Testing Engine...")

    ranked_path = os.path.join(OUTPUT_DIR, "ranked_products.json")
    if not os.path.exists(ranked_path):
        print("No ranked products found. Skipping A/B generation.")
        with open(os.path.join(OUTPUT_DIR, "ab_variants.json"), "w") as f:
            json.dump([], f)
    else:
        with open(ranked_path, "r", encoding="utf-8") as f:
            products = json.load(f)

        variants = generate_variants(products)

        out_path = os.path.join(OUTPUT_DIR, "ab_variants.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(variants, f, indent=4, ensure_ascii=False)

        print(f"Generated A/B variants for {len(variants)} products.")
        for v in variants[:3]:
            try:
                print(f"  {v['title'][:30]} -> Variant {v['assigned_variant']}: {v['active_copy'][:50]}...")
            except UnicodeEncodeError:
                print(f"  {v['title'][:30]} -> Variant {v['assigned_variant']}")
