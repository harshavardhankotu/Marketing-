"""
app.py
Production Flask application for the Autonomous B2B Lead Generation, Cold Outreach & Meeting Booking Engine.
Equipped with:
  - Flask-Login session management
  - Flask-WTF CSRF protection with automatic client-side token injection
  - Flask-Limiter rate limiting
  - Cal.com / Calendly HMAC webhook gateway with real-time Telegram alerts
  - Built-in fallback booking interface (/book/<slug>)
  - APScheduler background workers
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
import bcrypt

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import (
    Flask, flash, jsonify, redirect, render_template, request, url_for
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import (
    LoginManager, UserMixin, current_user, login_required, login_user, logout_user
)
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf

from core.config import (
    FLASK_SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD,
    ENGINE_MODE, PUBLIC_BOOKING_URL, SMTP_HOST, SMTP_PORT,
    SMTP_USER, SMTP_PASS, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_ID
)
from core.db_manager import (
    setup_database, get_dashboard_metrics, get_active_campaign,
    get_db_cursor, get_user_by_email, get_user_by_id,
    record_booking, get_lead_by_email
)
from core.lead_finder import harvest_b2b_leads
from core.sender_engine import process_outreach_queue
from core.booking_manager import handle_calcom_webhook, trigger_telegram_alert
from core.scheduler import init_scheduler

# Initialize Flask App
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config["WTF_CSRF_TIME_LIMIT"] = 3600

# Security & Limiter Extensions
csrf = CSRFProtect(app)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["300 per minute"],
    storage_uri="memory://"
)

# Authentication Manager
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message_category = "error"
login_manager.init_app(app)


class User(UserMixin):
    def __init__(self, user_dict):
        self.id = user_dict["id"]
        self.email = user_dict["email"]
        self.role = user_dict.get("role", "admin")


@login_manager.user_loader
def load_user(user_id):
    user_data = get_user_by_id(int(user_id))
    return User(user_data) if user_data else None


# Database and Background Scheduler Initialization
setup_database()
try:
    scheduler = init_scheduler(app)
except Exception as e:
    print(f"[app] Scheduler boot notice: {e}")


# Global response filter injecting CSRF tokens into all HTML responses
@app.after_request
def inject_security_headers_and_csrf(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    if response.content_type and "text/html" in response.content_type:
        token = generate_csrf()
        script = f"""
        <script>
        (function() {{
            const originalFetch = window.fetch;
            window.fetch = function(url, options) {{
                options = options || {{}};
                options.headers = options.headers || {{}};
                if (!options.headers['X-CSRFToken']) {{
                    options.headers['X-CSRFToken'] = '{token}';
                }}
                return originalFetch(url, options);
            }};
        }})();
        </script>
        """
        try:
            data = response.get_data(as_text=True)
            if "<head>" in data:
                response.set_data(data.replace("<head>", f"<head>{script}", 1))
        except Exception:
            pass
    return response


# ─── Authentication Routes ───────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user_data = get_user_by_email(email)
        if user_data:
            stored_hash = user_data["password_hash"].encode("utf-8")
            if bcrypt.checkpw(password.encode("utf-8"), stored_hash):
                user = User(user_data)
                login_user(user)
                flash("Signed in successfully.", "success")
                next_page = request.args.get("next")
                return redirect(next_page or url_for("dashboard"))

        flash("Invalid email or password credentials.", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("login"))


# ─── Dashboard & Core Views ───────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    """Main Glassmorphic KPI Dashboard."""
    kpi = get_dashboard_metrics()
    campaign = get_active_campaign()

    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT * FROM leads ORDER BY id DESC LIMIT 8")
        recent_leads = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
        SELECT o.*, l.company_name, l.email AS prospect_email FROM outreach_logs o
        JOIN leads l ON o.lead_id = l.id
        ORDER BY o.id DESC LIMIT 5
        """)
        recent_outreach = [dict(r) for r in cursor.fetchall()]

    return render_template(
        "index.html",
        kpi=kpi,
        campaign=campaign,
        recent_leads=recent_leads,
        recent_outreach=recent_outreach
    )


@app.route("/leads")
@login_required
def leads_page():
    """Prospect Leads Pipeline Data Table."""
    status_filter = request.args.get("status")
    verification_filter = request.args.get("verification")

    query = "SELECT * FROM leads WHERE 1=1"
    params = []

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    if verification_filter:
        query += " AND verification_status = ?"
        params.append(verification_filter)

    query += " ORDER BY id DESC"

    with get_db_cursor(commit=False) as cursor:
        cursor.execute(query, tuple(params))
        leads = [dict(r) for r in cursor.fetchall()]

    return render_template(
        "leads.html",
        leads=leads,
        current_status=status_filter,
        current_verification=verification_filter
    )


@app.route("/campaigns", methods=["GET", "POST"])
@login_required
def campaigns_page():
    """Campaign Management & ICP Targeting Interface."""
    if request.method == "POST":
        camp_id = request.form.get("campaign_id")
        name = request.form.get("name", "").strip()
        mode = request.form.get("mode", "self")
        target_niche = request.form.get("target_niche", "").strip()
        value_prop = request.form.get("value_prop", "").strip()
        booking_link = request.form.get("booking_link", "").strip()
        active = 1 if request.form.get("active") == "on" else 0

        with get_db_cursor(commit=True) as cursor:
            if camp_id:
                cursor.execute("""
                UPDATE campaigns
                SET name = ?, mode = ?, target_niche = ?, value_prop = ?, booking_link = ?, active = ?
                WHERE id = ?
                """, (name, mode, target_niche, value_prop, booking_link, active, camp_id))
            else:
                cursor.execute("""
                INSERT INTO campaigns (name, mode, target_niche, value_prop, booking_link, active)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (name, mode, target_niche, value_prop, booking_link, active))

        flash("Campaign updated successfully.", "success")
        return redirect(url_for("campaigns_page"))

    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT * FROM campaigns ORDER BY id DESC")
        campaigns = [dict(r) for r in cursor.fetchall()]

    return render_template("campaigns.html", campaigns=campaigns)


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    """Engine Configuration: SMTP, Gemini API, Telegram, and Daily Caps."""
    if request.method == "POST":
        mode = request.form.get("engine_mode", "self")
        booking_url = request.form.get("public_booking_url", "").strip()
        cap = request.form.get("daily_email_cap", "30").strip()
        min_delay = request.form.get("min_send_delay", "120").strip()
        max_delay = request.form.get("max_send_delay", "240").strip()

        with get_db_cursor(commit=True) as cursor:
            cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('engine_mode', ?)", (mode,))
            cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('public_booking_url', ?)", (booking_url,))
            cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('daily_email_cap', ?)", (cap,))
            cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('min_send_delay', ?)", (min_delay,))
            cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('max_send_delay', ?)", (max_delay,))

        flash("System settings saved successfully.", "success")
        return redirect(url_for("settings_page"))

    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT key, value FROM system_settings")
        settings_dict = {r["key"]: r["value"] for r in cursor.fetchall()}

    return render_template(
        "settings.html",
        settings=settings_dict,
        config={
            "SMTP_HOST": SMTP_HOST,
            "SMTP_PORT": SMTP_PORT,
            "SMTP_USER": SMTP_USER or "Not configured",
            "GEMINI_SET": bool(GEMINI_API_KEY and GEMINI_API_KEY != "your_gemini_api_key_here"),
            "TELEGRAM_SET": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_ID)
        }
    )


# ─── Built-in Fallback Booking Slot Page (/book/<slug>) ───────────────────────

@app.route("/book/<slug>", methods=["GET", "POST"])
def book_slot(slug):
    """
    Zero-dependency meeting reservation page.
    Provides direct calendar selection for prospects when Cal.com is not used.
    """
    campaign = get_active_campaign()
    company_title = campaign.get("name") if campaign else "GrowthOps Discovery"

    if request.method == "POST":
        attendee_name = request.form.get("name", "").strip()
        attendee_email = request.form.get("email", "").strip().lower()
        selected_slot = request.form.get("slot", "").strip()

        if not attendee_email or not selected_slot:
            flash("Please provide your email address and select an available slot.", "error")
            return redirect(url_for("book_slot", slug=slug))

        # Record booking directly
        booking_id = record_booking(
            attendee_email=attendee_email,
            event_type="direct_page_booking",
            booking_time=selected_slot,
            raw_payload=json.dumps({"name": attendee_name, "email": attendee_email, "slot": selected_slot})
        )

        # Trigger Telegram Alert
        lead = get_lead_by_email(attendee_email) or {
            "company_name": attendee_name or "Direct Booker",
            "contact_name": attendee_name,
            "email": attendee_email
        }
        trigger_telegram_alert(lead, selected_slot)

        return render_template("book.html", confirmed=True, slot=selected_slot, email=attendee_email, slug=slug)

    return render_template("book.html", confirmed=False, slug=slug, company_title=company_title)


# ─── Manual Operator Trigger Endpoints ────────────────────────────────────────

@app.route("/api/harvest", methods=["POST"])
@login_required
def trigger_harvest():
    """Manually triggers B2B lead harvesting."""
    niche = request.form.get("niche", "B2B SaaS Agency").strip()
    location = request.form.get("location", "Austin TX").strip()

    try:
        leads = harvest_b2b_leads(niche=niche, location=location, limit=10)
        flash(f"Discovered and verified {len(leads)} B2B prospects for '{niche}' in '{location}'.", "success")
    except Exception as exc:
        flash(f"Harvester failed: {exc}", "error")

    return redirect(url_for("dashboard"))


@app.route("/api/dispatch", methods=["POST"])
@login_required
def trigger_dispatch():
    """Manually triggers cold outreach queue processing."""
    try:
        # pace_sleep=False for manual dashboard button to avoid blocking the HTTP request
        report = process_outreach_queue(max_batch=5, pace_sleep=False)
        sent = report.get("sent_count", 0)
        if sent > 0:
            flash(f"Successfully dispatched {sent} cold emails. Remaining daily limit: {report.get('remaining_cap')}.", "success")
        else:
            flash(f"Outreach paused: {report.get('reason', 'No leads ready')}", "error")
    except Exception as exc:
        flash(f"Outreach dispatch failed: {exc}", "error")

    return redirect(url_for("dashboard"))


# ─── Webhook Booking Gateway (Cal.com / Calendly) ─────────────────────────────

@app.route("/api/webhook/booking", methods=["POST"])
@csrf.exempt
def webhook_booking():
    """
    CSRF-exempt webhook receiver listening for Cal.com or Calendly booking events.
    Verifies HMAC signature, marks lead as 'booked', and sends Telegram alert.
    """
    raw_body = request.get_data()
    raw_text = raw_body.decode("utf-8", errors="ignore")
    signature = request.headers.get("X-Cal-Signature-256") or request.headers.get("X-Signature")

    result = handle_calcom_webhook(raw_text, raw_body, signature)
    status_code = 200 if result.get("status") == "success" else 400
    return jsonify(result), status_code


# ─── Health & Liveness ────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
@csrf.exempt
def health():
    """Liveness probe for orchestrators and uptime monitors."""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "engine_mode": ENGINE_MODE,
        "metrics": get_dashboard_metrics()
    }), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
