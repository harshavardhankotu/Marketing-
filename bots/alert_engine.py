"""
Price-Drop & Restock Alert Engine
─────────────────────────────────────────────────────
Replicates: CamelCamelCamel, Keepa, Amazon Price Alerts

Compares latest scrape against previous state stored in SQLite.
Detects price drops, restocks, and discount increases.
Outputs alert events that feed into journey/distribution.

Input:  data/output/ranked_products.json (or segmented_products.json)
Output: data/output/alerts.json
"""

import json, os, sqlite3
from datetime import datetime

from config import DB_PATH, OUTPUT_DIR

# ═══════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════

PRICE_DROP_THRESHOLD = 5      # % drop to trigger alert
DISCOUNT_INCREASE_MIN = 5     # % increase in discount to trigger
RESTOCK_THRESHOLD = 0         # previous stock was <= this

# ═══════════════════════════════════════════════════════════════════════
# SCHEMA
# ═══════════════════════════════════════════════════════════════════════

def setup_tables():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()

    # Stores the latest known state of each product
    c.execute('''
    CREATE TABLE IF NOT EXISTS product_snapshots (
        product_id TEXT PRIMARY KEY,
        title TEXT,
        sector TEXT,
        price REAL,
        discount REAL,
        stock INTEGER,
        rating REAL,
        snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Alert event log
    c.execute('''
    CREATE TABLE IF NOT EXISTS alert_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT,
        title TEXT,
        sector TEXT,
        alert_type TEXT,
        old_value TEXT,
        new_value TEXT,
        change_pct REAL,
        priority TEXT DEFAULT 'normal',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# CORE DETECTION
# ═══════════════════════════════════════════════════════════════════════

def detect_alerts(products):
    """Compare products against stored snapshots. Returns list of alerts."""
    setup_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    alerts = []

    for p in products:
        pid = p.get("id") or p.get("product_id", "")
        if not pid:
            continue

        title = p.get("title", "Unknown")
        sector = p.get("sector", "")
        price = float(p.get("price", 0))
        discount = float(p.get("discount", 0))
        stock = int(p.get("stock", 50))
        rating = float(p.get("rating", 0) or 0)

        # Get previous snapshot
        c.execute("SELECT * FROM product_snapshots WHERE product_id = ?", (pid,))
        prev = c.fetchone()

        if prev:
            old_price = float(prev["price"])
            old_discount = float(prev["discount"])
            old_stock = int(prev["stock"])

            # 1) Price Drop
            if old_price > 0 and price < old_price:
                drop_pct = round(((old_price - price) / old_price) * 100, 1)
                if drop_pct >= PRICE_DROP_THRESHOLD:
                    alert = {
                        "product_id": pid, "title": title, "sector": sector,
                        "alert_type": "price_drop",
                        "old_value": str(old_price), "new_value": str(price),
                        "change_pct": drop_pct,
                        "priority": "high" if drop_pct >= 15 else "normal",
                        "message": f"💸 Price dropped {drop_pct}% (₹{old_price} → ₹{price})",
                        "journey_trigger": "price_drop_alert",
                    }
                    alerts.append(alert)

            # 2) Restock
            if old_stock <= RESTOCK_THRESHOLD and stock > 5:
                alert = {
                    "product_id": pid, "title": title, "sector": sector,
                    "alert_type": "restock",
                    "old_value": str(old_stock), "new_value": str(stock),
                    "change_pct": 0,
                    "priority": "high",
                    "message": f"🔄 Back in stock! ({old_stock} → {stock} units)",
                    "journey_trigger": "back_in_stock",
                }
                alerts.append(alert)

            # 3) Discount Increase
            if discount > old_discount:
                disc_increase = round(discount - old_discount, 1)
                if disc_increase >= DISCOUNT_INCREASE_MIN:
                    alert = {
                        "product_id": pid, "title": title, "sector": sector,
                        "alert_type": "discount_increase",
                        "old_value": str(old_discount), "new_value": str(discount),
                        "change_pct": disc_increase,
                        "priority": "high" if discount >= 25 else "normal",
                        "message": f"🔥 Discount increased! ({old_discount}% → {discount}% off)",
                        "journey_trigger": "limited_deal_window",
                    }
                    alerts.append(alert)

            # 4) Low Stock Warning (was available, now scarce)
            if old_stock >= 10 and stock < 5 and stock > 0:
                alert = {
                    "product_id": pid, "title": title, "sector": sector,
                    "alert_type": "low_stock_warning",
                    "old_value": str(old_stock), "new_value": str(stock),
                    "change_pct": 0,
                    "priority": "normal",
                    "message": f"⚠️ Running low! Only {stock} left (was {old_stock})",
                    "journey_trigger": "scarcity_push",
                }
                alerts.append(alert)

        # Update snapshot
        c.execute('''
        INSERT OR REPLACE INTO product_snapshots
            (product_id, title, sector, price, discount, stock, rating, snapshot_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (pid, title, sector, price, discount, stock, rating,
              datetime.utcnow().isoformat()))

    # Save alert events to DB
    for a in alerts:
        c.execute('''
        INSERT INTO alert_events
            (product_id, title, sector, alert_type, old_value, new_value, change_pct, priority)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (a["product_id"], a["title"], a["sector"], a["alert_type"],
              a["old_value"], a["new_value"], a["change_pct"], a["priority"]))

    conn.commit()
    conn.close()
    return alerts


def get_recent_alerts(limit=50):
    """Get recent alerts for dashboard display."""
    setup_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM alert_events ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ═══════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Running Alert Engine...")

    # Try ranked first, fall back to segmented
    ranked_path = os.path.join(OUTPUT_DIR, "ranked_products.json")
    seg_path = os.path.join(OUTPUT_DIR, "segmented_products.json")

    source = ranked_path if os.path.exists(ranked_path) else seg_path
    if not os.path.exists(source):
        print("No product data found. Skipping alerts.")
        # Write empty alerts
        with open(os.path.join(OUTPUT_DIR, "alerts.json"), "w") as f:
            json.dump([], f)
    else:
        with open(source, "r", encoding="utf-8") as f:
            products = json.load(f)

        alerts = detect_alerts(products)

        out_path = os.path.join(OUTPUT_DIR, "alerts.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(alerts, f, indent=4, ensure_ascii=False)

        print(f"Detected {len(alerts)} alerts.")
        for a in alerts[:5]:
            try:
                print(f"  [{a['alert_type']}] {a['title']}: {a['message']}")
            except UnicodeEncodeError:
                print(f"  [{a['alert_type']}] {a['title']}: {a['alert_type']} detected")
