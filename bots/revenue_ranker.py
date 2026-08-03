"""
Expected Value (EV) revenue ranker.

Automatically prioritises the highest-yielding sectors by computing

    EV = Payout × Conversion Rate

where ``Payout`` is the commission configured for a sector and
``Conversion Rate`` is the real, observed click-to-conversion ratio taken from
the ``affiliate_clicks`` and ``affiliate_conversions`` tables.

This is the closed-loop optimisation that lets the suite spend creative
budget on the verticals that actually convert — with zero incentivized
traffic, purely from organic attribution.
"""

import os
import sys
import sqlite3
import json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH  # noqa: E402

# Default commission (payout) baseline per sector, in INR. Overridable from
# the operator_settings JSON blob ("commission_rates").
DEFAULT_COMMISSION_RATES = {
    "electronics": 0.03,
    "home_kitchen": 0.035,
}


def _commission_for_sector(sector, commission_rates):
    rates = commission_rates or {}
    rate = rates.get(sector)
    if rate is None:
        rate = DEFAULT_COMMISSION_RATES.get(sector, 0.02)
    return float(rate)


def _load_commission_rates():
    """Read the operator-configured commission rates blob."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    try:
        row = conn.execute(
            "SELECT value FROM operator_settings WHERE key = 'commission_rates'"
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return {}
    finally:
        conn.close()


def rank_verticals(require_clicks=False):
    """
    Rank every active sector by Expected Value.

    Returns a list of dicts sorted by EV descending:
        [{sector, payout, clicks, conversions, conversion_rate, ev}, ...]

    ``require_clicks`` skips sectors with zero recorded clicks.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        cursor = conn.cursor()

        # Clicks per product (human only), then map to sector via campaigns.
        cursor.execute(
            """
            SELECT c.sector, COUNT(*) AS clicks
            FROM affiliate_clicks ac
            JOIN campaigns c ON c.product_id = ac.product_id
            WHERE ac.is_bot = 0
            GROUP BY c.sector
            """
        )
        click_rows = {r[0]: r[1] for r in cursor.fetchall()}

        # Conversions per product, mapped to sector.
        cursor.execute(
            """
            SELECT c.sector, COUNT(*) AS conversions
            FROM affiliate_conversions conv
            JOIN campaigns c ON c.product_id = conv.product_id
            WHERE conv.status = 'converted'
            GROUP BY c.sector
            """
        )
        conv_rows = {r[0]: r[1] for r in cursor.fetchall()}

        # All active sectors (published + pending) as an ordered basis.
        cursor.execute("SELECT DISTINCT sector FROM campaigns")
        sectors = [r[0] for r in cursor.fetchall() if r[0]]

        # Ensure every known sector is ranked even with zero data.
        for sector in list(DEFAULT_COMMISSION_RATES):
            if sector not in sectors:
                sectors.append(sector)

        commission_rates = _load_commission_rates()

        ranking = []
        for sector in sectors:
            clicks = click_rows.get(sector, 0)
            conversions = conv_rows.get(sector, 0)
            conversion_rate = conversions / clicks if clicks > 0 else 0.0
            payout = _commission_for_sector(sector, commission_rates)
            ev = payout * conversion_rate
            if require_clicks and clicks == 0:
                continue
            ranking.append({
                "sector": sector,
                "payout": payout,
                "clicks": clicks,
                "conversions": conversions,
                "conversion_rate": round(conversion_rate, 6),
                "ev": round(ev, 8),
            })

        ranking.sort(key=lambda r: r["ev"], reverse=True)
        return ranking
    finally:
        conn.close()


def best_sector():
    """Return the currently highest-EV sector (or None when no data)."""
    ranking = rank_verticals()
    if not ranking:
        return None
    return ranking[0]
