"""
Affiliate click & conversion tracker.

Responsible for:
    * extracting tracking parameters from click URLs,
    * filtering bot traffic (``is_bot = 0`` for human analytics),
    * recording conversions idempotently, and
    * updating variant performance used by the A/B bandit.

All writes use explicit ``BEGIN IMMEDIATE`` locks and every connection is
closed in a ``finally`` block.
"""

import os
import re
import sys
import sqlite3
from urllib.parse import urlparse, parse_qs

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH  # noqa: E402

# Extended bot fingerprint list — kept conservative to avoid false positives.
BOT_USER_AGENT_PATTERNS = [
    "bot", "crawl", "spider", "slurp", "mediapartners", "monitor",
    "facebookexternalhit", "linkedinbot", "twitterbot", "slackbot",
    "discordbot", "whatsapp", "telegrambot", "baiduspider", "bingbot",
    "yandexbot", "duckduckbot", "googlebot", "headlesschrome", "phantomjs",
    "curl", "wget", "python-requests", "go-http-client", "okhttp", "java/",
    "feedfetcher", "rogerbot", "pinterest", "snapchat", "embed.ly",
]


def _conn():
    return sqlite3.connect(DB_PATH, timeout=30.0)


def is_bot_ua(user_agent):
    """Heuristic bot detection on a raw User-Agent string."""
    if not user_agent:
        # Absent UA on a tracking endpoint is suspicious but not conclusive.
        return False
    ua = user_agent.lower()
    return any(pattern in ua for pattern in BOT_USER_AGENT_PATTERNS)


def extract_tracking_params(url):
    """
    Pull known tracking parameters out of an affiliate URL.

    Returns a dict with ``variant`` and any whitelisted keys.
    """
    if not url:
        return {"variant": ""}
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    variant = (query.get("var") or query.get("variant") or [""])[0]
    if variant not in ("A", "B"):
        variant = ""
    return {
        "variant": variant,
        "tag": (query.get("tag") or [""])[0],
        "channel": (query.get("channel") or [""])[0],
    }


def record_click(product_id, channel="direct", user_agent="", ip_address="", variant="", session_id=""):
    """
    Record a single click event. Returns the click id.

    If the same product is clicked from the same IP within 30 seconds the call
    is treated as a duplicate and the previous click id is returned.
    """
    if variant not in ("A", "B"):
        variant = ""
    if not session_id:
        session_id = ip_address or ""

    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()

        # De-duplicate rapid refreshes.
        if ip_address and product_id:
            cursor.execute(
                """
                SELECT id FROM affiliate_clicks
                WHERE product_id = ? AND ip_address = ?
                  AND timestamp >= datetime('now', '-30 seconds')
                ORDER BY id DESC LIMIT 1
                """,
                (product_id, ip_address),
            )
            dup = cursor.fetchone()
            if dup:
                conn.rollback()
                return dup[0]

        is_bot = 1 if is_bot_ua(user_agent) else 0
        cursor.execute(
            """
            INSERT INTO affiliate_clicks (product_id, channel, session_id, user_agent, ip_address, is_bot, variant)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (product_id, channel, session_id, user_agent, ip_address, is_bot, variant),
        )
        click_id = cursor.lastrowid
        conn.commit()
        return click_id
    except sqlite3.Error as exc:
        conn.rollback()
        print(f"[AFFILIATE_TRACKER] record_click failed: {exc}")
        raise
    finally:
        conn.close()


def record_conversion(transaction_id, product_id, session_id="", sale_amount=0.0,
                      commission_amount=0.0):
    """
    Record a verified conversion.

    The caller is responsible for HMAC validation and idempotency guards
    (see ``idempotency.py``); this function only persists the row.
    """
    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO affiliate_conversions
                (transaction_id, product_id, session_id, sale_amount, commission_amount, status)
            VALUES (?, ?, ?, ?, ?, 'converted')
            """,
            (transaction_id, product_id, session_id, float(sale_amount), float(commission_amount)),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Duplicate transaction_id already stored.
        return False
    except sqlite3.Error as exc:
        conn.rollback()
        print(f"[AFFILIATE_TRACKER] record_conversion failed: {exc}")
        raise
    finally:
        conn.close()


def get_variant_performance(product_id=None):
    """Return per-variant click/conversion stats (feeds the bandit UI)."""
    conn = _conn()
    try:
        conn.row_factory = sqlite3.Row
        if product_id:
            rows = conn.execute(
                "SELECT product_id, variant, COUNT(*) AS clicks "
                "FROM affiliate_clicks WHERE is_bot = 0 AND variant IN ('A','B') AND product_id = ? "
                "GROUP BY variant",
                (product_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT product_id, variant, COUNT(*) AS clicks "
                "FROM affiliate_clicks WHERE is_bot = 0 AND variant IN ('A','B') "
                "GROUP BY product_id, variant"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_click_stats(limit=50):
    """Dashboard analytics over human clicks and conversions."""
    conn = _conn()
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) AS total FROM affiliate_clicks WHERE is_bot = 0")
        total_clicks = cursor.fetchone()["total"]

        cursor.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(commission_amount), 0) AS commission "
            "FROM affiliate_conversions WHERE status = 'converted'"
        )
        conv = cursor.fetchone()

        cursor.execute(
            """
            SELECT product_id, channel, COUNT(*) AS clicks
            FROM affiliate_clicks WHERE is_bot = 0
            GROUP BY product_id, channel ORDER BY clicks DESC LIMIT ?
            """,
            (limit,),
        )
        top = [dict(r) for r in cursor.fetchall()]

        # Per-channel P&L: clicks per channel, plus conversions/commission
        # attributed back through session_id -> the channel that click came from.
        cursor.execute(
            """
            SELECT ac.channel,
                   COUNT(DISTINCT ac.id) AS clicks,
                   COUNT(DISTINCT conv.id) AS conversions,
                   COALESCE(SUM(conv.commission_amount), 0) AS commission
            FROM affiliate_clicks ac
            LEFT JOIN affiliate_conversions conv
                   ON conv.session_id = ac.session_id AND conv.status = 'converted'
            WHERE ac.is_bot = 0
            GROUP BY ac.channel
            ORDER BY commission DESC, clicks DESC
            """
        )
        by_channel = []
        for r in cursor.fetchall():
            row = dict(r)
            row["commission"] = round(row["commission"] or 0, 2)
            row["conversion_rate"] = round(row["conversions"] / row["clicks"], 4) if row["clicks"] else 0.0
            by_channel.append(row)

        cursor.execute(
            "SELECT sector, COUNT(*) AS clicks FROM affiliate_clicks c JOIN campaigns g ON c.product_id = g.product_id "
            "WHERE c.is_bot = 0 GROUP BY sector"
        )
        by_sector = [dict(r) for r in cursor.fetchall()]

        return {
            "total_clicks": total_clicks,
            "total_converted": conv["total"] if conv else 0,
            "total_commission": round(conv["commission"] if conv else 0, 2),
            "top_products": top,
            "by_channel": by_channel,
            "by_sector": by_sector,
        }
    finally:
        conn.close()
