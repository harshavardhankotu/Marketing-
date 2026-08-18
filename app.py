"""
Autonomous Affiliate Marketing & ML Optimization Suite — Flask application.

Strict compliance: Amazon Associates TOS / ASCI / CPA-network terms.
Organic traffic only. NO wallets, NO cashback loops, NO telephony/DNI.

Key routes:
    * GET  /login  /logout  /  /history  /settings   (admin console)
    * GET  /go/<product_id>                          (tracked redirect)
    * POST /postback/conversion                      (HMAC-verified webhook)
    * GET  /api/performance etc.                     (dashboard JSON APIs)
"""

import os
import sys
import json
import hmac
import html
import hashlib
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

from flask import (
    Flask, render_template, jsonify, request, redirect, g, flash, Response,
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required, current_user,
)
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded
from dotenv import load_dotenv
import bcrypt

# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP PATHS
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
for sub in ("bots", "scrapers", "generators"):
    sys.path.insert(0, os.path.join(BASE_DIR, sub))

load_dotenv(os.path.join(BASE_DIR, ".env"))

import config  # noqa: E402
import db_manager  # noqa: E402
from config import OUTPUT_DIR, TRUSTED_DOMAINS  # noqa: E402
from generators import background_factory  # noqa: E402

app = Flask(__name__)
app.config["SECRET_KEY"] = config.FLASK_SECRET_KEY
app.config["WTF_CSRF_TIME_LIMIT"] = None


# ─────────────────────────────────────────────────────────────────────────────
# AMBIENT BACKGROUND — injected into every template
# ─────────────────────────────────────────────────────────────────────────────
@app.context_processor
def _inject_ambient_background():
    try:
        bg = background_factory.get_background_for_ui()
    except Exception:
        bg = {
            "theme": "train", "has_video": False,
            "video_url": "", "poster_url": "",
        }
    return {"bg": bg}


# ─────────────────────────────────────────────────────────────────────────────
# AUTH — pre-CSRF gate for /api/
# ─────────────────────────────────────────────────────────────────────────────
@app.before_request
def _api_auth_gate():
    if request.path.startswith("/api/"):
        if not current_user.is_authenticated:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
    if request.path in ("/api/scheduler_run_now", "/api/reliability_reset") and request.method == "POST":
        if not current_user.is_authenticated or getattr(current_user, "role", "") != "admin":
            return jsonify({"status": "error", "message": "Admin role required"}), 403


csrf = CSRFProtect(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

login_manager = LoginManager()
login_manager.login_view = "login_route"
login_manager.init_app(app)


class User(UserMixin):
    def __init__(self, user_id, username, role):
        self.id = user_id
        self.username = username
        self.role = role


@login_manager.user_loader
def load_user(user_id):
    try:
        conn = sqlite3.connect(db_manager.DB_PATH, timeout=30.0)
        row = conn.execute(
            "SELECT id, username, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        conn.close()
        if row:
            return User(row[0], row[1], row[2])
    except Exception as exc:
        print(f"[AUTH] load_user error: {exc}")
    return None


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    return redirect("/login")


@app.errorhandler(RateLimitExceeded)
def _ratelimit_handler(err):
    resp = jsonify({"status": "error", "message": "Too many requests. Retry later."})
    resp.status_code = 429
    resp.headers["Retry-After"] = str(getattr(err, "retry_after", 60))
    return resp


# Global CSRF token injection + security headers
@app.after_request
def _inject_csrf_and_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    response.headers["X-XSS-Protection"] = "1; mode=block"

    if response.content_type and "text/html" in response.content_type:
        token = generate_csrf()
        script = f"""
        <script>
        (function() {{
            const orig = window.fetch;
            window.fetch = function(url, options) {{
                options = options || {{}};
                options.headers = options.headers || {{}};
                if (!options.headers['X-CSRFToken']) {{
                    options.headers['X-CSRFToken'] = '{token}';
                }}
                return orig(url, options);
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


# ─────────────────────────────────────────────────────────────────────────────
# DB CONTEXT HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(db_manager.DB_PATH, timeout=30.0)
        db.execute("PRAGMA foreign_keys = ON;")
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def _close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per 10 minutes")
def login_route():
    if current_user.is_authenticated:
        return redirect("/")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = sqlite3.connect(db_manager.DB_PATH, timeout=30.0)
        row = conn.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()
        if row and bcrypt.checkpw(password.encode("utf-8"), row[2].encode("utf-8")):
            login_user(User(row[0], row[1], row[3]))
            return redirect("/")
        flash("Invalid username or password.")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout_route():
    logout_user()
    return redirect("/login")


# ─────────────────────────────────────────────────────────────────────────────
# PAGES
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/history")
@login_required
def history_page():
    return render_template("history.html")


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    if current_user.role != "admin":
        return "Access Denied: Admin role required", 403

    if request.method == "POST":
        try:
            db_manager.set_system_setting("auto_publish_timeout", request.form.get("auto_publish_timeout", "30"))
            db_manager.set_system_setting("primary_routing_domain", request.form.get("primary_routing_domain", "").strip())
            db_manager.set_operator_setting("postback_secret", request.form.get("postback_secret", "").strip())
            rates_raw = request.form.get("commission_rates", "{}")
            json.loads(rates_raw)  # validate
            db_manager.set_operator_setting("commission_rates", rates_raw)
            flash("Settings updated successfully!", "success")
        except Exception as exc:
            flash(f"Error updating settings: {exc}", "error")

    settings = {
        "auto_publish_timeout": db_manager.get_system_setting("auto_publish_timeout", "30"),
        "primary_routing_domain": db_manager.get_system_setting("primary_routing_domain", ""),
        "postback_secret": db_manager.get_postback_secret(),
        "commission_rates": db_manager.get_operator_setting("commission_rates", "{}"),
    }
    return render_template("settings.html", settings=settings)


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/health")
@app.route("/api/health")
@csrf.exempt
def health_check():
    health = {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "database": {}, "scheduler": {}}
    try:
        conn = sqlite3.connect(db_manager.DB_PATH, timeout=5.0)
        tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        conn.close()
        health["database"] = {"connected": True, "tables": tables}
    except Exception as exc:
        health["status"] = "degraded"
        health["database"] = {"connected": False, "error": str(exc)}
    try:
        from bots import scheduler_engine
        status = scheduler_engine.get_status()
        health["scheduler"] = {"active": status.get("scheduler_running", False), "jobs": len(status.get("jobs", []))}
    except Exception:
        health["scheduler"] = {"active": False, "jobs": 0}
    return jsonify(health), (200 if health["status"] == "ok" else 503)


# ─────────────────────────────────────────────────────────────────────────────
# TRACKED REDIRECT ROUTE  (/go/<product_id>)
# ─────────────────────────────────────────────────────────────────────────────
def is_safe_url(url):
    """Whitelist check against TRUSTED_DOMAINS (open-redirect protection)."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        netloc = parsed.netloc.lower()
        domain = netloc.split(":")[0]
        for trusted in TRUSTED_DOMAINS:
            trusted = trusted.strip().lower()
            if not trusted:
                continue
            if domain == trusted or domain.endswith("." + trusted):
                return True
        return False
    except Exception:
        return False


@app.route("/go/<product_id>")
@limiter.limit("60 per minute")
def track_click(product_id):
    import affiliate_tracker
    import ab_engine

    affiliate_url = request.args.get("url", "")
    title = html.escape(request.args.get("title", ""))
    sector = html.escape(request.args.get("sector", ""))

    # Open-redirect protection.
    if affiliate_url and not is_safe_url(affiliate_url):
        print(f"[GO] Blocked unsafe redirect target: {affiliate_url}")
        return jsonify({"status": "error", "message": "Unsafe redirect URL rejected."}), 400

    # A/B bandit picks which creative variant to attribute.
    variant = request.args.get("var", "")
    if variant not in ("A", "B"):
        selection = ab_engine.select_variant(product_id)
        variant = selection["variant"]

    click_id = affiliate_tracker.record_click(
        product_id=product_id,
        channel=request.args.get("channel", "direct"),
        user_agent=request.headers.get("User-Agent", ""),
        ip_address=request.remote_addr or "",
        session_id=request.remote_addr or "",
        variant=variant,
    )

    if affiliate_url:
        return redirect(affiliate_url)
    return jsonify({"status": "click_recorded", "click_id": click_id})


# ─────────────────────────────────────────────────────────────────────────────
# HMAC POSTBACK WEBHOOK  (/postback/conversion)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/postback/conversion", methods=["POST"])
@csrf.exempt
@limiter.limit("120 per minute")
def postback_conversion():
    signature = request.headers.get("X-Signature", "")
    if not signature:
        return jsonify({"status": "error", "message": "Missing X-Signature header"}), 401

    raw_payload = request.get_data()
    secret = db_manager.get_postback_secret()

    if not idempotency_verify(raw_payload, signature, secret):
        print("[POSTBACK] Forged/invalid HMAC rejected.")
        return jsonify({"status": "error", "message": "Invalid signature"}), 401

    try:
        data = request.json or {}
    except Exception:
        data = {}

    transaction_id = (data.get("transaction_id") or "").strip()
    if not transaction_id:
        return jsonify({"status": "error", "message": "transaction_id is required"}), 400

    # Idempotency guard — network retries never double-count.
    from bots.idempotency import check_and_mark
    if not check_and_mark(transaction_id, event_type="conversion"):
        return jsonify({"status": "success", "message": "Conversion already processed (idempotent)"})

    import affiliate_tracker
    try:
        affiliate_tracker.record_conversion(
            transaction_id=transaction_id,
            product_id=data.get("product_id", ""),
            session_id=data.get("session_id", ""),
            sale_amount=float(data.get("sale_amount", 0) or 0),
            commission_amount=float(data.get("commission_amount", 0) or 0),
        )
    except Exception as exc:
        from bots.idempotency import release
        release(transaction_id)
        return jsonify({"status": "error", "message": str(exc)}), 500

    return jsonify({"status": "success", "message": "Conversion recorded"})


def idempotency_verify(raw_payload, signature, secret):
    import hmac as _hmac
    import hashlib as _hashlib
    expected = _hmac.new(secret.encode("utf-8"), raw_payload, _hashlib.sha256).hexdigest()
    return _hmac.compare_digest(expected, signature.lower())


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD APIs
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/performance", methods=["GET"])
@login_required
def api_performance():
    try:
        import affiliate_tracker
        import ab_engine
        import revenue_ranker
        from bots import alert_engine

        stats = affiliate_tracker.get_click_stats()
        ab = ab_engine.get_experiment_results()
        ranking = revenue_ranker.rank_verticals()
        alerts = alert_engine.get_recent_alerts(limit=10)

        db = get_db()
        pending = db.execute(
            "SELECT COUNT(*) FROM campaigns WHERE status = 'pending_approval'"
        ).fetchone()[0]
        published = db.execute(
            "SELECT COUNT(*) FROM campaigns WHERE status = 'published'"
        ).fetchone()[0]

        return jsonify({
            "status": "success",
            "stats": stats,
            "ab": ab,
            "sector_ranking": ranking,
            "alerts": alerts,
            "campaigns": {"pending": pending, "published": published},
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/sectors", methods=["GET"])
@login_required
def api_sectors():
    from scrapers.product_scraper import SECTOR_CONFIG
    return jsonify([
        {"key": k, "display": v["display"]} for k, v in SECTOR_CONFIG.items()
    ])


@app.route("/api/run_pipeline", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def api_run_pipeline():
    try:
        from scrapers.product_scraper import fetch_active_campaigns, SECTOR_CONFIG
        from generators.ai_copywriter import generate_multilingual_copy
        from generators.video_script_engine import render_video_clip, generate_video_scripts

        data = request.json or {}
        sector = data.get("sector", "electronics")
        if sector not in SECTOR_CONFIG:
            return jsonify({"status": "error", "message": f"Invalid sector '{sector}'"}), 400

        products = fetch_active_campaigns(sector)
        saved = 0
        for product in products:
            product["sector"] = sector
            copies = generate_multilingual_copy(product)
            product["caption"] = copies.get("en", "")
            product["copy"] = copies
            script = generate_video_scripts(product)
            product["graphic_path"] = render_video_clip(product, script)
            db_manager.save_campaign(product, sector=sector)
            saved += 1

        return jsonify({
            "status": "success",
            "sector": sector,
            "campaigns_created": saved,
            "run_at": datetime.utcnow().isoformat(),
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/history", methods=["GET"])
@login_required
def api_history():
    try:
        sector = request.args.get("sector", "")
        status = request.args.get("status", "")
        limit = min(int(request.args.get("limit", 100)), 500)
        campaigns = db_manager.list_campaigns(status=status or None, sector=sector or None, limit=limit)

        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
        clicks = conn.execute("SELECT COUNT(*) FROM affiliate_clicks WHERE is_bot = 0").fetchone()[0]
        convs = conn.execute("SELECT COUNT(*) FROM affiliate_conversions WHERE status = 'converted'").fetchone()[0]
        sector_counts = [dict(r) for r in conn.execute(
            "SELECT sector, COUNT(*) as count FROM campaigns GROUP BY sector ORDER BY count DESC"
        ).fetchall()]

        return jsonify({
            "status": "success",
            "campaigns": campaigns,
            "stats": {"total": total, "clicks": clicks, "conversions": convs},
            "sector_counts": sector_counts,
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/clicks", methods=["GET"])
@login_required
def api_clicks():
    import affiliate_tracker
    try:
        return jsonify({"status": "success", **affiliate_tracker.get_click_stats()})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/ab", methods=["GET"])
@login_required
def api_ab():
    import ab_engine
    try:
        return jsonify({"status": "success", **ab_engine.get_experiment_results()})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/reliability_status", methods=["GET"])
@login_required
def api_reliability_status():
    try:
        from bots.quota_manager import get_all_quotas, get_all_breakers
        from bots.job_queue import get_queue_summary, get_dead_letter_jobs
        return jsonify({
            "status": "success",
            "quotas": get_all_quotas(),
            "breakers": get_all_breakers(),
            "queue": get_queue_summary(),
            "dead_letter_jobs": get_dead_letter_jobs(limit=20),
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/reliability_reset", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def api_reliability_reset():
    if current_user.role != "admin":
        return jsonify({"status": "error", "message": "Admin role required"}), 403
    try:
        from bots.quota_manager import reset_breaker, reset_quota
        from bots.job_queue import requeue_dead_job, purge_queue

        data = request.json or {}
        target = data.get("target")
        provider = data.get("provider")
        job_id = data.get("job_id")

        if target == "breaker" and provider:
            reset_breaker(provider)
            return jsonify({"status": "success", "message": f"Breaker '{provider}' reset."})
        if target == "quota" and provider:
            reset_quota(provider)
            return jsonify({"status": "success", "message": f"Quota '{provider}' reset."})
        if target == "requeue_dead" and job_id:
            ok = requeue_dead_job(int(job_id))
            return jsonify({"status": "success" if ok else "error", "message": "Requeued." if ok else "Job not found."}), (200 if ok else 404)
        if target == "purge_queue":
            purge_queue()
            return jsonify({"status": "success", "message": "Queues purged."})
        return jsonify({"status": "error", "message": "Invalid target."}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/scheduler_status", methods=["GET"])
@login_required
def api_scheduler_status():
    try:
        from bots import scheduler_engine
        return jsonify({"status": "success", **scheduler_engine.get_status()})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/scheduler_run_now", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def api_scheduler_run_now():
    if current_user.role != "admin":
        return jsonify({"status": "error", "message": "Admin role required"}), 403
    try:
        from bots import scheduler_engine
        data = request.json or {}
        job_id = data.get("job_id", "")
        if not job_id:
            return jsonify({"status": "error", "message": "job_id required"}), 400
        return jsonify(scheduler_engine.trigger_now(job_id))
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/review_dead_letter", methods=["GET"])
@login_required
def api_review_dead_letter():
    try:
        from bots.job_queue import get_dead_letter_jobs
        jobs = get_dead_letter_jobs(limit=50)
        return jsonify({"status": "success", "jobs": jobs, "count": len(jobs)})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/campaign/<int:campaign_id>/approve", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def api_approve_campaign(campaign_id):
    """Approve a pending campaign and distribute it live."""
    try:
        from bots.distributor import distribute_campaign
        ok = distribute_campaign(campaign_id)
        return jsonify({
            "status": "success" if ok else "error",
            "message": "Campaign approved and distributed." if ok else "Distribution failed.",
        }), (200 if ok else 500)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/campaign/<int:campaign_id>/reject", methods=["POST"])
@login_required
def api_reject_campaign(campaign_id):
    try:
        db_manager.update_campaign_status(campaign_id, "rejected")
        return jsonify({"status": "success", "message": "Campaign rejected."})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA SERVING
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/image/<path:filename>")
def serve_image(filename):
    from flask import send_from_directory
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/static/campaigns/<path:filename>")
def serve_campaign_asset(filename):
    from config import CAMPAIGN_STATIC_DIR
    from flask import send_from_directory
    return send_from_directory(CAMPAIGN_STATIC_DIR, filename)


@app.route("/api/background/status")
@login_required
def api_background_status():
    themes = background_factory.list_themes()
    active = background_factory.get_background_for_ui().get("theme", "train")
    return jsonify({"status": "success", "themes": themes, "active": active})


@app.route("/api/background/<theme>", methods=["POST"])
@login_required
def api_background_set(theme):
    if theme not in background_factory.THEMES:
        return jsonify({"status": "error", "message": f"Unknown theme: {theme}"}), 400
    try:
        background_factory.set_active_theme(theme)
        background_factory.render_poster(theme)
        return jsonify({
            "status": "success",
            "message": f"Ambient theme switched to '{theme}'.",
            "active": theme,
            "poster_url": f"/static/backgrounds/{theme}.jpg",
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────────────────────
def _seed():
    db_manager.setup_database()
    db_manager.seed_admin_user(username="admin", password=config.ADMIN_DEFAULT_PASSWORD)
    try:
        background_factory.ensure_backgrounds()
    except Exception as exc:
        print(f"[STARTUP] Ambient background generation failed: {exc}")


def _start_scheduler():
    import os as _os
    if app.debug and not _os.environ.get("WERKZEUG_RUN_MAIN"):
        return
    from bots import scheduler_engine
    try:
        scheduler_engine.start(app)
    except Exception as exc:
        print(f"[STARTUP] Scheduler failed to start: {exc}")


_seed()
_start_scheduler()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
