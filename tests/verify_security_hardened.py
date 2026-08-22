"""
Security hardening verification â€” HMAC webhook authentication, CSRF, and
idempotent conversion processing.

Confirms:
    * a correctly signed postback is accepted (HTTP 200).
    * a forged signature is rejected (HTTP 401).
    * a missing signature header is rejected (HTTP 401).
    * duplicate transaction IDs are processed exactly once (idempotency).
    * CSRF protection rejects token-less mutating POSTs.
"""

import os
import sys
import json
import tempfile
import hmac
import hashlib

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

_TMP = tempfile.mkdtemp(prefix="affiliate_sec_")
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


def _sign(payload_bytes, secret):
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def test_hmac_webhook():
    print("\n== HMAC Webhook ==")
    client = app_module.app.test_client()

    payload = {
        "transaction_id": "TXN_HMAC_001",
        "product_id": "B0TEST1234",
        "session_id": "sess-abc",
        "sale_amount": 5999.0,
        "commission_amount": 120.0,
    }
    body = json.dumps(payload)

    # Valid signature accepted.
    sig = _sign(body.encode("utf-8"), db_manager.get_postback_secret())
    resp = client.post(
        "/postback/conversion",
        data=body,
        content_type="application/json",
        headers={"X-Signature": sig},
    )
    check("valid signature accepted (200)", resp.status_code == 200, f"got {resp.status_code}: {resp.data}")

    # Forged signature rejected.
    forged_sig = _sign(body.encode("utf-8"), "wrong_secret")
    resp = client.post(
        "/postback/conversion",
        data=body,
        content_type="application/json",
        headers={"X-Signature": forged_sig},
    )
    check("forged signature rejected (401)", resp.status_code == 401, f"got {resp.status_code}")

    # Missing signature rejected.
    resp = client.post("/postback/conversion", data=body, content_type="application/json")
    check("missing signature rejected (401)", resp.status_code == 401, f"got {resp.status_code}")


def test_idempotency():
    print("\n== Conversion Idempotency ==")
    client = app_module.app.test_client()

    payload = {
        "transaction_id": "TXN_DUP_001",
        "product_id": "B0TEST5678",
        "session_id": "sess-dup",
        "sale_amount": 8999.0,
        "commission_amount": 240.0,
    }
    body = json.dumps(payload)
    sig = _sign(body.encode("utf-8"), db_manager.get_postback_secret())
    headers = {"X-Signature": sig, "Content-Type": "application/json"}

    resp1 = client.post("/postback/conversion", data=body, headers=headers)
    resp2 = client.post("/postback/conversion", data=body, headers=headers)

    check("first delivery recorded (200)", resp1.status_code == 200)
    check("duplicate delivery idempotent (200)", resp2.status_code == 200)
    check("duplicate flagged as idempotent", b"idempotent" in resp2.data.lower())

    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM affiliate_conversions WHERE transaction_id = 'TXN_DUP_001'"
    ).fetchone()[0]
    conn.close()
    check("conversion stored exactly once", count == 1, f"got {count} rows")


def test_csrf_protection():
    print("\n== CSRF Protection ==")
    client = app_module.app.test_client()
    app_module.app.config["WTF_CSRF_ENABLED"] = True

    # Obtain a valid CSRF token by GETting the login page.
    resp = client.get("/login")
    token_html = resp.get_data(as_text=True)
    import re
    match = re.search(r'name="csrf_token" value="([^"]+)"', token_html)
    check("login page exposes csrf_token", match is not None)
    token = match.group(1) if match else ""

    resp = client.post("/login", data={
        "username": "admin",
        "password": "admin123",
        "csrf_token": token,
    }, follow_redirects=False)
    check("login with csrf_token succeeds (302)", resp.status_code == 302, f"got {resp.status_code}")

    # Mutating POST without a CSRF token must be rejected (400).
    resp = client.post("/settings", data={"auto_publish_timeout": "15"})
    check("token-less POST /settings rejected (400)", resp.status_code == 400, f"got {resp.status_code}")


if __name__ == "__main__":
    print("verify_security_hardened â€” security hardening verification")
    test_hmac_webhook()
    test_idempotency()
    test_csrf_protection()
    print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
