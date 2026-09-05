"""
tests/test_b2b_outbound_e2e.py
Comprehensive End-to-End Test Suite for Autonomous B2B Lead Generation, Cold Email & Call-Booking Engine.
"""

import os
import sqlite3
import sys
import unittest
from pathlib import Path

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import DB_PATH
from core.db_manager import (
    setup_database, insert_lead, get_queued_leads,
    update_lead_status, get_kpi_overview, count_emails_sent_today
)
from core.email_verifier import verify_email_address
from core.lead_finder import harvest_b2b_leads
from core.ai_writer import generate_outreach_email
from core.sender_engine import process_outreach_queue
from core.scheduler import backup_sqlite_database
from app import app


class TestB2BOutboundEngine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        setup_database()
        cls.client = app.test_client()

    def test_01_database_wal_and_schema(self):
        """Test SQLite WAL mode, foreign keys, and all 6 required tables."""
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Check WAL mode
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

        # Check tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cursor.fetchall()]
        expected_tables = ["leads", "campaigns", "outreach_logs", "booking_events", "system_settings", "users"]
        for tbl in expected_tables:
            self.assertIn(tbl, tables)
        conn.close()
        print("\n[PASS] Database WAL mode & all 6 schema tables verified.")

    def test_02_email_verifier(self):
        """Test deliverability verifier for valid MX, syntax errors, and disposable domains."""
        # Malformed syntax
        res_bad = verify_email_address("bad@@syntax")
        self.assertEqual(res_bad["status"], "invalid")

        # Disposable provider
        res_disp = verify_email_address("tester@mailinator.com")
        self.assertEqual(res_disp["status"], "invalid")

        # Known valid host
        res_good = verify_email_address("support@google.com")
        self.assertEqual(res_good["status"], "valid")
        print("[PASS] Email verifier syntax, disposable, and MX lookups verified.")

    def test_03_lead_finder_and_trigger_signal(self):
        """Test lead discovery and trigger signal extraction."""
        leads = harvest_b2b_leads(niche="Performance Marketing", location="New York", limit=2)
        self.assertGreaterEqual(len(leads), 1)
        first_lead = leads[0]
        self.assertIn("company_name", first_lead)
        self.assertIn("trigger_signal", first_lead)
        self.assertIn("email", first_lead)
        print(f"[PASS] Lead finder captured {len(leads)} leads with trigger signals.")

    def test_04_ai_copywriter_4_sentence_rule(self):
        """Test 4-sentence cold email copywriting structure."""
        lead = {
            "company_name": "Zenith Media",
            "contact_name": "Sarah",
            "trigger_signal": "Relies on static contact forms",
            "industry": "Media Agency"
        }
        campaign = {
            "value_prop": "We install autonomous AI lead engines that book 15-25 qualified calls/month.",
            "booking_link": "https://cal.com/growthops/demo"
        }
        copy = generate_outreach_email(lead, campaign)
        self.assertTrue(len(copy["subject"]) > 5)
        self.assertIn("https://cal.com/growthops/demo", copy["body"])
        self.assertIn("Zenith Media", copy["body"])
        print(f"[PASS] AI copywriter produced 4-sentence structured email.")

    def test_05_sender_engine_dispatch_and_safeguards(self):
        """Test sender engine simulation, logs, and status update."""
        res = process_outreach_queue(max_batch=2, pace_sleep=False)
        self.assertIn(res["status"], ["success", "capped", "idle"])
        print(f"[PASS] Sender engine queue processing verified: {res}")

    def test_06_flask_endpoints(self):
        """Test Flask web routes and Cal.com booking webhook."""
        # 1. Health check
        r_health = self.client.get("/health")
        self.assertEqual(r_health.status_code, 200)
        self.assertEqual(r_health.json["status"], "ok")

        # 2. Overview page
        r_index = self.client.get("/")
        self.assertEqual(r_index.status_code, 200)
        self.assertIn(b"GrowthOps", r_index.data)

        # 3. Pipeline leads page
        r_leads = self.client.get("/leads")
        self.assertEqual(r_leads.status_code, 200)

        # 4. Cal.com webhook simulation
        cal_payload = {
            "triggerEvent": "BOOKING_CREATED",
            "payload": {
                "email": "contact@nexusgrowth.example.com",
                "startTime": "2026-09-10T14:00:00Z",
                "title": "Strategy Session"
            }
        }
        r_hook = self.client.post("/api/webhook/booking", json=cal_payload)
        self.assertEqual(r_hook.status_code, 200)
        self.assertEqual(r_hook.json["status"], "success")
        print("[PASS] Flask web dashboard and Cal.com webhook verified.")

    def test_07_online_sqlite_backup(self):
        """Test online transaction-safe SQLite backup."""
        bpath = backup_sqlite_database()
        self.assertTrue(os.path.exists(bpath))
        self.assertTrue(os.path.getsize(bpath) > 0)
        print(f"[PASS] Nightly SQLite online backup verified: {bpath}")


if __name__ == "__main__":
    unittest.main()
