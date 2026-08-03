"""
Attribution verification — open-redirect protection and bot-click filtering.

Confirms:
    * trusted affiliate domains (amazon.in) pass the /go/ redirect.
    * untrusted targets (evil.com) are rejected with HTTP 400.
    * bot user-agents are filtered (is_bot = 1) and never counted as humans.
"""

import os
import sys
import tempfile
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

_TMP = tempfile.mkdtemp(prefix="affiliate_attrib_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["FLASK_SECRET_KEY"] = "test_secret"

import app as app_module  # noqa: E402
import db_manager  # noqa: E402
import affiliate_tracker  # noqa: E402
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


def test_open_redirect_protection():
    print("\n== Open Redirect Protection ==")
    client = app_module.app.test_client()

    # Trusted domain passes.
    target = "https://www.amazon.in/dp/B0TEST1234?tag=affiliate-21"
    resp = client.get(f"/go/PROD1?url={target}&title=Test%20Item&sector=electronics", follow_redirects=False)
    check("trusted amazon.in redirect accepted (302)", resp.status_code == 302, f"got {resp.status_code}")
    if resp.status_code == 302:
        check("redirect target preserved", "amazon.in" in resp.headers.get("Location", ""))

    # Subdomain of trusted domain passes.
    target2 = "https://smile.amazon.in/dp/B0TEST1234"
    resp = client.get(f"/go/PROD2?url={target2}", follow_redirects=False)
    check("trusted subdomain accepted (302)", resp.status_code == 302, f"got {resp.status_code}")

    # Evil domain rejected.
    evil = "https://evil.com/phish?steal=1"
    resp = client.get(f"/go/PROD3?url={evil}", follow_redirects=False)
    check("evil.com rejected (400)", resp.status_code == 400, f"got {resp.status_code}")

    # Scheme-only (javascript:) rejected.
    resp = client.get("/go/PROD4?url=javascript:alert(1)", follow_redirects=False)
    check("javascript: URL rejected (400)", resp.status_code == 400, f"got {resp.status_code}")


def test_bot_click_filtering():
    print("\n== Bot Click Filtering ==")
    db_manager.setup_database()

    human_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    bot_ua = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

    click_human = affiliate_tracker.record_click(
        product_id="PROD_H", channel="telegram", user_agent=human_ua,
        ip_address="203.0.113.1", variant="A",
    )
    click_bot = affiliate_tracker.record_click(
        product_id="PROD_B", channel="telegram", user_agent=bot_ua,
        ip_address="203.0.113.2", variant="B",
    )

    conn = sqlite3.connect(DB_PATH)
    human_flag = conn.execute("SELECT is_bot FROM affiliate_clicks WHERE id = ?", (click_human,)).fetchone()[0]
    bot_flag = conn.execute("SELECT is_bot FROM affiliate_clicks WHERE id = ?", (click_bot,)).fetchone()[0]
    conn.close()

    check("human UA flagged is_bot=0", human_flag == 0, f"got {human_flag}")
    check("bot UA flagged is_bot=1", bot_flag == 1, f"got {bot_flag}")

    # Human-only analytics must exclude the bot click.
    stats = affiliate_tracker.get_click_stats()
    prod_b_count = sum(1 for p in stats["top_products"] if p["product_id"] == "PROD_B")
    check("bot click excluded from analytics", prod_b_count == 0)


def test_variant_tracking():
    print("\n== Variant Attribution ==")
    variant = affiliate_tracker.record_click(
        product_id="PROD_V", channel="direct", user_agent="Mozilla/5.0 human",
        ip_address="198.51.100.7", variant="A",
    )
    conn = sqlite3.connect(DB_PATH)
    stored = conn.execute("SELECT variant FROM affiliate_clicks WHERE id = ?", (variant,)).fetchone()[0]
    conn.close()
    check("variant A stored", stored == "A", f"got {stored}")


if __name__ == "__main__":
    print("verify_attribution — attribution & redirect verification")
    test_open_redirect_protection()
    test_bot_click_filtering()
    test_variant_tracking()
    print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
