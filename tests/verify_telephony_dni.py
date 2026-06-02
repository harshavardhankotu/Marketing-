import unittest
import sqlite3
import hmac
import hashlib
import time
import json
import os
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.path.join(PROJECT_ROOT, 'bots') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'bots'))
if os.path.join(PROJECT_ROOT, 'scrapers') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scrapers'))

# Disable WTForms CSRF check for simple REST testing
from app import app
app.config['WTF_CSRF_ENABLED'] = False
app.config['TESTING'] = True

from bots.config import DB_PATH
import bots.db_manager as db_manager
import bots.distributor as distributor
import generators.video_script_engine as video_script_engine
import bots.affiliate_tracker as affiliate_tracker

class TestTelephonyDNIMatrix(unittest.TestCase):

    def setUp(self):
        # Force database setup and reset cpa_phone_pool state
        db_manager.setup_database()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE cpa_phone_pool SET status = 'available', assigned_campaign_id = NULL, assigned_product_id = NULL, allocated_at = NULL")
        # Ensure we have our seed users
        conn.commit()
        conn.close()
        self.client = app.test_client()

    def test_01_telephony_pool_schema_and_seeds(self):
        """1. Verify cpa_phone_pool exists and is pre-seeded with 11 default vectors"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM cpa_phone_pool WHERE status = 'available'")
        count = c.fetchone()[0]
        conn.close()
        self.assertEqual(count, 11, f"Pre-seeded available numbers expected to be 11, found {count}")

    def test_02_dynamic_number_allocation(self):
        """2. Verify voice vector allocator reserves available numbers idempotently and recycles circular pool on exhaustion"""
        # Test string hash allocation
        prod_hash = "hash_cpa_001"
        vector = distributor.allocate_tracking_voice_vector(prod_hash)
        self.assertIsNotNone(vector.get("tracking_number"))
        self.assertIsNotNone(vector.get("extension_pin"))
        
        # Test idempotency
        vector2 = distributor.allocate_tracking_voice_vector(prod_hash)
        self.assertEqual(vector["tracking_number"], vector2["tracking_number"])
        self.assertEqual(vector["extension_pin"], vector2["extension_pin"])

        # Test database status transition
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT status, assigned_product_id FROM cpa_phone_pool WHERE tracking_number = ?", (vector["tracking_number"],))
        row = c.fetchone()
        conn.close()
        self.assertEqual(row[0], "allocated")
        self.assertEqual(row[1], prod_hash)

        # Test pool exhaustion recycling fallback: allocate remaining 10 slots
        for i in range(15):
            distributor.allocate_tracking_voice_vector(f"exhaust_hash_{i}")

        # Ensure no crash happens and we still get trackable fallback vectors
        vector_last = distributor.allocate_tracking_voice_vector("final_cpa_flow")
        self.assertIsNotNone(vector_last.get("tracking_number"))

    def test_03_telephony_ingestion_correlation_by_phone(self):
        """3. Inbound webhook postback by dialing phone number attributes campaign context, suppresses session, and auto-frees number"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM operator_settings WHERE key = 'postback_secret'")
        secret = c.fetchone()[0]
        conn.close()

        product_id = "dni_phone_prod"
        session_id = "dni_session_1"
        transaction_id = f"tx_dni_phone_{int(time.time())}"

        # 1. Allocate number to product
        vector = distributor.allocate_tracking_voice_vector(product_id)
        phone = vector["tracking_number"]

        # 2. Record click session context
        affiliate_tracker.record_click(
            product_id=product_id,
            product_title="Auto Insurance CPA Sweep",
            sector="auto_insurance",
            channel="social",
            affiliate_link="https://offers.cpa-arbitrage.com/auto-sweep",
            session_id=session_id,
            user_agent="Mozilla/5.0"
        )

        # 3. Simulate inbound telephony webhook without click_id but containing tracking_number
        payload = {
            "tracking_number": phone,
            "sale_amount": 3500.0,
            "commission_amount": 75.0,
            "transaction_id": transaction_id,
            "network_name": "ringba_telephony"
        }
        raw_payload = json.dumps(payload)
        sig = hmac.new(secret.encode('utf-8'), raw_payload.encode('utf-8'), hashlib.sha256).hexdigest()

        # 4. Trigger Webhook Postback
        r = self.client.post(
            '/postback/cpa_lead',
            data=raw_payload,
            content_type='application/json',
            headers={"X-Signature": sig}
        )
        self.assertEqual(r.status_code, 200)

        # 5. Assert conversion table is updated
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT status, sale_amount, commission_amount FROM affiliate_conversions WHERE product_id = ?", (product_id,))
        row = c.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 'converted')
        self.assertEqual(row[1], 3500.0)

        # 6. Assert retargeting suppression is triggered
        c.execute("SELECT COUNT(*) FROM retargeting_suppression WHERE session_id = ? AND product_id = ?", (session_id, product_id))
        self.assertEqual(c.fetchone()[0], 1)

        # 7. Assert phone number status is freed back to available
        c.execute("SELECT status, assigned_product_id FROM cpa_phone_pool WHERE tracking_number = ?", (phone,))
        pool_row = c.fetchone()
        self.assertEqual(pool_row[0], 'available')
        self.assertIsNone(pool_row[1])

        conn.close()

    def test_04_telephony_ingestion_correlation_by_pin(self):
        """4. Inbound webhook postback by extension pin attributes campaign context, suppresses session, and auto-frees pin"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM operator_settings WHERE key = 'postback_secret'")
        secret = c.fetchone()[0]
        conn.close()

        product_id = "dni_pin_prod"
        session_id = "dni_session_2"
        transaction_id = f"tx_dni_pin_{int(time.time())}"

        # 1. Allocate extension pin to product
        vector = distributor.allocate_tracking_voice_vector(product_id)
        pin = vector["extension_pin"]

        # 2. Record click session context
        affiliate_tracker.record_click(
            product_id=product_id,
            product_title="Medical Health cover CPA",
            sector="health_insurance",
            channel="youtube",
            affiliate_link="https://offers.cpa-arbitrage.com/health-quote",
            session_id=session_id,
            user_agent="Mozilla/5.0"
        )

        # 3. Simulate inbound telephony webhook without click_id but containing extension_pin
        payload = {
            "extension_pin": pin,
            "sale_amount": 4000.0,
            "commission_amount": 90.0,
            "transaction_id": transaction_id,
            "network_name": "twilio_call_arbitrage"
        }
        raw_payload = json.dumps(payload)
        sig = hmac.new(secret.encode('utf-8'), raw_payload.encode('utf-8'), hashlib.sha256).hexdigest()

        # 4. Trigger Webhook Postback
        r = self.client.post(
            '/postback/cpa_lead',
            data=raw_payload,
            content_type='application/json',
            headers={"X-Signature": sig}
        )
        self.assertEqual(r.status_code, 200)

        # 5. Assert conversion table is updated
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT status, sale_amount, commission_amount FROM affiliate_conversions WHERE product_id = ?", (product_id,))
        row = c.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 'converted')
        self.assertEqual(row[1], 4000.0)

        # 6. Assert retargeting suppression is triggered
        c.execute("SELECT COUNT(*) FROM retargeting_suppression WHERE session_id = ? AND product_id = ?", (session_id, product_id))
        self.assertEqual(c.fetchone()[0], 1)

        # 7. Assert phone extension pin status is freed back to available
        c.execute("SELECT status, assigned_product_id FROM cpa_phone_pool WHERE extension_pin = ?", (pin,))
        pool_row = c.fetchone()
        self.assertEqual(pool_row[0], 'available')
        self.assertIsNone(pool_row[1])

        conn.close()

if __name__ == "__main__":
    unittest.main()
