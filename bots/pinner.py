"""
Auto-pinner — pins the day's best published deal to the Telegram channel.

Free Telegram Bot API (``pinChatMessage``); requires the bot to be an admin
with pin rights in the channel. The pinned post is the first thing every
visitor sees, so it goes to the highest-scoring deal distributed in the last
24 hours (fallback: best overall published). Never fabricates success: when
no real Telegram message exists (mock-mode deliveries carry no message_id),
the job skips cleanly.
"""

import os
import sys
import requests

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.path.join(PROJECT_ROOT, 'bots') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'bots'))

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  # noqa: E402


def _configured():
    ok_token = bool(TELEGRAM_BOT_TOKEN) and "your_" not in TELEGRAM_BOT_TOKEN
    return ok_token and bool(TELEGRAM_CHAT_ID)


def _top_pinned_candidate():
    """Best published campaign (24h window first) with a telegram message_id."""
    from db_manager import _connection

    conn = _connection()
    try:
        row = conn.execute(
            """
            SELECT c.id, c.title, dl.message_id
            FROM campaigns c
            JOIN distribution_logs dl ON dl.campaign_id = c.id
            WHERE c.status = 'published'
              AND dl.channel = 'telegram'
              AND dl.message_id IS NOT NULL
            ORDER BY CASE WHEN dl.timestamp >= datetime('now', '-1 day') THEN 0 ELSE 1 END,
                     c.deal_score DESC,
                     dl.id DESC
            LIMIT 1
            """
        ).fetchone()
        return {"campaign_id": row[0], "title": row[1], "message_id": str(row[2])} if row else None
    finally:
        conn.close()


def pin_top_deal():
    """
    Pin today's best deal. Returns dict {pinned, reason?, campaign_id?, title?}.
    Safe to run daily via the scheduler; never raises.
    """
    candidate = _top_pinned_candidate()
    if not candidate:
        return {"pinned": False, "reason": "no_published_telegram_message"}
    if not _configured():
        return {"pinned": False, "reason": "telegram_not_configured",
                "campaign_id": candidate["campaign_id"]}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/pinChatMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "message_id": int(candidate["message_id"])},
            timeout=10,
        )
        payload = resp.json() if resp.status_code == 200 else {}
        if resp.status_code == 200 and payload.get("ok"):
            print(f"[PINNER] Pinned campaign {candidate['campaign_id']} "
                  f"({candidate['title'][:40]})")
            return {"pinned": True, "campaign_id": candidate["campaign_id"],
                    "title": candidate["title"]}
        error = payload.get("description") or f"http_{resp.status_code}"
        print(f"[PINNER] Pin failed: {error}")
        return {"pinned": False, "reason": error}
    except Exception as exc:
        print(f"[PINNER] Pin failed: {exc}")
        return {"pinned": False, "reason": str(exc)}


if __name__ == "__main__":
    print(pin_top_deal())
