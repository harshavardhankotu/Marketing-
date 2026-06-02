from flask import Flask, render_template, jsonify, send_from_directory, request, redirect, g, flash
import sqlite3
import subprocess
import os
import json
import sys
import time
from datetime import datetime
import hmac
import hashlib

# Import Flask-Login, Flask-WTF, Flask-Limiter, and bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded
import bcrypt
from dotenv import load_dotenv

# Initialize environment variables at startup
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'a_very_secret_key_for_session_signing_987654')


@app.before_request
def check_api_auth_before_csrf():
    admin_only_mutating = [
        '/api/reliability_reset',
        '/api/scheduler_config',
        '/api/scheduler_run_now',
        '/api/review_dead_letter'
    ]
    # Check unauthenticated for all API paths
    if request.path.startswith('/api/'):
        if not current_user.is_authenticated:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
    
    # Check admin role for administrative mutating endpoints
    if request.path in admin_only_mutating and request.method == 'POST':
        if not current_user.is_authenticated:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        if current_user.role != 'admin':
            return jsonify({"status": "error", "message": "Access Denied: Admin role required"}), 403

# Enable global CSRF protection
csrf = CSRFProtect(app)

# Enable Rate Limiting
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://"
)

# Enable Login Manager
login_manager = LoginManager()
login_manager.login_view = 'login_route'
login_manager.init_app(app)

# User representation for Flask-Login
class User(UserMixin):
    def __init__(self, id, email, role):
        self.id = id
        self.email = email
        self.role = role

# Load user from SQLite database
@login_manager.user_loader
def load_user(user_id):
    try:
        import db_manager
        conn = sqlite3.connect(db_manager.DB_PATH, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("SELECT id, email, role FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return User(id=row[0], email=row[1], role=row[2])
    except Exception as e:
        print(f"[SRE_AUTH] Error loading user: {e}")
    return None

# Custom handler for Flask-Login unauthorized events
@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith('/api/'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    return redirect('/login')

# Custom error handler for RateLimitExceeded
@app.errorhandler(RateLimitExceeded)
def ratelimit_handler(e):
    response = jsonify({
        "status": "error",
        "message": f"Too many requests. Stricter limits applied: {e.description}"
    })
    response.status_code = 429
    retry_after = getattr(e, 'retry_after', 60)
    response.headers["Retry-After"] = str(retry_after)
    return response

# Global response filter to automatically inject CSRF token into fetch calls in the browser
@app.after_request
def inject_csrf_token(response):
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
                data = data.replace("<head>", f"<head>{script}", 1)
                response.set_data(data)
        except Exception:
            pass
    return response

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        import db_manager
        # Ensure SQLite foreign key enforcement is applied on every fresh connection context
        db = g._database = sqlite3.connect(db_manager.DB_PATH, timeout=30.0)
        db.execute("PRAGMA foreign_keys = ON;")
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# Add module paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bots'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scrapers'))

import db_manager
from config import OUTPUT_DIR

# Ensure directories exist
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs('assets', exist_ok=True)

import retargeting_engine
import pipeline_service
import scheduler_engine

# Seed users on startup (admin@marketing.ai & guest@marketing.ai)
def seed_users():
    db_manager.setup_database()
    conn = sqlite3.connect(db_manager.DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    
    # Pre-seed admin@marketing.ai (role: admin, pwd: ADMIN_DEFAULT_PASSWORD from env)
    cursor.execute("SELECT 1 FROM users WHERE email = ?", ("admin@marketing.ai",))
    if not cursor.fetchone():
        default_pwd = os.getenv("ADMIN_DEFAULT_PASSWORD", "admin123")
        pwd_hash = bcrypt.hashpw(default_pwd.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cursor.execute("INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
                       ("admin@marketing.ai", pwd_hash, "admin"))
                       
    # Pre-seed guest@marketing.ai (role: guest, pwd: guest123)
    cursor.execute("SELECT 1 FROM users WHERE email = ?", ("guest@marketing.ai",))
    if not cursor.fetchone():
        pwd_hash = bcrypt.hashpw("guest123".encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cursor.execute("INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
                       ("guest@marketing.ai", pwd_hash, "guest"))
                       
    conn.commit()
    conn.close()
    print("[SRE_STARTUP] Pre-seeded system users.")

# Helper to fetch postback signature secret
def get_postback_secret():
    conn = None
    try:
        conn = sqlite3.connect(db_manager.DB_PATH, timeout=5.0)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM operator_settings WHERE key = 'postback_secret'")
        row = cursor.fetchone()
        if row:
            return row[0]
    except Exception:
        pass
    finally:
        if conn:
            conn.close()
    return os.getenv('POSTBACK_SECRET', 'default_secret_key_123')

# ═══════════════════════════════════════════════════════════════════════
# AUTHENTICATION ROUTES
# ═══════════════════════════════════════════════════════════════════════

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per 10 minutes")
def login_route():
    if current_user.is_authenticated:
        return redirect('/')
        
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = sqlite3.connect(db_manager.DB_PATH, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("SELECT id, email, password_hash, role FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            stored_hash = row[2]
            if bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8')):
                user = User(id=row[0], email=row[1], role=row[3])
                login_user(user)
                return redirect('/')
                
        flash("Invalid email or password.")
        
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout_route():
    logout_user()
    return redirect('/login')

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings_page():
    if current_user.role != 'admin':
        return "Access Denied: Admin role required", 403
        
    conn = sqlite3.connect(db_manager.DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if request.method == 'POST':
        rates_raw = request.form.get('commission_rates')
        secret_raw = request.form.get('postback_secret')
        amazon_tag = request.form.get('amazon_tag', 'marketingai-21')
        flipkart_tag = request.form.get('flipkart_tag', 'marketingai')
        auto_publish_timeout = request.form.get('auto_publish_timeout', '30')
        cpa_network_id = request.form.get('cpa_network_id', '').strip()
        primary_routing_domain = request.form.get('primary_routing_domain', '').strip()
        
        # Build active sectors mapping from form checkboxes
        from product_scraper import SECTOR_CONFIG
        sectors_dict = {}
        for s in SECTOR_CONFIG.keys():
            sectors_dict[s] = (request.form.get(f"sector_{s}") == "on")
        active_sectors_json = json.dumps(sectors_dict)
        
        if not cpa_network_id:
            flash("Error: CPA Network ID is a required field.", "error")
        elif not primary_routing_domain:
            flash("Error: Primary Routing Domain is a required field.", "error")
        else:
            try:
                json.loads(rates_raw)
                # Save operator settings
                cursor.execute("INSERT OR REPLACE INTO operator_settings (key, value) VALUES ('commission_rates', ?)", (rates_raw,))
                cursor.execute("INSERT OR REPLACE INTO operator_settings (key, value) VALUES ('postback_secret', ?)", (secret_raw,))
                
                # Save system settings
                cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('amazon_tag', ?)", (amazon_tag,))
                cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('flipkart_tag', ?)", (flipkart_tag,))
                cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('auto_publish_timeout', ?)", (auto_publish_timeout,))
                cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('cpa_network_id', ?)", (cpa_network_id,))
                cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('primary_routing_domain', ?)", (primary_routing_domain,))
                cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('active_sectors', ?)", (active_sectors_json,))
                
                conn.commit()
                flash("Settings updated successfully!", "success")
            except Exception as e:
                flash(f"Error updating settings: {e}", "error")
            
    # Load current values
    cursor.execute("SELECT value FROM operator_settings WHERE key = 'commission_rates'")
    rates_row = cursor.fetchone()
    commission_rates = rates_row["value"] if rates_row else "{}"
    
    cursor.execute("SELECT value FROM operator_settings WHERE key = 'postback_secret'")
    secret_row = cursor.fetchone()
    postback_secret = secret_row["value"] if secret_row else os.getenv('POSTBACK_SECRET', 'default_secret_key_123')
    
    # Load system settings
    cursor.execute("SELECT value FROM system_settings WHERE key = 'amazon_tag'")
    amazon_row = cursor.fetchone()
    amazon_tag = amazon_row["value"] if amazon_row else "marketingai-21"
    
    cursor.execute("SELECT value FROM system_settings WHERE key = 'flipkart_tag'")
    flipkart_row = cursor.fetchone()
    flipkart_tag = flipkart_row["value"] if flipkart_row else "marketingai"
    
    cursor.execute("SELECT value FROM system_settings WHERE key = 'auto_publish_timeout'")
    timeout_row = cursor.fetchone()
    auto_publish_timeout = timeout_row["value"] if timeout_row else "30"
    
    cursor.execute("SELECT value FROM system_settings WHERE key = 'cpa_network_id'")
    cpa_row = cursor.fetchone()
    cpa_network_id = cpa_row["value"] if cpa_row else "cpa_lead_net_9876"
    
    cursor.execute("SELECT value FROM system_settings WHERE key = 'primary_routing_domain'")
    domain_row = cursor.fetchone()
    primary_routing_domain = domain_row["value"] if domain_row else "https://offers.cpa-arbitrage.com"
    
    cursor.execute("SELECT value FROM system_settings WHERE key = 'active_sectors'")
    sectors_row = cursor.fetchone()
    active_sectors_json = sectors_row["value"] if sectors_row else "{}"
    try:
        active_sectors = json.loads(active_sectors_json)
    except Exception:
        active_sectors = {}
        
    conn.close()
    
    from product_scraper import SECTOR_CONFIG
    sectors_list = [{"key": k, "display": v["display"], "active": active_sectors.get(k, True)} for k, v in SECTOR_CONFIG.items()]
    
    return render_template(
        'settings.html', 
        commission_rates=commission_rates, 
        postback_secret=postback_secret,
        amazon_tag=amazon_tag,
        flipkart_tag=flipkart_tag,
        auto_publish_timeout=auto_publish_timeout,
        cpa_network_id=cpa_network_id,
        primary_routing_domain=primary_routing_domain,
        sectors_list=sectors_list
    )

# ═══════════════════════════════════════════════════════════════════════
# PAGES
# ═══════════════════════════════════════════════════════════════════════

@app.route('/')
@login_required
def index():
    return render_template('index.html')


@app.route('/history')
@login_required
def history_page():
    return render_template('history.html')


# ═══════════════════════════════════════════════════════════════════════
# SECTORS
# ═══════════════════════════════════════════════════════════════════════

@app.route('/api/sectors', methods=['GET'])
@login_required
def get_sectors():
    """Return available sectors for the frontend dropdown."""
    from product_scraper import SECTOR_CONFIG
    sectors = [
        {"key": k, "display": v["display"]}
        for k, v in SECTOR_CONFIG.items()
    ]
    return jsonify(sectors)


# ═══════════════════════════════════════════════════════════════════════
# SINGLE SECTOR ENDPOINT
# ═══════════════════════════════════════════════════════════════════════

@app.route('/api/run_pipeline', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def run_pipeline():
    try:
        data = request.json or {}
        sector = data.get('sector', 'auto_insurance')
        dry_run = data.get('dry_run', False)

        if dry_run:
            return jsonify({
                "status":         "success",
                "data":           [],
                "sector":         sector,
                "sector_display": sector,
                "market_analysis": {},
                "segment_summary": {},
                "pipeline_time":  0.0,
                "run_at":         datetime.utcnow().isoformat(),
            })

        result = pipeline_service._run_single_sector(sector)

        return jsonify({
            "status":         "success",
            "data":           result["data"],
            "sector":         result["sector"],
            "sector_display": result["sector_display"],
            "market_analysis": result["market_analysis"],
            "segment_summary": result["segment_summary"],
            "pipeline_time":  result["pipeline_time"],
            "run_at":         datetime.utcnow().isoformat(),
        })

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode('utf-8') if e.stderr else str(e)
        print(f"Subprocess failed: {error_msg}")
        return jsonify({"status": "error", "message": f"Pipeline step failed: {error_msg}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# BATCH (RUN ALL SECTORS) ENDPOINT
# ═══════════════════════════════════════════════════════════════════════

@app.route('/api/run_all', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def run_all_sectors():
    """Run the full 14-step pipeline for every sector sequentially."""
    try:
        result = pipeline_service.run_all_sectors_internal()
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# CAMPAIGN HISTORY API
# ═══════════════════════════════════════════════════════════════════════

@app.route('/api/history', methods=['GET'])
@login_required
def get_campaign_history():
    """Return campaign history from SQLite for the history dashboard."""
    try:
        db_manager.setup_database()
        conn = get_db()
        cursor = conn.cursor()

        sector = request.args.get('sector', '')
        limit  = int(request.args.get('limit', 100))

        query  = "SELECT * FROM campaigns ORDER BY created_at DESC"
        params = []
        if sector:
            query  = "SELECT * FROM campaigns WHERE sector = ? ORDER BY created_at DESC"
            params = [sector]
        query += f" LIMIT {limit}"

        cursor.execute(query, params)
        campaigns = [dict(r) for r in cursor.fetchall()]

        # Sync clicks counted in affiliate_clicks with campaigns rows
        for c in campaigns:
            pid = c.get("product_id")
            link = c.get("affiliate_link")
            cursor.execute("SELECT COUNT(*) as actual_clicks FROM affiliate_clicks WHERE product_id = ? OR affiliate_link = ?", (pid, link))
            click_row = cursor.fetchone()
            c["total_clicks"] = click_row["actual_clicks"] if click_row else 0

        # Sync and filter general statistics by sector if provided
        if sector:
            cursor.execute("SELECT COUNT(*) as total, SUM(total_views) as views FROM campaigns WHERE sector = ?", (sector,))
            camp_row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) as clicks FROM affiliate_clicks WHERE sector = ?", (sector,))
            clicks_row = cursor.fetchone()
        else:
            cursor.execute("SELECT COUNT(*) as total, SUM(total_views) as views FROM campaigns")
            camp_row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) as clicks FROM affiliate_clicks")
            clicks_row = cursor.fetchone()
        
        stats = {
            "total": camp_row["total"] if camp_row else 0,
            "views": (camp_row["views"] if camp_row and camp_row["views"] else 0),
            "clicks": (clicks_row["clicks"] if clicks_row and clicks_row["clicks"] else 0)
        }

        cursor.execute("SELECT sector, COUNT(*) as count FROM campaigns GROUP BY sector ORDER BY count DESC")
        sector_counts = [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT DISTINCT created_at FROM campaigns ORDER BY created_at DESC LIMIT 10")
        recent_runs = [r['created_at'] for r in cursor.fetchall()]

        conn.close()

        return jsonify({
            "status":        "success",
            "campaigns":     campaigns,
            "stats":         stats,
            "sector_counts": sector_counts,
            "recent_runs":   recent_runs,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# Whitelist of trusted domains for affiliate redirects (Open Redirect prevention)
TRUSTED_DOMAINS = [
    "amazon.in",
    "amazon.com",
    "flipkart.com",
    "myntra.com",
    "ajio.com",
    "thedermaco.com",
    "dotandkey.com",
    "mcaffeine.com",
    "api.mock-affiliate-network.com",
    "fktr.in",
    "ajiio.in",
    "myntr.it",
    "bitli.in",
    "linkredirect.in",
    "onboarding.kotak.bank.in",
    "kotak.com",
    "offers.cpa-arbitrage.com",
    "cpa-arbitrage.com",
    "offers.cpa-lead-network.com"
]

def is_safe_url(url):
    if not url:
        return False
    # Dynamic click-to-call safety check
    if url.startswith("tel:"):
        import re
        return bool(re.match(r"^tel:\+?[0-9\-]+$", url))
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False
        netloc = parsed.netloc.lower()
        domain = netloc.split(':')[0]
        
        for trusted in TRUSTED_DOMAINS:
            if domain == trusted or domain.endswith("." + trusted):
                return True
        return False
    except Exception:
        return False

@app.route('/go/<product_id>')
@app.route('/go/<product_id>/<channel>')
@limiter.limit("60 per minute")
def track_click(product_id, channel="direct"):
    """Redirect through tracking layer, then send user to affiliate link."""
    import affiliate_tracker
    import html

    affiliate_link = request.args.get('url', '')
    title          = html.escape(request.args.get('title', ''))
    sector         = html.escape(request.args.get('sector', ''))
    score          = float(request.args.get('score', 0))
    commission     = float(request.args.get('commission', 0))

    # Secure Open Redirect check
    if affiliate_link and not is_safe_url(affiliate_link):
        print(f"SECURITY WARNING: Prevented open redirect attempt to: {affiliate_link}")
        return jsonify({"status": "error", "message": "Unsafe redirect URL rejected."}), 400

    variant = request.args.get('var') or request.args.get('variant') or ""
    if variant not in ('A', 'B'):
        variant = ""

    click_id = affiliate_tracker.record_click(
        product_id=product_id,
        product_title=title,
        sector=sector,
        channel=channel,
        affiliate_link=affiliate_link,
        user_agent=request.headers.get('User-Agent', ''),
        referrer=request.headers.get('Referer', ''),
        session_id=request.remote_addr,
        revenue_score=score,
        est_commission_pct=commission,
        variant=variant,
    )

    # Record A/B click if variant is specified
    variant = request.args.get('var') or request.args.get('variant')
    if variant in ('A', 'B'):
        try:
            import ab_engine
            ab_engine.record_ab_click(product_id, variant)
        except Exception as e:
            print(f"[A/B ENGINE] Error recording A/B click: {e}")

    if affiliate_link:
        return redirect(affiliate_link)
    return jsonify({"status": "click_recorded", "click_id": click_id})


# ═══════════════════════════════════════════════════════════════════════
# CLICK ANALYTICS API
# ═══════════════════════════════════════════════════════════════════════

@app.route('/api/clicks', methods=['GET'])
@login_required
def get_click_analytics():
    """Return affiliate click/conversion analytics."""
    try:
        import affiliate_tracker
        stats = affiliate_tracker.get_click_stats()
        return jsonify({"status": "success", **stats})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/alerts', methods=['GET'])
@login_required
def get_alerts():
    """Return recent price-drop / restock alerts."""
    try:
        import alert_engine
        alerts = alert_engine.get_recent_alerts()
        return jsonify({"status": "success", "alerts": alerts, "total": len(alerts)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/ab', methods=['GET'])
@login_required
def get_ab_results():
    """Return A/B experiment results."""
    try:
        import ab_engine
        results = ab_engine.get_experiment_results()
        return jsonify({"status": "success", **results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# AGENCY POLISH & PREVIEW GATE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════

@app.route('/api/campaigns/pending', methods=['GET'])
@login_required
def get_pending_campaigns():
    try:
        db_manager.setup_database()
        conn = get_db()
        cursor = conn.cursor()
        
        # Get all campaigns with pending_approval status
        cursor.execute("SELECT * FROM campaigns WHERE status = 'pending_approval' ORDER BY created_at DESC")
        campaigns = [dict(r) for r in cursor.fetchall()]
        conn.close()
        
        return jsonify({"status": "success", "campaigns": campaigns})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/campaign/<int:campaign_id>/approve', methods=['POST'])
@login_required
def approve_campaign(campaign_id):
    try:
        import distributor
        success = distributor.distribute_campaign(campaign_id)
        if success:
            return jsonify({"status": "success", "message": "Campaign approved and distributed live."})
        else:
            return jsonify({"status": "error", "message": "Failed to distribute campaign."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/campaign/<int:campaign_id>/reject', methods=['POST'])
@login_required
def reject_campaign(campaign_id):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE campaigns SET status = 'rejected' WHERE id = ?", (campaign_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Campaign rejected."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/export/reports', methods=['GET'])
@login_required
def export_reports():
    import csv
    import io
    from flask import Response
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Query to join campaigns, clicks, and conversions
        cursor.execute('''
            SELECT 
                c.id AS campaign_id,
                c.title AS product_title,
                c.sector AS sector,
                COALESCE(clk.variant, 'N/A') AS variant_used,
                COUNT(clk.id) AS total_clicks,
                SUM(CASE WHEN conv.status = 'converted' THEN 1 ELSE 0 END) AS verified_conversions,
                SUM(CASE WHEN conv.status = 'converted' THEN conv.commission_amount ELSE 0.0 END) AS total_revenue
            FROM campaigns c
            LEFT JOIN affiliate_clicks clk ON c.product_id = clk.product_id AND clk.is_bot = 0
            LEFT JOIN affiliate_conversions conv ON clk.id = conv.click_id
            GROUP BY c.id, clk.variant
            ORDER BY c.id DESC, clk.variant ASC
        ''')
        rows = cursor.fetchall()
        
        # Generate CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write headers
        writer.writerow([
            "Campaign ID", 
            "Product Title", 
            "Sector", 
            "Variant Used", 
            "Total Human Clicks", 
            "Verified Conversions", 
            "Total Revenue (Rs)"
        ])
        
        # Write data rows
        for row in rows:
            writer.writerow([
                row["campaign_id"],
                row["product_title"],
                row["sector"],
                row["variant_used"],
                row["total_clicks"],
                row["verified_conversions"],
                round(row["total_revenue"], 2)
            ])
            
        csv_data = output.getvalue()
        output.close()
        
        filename = f"marketing_report_{datetime.now().strftime('%Y%m%d')}.csv"
        
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        print(f"[EXPORT REPORT] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# RETARGETING
# ═══════════════════════════════════════════════════════════════════════

@app.route('/api/run_retargeting', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def run_retargeting():
    """Run the retargeting engine (shared service layer)."""
    try:
        plans = pipeline_service.run_retargeting_internal()
        return jsonify({
            "status":  "success",
            "message": f"Generated {len(plans)} retargeting campaigns.",
            "plans":   plans,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/retargeting_stats', methods=['GET'])
@login_required
def get_retargeting_stats():
    """Return stats from the retargeting engine."""
    try:
        stats = retargeting_engine.get_retargeting_stats()
        return jsonify({"status": "success", **stats})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# SCHEDULER API
# ═══════════════════════════════════════════════════════════════════════

@app.route('/api/scheduler_status', methods=['GET'])
@login_required
def get_scheduler_status():
    """Return current status of all scheduled jobs."""
    try:
        status = scheduler_engine.get_status()
        return jsonify({"status": "success", **status})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/scheduler_run_now', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def scheduler_run_now():
    """Manually trigger a named job immediately."""
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Access Denied: Admin role required"}), 403
    try:
        data   = request.json or {}
        job_id = data.get('job_id', '')
        if not job_id:
            return jsonify({"status": "error", "message": "job_id required"}), 400
        result = scheduler_engine.trigger_now(job_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/scheduler_config', methods=['GET', 'POST'])
@login_required
@limiter.limit("30 per minute")
def scheduler_config():
    """GET current job config. POST to enable/disable a job."""
    try:
        if request.method == 'GET':
            status = scheduler_engine.get_status()
            return jsonify({"status": "success", "jobs": status["jobs"]})

        # POST — enable or disable a job (mutating)
        if current_user.role != 'admin':
            return jsonify({"status": "error", "message": "Access Denied: Admin role required"}), 403
        data    = request.json or {}
        job_id  = data.get('job_id', '')
        enabled = bool(data.get('enabled', True))
        if not job_id:
            return jsonify({"status": "error", "message": "job_id required"}), 400
        result = scheduler_engine.set_job_enabled(job_id, enabled)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# IMAGE SERVING
# ═══════════════════════════════════════════════════════════════════════

@app.route('/image/<path:filename>')
def serve_image(filename):
    return send_from_directory(OUTPUT_DIR, filename)


# ═══════════════════════════════════════════════════════════════════════
# NEWSLETTER SERVING
# ═══════════════════════════════════════════════════════════════════════

@app.route('/newsletter')
@login_required
def serve_newsletter():
    """Serve the generated email newsletter preview."""
    try:
        return send_from_directory(OUTPUT_DIR, 'newsletter.html')
    except Exception as e:
        return f"<h3>📧 Newsletter Preview Not Found</h3><p>Please run the pipeline for any sector to generate the newsletter first.</p>", 404


# ═══════════════════════════════════════════════════════════════════════
# SRE RELIABILITY & AUTONOMOUS CONTROL LAYER APIs
# ═══════════════════════════════════════════════════════════════════════

@app.route('/api/reliability_status', methods=['GET'])
@login_required
def get_reliability_status():
    """Return circuit breaker states, daily quota usage, and job queue status."""
    try:
        from bots.quota_manager import get_all_quotas
        from bots.job_queue import get_queue_summary
        
        db_manager.setup_database()
        conn = get_db()
        cursor = conn.cursor()
        
        # Pull circuit breakers
        cursor.execute("SELECT * FROM circuit_breaker_state ORDER BY provider")
        breakers = [dict(r) for r in cursor.fetchall()]
        
        # Pull dead letter jobs specifically for details
        cursor.execute("SELECT * FROM dead_letter_jobs ORDER BY failed_at DESC LIMIT 20")
        dead_jobs = [dict(r) for r in cursor.fetchall()]
        
        # Pull active queue details
        cursor.execute("SELECT * FROM job_queue WHERE state IN ('pending', 'running', 'failed') ORDER BY created_at DESC LIMIT 20")
        active_jobs = [dict(r) for r in cursor.fetchall()]
        
        conn.close()
        
        quotas = get_all_quotas()
        queue = get_queue_summary()
        
        # Assess overall degraded mode status
        degraded_mode = False
        for b in breakers:
            if b.get("state") == "OPEN":
                degraded_mode = True
        for q_name, q_val in quotas.items():
            if q_val.get("status") == "BLOCKED":
                degraded_mode = True
                
        return jsonify({
            "status": "success",
            "breakers": breakers,
            "quotas": quotas,
            "queue": queue,
            "active_jobs": active_jobs,
            "dead_jobs": dead_jobs,
            "degraded_mode": degraded_mode
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/reliability_reset', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def reset_reliability():
    """Allows manual operator reset of circuit breakers, quotas, or job requeues. Requires admin role."""
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Access Denied: Admin role required"}), 403
        
    try:
        data = request.json or {}
        target = data.get("target")  # "breaker", "quota", "requeue_dead", "purge_queue"
        provider = data.get("provider")
        job_id = data.get("job_id")
        
        from bots.config import DB_PATH
        
        if target == "breaker" and provider:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO circuit_breaker_state (provider, state, failure_count, success_count) "
                "VALUES (?, 'CLOSED', 0, 0) "
                "ON CONFLICT(provider) DO UPDATE SET state = 'CLOSED', failure_count = 0, success_count = 0",
                (provider,)
            )
            conn.commit()
            conn.close()
            print(f"[SRE] Circuit breaker '{provider}' manually reset to CLOSED.")
            return jsonify({"status": "success", "message": f"Circuit breaker '{provider}' reset to CLOSED."})
            
        elif target == "quota" and provider:
            from bots.quota_manager import reset_quota
            reset_quota(provider)
            print(f"[SRE] Quota for '{provider}' manually reset to 0.")
            return jsonify({"status": "success", "message": f"Quota for '{provider}' reset to 0."})
            
        elif target == "requeue_dead" and job_id:
            from bots.job_queue import requeue_dead_job
            requeue_dead_job(job_id)
            return jsonify({"status": "success", "message": f"Job '{job_id}' successfully requeued."})
            
        elif target == "purge_queue":
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM job_queue")
            cursor.execute("DELETE FROM dead_letter_jobs")
            conn.commit()
            conn.close()
            print("[SRE] Job queue and Dead Letter tables successfully purged.")
            return jsonify({"status": "success", "message": "Job queue and dead letter storage purged."})
            
        return jsonify({"status": "error", "message": "Invalid parameters or reset targets."}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/queue_job', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def queue_job_route():
    """Allows UI or operators to enqueue background pipeline / retargeting jobs."""
    try:
        data = request.json or {}
        task_name = data.get("task_name")
        payload = data.get("payload", {})
        
        if not task_name:
            return jsonify({"status": "error", "message": "task_name required"}), 400
            
        from bots.job_queue import enqueue_job
        job_id = enqueue_job(task_name, payload)
        return jsonify({"status": "success", "job_id": job_id, "message": f"Task '{task_name}' successfully enqueued."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/review_dead_letter', methods=['GET', 'POST'])
@login_required
@limiter.limit("30 per minute")
def review_dead_letter_route():
    """Returns dead letter jobs or performs manual rerun/deletion operations."""
    try:
        from bots.job_queue import get_dead_letter_jobs, rerun_dead_job_manual, delete_dead_job_manual
        
        if request.method == 'GET':
            jobs = get_dead_letter_jobs()
            return jsonify({
                "status": "success",
                "dead_letter_jobs": jobs,
                "count": len(jobs)
            })
            
        elif request.method == 'POST':
            if current_user.role != 'admin':
                return jsonify({"status": "error", "message": "Access Denied: Admin role required"}), 403
            # Manual rate limiting check
            # Since scheduler_config shares routes, we can just rate limit the mutating actions
            data = request.json or {}
            job_id = data.get("job_id")
            action = data.get("action")  # "rerun" or "delete"
            
            if not job_id or not action:
                return jsonify({"status": "error", "message": "job_id and action ('rerun' or 'delete') are required"}), 400
                
            if action == 'rerun':
                success = rerun_dead_job_manual(job_id)
                if success:
                    return jsonify({"status": "success", "message": f"Job '{job_id}' successfully marked as reprocessed and enqueued for rerun."})
                else:
                    return jsonify({"status": "error", "message": f"Failed to manually rerun job '{job_id}' or job not found."}), 404
                    
            elif action == 'delete':
                success = delete_dead_job_manual(job_id)
                if success:
                    return jsonify({"status": "success", "message": f"Job '{job_id}' successfully marked as reprocessed and removed from dead letter queue."})
                else:
                    return jsonify({"status": "error", "message": f"Failed to delete job '{job_id}' or job not found."}), 404
                    
            else:
                return jsonify({"status": "error", "message": f"Invalid action '{action}'. Supported actions are 'rerun' and 'delete'."}), 400
                
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/wallet', methods=['GET'])
@login_required
@limiter.limit("30 per minute")
def wallet_status():
    """Returns the current user wallet balance and recent payouts."""
    from bots import payout_gateway
    balance = payout_gateway.get_wallet_balance(current_user.email)
    payouts = payout_gateway.get_payout_history(current_user.email)
    return jsonify({
        "status": "success",
        "user_id": current_user.email,
        "balance": balance,
        "payouts": payouts
    })


@app.route('/api/payout', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def process_payout():
    """Processes a secure instant UPI cashback payout."""
    try:
        from bots import payout_gateway
        data = request.json or {}
        upi_id = data.get("upi_id", "").strip()
        amount = float(data.get("amount") or 0)
        
        if not upi_id:
            return jsonify({"status": "error", "message": "UPI ID is required"}), 400
        if amount <= 0:
            return jsonify({"status": "error", "message": "Amount must be greater than zero"}), 400
            
        res = payout_gateway.process_upi_payout(current_user.email, upi_id, amount)
        return jsonify(res)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC WEBHOOK CONVERSION WEBHOOK (HMAC Protected, CSRF Exempt)
# ═══════════════════════════════════════════════════════════════════════

@app.route('/postback/cpa_lead', methods=['POST'])
@csrf.exempt
def postback_cpa_lead():
    # 1. Validate the postback signature first.
    signature = request.headers.get('X-Signature') or request.args.get('signature')
    if not signature:
        return jsonify({"status": "error", "message": "Missing X-Signature"}), 403
        
    raw_payload = request.get_data()
    secret = get_postback_secret()
    expected_sig = hmac.new(secret.encode('utf-8'), raw_payload, hashlib.sha256).hexdigest()
    
    if not hmac.compare_digest(expected_sig, signature):
        return jsonify({"status": "error", "message": "Invalid HMAC signature"}), 403
        
    try:
        data = request.json or {}
        product_id = data.get("product_id")
        click_id = data.get("click_id")
        session_id = data.get("session_id")
        sale_amount = float(data.get("sale_amount") or data.get("order_value") or 0.0)
        commission_amount = float(data.get("commission_amount") or data.get("commission_value") or 0.0)
        transaction_id = data.get("transaction_id")
        network_name = data.get("network_name") or "mock_network"
        
        # Telephony fields
        phone_val = data.get("phone_number") or data.get("tracking_number") or data.get("phone")
        pin_val = data.get("extension_pin") or data.get("pin") or data.get("extension")
        
        if not transaction_id:
            return jsonify({"status": "error", "message": "transaction_id is required"}), 400

        # Inbound Telephony Session Mapping
        if not click_id:
            conn_phone = sqlite3.connect(db_manager.DB_PATH, timeout=5.0)
            cursor_phone = conn_phone.cursor()
            row_phone = None
            if phone_val:
                cursor_phone.execute("""
                SELECT assigned_campaign_id, assigned_product_id 
                FROM cpa_phone_pool 
                WHERE (tracking_number = ? OR tracking_number LIKE ?) AND status = 'allocated'
                """, (phone_val, f"%{phone_val}%"))
                row_phone = cursor_phone.fetchone()
                
            if not row_phone and pin_val:
                cursor_phone.execute("""
                SELECT assigned_campaign_id, assigned_product_id 
                FROM cpa_phone_pool 
                WHERE extension_pin = ? AND status = 'allocated'
                """, (str(pin_val),))
                row_phone = cursor_phone.fetchone()
            conn_phone.close()
            
            if row_phone:
                assigned_camp_id, assigned_prod_id = row_phone
                if not product_id:
                    product_id = assigned_prod_id

        # We'll use a direct independent SQLite connection to handle explicit transaction safety and immediate locks
        conn = sqlite3.connect(db_manager.DB_PATH, timeout=30.0)
        cursor = conn.cursor()
        
        # Check idempotency first before starting atomic transactions to prevent database contention
        cursor.execute("SELECT 1 FROM conversion_postback_log WHERE transaction_id = ?", (transaction_id,))
        if cursor.fetchone():
            conn.close()
            return jsonify({"status": "success", "message": "Conversion already processed (idempotent)"})
            
        try:
            # Start atomic transaction with BEGIN IMMEDIATE
            conn.execute("BEGIN IMMEDIATE")
            
            # 1. Insert the conversion postback log record
            cursor.execute('''
            INSERT INTO conversion_postback_log (transaction_id, product_id, commission_value, network_name, raw_payload)
            VALUES (?, ?, ?, ?, ?)
            ''', (transaction_id, product_id, commission_amount, network_name, json.dumps(data)))
            
            # 2. Update affiliate_conversions status to 'converted' (if applicable)
            if click_id:
                cursor.execute("SELECT 1 FROM affiliate_conversions WHERE click_id = ?", (click_id,))
                if not cursor.fetchone():
                    cursor.execute("""
                    INSERT INTO affiliate_conversions (click_id, product_id, status, sale_amount, commission_amount, converted_at)
                    VALUES (?, ?, 'converted', ?, ?, CURRENT_TIMESTAMP)
                    """, (click_id, product_id or 'unknown', sale_amount, commission_amount))
                else:
                    cursor.execute('''
                    UPDATE affiliate_conversions
                    SET status = 'converted', sale_amount = ?, commission_amount = ?, converted_at = CURRENT_TIMESTAMP
                    WHERE click_id = ?
                    ''', (sale_amount, commission_amount, click_id))
            elif product_id:
                cursor.execute("SELECT 1 FROM affiliate_conversions WHERE product_id = ? AND status = 'pending_conversion'", (product_id,))
                if not cursor.fetchone():
                    cursor.execute("""
                    INSERT INTO affiliate_conversions (product_id, status, sale_amount, commission_amount, converted_at)
                    VALUES (?, 'converted', ?, ?, CURRENT_TIMESTAMP)
                    """, (product_id, sale_amount, commission_amount))
                else:
                    cursor.execute('''
                    UPDATE affiliate_conversions
                    SET status = 'converted', sale_amount = ?, commission_amount = ?, converted_at = CURRENT_TIMESTAMP
                    WHERE product_id = ? AND status = 'pending_conversion'
                    ''', (sale_amount, commission_amount, product_id))
                
            # 3. Lookup product_id and session_id if they are missing but click_id is available
            if click_id:
                if not product_id or not session_id:
                    cursor.execute("SELECT product_id, session_id FROM affiliate_clicks WHERE id = ?", (click_id,))
                    row = cursor.fetchone()
                    if row:
                        if not product_id:
                            product_id = row[0]
                        if not session_id:
                            session_id = row[1]
            
            # Telephony voice session fallback: search last session for that product
            if not session_id and product_id:
                cursor.execute("SELECT session_id, id FROM affiliate_clicks WHERE product_id = ? ORDER BY clicked_at DESC LIMIT 1", (product_id,))
                row = cursor.fetchone()
                if row:
                    session_id = row[0]
                    if not click_id:
                        click_id = row[1]
            
            if not session_id:
                session_id = "inbound_voice_session"
            
            # Enforce that both product_id and session_id must be resolved for suppression to happen,
            # otherwise fail the transaction to roll back both writes
            if not session_id or not product_id:
                raise ValueError("Both session_id and product_id must be resolved to write suppression record.")
                    
            # 4. Insert or upsert the retargeting suppression record
            cursor.execute('''
            INSERT OR REPLACE INTO retargeting_suppression (session_id, product_id)
            VALUES (?, ?)
            ''', (session_id, product_id))

            # 4.5. Phase 10: Auto-credit 15% of the CPA commission payout to the operator's wallet
            cashback = 0.15 * commission_amount
            if cashback > 0:
                cursor.execute("INSERT OR IGNORE INTO user_wallets (user_id, available_balance) VALUES ('guest@marketing.ai', 0.0)")
                cursor.execute("""
                UPDATE user_wallets
                SET available_balance = available_balance + ?
                WHERE user_id = 'guest@marketing.ai'
                """, (cashback,))
                print(f"[WEBHOOK CASHBACK] Credited Rs.{cashback:.2f} (15% of Rs.{commission_amount:.2f}) to guest@marketing.ai")
            
            # 5. Free the telephony vectors back into the available pool
            if product_id:
                cursor.execute("""
                UPDATE cpa_phone_pool
                SET status = 'available', assigned_campaign_id = NULL, assigned_product_id = NULL, allocated_at = NULL
                WHERE assigned_product_id = ? OR assigned_campaign_id = (SELECT id FROM campaigns WHERE product_id = ?)
                """, (product_id, product_id))
                
            # Simulate a forced failure for testing transactional integrity
            if data.get("force_failure_test"):
                raise RuntimeError("Forced database transaction failure for testing rollback.")
                
            conn.commit()
            return jsonify({"status": "success", "message": "Conversion processed successfully"})
        except Exception as write_err:
            conn.rollback()
            raise write_err
        finally:
            conn.close()
            
    except sqlite3.IntegrityError:
        # Handle race condition in a thread-safe / multiprocessing manner
        return jsonify({"status": "success", "message": "Conversion already processed (idempotent)"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# STARTUP — launch scheduler & background worker queue threads
# ═══════════════════════════════════════════════════════════════════════

def _launch_job_queue_consumer():
    """Starts the background worker thread to process enqueued SQLite jobs."""
    import threading
    import time
    from bots.job_queue import acquire_next_job, complete_job, fail_job
    
    def consumer_loop():
        print("[SRE_WORKER] Background queue consumer worker thread active.")
        while True:
            try:
                job = acquire_next_job()
                if job:
                    job_id = job["job_id"]
                    task_name = job["task_name"]
                    payload = job["payload"]
                    
                    print(f"[SRE_WORKER] Processing job '{job_id}' (Task: '{task_name}')...")
                    
                    try:
                        if task_name == "run_single_sector":
                            sector = payload.get("sector", "auto_insurance")
                            pipeline_service._run_single_sector(sector)
                        elif task_name == "run_retargeting":
                            pipeline_service.run_retargeting_internal()
                        else:
                            raise ValueError(f"Unsupported task type: {task_name}")
                            
                        complete_job(job_id)
                        print(f"[SRE_WORKER] Job '{job_id}' completed successfully.")
                    except Exception as execution_err:
                        print(f"[SRE_WORKER] Job '{job_id}' execution failed: {execution_err}")
                        fail_job(job_id, str(execution_err))
                else:
                    time.sleep(2)
            except Exception as loop_err:
                print(f"[SRE_WORKER] Queue consumer loop encountered error: {loop_err}")
                time.sleep(5)
                
    t = threading.Thread(target=consumer_loop, daemon=True)
    t.start()


def _start_scheduler():
    """
    Start APScheduler exactly once.
    Under Flask debug mode the reloader forks a child process;
    we use the WERKZEUG_RUN_MAIN env variable to detect the child.
    """
    import os as _os
    if app.debug and not _os.environ.get('WERKZEUG_RUN_MAIN'):
        return
        
    # SRE Crash Recovery: reset any hanging 'running' states from prior crash/restart
    try:
        from bots.job_queue import recover_stale_jobs
        recover_stale_jobs()
    except Exception as e:
        print(f"[SRE_STARTUP] Failed to recover stale queue jobs at startup: {e}")
        
    scheduler_engine.start(app)
    
    # Launch job queue daemon consumer thread
    try:
        _launch_job_queue_consumer()
    except Exception as e:
        print(f"[SRE_STARTUP] Failed to launch job queue consumer thread: {e}")


# Run seeding on startup
seed_users()
_start_scheduler()


if __name__ == '__main__':
    app.run(debug=True, port=5000)
