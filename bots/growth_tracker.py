"""
Channel growth tracker — free audience telemetry via the Telegram Bot API.

``getChatMemberCount`` costs nothing and needs only TELEGRAM_BOT_TOKEN +
TELEGRAM_CHAT_ID. A daily snapshot builds the growth curve that tells the
operator which cross-promos/directories actually bring members:

    * no credentials  -> snapshots fall back to manual entry, never crash
    * API errors      -> recorded on the telegram breaker; mock-safe return

Growth is the real bottleneck of deal-channel revenue — this module makes it
a first-class metric next to clicks and commission.
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
    """True when a usable Telegram bot + chat are configured."""
    ok_token = bool(TELEGRAM_BOT_TOKEN) and "your_" not in TELEGRAM_BOT_TOKEN
    return ok_token and bool(TELEGRAM_CHAT_ID)


def fetch_member_count(chat_id=None):
    """
    Query the live member count from the Bot API.

    Returns (count:int, error:str|None). Never raises.
    """
    if not _configured():
        return None, "telegram_not_configured"
    chat = chat_id or TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChatMemberCount"
    try:
        resp = requests.post(url, data={"chat_id": chat}, timeout=10)
        if resp.status_code == 200:
            payload = resp.json()
            if payload.get("ok"):
                return int(payload.get("result", 0)), None
            return None, f"api_error: {payload.get('description', 'unknown')}"
        return None, f"http_{resp.status_code}"
    except requests.RequestException as exc:
        return None, f"network: {exc}"


def capture_snapshot(channel="telegram"):
    """
    Fetch the live count (when configured) and store a snapshot.

    Falls back to skipping storage when unconfigured so tests/offline runs
    stay clean. Returns dict {captured, count, error}.
    """
    import db_manager

    count, error = fetch_member_count()
    if count is None:
        print(f"[GROWTH] Snapshot skipped ({error}).")
        return {"captured": False, "count": None, "error": error}

    db_manager.record_growth_snapshot(count, channel=channel, source="bot_api")
    print(f"[GROWTH] {channel}: {count} members captured.")
    return {"captured": True, "count": count, "error": None}


def record_manual(count, channel="telegram"):
    """Store an operator-entered count (used before bot credentials exist)."""
    import db_manager
    count = int(count)
    if count < 0 or count > 10_000_000:
        raise ValueError("member count out of sane range")
    db_manager.record_growth_snapshot(count, channel=channel, source="manual")
    return {"captured": True, "count": count}


def growth_summary(channel="telegram"):
    """Expose db summary with config state for the dashboard widget."""
    import db_manager

    summary = db_manager.get_growth_summary(channel)
    out = {
        "channel": channel,
        "bot_configured": _configured(),
        "summary": summary,
    }
    if summary:
        out.update({
            "current": summary["current"],
            "delta_24h": summary["delta_24h"],
            "delta_7d": summary["delta_7d"],
            "history": summary["history"],
        })
    else:
        out.update({"current": None, "delta_24h": None, "delta_7d": None, "history": []})
    return out


def build_tracked_link(base_url, product_id, target_url, channel):
    """
    Build a tracked /go/ share link for manual distribution (Reddit, X,
    WhatsApp status, cross-promo partners...). The ``channel`` label flows
    through click attribution so P&L reports show what actually earns.
    """
    import urllib.parse

    base_url = (base_url or "").rstrip("/")
    qs = urllib.parse.urlencode({
        "url": target_url,
        "title": "",
        "sector": "",
        "channel": channel,
    })
    # Keep title/sector out when empty to keep shared URLs clean.
    qs = "&".join(p for p in qs.split("&") if not p.endswith("="))
    return f"{base_url}/go/{urllib.parse.quote(str(product_id))}?{qs}"


if __name__ == "__main__":
    result = capture_snapshot()
    print(growth_summary())
