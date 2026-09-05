"""
app.py
Autonomous B2B Lead Generation, Cold Email & Call-Booking Dashboard.
Exposes real-time KPIs, lead pipelines, webhook booking gateways, and background automation triggers.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect
import requests

from core.config import (
    FLASK_SECRET_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_ID,
    ENGINE_MODE, PUBLIC_BOOKING_URL, SMTP_HOST, SMTP_PORT
)
from core.db_manager import (
    setup_database, get_kpi_overview, get_active_campaign,
    get_queued_leads, record_booking_event, get_db_cursor
)
from core.lead_finder import harvest_b2b_leads
from core.sender_engine import process_outreach_queue
from core.scheduler import init_scheduler

# Initialize Flask application
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config["WTF_CSRF_TIME_LIMIT"] = 3600

# Security extensions
csrf = CSRFProtect(app)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["300 per minute"],
    storage_uri="memory://"
)

# Initialize database schema on boot
setup_database()

# Initialize background scheduler (Asia/Kolkata timezone)
try:
    scheduler = init_scheduler(app)
except Exception as e:
    print(f"[app] Scheduler initialization notice: {e}")


def send_telegram_alert(attendee_email: str, booking_time: str, event_type: str) -> None:
    """Sends immediate high-priority Telegram alert when a call is booked."""
    token = os.getenv("TELEGRAM_BOT_TOKEN") or TELEGRAM_BOT_TOKEN
    chat_id = os.getenv("TELEGRAM_ADMIN_ID") or TELEGRAM_ADMIN_ID

    if token and chat_id and token != "your_telegram_bot_token_here":
        try:
            msg = (
                f"🔥 NEW STRATEGY CALL BOOKED!\n\n"
                f"👤 Attendee: {attendee_email}\n"
                f"📅 Time: {booking_time}\n"
                f"🎯 Type: {event_type}\n"
                f"⚡ Mode: {ENGINE_MODE}"
            )
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg},
                timeout=6.0
            )
            print(f"[telegram] Alert successfully sent for {attendee_email}")
        except Exception as exc:
            print(f"[telegram] Failed to send alert: {exc}")
    else:
        print(f"[telegram:LOG] Call booked by {attendee_email} at {booking_time} (Telegram unconfigured)")


# ─── Dashboard Routes ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Main KPI Overview & Autonomous Control Hub."""
    kpi = get_kpi_overview()
    campaign = get_active_campaign()
    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT * FROM leads ORDER BY id DESC LIMIT 10")
        recent_leads = [dict(r) for r in cursor.fetchall()]

    return render_template(
        "index.html",
        kpi=kpi,
        campaign=campaign,
        recent_leads=recent_leads
    )


@app.route("/leads")
def leads_page():
    """Complete Outbound Pipeline Leads Table."""
    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT * FROM leads ORDER BY id DESC")
        all_leads = [dict(r) for r in cursor.fetchall()]

    return render_template("leads.html", leads=all_leads)


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    """System and Outbound Engine Configuration."""
    if request.method == "POST":
        new_mode = request.form.get("engine_mode", "self")
        new_url = request.form.get("public_booking_url", "").strip()
        new_cap = request.form.get("daily_email_cap", "30").strip()

        with get_db_cursor(commit=True) as cursor:
            cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('engine_mode', ?)", (new_mode,))
            if new_url:
                cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('public_booking_url', ?)", (new_url,))
            if new_cap:
                cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('daily_email_cap', ?)", (new_cap,))

        flash("System settings saved successfully.", "success")
        return redirect(url_for("settings_page"))

    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT key, value FROM system_settings")
        settings_dict = {r["key"]: r["value"] for r in cursor.fetchall()}

    return render_template(
        "settings.html",
        settings=settings_dict,
        config={"SMTP_HOST": SMTP_HOST, "SMTP_PORT": SMTP_PORT}
    )


# ─── Manual Action Endpoints ──────────────────────────────────────────────────

@app.route("/api/harvest", methods=["POST"])
def trigger_harvest():
    """Manually triggers B2B lead harvesting."""
    niche = request.form.get("niche", "Digital Agency").strip()
    location = request.form.get("location", "Austin TX").strip()

    try:
        leads = harvest_b2b_leads(niche=niche, location=location, limit=10)
        flash(f"Successfully harvested and audited {len(leads)} B2B leads for '{niche}' in '{location}'.", "success")
    except Exception as exc:
        flash(f"Error during lead harvest: {exc}", "error")

    return redirect(url_for("index"))


@app.route("/api/dispatch", methods=["POST"])
def trigger_dispatch():
    """Manually triggers cold email batch dispatching."""
    try:
        # pace_sleep is False for manual web button to prevent HTTP gateway timeout
        result = process_outreach_queue(max_batch=5, pace_sleep=False)
        sent = result.get("sent_count", 0)
        if sent > 0:
            flash(f"Dispatched {sent} hyper-personalized cold emails. Remaining daily cap: {result.get('remaining_cap')}.", "success")
        else:
            flash(f"Queue paused or empty: {result.get('reason', 'No action needed')}", "error")
    except Exception as exc:
        flash(f"Outreach dispatch failed: {exc}", "error")

    return redirect(url_for("index"))


# ─── Webhook Booking Gateway (Cal.com / Calendly) ─────────────────────────────

@app.route("/api/webhook/booking", methods=["POST"])
@csrf.exempt
def webhook_booking():
    """
    Listens for Cal.com or Calendly booking webhooks.
    Marks the lead as 'booked', stores payload in booking_events,
    and fires real-time Telegram notification.
    """
    try:
        data = request.json or {}
        raw_text = request.get_data(as_text=True)

        # Handle Cal.com and Calendly payload structures
        event_type = data.get("triggerEvent") or data.get("event") or "booking.created"
        payload_obj = data.get("payload") or data

        # Extract attendee email
        attendee_email = None
        booking_time = datetime.utcnow().isoformat()

        # Cal.com format
        if "attendees" in payload_obj and isinstance(payload_obj["attendees"], list) and payload_obj["attendees"]:
            attendee_email = payload_obj["attendees"][0].get("email")
            booking_time = payload_obj.get("startTime", booking_time)
        elif "email" in payload_obj:
            attendee_email = payload_obj.get("email")
            booking_time = payload_obj.get("startTime") or payload_obj.get("start_time", booking_time)

        # Calendly format
        elif "resource" in payload_obj and "email" in payload_obj["resource"]:
            attendee_email = payload_obj["resource"]["email"]
            booking_time = payload_obj["resource"].get("start_time", booking_time)

        if not attendee_email:
            # Fallback heuristic: search for any email in JSON string
            import re
            emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", raw_text)
            attendee_email = emails[0] if emails else "unknown@booking.client"

        # Record booking in SQLite
        booking_id = record_booking_event(
            attendee_email=attendee_email,
            event_type=event_type,
            booking_time=str(booking_time),
            raw_payload=raw_text
        )

        # Trigger real-time Telegram alert
        send_telegram_alert(attendee_email, str(booking_time), str(event_type))

        return jsonify({
            "status": "success",
            "message": "Booking processed and lead marked as booked",
            "booking_id": booking_id,
            "attendee_email": attendee_email
        }), 200

    except Exception as exc:
        print(f"[webhook] Processing failed: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 400


# ─── System Health & Liveness ─────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
@csrf.exempt
def health_check():
    """Liveness & readiness telemetry endpoint."""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "engine_mode": ENGINE_MODE,
        "kpi": get_kpi_overview()
    }), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
