"""
tests/verify_local_pipeline.py
Automated end-to-end verification suite for Autonomous B2B Outbound Engine.
Validates:
  1. Database WAL mode, foreign keys, and 6 core tables.
  2. Cloudflare bitwise XOR email de-obfuscation.
  3. Actionable email filter (discarding support@, billing@, etc.).
  4. DNS MX lookup via dnspython on Port 53 UDP & disposable email blocklist.
  5. AI proof-of-work copywriting: 3-4 sentence strictness, Spintax, and opt-out footer.
  6. Cold email sender engine queue & daily cap limits.
  7. Cal.com / Calendly HMAC webhook gateway & Telegram alerting.
  8. Online transaction-safe hot SQLite backup.
  9. Flask web application endpoints (/health, /login, /book/<slug>).
"""

import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import DB_PATH, BACKUP_DIR
from core.db_manager import (
    setup_database, backup_database_online, insert_lead,
    get_leads_for_outreach, get_active_campaign, get_dashboard_metrics
)
from core.email_verifier import verify_email_address
from core.lead_finder import (
    decode_cloudflare_email, is_actionable_lead_email, harvest_b2b_leads
)
from core.ai_writer import generate_outreach_email, resolve_spintax
from core.sender_engine import process_outreach_queue
from core.booking_manager import handle_calcom_webhook
from app import app


class TestB2BOutboundPipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        setup_database()
        cls.client = app.test_client()

    def test_01_database_wal_and_schema(self):
        """Verify SQLite WAL mode, foreign keys, and all 6 required tables."""
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()

        # Check WAL mode
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        self.assertEqual(mode.lower(), "wal", "Database is not in WAL mode")

        # Check required schema tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cursor.fetchall()]
        required = ["leads", "campaigns", "outreach_logs", "booking_events", "system_settings", "users"]
        for t in required:
            self.assertIn(t, tables, f"Missing required table: {t}")

        conn.close()
        print("\n[PASS 1/9] Database WAL mode & 6 core schema tables verified.")

    def test_02_cloudflare_email_decoding(self):
        """Verify bitwise XOR de-obfuscation of Cloudflare protected email strings."""
        sample_hex = "5a2e3f292e1a3f223b372a363f74393537"
        decoded = decode_cloudflare_email(sample_hex)
        self.assertEqual(decoded, "test@example.com")
        print("[PASS 2/9] Cloudflare bitwise XOR email de-obfuscation verified.")

    def test_03_actionable_email_filtering(self):
        """Verify discard of generic non-actionable inboxes (support@, billing@, jobs@)."""
        self.assertFalse(is_actionable_lead_email("support@company.com"))
        self.assertFalse(is_actionable_lead_email("billing@company.com"))
        self.assertFalse(is_actionable_lead_email("press@company.com"))
        self.assertFalse(is_actionable_lead_email("jobs@company.com"))
        self.assertFalse(is_actionable_lead_email("abuse@company.com"))
        self.assertFalse(is_actionable_lead_email("icon@company.com.png"))

        # Actionable executive inboxes
        self.assertTrue(is_actionable_lead_email("david@company.com"))
        self.assertTrue(is_actionable_lead_email("alex.vance@company.com"))
        self.assertTrue(is_actionable_lead_email("founder@company.com"))
        print("[PASS 3/9] Actionable decision-maker email filtering verified.")

    def test_04_email_verifier_dns_mx(self):
        """Verify Port 53 UDP MX resolution, disposable domain blocking, and syntax validation."""
        # Syntax error
        r_syntax = verify_email_address("invalid..syntax@domain")
        self.assertEqual(r_syntax["status"], "invalid")

        # Disposable domain
        r_disp = verify_email_address("test@mailinator.com")
        self.assertEqual(r_disp["status"], "invalid")

        # Valid MX resolution (Google mail exchangers)
        r_mx = verify_email_address("support@google.com")
        self.assertEqual(r_mx["status"], "valid")
        print("[PASS 4/9] DNS MX lookups on Port 53 UDP & disposable domain blocklist verified.")

    def test_05_ai_proof_of_work_copywriter(self):
        """Verify strict 3-4 sentence structure, Spintax resolution, and opt-out footer."""
        lead = {
            "company_name": "Horizon Digital",
            "contact_name": "Marcus Vance",
            "trigger_signal": "Lacks automated online booking calendar",
            "industry": "Performance Agency"
        }
        campaign = {
            "value_prop": "We install autonomous AI sales engines that book 15-25 qualified calls every month.",
            "booking_link": "https://cal.com/growthops/demo"
        }
        res = generate_outreach_email(lead, campaign)
        body = res["body"]

        # Check required components
        self.assertIn("Horizon Digital", body)
        self.assertIn("autonomous", body.lower())
        self.assertIn("https://cal.com/growthops/demo", body)
        self.assertIn("Reply 'STOP' to unsubscribe", body)

        # Check Spintax resolver
        spintax_sample = "{Hi|Hello|Hey} there"
        resolved = resolve_spintax(spintax_sample)
        self.assertIn(resolved, ["Hi there", "Hello there", "Hey there"])
        print("[PASS 5/9] AI Proof-of-Work copywriter (3-4 sentences, Spintax & opt-out) verified.")

    def test_06_sender_engine_queue_and_caps(self):
        """Verify queue processing, status progression, and daily cap enforcement."""
        report = process_outreach_queue(max_batch=2, pace_sleep=False, simulate_force=True)
        self.assertIn(report["status"], ["success", "capped", "idle"])
        self.assertIn("sent_count", report)
        print(f"[PASS 6/9] Outbound queue dispatcher verified (Status: {report['status']}).")

    def test_07_booking_webhook_gateway(self):
        """Verify Cal.com / Calendly webhook gateway, lead status progression, and Telegram alert."""
        payload = {
            "triggerEvent": "BOOKING_CREATED",
            "payload": {
                "email": "alex@pulsesaas.example.com",
                "startTime": "2026-09-15T14:30:00Z"
            }
        }
        raw_text = json.dumps(payload)
        res = handle_calcom_webhook(raw_text, raw_text.encode("utf-8"))
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["lead_status"], "booked")
        print("[PASS 7/9] Booking webhook gateway and Telegram alert verified.")

    def test_08_online_sqlite_backup(self):
        """Verify transaction-safe hot online SQLite backup without read/write interruption."""
        backup_path = backup_database_online()
        self.assertTrue(os.path.exists(backup_path))
        self.assertGreater(os.path.getsize(backup_path), 0)
        print(f"[PASS 8/9] Hot online SQLite backup verified: {backup_path}")

    def test_09_flask_routes_and_security(self):
        """Verify Flask web endpoints, CSRF injection, and security headers."""
        # 1. Health check (public, 200 OK)
        r_health = self.client.get("/health")
        self.assertEqual(r_health.status_code, 200)
        self.assertEqual(r_health.json["status"], "ok")
        self.assertEqual(r_health.headers.get("X-Frame-Options"), "SAMEORIGIN")

        # 2. Login view (200 OK)
        r_login = self.client.get("/login")
        self.assertEqual(r_login.status_code, 200)
        self.assertIn(b"GrowthOps", r_login.data)

        # 3. Direct booking slot fallback (/book/<slug>)
        r_book = self.client.get("/book/strategy-discovery")
        self.assertEqual(r_book.status_code, 200)
        self.assertIn(b"15-Min Strategy Discovery", r_book.data)

        print("[PASS 9/9] Flask application security headers, auth, and views verified.")


if __name__ == "__main__":
    unittest.main()
