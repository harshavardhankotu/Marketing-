"""
Administrative failure-alerting engine.

Sends operational alerts (circuit trips, quota blocks, dead-letter jobs,
failed distributions) to the configured Telegram admin chat. Uses the same
resilience shield as outbound distribution so alerts never hammer the API.

When Telegram credentials are absent (e.g. local dev) alerts are logged to
stdout instead of failing loudly.
"""

import os
import sys
import time
import requests

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import TELEGRAM_BOT_TOKEN, ADMIN_TELEGRAM_ID  # noqa: E402
from quota_manager import consume_quota, QuotaExceededException  # noqa: E402

# Recent alerts held in-memory for the dashboard ticker.
_ALERT_BUFFER = []
_MAX_BUFFER = 50


def _is_configured():
    return bool(TELEGRAM_BOT_TOKEN) and bool(ADMIN_TELEGRAM_ID) and 'your_' not in TELEGRAM_BOT_TOKEN


def _log_local(level, message):
    print(f"[ALERT:{level}] {message}")
    _ALERT_BUFFER.append({"level": level, "message": message, "timestamp": time.time()})
    if len(_ALERT_BUFFER) > _MAX_BUFFER:
        del _ALERT_BUFFER[: len(_ALERT_BUFFER) - _MAX_BUFFER]


def send_telegram_alert(message, level="info"):
    """
    Dispatch an alert to the admin Telegram chat.

    Returns the API response dict on success, ``None`` when not configured.
    Quota/breaker failures degrade to a local log rather than an exception.
    """
    _log_local(level, message)
    if not _is_configured():
        return None

    try:
        consume_quota("telegram")
    except QuotaExceededException:
        print("[ALERT_ENGINE] Telegram quota exhausted — alert logged locally only.")
        return None

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": ADMIN_TELEGRAM_ID, "text": message, "disable_web_page_preview": True},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        print(f"[ALERT_ENGINE] Failed to deliver Telegram alert: {exc}")
        return None


def notify_circuit_trip(provider):
    send_telegram_alert(
        f"⚠️ CIRCUIT BREAKER TRIPPED\nProvider: `{provider}`\nOutbound calls will be short-circuited until cooldown.",
        level="warning",
    )


def notify_quota_block(provider, usage, cap):
    send_telegram_alert(
        f"🚫 DAILY QUOTA BLOCKED\nProvider: `{provider}`\nUsage {usage}/{cap}. Resets at midnight UTC.",
        level="warning",
    )


def notify_dead_letter(job_id, job_type, error):
    send_telegram_alert(
        f"📦 DEAD-LETTER JOB\nJob ID: `{job_id}`\nType: `{job_type}`\nError: {error[:300]}",
        level="error",
    )


def notify_distribution_failure(campaign_id, channel, error):
    send_telegram_alert(
        f"❌ DISTRIBUTION FAILURE\nCampaign: `{campaign_id}`\nChannel: `{channel}`\nError: {error[:300]}",
        level="error",
    )


def get_recent_alerts(limit=20):
    """Return recent in-memory alerts (for the dashboard ticker)."""
    return list(reversed(_ALERT_BUFFER[-limit:]))
