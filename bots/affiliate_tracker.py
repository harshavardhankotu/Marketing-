"""
Affiliate Click & Conversion Tracker
─────────────────────────────────────────────────────
Replicates:
  • AppsFlyer / Adjust  — click attribution
  • impact.com           — postback conversion tracking
  • Amazon Associates    — click-through reporting

Tracks every affiliate link click through a local redirect,
logs the event to SQLite, and supports mock conversion states.

Tables created:
  • affiliate_clicks   — one row per click event
  • affiliate_conversions — one row per conversion (mock or real)

States: clicked → pending_conversion → converted | expired
"""

import sqlite3
import os
import json
from datetime import datetime
from config import DB_PATH


# ═══════════════════════════════════════════════════════════════════════
# SCHEMA
# ═══════════════════════════════════════════════════════════════════════

def setup_tracking_tables():
    """Create click and conversion tables if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()

    c.execute('''
    CREATE TABLE IF NOT EXISTS affiliate_clicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT NOT NULL,
        product_title TEXT,
        sector TEXT,
        channel TEXT,
        affiliate_link TEXT,
        user_agent TEXT,
        referrer TEXT,
        session_id TEXT,
        clicked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        revenue_score REAL DEFAULT 0,
        est_commission_pct REAL DEFAULT 0,
        is_bot INTEGER DEFAULT 0,
        variant TEXT
    )
    ''')

    # Safe migration for affiliate_clicks 'variant' column if it doesn't exist
    c.execute("PRAGMA table_info(affiliate_clicks)")
    columns = [row[1] for row in c.fetchall()]
    if 'variant' not in columns:
        c.execute("ALTER TABLE affiliate_clicks ADD COLUMN variant TEXT")

    c.execute('''
    CREATE TABLE IF NOT EXISTS affiliate_conversions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        click_id INTEGER,
        product_id TEXT NOT NULL,
        status TEXT DEFAULT 'pending_conversion',
        sale_amount REAL DEFAULT 0,
        commission_amount REAL DEFAULT 0,
        converted_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (click_id) REFERENCES affiliate_clicks (id)
    )
    ''')

    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════
# CLICK TRACKING
# ═══════════════════════════════════════════════════════════════════════

def is_bot_ua(user_agent):
    if not user_agent:
        return False
    ua_lower = user_agent.lower()
    bot_keywords = ["telegrambot", "facebookexternalhit", "twitterbot", "googlebot", "bingbot", "slackbot"]
    return any(kw in ua_lower for kw in bot_keywords)

def record_click(product_id, product_title="", sector="", channel="",
                 affiliate_link="", user_agent="", referrer="",
                 session_id="", revenue_score=0, est_commission_pct=0, variant=""):
    """Record a single affiliate click event. Returns click_id."""
    setup_tracking_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()

    # 60 seconds deduplication check
    if session_id and product_id:
        c.execute('''
        SELECT id FROM affiliate_clicks
        WHERE session_id = ? AND product_id = ?
        AND clicked_at >= datetime('now', '-60 seconds')
        LIMIT 1
        ''', (session_id, product_id))
        dup = c.fetchone()
        if dup:
            conn.close()
            return dup[0]

    is_bot = 1 if is_bot_ua(user_agent) else 0

    c.execute('''
    INSERT INTO affiliate_clicks
        (product_id, product_title, sector, channel, affiliate_link,
         user_agent, referrer, session_id, revenue_score, est_commission_pct, is_bot, variant)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (product_id, product_title, sector, channel, affiliate_link,
          user_agent, referrer, session_id, revenue_score, est_commission_pct, is_bot, variant))

    click_id = c.lastrowid

    # Auto-create a pending conversion record (mock attribution pipeline)
    c.execute('''
    INSERT INTO affiliate_conversions (click_id, product_id, status)
    VALUES (?, ?, 'pending_conversion')
    ''', (click_id, product_id))

    conn.commit()
    conn.close()
    return click_id



# ═══════════════════════════════════════════════════════════════════════
# CONVERSION TRACKING (mock-ready)
# ═══════════════════════════════════════════════════════════════════════

def record_conversion(click_id, sale_amount=0, commission_amount=0):
    """Mark a pending conversion as converted. Called by postback or mock."""
    setup_tracking_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()

    c.execute('''
    UPDATE affiliate_conversions
    SET status = 'converted',
        sale_amount = ?,
        commission_amount = ?,
        converted_at = ?
    WHERE click_id = ?
    ''', (sale_amount, commission_amount, datetime.utcnow().isoformat(), click_id))

    conn.commit()
    conn.close()


def expire_old_conversions(hours=72):
    """Mark pending conversions older than N hours as expired."""
    setup_tracking_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()

    c.execute('''
    UPDATE affiliate_conversions
    SET status = 'expired'
    WHERE status = 'pending_conversion'
    AND created_at < datetime('now', ?)
    ''', (f'-{hours} hours',))

    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected


# ═══════════════════════════════════════════════════════════════════════
# ANALYTICS QUERIES
# ═══════════════════════════════════════════════════════════════════════

def get_click_stats(limit=50):
    """Return click analytics for dashboard."""
    setup_tracking_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Top clicked products
    c.execute('''
    SELECT product_id, product_title, sector, COUNT(*) as clicks,
           AVG(revenue_score) as avg_score, AVG(est_commission_pct) as avg_commission
    FROM affiliate_clicks
    WHERE is_bot = 0
    GROUP BY product_id
    ORDER BY clicks DESC
    LIMIT ?
    ''', (limit,))
    top_products = [dict(r) for r in c.fetchall()]

    # Clicks by channel
    c.execute('''
    SELECT channel, COUNT(*) as clicks
    FROM affiliate_clicks
    WHERE is_bot = 0
    GROUP BY channel
    ORDER BY clicks DESC
    ''')
    by_channel = [dict(r) for r in c.fetchall()]

    # Clicks by sector
    c.execute('''
    SELECT sector, COUNT(*) as clicks
    FROM affiliate_clicks
    WHERE is_bot = 0
    GROUP BY sector
    ORDER BY clicks DESC
    ''')
    by_sector = [dict(r) for r in c.fetchall()]

    # Conversion summary
    c.execute('''
    SELECT status, COUNT(*) as count,
           SUM(sale_amount) as total_sales,
           SUM(commission_amount) as total_commission
    FROM affiliate_conversions
    GROUP BY status
    ''')
    conversions = [dict(r) for r in c.fetchall()]

    # Total stats
    c.execute('SELECT COUNT(*) as total_clicks FROM affiliate_clicks WHERE is_bot = 0')
    total_clicks = c.fetchone()['total_clicks']

    c.execute('''
    SELECT COUNT(*) as converted, SUM(commission_amount) as total_commission
    FROM affiliate_conversions WHERE status = 'converted'
    ''')
    conv_row = dict(c.fetchone())

    conn.close()

    return {
        "total_clicks": total_clicks,
        "total_converted": conv_row.get("converted", 0) or 0,
        "total_commission": round(conv_row.get("total_commission", 0) or 0, 2),
        "top_products": top_products,
        "by_channel": by_channel,
        "by_sector": by_sector,
        "conversions": conversions,
    }


# ═══════════════════════════════════════════════════════════════════════
# RUNNER (setup + test)
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    setup_tracking_tables()
    print("Affiliate tracking tables ready.")
    stats = get_click_stats()
    print(f"Current stats: {stats['total_clicks']} clicks, "
          f"{stats['total_converted']} conversions, "
          f"₹{stats['total_commission']} commission")
