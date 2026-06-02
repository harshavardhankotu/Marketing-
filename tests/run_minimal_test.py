import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests
import json
import sqlite3

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def check_db_counts(label=""):
    from bots.config import DB_PATH
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM campaigns")
    campaigns = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM distribution_logs")
    dist = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM affiliate_clicks")
    clicks = c.fetchone()[0]
    conn.close()
    print(f"  [DB {label}] Campaigns: {campaigns} | Dist Logs: {dist} | Clicks: {clicks}")
    return campaigns, dist, clicks

def main():
    base = 'http://127.0.0.1:5000'
    print("==================================================")
    print("STARTING MINIMAL TEST PLAN IN WORKSPACE")
    print("==================================================")
    
    # Check database status beforehand
    print("\n[STEP 1] Checking DB Baseline:")
    c0, d0, cl0 = check_db_counts("BASELINE")
    
    # 1. Test standard HTTP Routes
    print("\n[STEP 2] Verifying standard routes:")
    routes = [
        ('/', 'GET'),
        ('/history', 'GET'),
        ('/api/history', 'GET'),
        ('/api/clicks', 'GET'),
        ('/api/retargeting_stats', 'GET'),
        ('/api/scheduler_status', 'GET'),
    ]
    
    for route, method in routes:
        try:
            r = requests.get(base + route, timeout=10)
            print(f"  {method} {route} -> Code: {r.status_code}")
        except Exception as e:
            print(f"  {method} {route} -> ERROR: {e}")
            
    # 2. Test POST /api/run_retargeting
    print("\n[STEP 3] Triggering POST /api/run_retargeting:")
    try:
        r = requests.post(base + '/api/run_retargeting', json={}, timeout=10)
        print(f"  POST /api/run_retargeting -> Code: {r.status_code}")
        if r.status_code == 200:
            print(f"    Payload: {list(r.json().keys())}")
    except Exception as e:
        print(f"  POST /api/run_retargeting -> ERROR: {e}")
        
    # 3. Test GET /go/prod_0 click tracker
    print("\n[STEP 4] Testing GET /go/prod_0 tracking redirect:")
    prod_id = "prod_0"
    target_url = "https://www.amazon.in/s?k=iPhone+5s&tag=agency_tag-21"
    redirect_route = f"/go/{prod_id}?url={target_url}&title=iPhone+5s&sector=smartphones&score=95&commission=10"
    try:
        r = requests.get(base + redirect_route, allow_redirects=False, timeout=10)
        print(f"  GET {redirect_route}")
        print(f"    Code: {r.status_code}")
        print(f"    Location Header: {r.headers.get('Location')}")
    except Exception as e:
        print(f"  GET {redirect_route} -> ERROR: {e}")
        
    # Check database status after click tracker
    print("\n[STEP 5] Checking DB post click redirect:")
    c1, d1, cl1 = check_db_counts("POST-CLICK")
    if cl1 > cl0:
        print("  [PASS] Click-tracker successfully updated affiliate_clicks table!")
    else:
        print("  [FAIL] click count did not increment.")
        
    # 4. Trigger E2E Single Workflow for "live_links"
    print("\n[STEP 6] Triggering E2E pipeline for 'live_links' sector:")
    pipeline_url = f"{base}/api/run_pipeline"
    payload = {"sector": "live_links"}
    try:
        print("  Sending POST request (this runs Playwright and 19 pipeline subprocesses)...")
        r = requests.post(pipeline_url, json=payload, timeout=300)
        print(f"  POST /api/run_pipeline -> Code: {r.status_code}")
        if r.status_code == 200:
            res = r.json()
            print("  [PASS] E2E Pipeline completed successfully!")
            print(f"    Products completed: {len(res.get('data', []))}")
        else:
            print("  [FAIL] Pipeline returned error:")
            print(r.text)
    except Exception as e:
        print(f"  POST /api/run_pipeline -> ERROR: {e}")
        
    # Final check database status after E2E pipeline
    print("\n[STEP 7] Checking DB post E2E pipeline:")
    c2, d2, cl2 = check_db_counts("POST-E2E")
    if c2 > c1:
        print(f"  [PASS] E2E pipeline successfully wrote {c2 - c1} new campaign records to SQLite campaigns table!")
    else:
        print("  [FAIL] Campaign row count did not increment.")

if __name__ == "__main__":
    main()
