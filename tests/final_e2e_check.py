"""
Master end-to-end pipeline verification.

Exercises the complete closed loop against a temp database:
    sourcing → copy → video/poster → save campaign → preview gate approval
    → distribution (mock) → tracked click → HMAC postback conversion
    → A/B bandit + EV ranking reflected in the performance API.
"""

import os
import sys
import json
import tempfile
import hmac
import hashlib
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

_TMP = tempfile.mkdtemp(prefix="affiliate_e2e_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["FLASK_SECRET_KEY"] = "test_secret"
os.environ["POSTBACK_SECRET"] = "test_postback_secret"
os.environ["MOCK_SOURCING"] = "True"
os.environ["FAST_VIDEO_RENDER"] = "True"

import app as app_module  # noqa: E402
import db_manager  # noqa: E402
from config import DB_PATH  # noqa: E402

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def _sign(body, secret):
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def main():
    client = app_module.app.test_client()
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    client.post("/login", data={"username": "admin", "password": "admin123"})

    # ── 1. Source + compose + persist campaigns ────────────────────────────
    print("\n== Pipeline: source → compose → persist ==")
    resp = client.post("/api/run_pipeline", json={"sector": "electronics"})
    check("pipeline run returns 200", resp.status_code == 200, f"got {resp.status_code}: {resp.data}")
    data = resp.get_json()
    check("pipeline created campaigns", data.get("campaigns_created", 0) > 0, str(data))

    conn = sqlite3.connect(DB_PATH)
    pending = conn.execute("SELECT COUNT(*) FROM campaigns WHERE status='pending_approval'").fetchone()[0]
    conn.close()
    check("campaigns persisted as pending_approval", pending > 0, f"got {pending}")

    # ── 2. Preview gate approval → distribution ────────────────────────────
    print("\n== Preview gate: approve → distribute ==")
    conn = sqlite3.connect(DB_PATH)
    camp = conn.execute("SELECT id, product_id, title, target_url FROM campaigns LIMIT 1").fetchone()
    conn.close()
    campaign_id = camp[0]
    product_id = camp[1]
    target_url = camp[3]

    resp = client.post(f"/api/campaign/{campaign_id}/approve")
    check("approve endpoint returns 200", resp.status_code == 200, f"got {resp.status_code}: {resp.data}")

    conn = sqlite3.connect(DB_PATH)
    status = conn.execute("SELECT status FROM campaigns WHERE id=?", (campaign_id,)).fetchone()[0]
    logs = conn.execute("SELECT COUNT(*) FROM distribution_logs WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
    conn.close()
    check("campaign status updated to published", status == "published", f"got {status}")
    check("distribution logs written", logs > 0, f"got {logs}")

    # ── 3. Tracked click through /go/ ──────────────────────────────────────
    print("\n== Click attribution ==")
    go_url = f"/go/{product_id}?url={target_url}&title=Test&sector=electronics&var=A"
    resp = client.get(go_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"}, follow_redirects=False)
    check("tracked click redirects (302)", resp.status_code == 302, f"got {resp.status_code}")

    conn = sqlite3.connect(DB_PATH)
    clicks = conn.execute("SELECT COUNT(*) FROM affiliate_clicks WHERE product_id=? AND is_bot=0", (product_id,)).fetchone()[0]
    conn.close()
    check("human click recorded", clicks >= 1, f"got {clicks}")

    # ── 4. HMAC conversion postback ────────────────────────────────────────
    print("\n== Conversion postback ==")
    payload = {
        "transaction_id": "E2E_TXN_001",
        "product_id": product_id,
        "session_id": "e2e-session",
        "sale_amount": 4999.0,
        "commission_amount": 150.0,
    }
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body, db_manager.get_postback_secret())
    resp = client.post("/postback/conversion", data=body,
                       content_type="application/json", headers={"X-Signature": sig})
    check("postback accepted (200)", resp.status_code == 200, f"got {resp.status_code}: {resp.data}")

    # ── 5. Optimisation loop: A/B bandit + EV ranking ──────────────────────
    print("\n== Optimisation loop ==")
    resp = client.get("/api/performance")
    data = resp.get_json()
    check("performance API healthy", data.get("status") == "success")

    check("sector ranking present", isinstance(data.get("sector_ranking"), list))
    if data.get("sector_ranking"):
        check("sector ranking is EV-ordered",
              data["sector_ranking"][0]["ev"] >= data["sector_ranking"][-1]["ev"])

    check("A/B results present", isinstance(data.get("ab", {}).get("results"), list))
    check("conversion reflected in stats",
          data.get("stats", {}).get("total_converted", 0) >= 1)

    # ── 6. Scheduler status ────────────────────────────────────────────────
    print("\n== Scheduler status ==")
    resp = client.get("/api/scheduler_status")
    check("scheduler status returns 200", resp.status_code == 200)
    sdata = resp.get_json()
    check("scheduler jobs registered", len(sdata.get("jobs", [])) >= 1)


if __name__ == "__main__":
    print("final_e2e_check — master pipeline verification")
    main()
    print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
