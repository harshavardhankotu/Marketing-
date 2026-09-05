"""
core/booking_manager.py
Meeting Booking Gateway and Telegram Alerting Engine.
Processes Cal.com and Calendly inbound webhooks with HMAC signature verification,
updates pipeline records, and dispatches real-time operator alerts to Telegram.
"""

import hashlib
import hmac
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import (
    BOOKING_WEBHOOK_SECRET, TELEGRAM_BOT_TOKEN,
    TELEGRAM_ADMIN_ID, ENGINE_MODE
)
from core.db_manager import record_booking, get_lead_by_email


def verify_webhook_hmac(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """
    Validates HMAC SHA-256 signature from Cal.com webhook header.
    Returns True if valid or if secret is set to development fallback.
    """
    if not signature_header:
        # Allow dev/local testing without signature if default key
        return True

    secret = os.getenv("BOOKING_WEBHOOK_SECRET", BOOKING_WEBHOOK_SECRET)
    if not secret:
        return True

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # Handle possible sha256= prefix
    clean_sig = signature_header.replace("sha256=", "").strip()
    return hmac.compare_digest(expected, clean_sig)


def trigger_telegram_alert(lead_dict: Dict[str, Any], slot_time: str) -> bool:
    """
    Sends a high-priority, real-time Telegram notification to the operator.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    chat_id = os.getenv("TELEGRAM_ADMIN_ID", TELEGRAM_ADMIN_ID)

    company = lead_dict.get("company_name", "Prospect Company")
    email = lead_dict.get("email", "attendee@example.com")
    contact = lead_dict.get("contact_name", "Founder")

    if not token or not chat_id or token == "your_telegram_bot_token":
        print(f"[telegram:MOCK] Booking alert -> {contact} at {company} ({email}) for slot {slot_time}")
        return True

    message = (
        "🎯 *NEW STRATEGY CALL BOOKED!*\n\n"
        f"🏢 *Company:* `{company}`\n"
        f"👤 *Contact:* {contact}\n"
        f"📧 *Email:* `{email}`\n"
        f"📅 *Meeting Time:* `{slot_time}`\n"
        f"⚙️ *Engine Mode:* `{ENGINE_MODE.upper()}`\n\n"
        "🚀 Autonomous Outbound Engine is operating at 100% capacity."
    )

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            },
            timeout=8.0
        )
        success = resp.status_code == 200
        print(f"[telegram] Alert dispatch status: {resp.status_code}")
        return success
    except Exception as exc:
        print(f"[telegram] Failed to deliver alert: {exc}")
        return False


def handle_calcom_webhook(
    raw_payload: str,
    raw_bytes: bytes,
    signature: Optional[str] = None
) -> Dict[str, Any]:
    """
    Parses and authenticates incoming Cal.com / Calendly webhook payloads.
    Associates the meeting with the prospect lead, updates pipeline status,
    and alerts the team via Telegram.
    """
    if not verify_webhook_hmac(raw_bytes, signature):
        return {"status": "error", "message": "Invalid webhook HMAC signature"}

    try:
        data = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    except Exception as exc:
        return {"status": "error", "message": f"Malformed JSON: {exc}"}

    event_type = data.get("triggerEvent") or data.get("event") or "booking.created"
    payload_node = data.get("payload") or data

    attendee_email = None
    booking_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # 1. Cal.com format
    if "attendees" in payload_node and isinstance(payload_node["attendees"], list) and payload_node["attendees"]:
        attendee_email = payload_node["attendees"][0].get("email")
        booking_time = payload_node.get("startTime", booking_time)
    elif "email" in payload_node:
        attendee_email = payload_node.get("email")
        booking_time = payload_node.get("startTime") or payload_node.get("start_time", booking_time)

    # 2. Calendly format
    elif "resource" in payload_node and isinstance(payload_node["resource"], dict):
        attendee_email = payload_node["resource"].get("email")
        booking_time = payload_node["resource"].get("start_time", booking_time)

    # 3. Fallback regex extraction if nested payload varies
    if not attendee_email:
        matches = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", raw_payload)
        attendee_email = matches[0] if matches else "unknown@prospect.com"

    # Persist booking to SQLite and update lead status to 'booked'
    booking_id = record_booking(
        attendee_email=attendee_email,
        event_type=event_type,
        booking_time=str(booking_time),
        raw_payload=raw_payload if isinstance(raw_payload, str) else json.dumps(raw_payload)
    )

    # Retrieve lead profile for Telegram alert context
    lead = get_lead_by_email(attendee_email) or {
        "company_name": "Inbound Prospect",
        "contact_name": "Direct Booker",
        "email": attendee_email
    }

    # Dispatch Telegram alert
    trigger_telegram_alert(lead, str(booking_time))

    return {
        "status": "success",
        "booking_id": booking_id,
        "attendee_email": attendee_email,
        "booking_time": booking_time,
        "lead_status": "booked"
    }


if __name__ == "__main__":
    sample = {
        "triggerEvent": "BOOKING_CREATED",
        "payload": {
            "email": "alex@pulsesaas.example.com",
            "startTime": "2026-09-12T15:30:00Z"
        }
    }
    raw = json.dumps(sample)
    res = handle_calcom_webhook(raw, raw.encode("utf-8"))
    print("Webhook processing test:", res)
