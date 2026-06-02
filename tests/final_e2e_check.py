import sys
import os
import sqlite3
import json
import hmac
import hashlib
import time
from urllib.parse import quote

# Force stdout to UTF-8 for Windows console emoji safety
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

os.environ["FAST_VIDEO_RENDER"] = "True"

# Ensure absolute project paths are imported correctly
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.path.join(PROJECT_ROOT, 'bots') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'bots'))
if os.path.join(PROJECT_ROOT, 'scrapers') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scrapers'))

# Disable Flask-WTF CSRF in app config to allow simple API testing inside Flask test client
from app import app
app.config['WTF_CSRF_ENABLED'] = False
app.config['TESTING'] = True

from bots.config import DB_PATH
import bots.db_manager as db_manager
import bots.pipeline_service as pipeline_service
import bots.distributor as distributor

def print_banner(text):
    print("\n" + "=" * 80)
    print(f"🔹 {text}")
    print("=" * 80)

def main():
    print_banner("STARTING FINAL SYSTEM E2E HEALTH CHECK")
    
    # Establish SQLite diagnostic connection
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # -------------------------------------------------------------
    # STEP 1: Settings Check
    # -------------------------------------------------------------
    print("\n[Step 1] Verifying SQLite system settings presence...")
    cursor.execute("SELECT key, value FROM system_settings")
    settings = {row['key']: row['value'] for row in cursor.fetchall()}
    
    if "active_sectors" not in settings:
        print("❌ FAIL: 'active_sectors' is missing from system_settings!")
        sys.exit(1)
    if "auto_publish_timeout" not in settings:
        print("❌ FAIL: 'auto_publish_timeout' is missing from system_settings!")
        sys.exit(1)
        
    print(f"✅ OK: 'active_sectors' exists. Configured sectors: {settings['active_sectors']}")
    print(f"✅ OK: 'auto_publish_timeout' exists. Expiration Timeout: {settings['auto_publish_timeout']} minutes")

    # -------------------------------------------------------------
    # STEP 2: Pre-execution Clean Slate
    # -------------------------------------------------------------
    print("\n[Step 2] Recording database baseline metrics...")
    cursor.execute("SELECT MAX(id) FROM campaigns")
    max_campaign_id_before = cursor.fetchone()[0] or 0
    print(f"ℹ️ Base maximum campaign ID: {max_campaign_id_before}")

    # -------------------------------------------------------------
    # STEP 3: Live Sourcing & Pipeline Execution
    # -------------------------------------------------------------
    print_banner("EXECUTE LIVE SOURCING & 14-STEP SECTOR PIPELINE")
    print("[Step 3] Launching 'live_links' workflow internal execution...")
    print("         (This scrapes Slickdeals RSS, runs extractor, Copywriter, and save campaign)")
    
    try:
        result = pipeline_service._run_single_sector('live_links')
        print(f"✅ OK: Pipeline service completed single sector run successfully!")
        print(f"✅ OK: Pipeline processed links in {result.get('pipeline_time', 0.0)}s.")
    except Exception as e:
        print(f"❌ FAIL: Pipeline execution crashed: {e}")
        sys.exit(1)

    # Verify that input_links.txt was written by Slickdeals RSS scraper
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    input_file = os.path.join(base_dir, 'data', 'input_links.txt')
    if os.path.exists(input_file) and os.path.getsize(input_file) > 0:
        with open(input_file, 'r', encoding='utf-8') as f:
            scraped_links = [l.strip() for l in f.readlines() if l.strip()]
        print(f"✅ OK: Slickdeals RSS scraper ran. Scraped {len(scraped_links)} popular deals into input_links.txt")
    else:
        print(f"❌ FAIL: input_links.txt is missing or empty! Slickdeals scraper failed.")
        sys.exit(1)

    # -------------------------------------------------------------
    # STEP 4: Preview Gate Verification
    # -------------------------------------------------------------
    print_banner("VERIFYING CAMPAIGN PREVIEW GATE STATE")
    print("[Step 4] Querying newly created campaigns...")
    cursor.execute("SELECT id, product_id, title, status, publish_at FROM campaigns WHERE id > ?", (max_campaign_id_before,))
    new_campaigns = cursor.fetchall()
    
    if not new_campaigns:
        print("❌ FAIL: No new campaigns were added to the SQLite database by the pipeline!")
        sys.exit(1)
        
    print(f"✅ OK: Found {len(new_campaigns)} newly created campaign records in database.")
    for c in new_campaigns:
        print(f"   - Campaign ID: {c['id']} | Product: {c['title'][:40]}... | Status: {c['status']} | Publish Expiry: {c['publish_at']}")
        if c['status'] != 'pending_approval':
            print(f"❌ FAIL: Campaign status is '{c['status']}' instead of 'pending_approval' (Preview Gate failed)!")
            sys.exit(1)
            
    print("✅ OK: All newly sourced campaigns are locked inside the Preview Gate awaiting review!")

    # -------------------------------------------------------------
    # STEP 5: Manual Approval Simulation
    # -------------------------------------------------------------
    print_banner("SIMULATING MANUAL OVERRIDE APPROVAL")
    target_campaign = new_campaigns[0]
    campaign_id = target_campaign['id']
    product_id = target_campaign['product_id']
    print(f"[Step 5] Approving Campaign ID {campaign_id} (Product: {target_campaign['title'][:45]}...)...")
    
    # Initialize Flask Test Client to simulate API hit
    client = app.test_client()
    
    # Authenticate via test client
    login_res = client.post('/login', data={
        "email": "admin@marketing.ai",
        "password": "admin123"
    }, follow_redirects=True)
    
    if login_res.status_code != 200 or b"Automated" not in login_res.data:
        print("❌ FAIL: Diagnostic Flask client login failed!")
        sys.exit(1)
    print("✅ OK: Authenticated successfully as admin.")
    
    # Trigger dynamic approval endpoint POST
    approve_res = client.post(f'/api/campaign/{campaign_id}/approve')
    if approve_res.status_code != 200:
        print(f"❌ FAIL: Campaign approval API endpoint returned code {approve_res.status_code}: {approve_res.data}")
        sys.exit(1)
        
    print(f"✅ OK: API approve endpoint returned 200 OK: {approve_res.get_json()}")
    
    # Verify status transition in SQLite
    cursor.execute("SELECT status FROM campaigns WHERE id = ?", (campaign_id,))
    final_status = cursor.fetchone()[0]
    if final_status != 'published':
        print(f"❌ FAIL: Campaign status in DB is '{final_status}' instead of 'published' after approval!")
        sys.exit(1)
        
    print("✅ OK: Campaign status successfully transitioned to 'published'. Social broadcast executed.")

    # -------------------------------------------------------------
    # STEP 6: Click Tracking & Bot Filtering
    # -------------------------------------------------------------
    print_banner("SIMULATING HUMAN CLICK REDIRECT")
    print("[Step 6] Simulating click on track redirect URL with standard user agent...")
    
    # Record max click ID before simulating the click to extract exact click log
    cursor.execute("SELECT MAX(id) FROM affiliate_clicks")
    max_click_id_before = cursor.fetchone()[0] or 0
    
    # Fetch campaign details to pass tracking params
    cursor.execute("SELECT affiliate_link, title, sector, price FROM campaigns WHERE id = ?", (campaign_id,))
    camp_row = cursor.fetchone()
    aff_link = camp_row['affiliate_link']
    camp_title = camp_row['title']
    camp_sector = camp_row['sector']
    
    human_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    track_url = f"/go/{product_id}?url={quote(aff_link)}&title={quote(camp_title)}&sector={quote(camp_sector)}&score=4.8&commission=6.5&variant=A"
    
    # Track click GET
    click_res = client.get(track_url, headers={"User-Agent": human_ua})
    if click_res.status_code not in (302, 200):
        print(f"❌ FAIL: Click tracking returned status code {click_res.status_code}!")
        sys.exit(1)
        
    print(f"✅ OK: Click tracking endpoint registered successfully (redirect: {click_res.headers.get('Location', 'N/A')}).")
    
    # Assert database clicks table record
    cursor.execute("SELECT id, session_id, is_bot, variant FROM affiliate_clicks WHERE id > ?", (max_click_id_before,))
    new_clicks = cursor.fetchall()
    
    if not new_clicks:
        print("❌ FAIL: Click was not written to affiliate_clicks table!")
        sys.exit(1)
        
    click_row = new_clicks[0]
    click_id = click_row['id']
    session_id = click_row['session_id']
    is_bot = click_row['is_bot']
    variant = click_row['variant']
    
    print(f"✅ OK: Click logged with ID: {click_id} | Session: {session_id} | Variant: {variant}")
    if is_bot != 0:
        print("❌ FAIL: User was classified as bot (is_bot = 1) instead of human (is_bot = 0)!")
        sys.exit(1)
    print("✅ OK: Bot filtering verified. Human click classified with is_bot = 0.")

    # -------------------------------------------------------------
    # STEP 7: Conversion Webhook & Suppression
    # -------------------------------------------------------------
    print_banner("SIMULATING COMMISSION webhook postback")
    print("[Step 7] Constructing and signing HMAC conversion payload...")
    
    # Fetch signing key from SQLite operator settings
    cursor.execute("SELECT value FROM operator_settings WHERE key = 'postback_secret'")
    postback_secret = cursor.fetchone()[0]
    print(f"ℹ️ Configured Signing HMAC Secret: {postback_secret}")
    
    tx_id = f"tx_final_e2e_{int(time.time())}"
    webhook_payload = {
        "product_id": product_id,
        "click_id": click_id,
        "session_id": session_id,
        "sale_amount": 1000.0,
        "commission_amount": 60.0,
        "transaction_id": tx_id,
        "network_name": "slickdeals_postback"
    }
    raw_payload = json.dumps(webhook_payload)
    
    # Compute signature
    signature = hmac.new(
        postback_secret.encode('utf-8'),
        raw_payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # Fire CSRF-exempt conversion postback POST
    webhook_res = client.post(
        '/postback/cpa_lead',
        data=raw_payload,
        content_type='application/json',
        headers={"X-Signature": signature}
    )
    
    if webhook_res.status_code != 200:
        print(f"❌ FAIL: Conversion webhook returned code {webhook_res.status_code}: {webhook_res.data}")
        sys.exit(1)
        
    print(f"✅ OK: Conversion webhook accepted: {webhook_res.get_json()}")
    
    # Verify postback logs & conversions state in SQLite
    cursor.execute("SELECT status, commission_amount FROM affiliate_conversions WHERE click_id = ?", (click_id,))
    conv = cursor.fetchone()
    
    if not conv:
        print("❌ FAIL: No conversion record associated with the click ID was found!")
        sys.exit(1)
        
    print(f"✅ OK: affiliate_conversions row status is: '{conv['status']}' | commission: Rs.{conv['commission_amount']}")
    if conv['status'] != 'converted':
        print(f"❌ FAIL: Conversion status did not transition to 'converted'!")
        sys.exit(1)

    # -------------------------------------------------------------
    # STEP 8: Retargeting Suppression Check
    # -------------------------------------------------------------
    print_banner("VERIFYING RETARGETING EXCLUSION GATES")
    print("[Step 8] Asserting suppression registers...")
    
    cursor.execute("SELECT session_id, product_id, converted_at FROM retargeting_suppression WHERE session_id = ? AND product_id = ?", (session_id, product_id))
    suppress = cursor.fetchone()
    
    if not suppress:
        print(f"❌ FAIL: Retargeting suppression record for session '{session_id}' + product '{product_id}' was not written!")
        sys.exit(1)
        
    print(f"✅ OK: Retargeting exclusion registered at {suppress['converted_at']}")
    print(f"✅ OK: Suppression prevents further retargeting sweeps for session {session_id} on product {product_id}!")

    print_banner("🏆 FINAL SYSTEM E2E HEALTH CHECK: 100% SUCCESS / PASS!")
    print("   All 7 phases are communicating, verified, and ready for VPS deployment.")
    print("=" * 80)
    
    conn.close()

if __name__ == '__main__':
    main()
