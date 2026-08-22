"""
Local backend verification â€” database integrity, auth flow, dashboard render.

Uses a dynamic temp-database workaround (DB_PATH env override) so tests never
touch the production SQLite file.
"""

import os
import sys
import tempfile
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

_TMP = tempfile.mkdtemp(prefix="affiliate_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["FLASK_SECRET_KEY"] = "test_secret"
os.environ["POSTBACK_SECRET"] = "test_postback_secret"
os.environ["ADMIN_DEFAULT_PASSWORD"] = "admin123"   # hermetic creds, not operator .env
# Sandbox: neutralize live external services regardless of operator .env
for _k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GEMINI_API_KEY",
           "SMTP_HOST", "AMAZON_PAAPI_ACCESS_KEY", "AMAZON_PAAPI_SECRET_KEY"):
    os.environ[_k] = ""

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


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def test_db_integrity():
    print("\n== DB Integrity ==")
    db_manager.setup_database()
    conn = sqlite3.connect(DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    required = {
        "campaigns", "distribution_logs", "affiliate_clicks",
        "affiliate_conversions", "system_settings", "users",
        "circuit_breaker_state", "api_quota_usage", "scheduler_jobs",
        "idempotency_keys", "job_queue", "dead_letter_jobs",
    }
    for table in required:
        check(f"table exists: {table}", table in tables)

    # Strict compliance â€” forbidden tables must NOT exist.
    for forbidden in ("user_wallets", "payout_transactions", "cpa_phone_pool"):
        check(f"no forbidden table: {forbidden}", forbidden not in tables)

    # campaign schema columns
    conn = sqlite3.connect(DB_PATH)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(campaigns)")}
    conn.close()
    for col in ("title", "sector", "target_url", "price", "discount", "commission", "status", "publish_at"):
        check(f"campaigns column: {col}", col in cols)

    # system_settings seeds
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT value FROM system_settings WHERE key='primary_routing_domain'").fetchone()
    conn.close()
    check("system_settings seeded: primary_routing_domain", row is not None and "amazon" in row[0])

    # WAL mode
    conn = sqlite3.connect(DB_PATH)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    check("journal_mode = WAL", str(mode).lower() == "wal")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def test_auth_flow():
    print("\n== Auth Flow ==")
    client = app_module.app.test_client()

    # Login page renders.
    resp = client.get("/login")
    check("GET /login returns 200", resp.status_code == 200)
    check("login page has form", b"name=\"username\"" in resp.data)

    # Disable CSRF for the pure auth-flow test (CSRF is verified separately).
    app_module.app.config["WTF_CSRF_ENABLED"] = False

    # Bad credentials are rejected.
    resp = client.post("/login", data={"username": "admin", "password": "wrong"},
                       follow_redirects=False)
    check("bad password stays on login (200)", resp.status_code == 200)

    # Good credentials log in.
    resp = client.post("/login", data={"username": "admin", "password": "admin123"},
                       follow_redirects=False)
    check("valid login redirects (302)", resp.status_code == 302)

    resp = client.get("/")
    check("GET / renders 200 after login", resp.status_code == 200)

    resp = client.get("/history")
    check("GET /history renders 200", resp.status_code == 200)

    resp = client.get("/settings")
    check("GET /settings renders 200 (admin)", resp.status_code == 200)

    # Logout.
    resp = client.get("/logout", follow_redirects=False)
    check("logout redirects (302)", resp.status_code == 302)

    # Access control â€” dashboard requires auth.
    resp = client.get("/", follow_redirects=False)
    check("GET / redirects when anonymous (302)", resp.status_code == 302)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def test_dashboard_api():
    print("\n== Dashboard API ==")
    client = app_module.app.test_client()
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    client.post("/login", data={"username": "admin", "password": "admin123"})

    resp = client.get("/api/performance")
    check("GET /api/performance returns 200", resp.status_code == 200)
    check("performance payload is success", resp.get_json().get("status") == "success")

    resp = client.get("/api/sectors")
    check("GET /api/sectors returns 200", resp.status_code == 200)
    check("electronics sector present", b"electronics" in resp.data)

    # Unauthenticated API access is rejected.
    client.get("/logout")
    resp = client.get("/api/performance")
    check("anonymous /api/performance rejected (401)", resp.status_code == 401)


if __name__ == "__main__":
    print("verify_local_backend â€” local backend verification")
    test_db_integrity()
    test_auth_flow()
    test_dashboard_api()
    print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
