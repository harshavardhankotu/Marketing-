import unittest
import requests
import sqlite3
import hmac
import hashlib
import time
import os
import sys
import threading
import json
import re
from datetime import datetime

# Set up module paths
os.environ["FAST_VIDEO_RENDER"] = "True"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../bots')))

from bots.config import DB_PATH
import bots.db_manager as db_manager
import generators.ai_copywriter as ai_copywriter
import bots.affiliate_tracker as affiliate_tracker
import bots.retargeting_engine as retargeting_engine
import bots.distributor as distributor
import bots.scheduler_engine as scheduler_engine

BASE_URL = "http://127.0.0.1:5000"

class TestSecurityHardening(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Ensure database is set up
        db_manager.setup_database()
        cls.session_admin = requests.Session()
        cls.session_guest = requests.Session()

        # Login as admin: get form to parse CSRF first
        r_login_admin = cls.session_admin.get(f"{BASE_URL}/login")
        csrf_admin_match = re.search(r'name="csrf_token" value="([^"]+)"', r_login_admin.text)
        csrf_admin = csrf_admin_match.group(1) if csrf_admin_match else ""

        cls.session_admin.post(f"{BASE_URL}/login", data={
            "email": "admin@marketing.ai",
            "password": "admin123",
            "csrf_token": csrf_admin
        })

        # Fetch index.html to extract custom script-injected X-CSRFToken
        r_home_admin = cls.session_admin.get(f"{BASE_URL}/")
        csrf_api_match = re.search(r"options\.headers\['X-CSRFToken'\] = '([^']+)';", r_home_admin.text)
        if csrf_api_match:
            cls.session_admin.headers.update({"X-CSRFToken": csrf_api_match.group(1)})

        # Login as guest: get form to parse CSRF first
        r_login_guest = cls.session_guest.get(f"{BASE_URL}/login")
        csrf_guest_match = re.search(r'name="csrf_token" value="([^"]+)"', r_login_guest.text)
        csrf_guest = csrf_guest_match.group(1) if csrf_guest_match else ""

        cls.session_guest.post(f"{BASE_URL}/login", data={
            "email": "guest@marketing.ai",
            "password": "guest123",
            "csrf_token": csrf_guest
        })

        # Fetch index.html for guest and extract active X-CSRFToken for APIs
        r_home_guest = cls.session_guest.get(f"{BASE_URL}/")
        csrf_api_match_g = re.search(r"options\.headers\['X-CSRFToken'\] = '([^']+)';", r_home_guest.text)
        if csrf_api_match_g:
            cls.session_guest.headers.update({"X-CSRFToken": csrf_api_match_g.group(1)})

    def test_00_env_secrets_sanitization(self):
        """0. Credential sanitization regression test ensures .env and .env.example contain only placeholders"""
        for filename in [".env", ".env.example"]:
            filepath = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", filename))
            if not os.path.exists(filepath):
                continue
            with open(filepath, "r") as f:
                content = f.read()
            for line in content.splitlines():
                if "=" in line:
                    key, val = line.split("=", 1)
                    val = val.strip()
                    if key.strip() in ["GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
                        # Assert that only placeholders are present
                        self.assertTrue(
                            val in ["", "your_gemini_api_key_here", "your_telegram_bot_token_here", "your_telegram_chat_id_here"],
                            f"Real secret detected in {filename} for key {key}: {val}"
                        )
                        # Specific Telegram bot token pattern check to prevent leaks
                        if re.search(r'\d{8,10}:[a-zA-Z0-9_-]{35}', val):
                            self.fail(f"Real Telegram Bot Token detected in {filename}: {val}")
                        # Specific Gemini key pattern check
                        if val.startswith("AIzaSy"):
                            self.fail(f"Real Gemini/Google API Key detected in {filename}: {val}")

    def test_01_unauthenticated_route_access(self):
        """1. Unauthenticated route access returns correct redirects or 401 JSON responses"""
        # GET / and /history should redirect to /login
        r1 = requests.get(f"{BASE_URL}/", allow_redirects=False)
        self.assertEqual(r1.status_code, 302)
        self.assertIn("/login", r1.headers.get("Location", ""))

        r2 = requests.get(f"{BASE_URL}/history", allow_redirects=False)
        self.assertEqual(r2.status_code, 302)
        self.assertIn("/login", r2.headers.get("Location", ""))

        # Guest (unauthenticated) access to mutating or administrative API endpoints should return 401 JSON
        endpoints = [
            ("/api/run_pipeline", "POST"),
            ("/api/reliability_reset", "POST"),
            ("/api/scheduler_config", "POST"),
            ("/api/review_dead_letter", "POST")
        ]
        for path, method in endpoints:
            if method == "POST":
                r = requests.post(f"{BASE_URL}{path}", json={}, headers={"X-CSRFToken": "mock"})
            else:
                r = requests.get(f"{BASE_URL}{path}")
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.json().get("status"), "error")
            self.assertIn("Unauthorized", r.json().get("message", ""))

    def test_02_privilege_enforcement(self):
        """2. Privilege enforcement prevents guests/non-admins from invoking destructive admin endpoints (403)"""
        # Guest trying to invoke reliability reset
        r1 = self.session_guest.post(f"{BASE_URL}/api/reliability_reset", json={"target": "quota", "provider": "gemini"})
        self.assertEqual(r1.status_code, 403)
        self.assertIn("Admin role required", r1.json().get("message", ""))

        # Guest trying to post to scheduler config (mutating)
        r2 = self.session_guest.post(f"{BASE_URL}/api/scheduler_config", json={"job_id": "pipeline_morning_daily", "enabled": False})
        self.assertEqual(r2.status_code, 403)
        self.assertIn("Admin role required", r2.json().get("message", ""))

        # Guest trying to trigger scheduler job immediately
        r3 = self.session_guest.post(f"{BASE_URL}/api/scheduler_run_now", json={"job_id": "pipeline_morning_daily"})
        self.assertEqual(r3.status_code, 403)
        self.assertIn("Admin role required", r3.json().get("message", ""))

        # Guest trying to access /settings page
        r4 = self.session_guest.get(f"{BASE_URL}/settings")
        self.assertEqual(r4.status_code, 403)

    def test_06_bot_user_agent_filtering(self):
        """6. Bot User-Agent clicks are flagged as is_bot=1 and excluded from metrics"""
        bot_uas = ["TelegramBot", "facebookexternalhit", "Twitterbot", "Googlebot"]
        for ua in bot_uas:
            prod_id = f"bot_test_{int(time.time())}"
            # Make request simulating crawler
            requests.get(
                f"{BASE_URL}/go/{prod_id}?url=https://offers.cpa-arbitrage.com/auto-sweep&title=Test&sector=auto_insurance",
                headers={"User-Agent": ua},
                allow_redirects=False
            )
            
            # Query db to verify click was flagged
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT is_bot FROM affiliate_clicks WHERE product_id = ? ORDER BY clicked_at DESC LIMIT 1", (prod_id,))
            row = c.fetchone()
            conn.close()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 1, f"Bot User-Agent '{ua}' was not flagged as bot traffic!")

    def test_07_duplicate_click_suppression(self):
        """7. Duplicate clicks within 60s from same session and product are suppressed to a single DB row"""
        prod_id = f"dup_test_{int(time.time())}"
        session_id = "127.0.0.1"
        url = "https://www.amazon.in/s?k=dup"

        # Record first click
        click_id1 = affiliate_tracker.record_click(
            product_id=prod_id,
            product_title="Dup Title",
            sector="fashion",
            channel="direct",
            affiliate_link=url,
            session_id=session_id,
            user_agent="Mozilla/5.0"
        )

        # Record second click immediately
        click_id2 = affiliate_tracker.record_click(
            product_id=prod_id,
            product_title="Dup Title",
            sector="fashion",
            channel="direct",
            affiliate_link=url,
            session_id=session_id,
            user_agent="Mozilla/5.0"
        )

        self.assertEqual(click_id1, click_id2)

        # Verify only one row exists in DB
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM affiliate_clicks WHERE product_id = ?", (prod_id,))
        count = c.fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_08_ftc_disclosure_presence(self):
        """8. English, Hindi, and Tamil generated and fallback copies contain explicit disclosures"""
        product = {
            "title": "Top-Tier Auto Insurance Quote Sweep",
            "price": "3,200",
            "platform": "Auto Insure Tunnel",
            "sector": "auto_insurance"
        }
        
        # Test fallback / mock captions generator
        copies = ai_copywriter.generate_multilingual_copy(product)
        
        # English disclosure
        self.assertIn("#Ad", copies["en"])
        self.assertIn("Disclosure: Paid partner. Earns commission on qualified quote submissions.", copies["en"])
        
        # Hindi disclosure
        self.assertIn("#Ad", copies["hi"])
        self.assertIn("डिस्क्लोज़र: पेड पार्टनर", copies["hi"])
        
        # Tamil disclosure
        self.assertIn("#Ad", copies["ta"])
        self.assertIn("வெளிப்படுத்தல்: கட்டண கூட்டாளர்", copies["ta"])

    def test_09_sqlite_pragma_verification(self):
        """9. SQLite connections have foreign_keys pragma enabled globally"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("PRAGMA foreign_keys")
        val = c.fetchone()[0]
        conn.close()
        self.assertEqual(val, 1)

    def test_10_sqlite_referential_integrity(self):
        """10. Foreign key constraints enforce referential integrity and fail on violation"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Attempt to insert dependent row with invalid foreign key (campaign_id = 9999999)
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("""
            INSERT INTO distribution_logs (campaign_id, platform, status, link)
            VALUES (9999999, 'Twitter', 'Success', 'https://twitter.com')
            """)
            conn.commit()
        conn.close()

    def test_11_conversion_postback_invalid_signature(self):
        """11. Conversion postback with an invalid signature is rejected with a 403 and writes nothing to the database"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM operator_settings WHERE key = 'postback_secret'")
        secret = c.fetchone()[0]
        conn.close()

        transaction_id = f"tx_invalid_{int(time.time())}"
        product_id = f"prod_invalid_{int(time.time())}"
        session_id = f"sess_invalid_{int(time.time())}"

        # 1. First record a click to simulate state
        click_id = affiliate_tracker.record_click(
            product_id=product_id,
            product_title="Invalid Sig Product",
            sector="auto_insurance",
            channel="direct",
            affiliate_link="https://offers.cpa-arbitrage.com/auto-sweep",
            session_id=session_id,
            user_agent="Mozilla/5.0"
        )

        # 2. Build payload
        payload = {
            "product_id": product_id,
            "click_id": click_id,
            "session_id": session_id,
            "sale_amount": 100.0,
            "commission_amount": 5.0,
            "transaction_id": transaction_id,
            "network_name": "cpa_lead_net_9876"
        }
        raw_payload = json.dumps(payload).encode('utf-8')

        # 3. Post with invalid signature
        r = requests.post(
            f"{BASE_URL}/postback/cpa_lead",
            data=raw_payload,
            headers={
                "X-Signature": "invalid_sig_here",
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(r.status_code, 403)
        self.assertIn("Invalid HMAC signature", r.json().get("message", ""))

        # 4. Assert that absolutely nothing is written to conversion_postback_log or retargeting_suppression
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM conversion_postback_log WHERE transaction_id = ?", (transaction_id,))
        self.assertEqual(c.fetchone()[0], 0, "Log row was written despite invalid signature!")

        c.execute("SELECT COUNT(*) FROM retargeting_suppression WHERE session_id = ? AND product_id = ?", (session_id, product_id))
        self.assertEqual(c.fetchone()[0], 0, "Suppression row was written despite invalid signature!")

        c.execute("SELECT status FROM affiliate_conversions WHERE click_id = ?", (click_id,))
        self.assertEqual(c.fetchone()[0], "pending_conversion", "Conversion status was modified despite invalid signature!")
        conn.close()

    def test_12_and_13_conversion_postback_valid_signature_and_suppression(self):
        """12 & 13. Valid HMAC conversion postback processes successfully, writes both rows, and excludes converted user from retargeting"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM operator_settings WHERE key = 'postback_secret'")
        secret = c.fetchone()[0]
        conn.close()

        product_id = f"conv_prod_{int(time.time())}"
        session_id = f"sess_{int(time.time())}"
        transaction_id = f"tx_valid_{int(time.time())}"

        # 1. Create a click record first so we have click_id and session_id mapped
        click_id = affiliate_tracker.record_click(
            product_id=product_id,
            product_title="Conv Product",
            sector="auto_insurance",
            channel="direct",
            affiliate_link="https://offers.cpa-arbitrage.com/auto-sweep",
            session_id=session_id,
            user_agent="Mozilla/5.0"
        )

        # 2. Build payload and compute signature
        payload = {
            "product_id": product_id,
            "click_id": click_id,
            "session_id": session_id,
            "sale_amount": 1500.0,
            "commission_amount": 90.0,
            "transaction_id": transaction_id,
            "network_name": "cpa_lead_net_9876"
        }
        raw_payload = json.dumps(payload).encode('utf-8')
        sig = hmac.new(secret.encode('utf-8'), raw_payload, hashlib.sha256).hexdigest()

        # 3. Post to webhook
        r = requests.post(
            f"{BASE_URL}/postback/cpa_lead",
            data=raw_payload,
            headers={
                "X-Signature": sig,
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("status"), "success")

        # 4. Assert conversion table is updated
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT status, sale_amount FROM affiliate_conversions WHERE click_id = ?", (click_id,))
        row = c.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 'converted')
        self.assertEqual(row[1], 1500.0)

        # 5. Assert conversion_postback_log contains the log record
        c.execute("SELECT COUNT(*) FROM conversion_postback_log WHERE transaction_id = ?", (transaction_id,))
        self.assertEqual(c.fetchone()[0], 1)

        # 6. Assert retargeting suppression entry exists
        c.execute("SELECT COUNT(*) FROM retargeting_suppression WHERE session_id = ? AND product_id = ?", (session_id, product_id))
        sup_count = c.fetchone()[0]
        self.assertEqual(sup_count, 1)

        # 7. Verify retargeting skips this suppressed session/product
        plans = retargeting_engine.generate_retargeting_campaigns(hours_ago_min=0, hours_ago_max=24)
        for plan in plans:
            self.assertNotEqual((plan["session_id"], plan["product_id"]), (session_id, product_id))

        conn.close()

    def test_13b_forced_failure_rolls_back_everything(self):
        """13b. Forced database failure triggers transaction rollback so nothing is written in both tables"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM operator_settings WHERE key = 'postback_secret'")
        secret = c.fetchone()[0]
        conn.close()

        product_id = f"fail_prod_{int(time.time())}"
        session_id = f"fail_sess_{int(time.time())}"
        transaction_id = f"tx_fail_{int(time.time())}"

        # 1. Create a click record first
        click_id = affiliate_tracker.record_click(
            product_id=product_id,
            product_title="Failed Product",
            sector="auto_insurance",
            channel="direct",
            affiliate_link="https://offers.cpa-arbitrage.com/auto-sweep",
            session_id=session_id,
            user_agent="Mozilla/5.0"
        )

        # 2. Build payload with force_failure_test=True
        payload = {
            "product_id": product_id,
            "click_id": click_id,
            "session_id": session_id,
            "sale_amount": 1200.0,
            "commission_amount": 72.0,
            "transaction_id": transaction_id,
            "network_name": "cpa_lead_net_9876",
            "force_failure_test": True
        }
        raw_payload = json.dumps(payload).encode('utf-8')
        sig = hmac.new(secret.encode('utf-8'), raw_payload, hashlib.sha256).hexdigest()

        # 3. Post to webhook (expect 500 error due to simulated runtime failure)
        r = requests.post(
            f"{BASE_URL}/postback/cpa_lead",
            data=raw_payload,
            headers={
                "X-Signature": sig,
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(r.status_code, 500)

        # 4. Assert that absolutely nothing is written or committed for both writes
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM conversion_postback_log WHERE transaction_id = ?", (transaction_id,))
        self.assertEqual(c.fetchone()[0], 0, "Postback log row was written despite rollback!")

        c.execute("SELECT COUNT(*) FROM retargeting_suppression WHERE session_id = ? AND product_id = ?", (session_id, product_id))
        self.assertEqual(c.fetchone()[0], 0, "Suppression row was written despite rollback!")

        # 5. Assert conversion state remains pending
        c.execute("SELECT status FROM affiliate_conversions WHERE click_id = ?", (click_id,))
        self.assertEqual(c.fetchone()[0], "pending_conversion", "Conversion status was modified despite rollback!")
        conn.close()

    def test_13c_duplicate_postback_idempotency(self):
        """13c. Duplicate conversion postbacks are handled idempotently without creating duplicate rows or throwing exceptions"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM operator_settings WHERE key = 'postback_secret'")
        secret = c.fetchone()[0]
        conn.close()

        product_id = f"dup_prod_{int(time.time())}"
        session_id = f"dup_sess_{int(time.time())}"
        transaction_id = f"tx_dup_{int(time.time())}"

        # 1. Create a click record first
        click_id = affiliate_tracker.record_click(
            product_id=product_id,
            product_title="Dup Product",
            sector="auto_insurance",
            channel="direct",
            affiliate_link="https://offers.cpa-arbitrage.com/auto-sweep",
            session_id=session_id,
            user_agent="Mozilla/5.0"
        )

        # 2. Build payload and compute signature
        payload = {
            "product_id": product_id,
            "click_id": click_id,
            "session_id": session_id,
            "sale_amount": 1000.0,
            "commission_amount": 60.0,
            "transaction_id": transaction_id,
            "network_name": "cpa_lead_net_9876"
        }
        raw_payload = json.dumps(payload).encode('utf-8')
        sig = hmac.new(secret.encode('utf-8'), raw_payload, hashlib.sha256).hexdigest()

        # 3. Post to webhook (First request: expect success 200)
        r1 = requests.post(
            f"{BASE_URL}/postback/cpa_lead",
            data=raw_payload,
            headers={
                "X-Signature": sig,
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json().get("status"), "success")

        # 4. Post to webhook again (Second request: expect success 200 with idempotent message)
        r2 = requests.post(
            f"{BASE_URL}/postback/cpa_lead",
            data=raw_payload,
            headers={
                "X-Signature": sig,
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json().get("status"), "success")
        self.assertIn("already processed", r2.json().get("message", ""))

        # 5. Assert that exactly one row exists in conversion_postback_log and retargeting_suppression
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM conversion_postback_log WHERE transaction_id = ?", (transaction_id,))
        self.assertEqual(c.fetchone()[0], 1)

        c.execute("SELECT COUNT(*) FROM retargeting_suppression WHERE session_id = ? AND product_id = ?", (session_id, product_id))
        self.assertEqual(c.fetchone()[0], 1)
        conn.close()

    def test_14_scheduler_blocked_quota(self):
        """14. Forced blocked quota/breaker leads scheduler pre-flight check to record skipped run state"""
        # Block quota
        from bots.quota_manager import consume_quota
        # Consume high quota to trigger warning/block (or just mock check_quota to return BLOCKED)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO api_quota_usage (provider, usage_date, request_count) VALUES ('gemini', CURRENT_DATE, 50000)")
        conn.commit()
        conn.close()

        # Trigger a scheduler pre-flight check manually on a test job
        ok_to_run = scheduler_engine._run_preflight_checks("pipeline_morning_daily")
        self.assertFalse(ok_to_run)

        # Assert skipped run state is recorded in DB
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT status, result_summary FROM scheduler_runs WHERE job_id = 'pipeline_morning_daily' ORDER BY started_at DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 'skipped')
        self.assertIn("Gemini quota exhausted", row[1])

        # Clean up quota block
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM api_quota_usage WHERE provider = 'gemini'")
        conn.commit()
        conn.close()

    def test_15_telegram_fallback_delivery(self):
        """15. Distributor sends text successfully even when visual image path is missing or invalid"""
        post = {
            "title": "Fallback Test Product",
            "price": "999",
            "affiliate_link": "https://www.amazon.in",
            "caption": "Beautiful test caption with #Ad",
            "graphic_path": "/image/missing_image_file.jpg"
        }
        # Calling live_post_to_telegram with unconfigured token falls back to mock_post_to_telegram which handles it beautifully
        res = distributor.live_post_to_telegram(post)
        self.assertEqual(res["platform"], "Telegram")
        self.assertEqual(res["status"], "Success")

    def test_16_open_redirect_and_malformed_url_safety(self):
        """16. Open redirect and javascript: scheme inputs are blocked with a 400 Bad Request"""
        untrusted_urls = [
            "https://evil.com/steal-creds",
            "javascript:alert('xss')",
            "data:text/html,<script>alert(1)</script>",
            "http://untrusted-affiliate.net"
        ]
        for url in untrusted_urls:
            r = requests.get(f"{BASE_URL}/go/test_prod?url={url}", allow_redirects=False)
            self.assertEqual(r.status_code, 400)
            self.assertIn("Unsafe redirect URL rejected.", r.json().get("message", ""))

        # Verify a trusted URL works and redirects successfully
        trusted_url = "https://www.amazon.in/dp/B0CHX19672"
        r = requests.get(f"{BASE_URL}/go/test_prod?url={trusted_url}", allow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers.get("Location"), trusted_url)

    def test_17_concurrency_collision(self):
        """17. Parallel requests do not cause uncaught database lock exceptions"""
        # Fire 20 parallel threads hitting track click
        errors = []
        def hit_endpoint():
            try:
                r = requests.get(f"{BASE_URL}/go/concurrency_test_{int(time.time())}?url=https://www.amazon.in/s?k=c", allow_redirects=False)
                if r.status_code not in (200, 302):
                    errors.append(f"Status code {r.status_code}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=hit_endpoint) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrency collision test encountered database or server errors: {errors}")

    def test_18_e2e_happy_path(self):
        """18. Running the complete sector pipeline and retargeting works end-to-end with security controls active"""
        # Trigger single sector run via pipeline service
        import pipeline_service
        res = pipeline_service._run_single_sector("auto_insurance")
        self.assertIsNotNone(res)
        self.assertEqual(res["sector"], "auto_insurance")
        self.assertGreater(len(res["data"]), 0)

    # ═══════════════════════════════════════════════════════════════════════
    # RATE LIMITING TESTS (Executed last to prevent premature rate limit triggers)
    # ═══════════════════════════════════════════════════════════════════════

    def test_50_login_rate_limiting(self):
        """3. Login Rate Limiting triggers 429 after excess attempts"""
        triggered = False
        for i in range(10):
            sess = requests.Session()
            r_get = sess.get(f"{BASE_URL}/login")
            match = re.search(r'name="csrf_token" value="([^"]+)"', r_get.text)
            csrf_token = match.group(1) if match else ""
            
            r = sess.post(f"{BASE_URL}/login", data={
                "email": f"hacker_{i}@marketing.ai",
                "password": "wrongpassword",
                "csrf_token": csrf_token
            })
            if r.status_code == 429:
                triggered = True
                self.assertIn("Retry-After", r.headers)
                break
        self.assertTrue(triggered, "Login rate limiter failed to trip after 10 rapid attempts!")

    def test_51_redirect_rate_limiting(self):
        """4. Click Redirect Rate Limiting triggers 429 after 60 requests/minute"""
        triggered = False
        prod_id = f"rate_limit_redirect_{int(time.time())}"
        for _ in range(70):
            r = requests.get(f"{BASE_URL}/go/{prod_id}?url=https://www.amazon.in/s?k=rate", allow_redirects=False)
            if r.status_code == 429:
                triggered = True
                self.assertIn("Retry-After", r.headers)
                break
        self.assertTrue(triggered, "Redirect rate limiter failed to trip after 65 burst requests!")

    def test_52_api_rate_limiting(self):
        """5. Mutating API rate limiting triggers 429 after 30 requests/minute"""
        triggered = False
        for _ in range(40):
            r = self.session_admin.post(f"{BASE_URL}/api/run_pipeline", json={"sector": "kitchen", "dry_run": True})
            if r.status_code == 429:
                triggered = True
                self.assertIn("Retry-After", r.headers)
                break
        self.assertTrue(triggered, "Mutating API rate limiter failed to trip after 35 burst requests!")

if __name__ == "__main__":
    import json
    unittest.main()
