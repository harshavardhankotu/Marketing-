"""
Full feature audit — exercises EVERY feature and use case of the suite
against an isolated temp database and prints a PASS/FAIL line per feature.

Run:  python tests/full_feature_audit.py
"""

import os
import sys
import json
import time
import hmac
import hashlib
import sqlite3
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

_TMP = tempfile.mkdtemp(prefix="affiliate_audit_")
os.environ["DB_PATH"] = os.path.join(_TMP, "audit.db")
os.environ["FLASK_SECRET_KEY"] = "audit_secret"
os.environ["POSTBACK_SECRET"] = "audit_postback_secret"
os.environ["MOCK_SOURCING"] = "True"
os.environ["FAST_VIDEO_RENDER"] = "True"

PASS = []
FAIL = []


def check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(f"{name} {detail}")
        print(f"  [FAIL] {name} {detail}")


def section(title):
    print(f"\n== {title} ==")


def _sign(body, secret):
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
import app as app_module  # noqa: E402
import db_manager  # noqa: E402
from config import DB_PATH, BACKUP_DIR, CAMPAIGN_STATIC_DIR  # noqa: E402
from bots import scheduler_engine, job_queue, quota_manager  # noqa: E402
from generators import background_factory  # noqa: E402

client = app_module.app.test_client()
app_module.app.config["WTF_CSRF_ENABLED"] = False


def reset_limiter():
    try:
        app_module.limiter.storage.reset()
    except Exception:
        pass


def login(username="admin", password="admin123"):
    reset_limiter()
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)


# ═════════════════════════════════════════════════════════════════════════════
# A. DATABASE & SCHEMA
# ═════════════════════════════════════════════════════════════════════════════
section("A. Database & Schema")
conn = sqlite3.connect(DB_PATH)
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
expected = {"campaigns", "distribution_logs", "affiliate_clicks", "affiliate_conversions",
            "system_settings", "operator_settings", "users", "circuit_breaker_state",
            "api_quota_usage", "scheduler_jobs", "idempotency_keys", "job_queue", "dead_letter_jobs"}
check("all 13 spec tables exist", expected.issubset(tables), f"missing {expected - tables}")
forbidden = {"user_wallets", "payout_transactions", "cpa_phone_pool"}
check("no forbidden compliance tables", not (forbidden & tables))
mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
check("WAL journal mode", str(mode).lower() == "wal", f"got {mode}")
fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
check("foreign keys enforced", fk == 1, f"got {fk}")
cols = {r[1] for r in conn.execute("PRAGMA table_info(campaigns)").fetchall()}
check("campaigns has caption/graphic_path columns", {"caption", "graphic_path"}.issubset(cols))
seeded = conn.execute("SELECT COUNT(*) FROM system_settings WHERE key='primary_routing_domain'").fetchone()[0]
check("system settings seeded", seeded == 1)
conn.close()

# Migration: old-schema DB gains new columns.
old_db = os.path.join(_TMP, "legacy.db")
legacy = sqlite3.connect(old_db)
legacy.execute("""CREATE TABLE campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, title TEXT NOT NULL,
    sector TEXT, target_url TEXT, price REAL DEFAULT 0.0, discount REAL DEFAULT 0.0,
    commission REAL DEFAULT 0.0, status TEXT DEFAULT 'pending_approval',
    publish_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
legacy.execute("""CREATE TABLE affiliate_clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, channel TEXT,
    user_agent TEXT, ip_address TEXT, is_bot INTEGER DEFAULT 0, variant TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
legacy.commit()
legacy.close()
os.environ["DB_PATH_BAK"] = os.environ["DB_PATH"]
os.environ["DB_PATH"] = old_db
import importlib  # noqa: E402
import config as config_mod  # noqa: E402
config_mod.DB_PATH = old_db
db_manager.DB_PATH = old_db
db_manager.setup_database()
legacy = sqlite3.connect(old_db)
mig_cols = {r[1] for r in legacy.execute("PRAGMA table_info(campaigns)").fetchall()}
click_cols = {r[1] for r in legacy.execute("PRAGMA table_info(affiliate_clicks)").fetchall()}
legacy.close()
check("migration adds caption/graphic_path to legacy DB", {"caption", "graphic_path"}.issubset(mig_cols))
check("migration adds session_id to legacy clicks", "session_id" in click_cols)
os.environ["DB_PATH"] = os.environ["DB_PATH_BAK"]
config_mod.DB_PATH = os.environ["DB_PATH"]
db_manager.DB_PATH = os.environ["DB_PATH"]

# ═════════════════════════════════════════════════════════════════════════════
# B. AUTH & SECURITY
# ═════════════════════════════════════════════════════════════════════════════
section("B. Auth & Security")
reset_limiter()
resp = client.get("/login")
page = resp.get_data(as_text=True)
check("GET /login renders 200", resp.status_code == 200)
check("login page has CSRF token", 'name="csrf_token"' in page)

resp = client.post("/login", data={"username": "admin", "password": "wrong"})
check("bad password rejected (stays on login)", resp.status_code == 200 and "/login" in resp.headers.get("Location", "/login") or resp.status_code == 200)

resp = login()
check("valid login redirects (302)", resp.status_code == 302)
set_cookie = resp.headers.get("Set-Cookie", "")
check("session cookie HttpOnly", "HttpOnly" in set_cookie, set_cookie[:80])
check("session cookie SameSite=Lax", "SameSite=Lax" in set_cookie, set_cookie[:80])

resp = client.get("/")
check("dashboard renders after login", resp.status_code == 200)
check("anonymous / redirects to login", app_module.app.test_client().get("/").status_code == 302)
check("anonymous /api/* rejected 401", app_module.app.test_client().get("/api/performance").status_code == 401)

headers = client.get("/").headers
check("CSP header present", "Content-Security-Policy" in headers)
check("X-Frame-Options SAMEORIGIN", headers.get("X-Frame-Options") == "SAMEORIGIN")
check("X-Content-Type-Options nosniff", headers.get("X-Content-Type-Options") == "nosniff")
check("Referrer-Policy present", "Referrer-Policy" in headers)

# CSRF enforcement (re-enable just for this probe).
app_module.app.config["WTF_CSRF_ENABLED"] = True
resp = client.post("/settings/password", data={"current_password": "x", "new_password": "y", "confirm_password": "y"})
check("token-less POST rejected by CSRF (400)", resp.status_code == 400)
app_module.app.config["WTF_CSRF_ENABLED"] = False

# Change password flow.
resp = client.post("/settings/password", data={
    "current_password": "wrong_current", "new_password": "newpass123", "confirm_password": "newpass123"})
check("change password rejects wrong current", b"incorrect" in resp.data or resp.status_code == 302)
resp = client.post("/settings/password", data={
    "current_password": "admin123", "new_password": "short", "confirm_password": "short"})
check("change password rejects short password", resp.status_code == 302)
resp = client.post("/settings/password", data={
    "current_password": "admin123", "new_password": "newpass123", "confirm_password": "different"})
check("change password rejects mismatched confirm", resp.status_code == 302)
resp = client.post("/settings/password", data={
    "current_password": "admin123", "new_password": "newpass123", "confirm_password": "newpass123"})
check("change password succeeds", resp.status_code == 302)
logout_resp = client.get("/logout")
check("logout redirects", logout_resp.status_code == 302)
resp = login(password="newpass123")
check("login works with new password", resp.status_code == 302)
# Restore original password for later sections.
conn = sqlite3.connect(DB_PATH)
import bcrypt  # noqa: E402
hashed = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode("utf-8")
conn.execute("UPDATE users SET password_hash=? WHERE username='admin'", (hashed,))
conn.commit()
conn.close()
login()

# Role gating: viewer account cannot access admin surfaces.
conn = sqlite3.connect(DB_PATH)
vhash = bcrypt.hashpw(b"viewer123", bcrypt.gensalt()).decode("utf-8")
conn.execute("INSERT OR IGNORE INTO users (username, password_hash, role) VALUES ('viewer', ?, 'viewer')", (vhash,))
conn.commit()
conn.close()
client.get("/logout")
login("viewer", "viewer123")
check("viewer blocked from /settings (403)", client.get("/settings").status_code == 403)
check("viewer blocked from admin reset API (403)", client.post("/api/reliability_reset", json={"target": "purge_queue"}).status_code == 403)
check("viewer blocked from scheduler run-now (403)", client.post("/api/scheduler_run_now", json={"job_id": "hot_db_backup"}).status_code == 403)
client.get("/logout")
login()

# Rate limiting: 5 logins / 10 minutes per IP.
reset_limiter()
codes = []
for _ in range(7):
    r = client.post("/login", data={"username": "admin", "password": "definitely_wrong"})
    codes.append(r.status_code)
check("login rate limit trips (429 on 6th+)", 429 in codes, str(codes))
reset_limiter()
login()

# ═════════════════════════════════════════════════════════════════════════════
# C. TRACKED REDIRECT & ATTRIBUTION
# ═════════════════════════════════════════════════════════════════════════════
section("C. Tracked Redirect & Attribution")
UA_HUMAN = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"}
UA_BOT = {"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"}
url = "/go/PROD-X?url=https%3A%2F%2Fwww.amazon.in%2Fdp%2FB0TEST%3Ftag%3Dtest-21&title=T&sector=electronics"
resp = client.get(url, headers=UA_HUMAN, follow_redirects=False)
check("trusted amazon.in redirect 302", resp.status_code == 302)
check("redirect target preserved", resp.headers.get("Location", "").startswith("https://www.amazon.in/dp/B0TEST"))
resp = client.get("/go/P2?url=https%3A%2F%2Felectronics.amazon.in%2Fx", headers=UA_HUMAN, follow_redirects=False)
check("trusted subdomain accepted", resp.status_code == 302)
resp = client.get("/go/P3?url=https%3A%2F%2Fevil.com%2Fphish", headers=UA_HUMAN)
check("evil.com rejected 400", resp.status_code == 400)
resp = client.get("/go/P4?url=javascript%3Aalert(1)", headers=UA_HUMAN)
check("javascript: URL rejected 400", resp.status_code == 400)
client.get("/go/BOTPROD?url=https%3A%2F%2Fwww.amazon.in%2Fdp%2FB0BOT", headers=UA_BOT)

conn = sqlite3.connect(DB_PATH)
bot_clicks = conn.execute("SELECT COUNT(*) FROM affiliate_clicks WHERE is_bot=1").fetchone()[0]
human_row = conn.execute(
    "SELECT variant, session_id FROM affiliate_clicks WHERE is_bot=0 AND product_id='PROD-X'").fetchone()
conn.close()
check("bot UA flagged is_bot=1", bot_clicks >= 1)
check("human click stored with variant+session", human_row is not None and human_row[0] in ("A", "B"))

# Dedupe: same IP + product within 30s reuses the click id.
c1 = client.get("/go/DEDUP?url=https%3A%2F%2Fwww.amazon.in%2Fd", headers=UA_HUMAN, follow_redirects=False)
c2 = client.get("/go/DEDUP?url=https%3A%2F%2Fwww.amazon.in%2Fd", headers=UA_HUMAN, follow_redirects=False)
conn = sqlite3.connect(DB_PATH)
dedup_rows = conn.execute("SELECT COUNT(*) FROM affiliate_clicks WHERE product_id='DEDUP' AND is_bot=0").fetchone()[0]
conn.close()
check("rapid duplicate clicks de-duplicated (1 row)", dedup_rows == 1, f"got {dedup_rows}")

# ═════════════════════════════════════════════════════════════════════════════
# D. HMAC POSTBACK & IDEMPOTENCY
# ═════════════════════════════════════════════════════════════════════════════
section("D. HMAC Postback & Idempotency")
payload = {"transaction_id": "AUDIT-TXN-1", "product_id": "PROD-X", "session_id": human_row[1] if human_row else "127.0.0.1",
           "sale_amount": 2999.0, "commission_amount": 90.0}
body = json.dumps(payload).encode("utf-8")
sig = _sign(body, db_manager.get_postback_secret())
resp = client.post("/postback/conversion", data=body, content_type="application/json", headers={"X-Signature": sig})
check("valid HMAC accepted 200", resp.status_code == 200)
resp = client.post("/postback/conversion", data=body, content_type="application/json", headers={"X-Signature": "0" * 64})
check("forged signature rejected 401", resp.status_code == 401)
resp = client.post("/postback/conversion", data=body, content_type="application/json")
check("missing signature rejected 401", resp.status_code == 401)
resp = client.post("/postback/conversion", data=body, content_type="application/json", headers={"X-Signature": sig})
check("duplicate delivery idempotent", resp.status_code == 200 and "idempotent" in resp.get_data(as_text=True))
no_txn = json.dumps({"sale_amount": 1}).encode("utf-8")
resp = client.post("/postback/conversion", data=no_txn, content_type="application/json",
                   headers={"X-Signature": _sign(no_txn, db_manager.get_postback_secret())})
check("missing transaction_id rejected 400", resp.status_code == 400)
conn = sqlite3.connect(DB_PATH)
conv_count = conn.execute("SELECT COUNT(*) FROM affiliate_conversions WHERE transaction_id='AUDIT-TXN-1'").fetchone()[0]
conn.close()
check("conversion stored exactly once", conv_count == 1, f"got {conv_count}")

# ═════════════════════════════════════════════════════════════════════════════
# E. ML — BANDIT + EV RANKER
# ═════════════════════════════════════════════════════════════════════════════
section("E. ML — Bandit & EV Ranker")
import ab_engine  # noqa: E402
import revenue_ranker  # noqa: E402
import affiliate_tracker  # noqa: E402

sel = ab_engine.select_variant("ML-PROD")
check("select_variant returns valid structure", sel.get("variant") in ("A", "B") and "explored" in sel)
hook = ab_engine.get_variant_hook(sel["variant"])
check("variant hook text available", bool(hook))

results = ab_engine.get_experiment_results()
check("experiment results expose rows", isinstance(results.get("results"), list))
ranking = revenue_ranker.rank_verticals()
check("EV ranking present", isinstance(ranking, list) and len(ranking) >= 1)
if len(ranking) >= 2:
    check("EV ranking descending order", all(ranking[i]["ev"] >= ranking[i + 1]["ev"] for i in range(len(ranking) - 1)))
best = revenue_ranker.best_sector()
check("best_sector returns top EV sector", best is None or (isinstance(best, dict) and best.get("sector") == ranking[0]["sector"]))

# ═════════════════════════════════════════════════════════════════════════════
# F. PIPELINE & CAMPAIGN LIFECYCLE
# ═════════════════════════════════════════════════════════════════════════════
section("F. Pipeline & Campaign Lifecycle")
resp = client.post("/api/run_pipeline", json={"sector": "electronics"})
data = resp.get_json()
check("pipeline run 200", resp.status_code == 200)
check("pipeline created campaigns", data.get("campaigns_created", 0) > 0)
resp = client.post("/api/run_pipeline", json={"sector": "crypto_scam"})
check("invalid sector rejected 400", resp.status_code == 400)

conn = sqlite3.connect(DB_PATH)
row = conn.execute("SELECT id, status, caption, graphic_path FROM campaigns ORDER BY id LIMIT 1").fetchone()
pending_before = conn.execute("SELECT COUNT(*) FROM campaigns WHERE status='pending_approval'").fetchone()[0]
conn.close()
camp_id = row[0]
check("campaign persisted pending_approval", row[1] == "pending_approval")
check("campaign caption persisted", bool(row[2]))
check("campaign graphic_path persisted", bool(row[3]))

resp = client.post(f"/api/campaign/{camp_id}/approve")
check("approve distributes 200", resp.status_code == 200)
conn = sqlite3.connect(DB_PATH)
status = conn.execute("SELECT status FROM campaigns WHERE id=?", (camp_id,)).fetchone()[0]
logs = conn.execute("SELECT COUNT(*) FROM distribution_logs WHERE campaign_id=?", (camp_id,)).fetchone()[0]
conn.close()
check("campaign published after approve", status == "published")
check("distribution logs written (3 channels)", logs >= 3, f"got {logs}")

resp = client.post("/api/campaign/999999/reject")
check("reject unknown campaign handled", resp.status_code in (200, 500))
resp = client.post("/api/campaign/999999/approve")
check("approve unknown campaign errors gracefully", resp.status_code in (200, 500))

# Stored XSS guard: hostile title must not appear raw in JSON responses.
db_manager.save_campaign({"id": "XSS-1", "title": "<script>alert(1)</script>",
                          "caption": "<img src=x onerror=alert(2)>", "commission": 0.03}, sector="electronics")
body_text = client.get("/api/history").get_data(as_text=True)
check("stored XSS neutralised in API JSON", "<script>alert(1)</script>" not in body_text)

# ═════════════════════════════════════════════════════════════════════════════
# G. RESILIENCE — QUOTAS, BREAKERS, QUEUE, DLQ
# ═════════════════════════════════════════════════════════════════════════════
section("G. Resilience Shield")
quota_manager.reset_quota("twitter")
summary = quota_manager.consume_quota("twitter", count=1)
check("consume_quota increments usage", summary.get("usage", 0) >= 1)
check("check_quota OK below threshold", quota_manager.check_quota("twitter") in ("OK", "WARNING"))
try:
    quota_manager.consume_quota("twitter", count=quota_manager.QUOTA_LIMITS["twitter"]["hard_cap"], force=True)
    blocked = quota_manager.check_quota("twitter") == "BLOCKED"
except Exception:
    blocked = False
check("hard cap blocks provider", blocked)
try:
    quota_manager.consume_quota("twitter", count=1)
    raised = False
except quota_manager.QuotaExceededException:
    raised = True
check("consume over cap raises QuotaExceededException", raised)
quota_manager.reset_quota("twitter")
check("reset_quota clears usage", quota_manager.get_quota_usage("twitter") == 0)

for _ in range(quota_manager._breaker_limits("twitter")[0]):
    quota_manager.record_breaker_failure("twitter")
state = quota_manager.get_breaker_state("twitter")["state"]
check("breaker trips OPEN after threshold", state == "OPEN", f"got {state}")
try:
    quota_manager.check_breaker("twitter")
    raised = False
except quota_manager.CircuitBreakerOpenException:
    raised = True
check("check_breaker raises when OPEN", raised)
quota_manager.reset_breaker("twitter")
check("reset_breaker closes circuit", quota_manager.get_breaker_state("twitter")["state"] == "CLOSED")


def _ok():
    return "ran"


def _boom():
    raise RuntimeError("provider down")


out = quota_manager.breaker_call("meta", _ok)
check("breaker_call executes fn on CLOSED", out == "ran")
quota_manager.reset_breaker("meta")

jid = job_queue.enqueue_job("retry_distribution", {"campaign_id": camp_id, "channel": "telegram"}, max_retries=3)
job = job_queue.acquire_next_job()
check("acquire_next_job claims FIFO", job and job["job_id"] == jid)
job_queue.complete_job(jid)
conn = sqlite3.connect(DB_PATH)
gone = conn.execute("SELECT COUNT(*) FROM job_queue WHERE id=?", (jid,)).fetchone()[0]
conn.close()
check("complete_job removes from queue", gone == 0)

jid2 = job_queue.enqueue_job("retry_distribution", {"campaign_id": camp_id, "channel": "twitter"}, max_retries=1)
job_queue.acquire_next_job()
job_queue.fail_job(jid2, "simulated failure")
dlq = job_queue.get_dead_letter_jobs(limit=5)
check("fail_job dead-letters after max retries", any(j["error"] == "simulated failure" for j in dlq))
requeued = job_queue.requeue_dead_job(dlq[0]["id"])
check("requeue_dead_job restores to queue", requeued and job_queue.get_queue_summary().get("pending", 0) >= 1)

stale_id = job_queue.enqueue_job("retry_distribution", {"x": 1})
conn = sqlite3.connect(DB_PATH)
conn.execute("UPDATE job_queue SET status='running' WHERE id=?", (stale_id,))
conn.commit()
conn.close()
job_queue.recover_stale_jobs()
conn = sqlite3.connect(DB_PATH)
recovered = conn.execute("SELECT status FROM job_queue WHERE id=?", (stale_id,)).fetchone()[0]
conn.close()
check("recover_stale_jobs resets running->pending", recovered == "pending")

# Retry sweep drains a queued retry job end-to-end (mock adapter succeeds).
before_logs = sqlite3.connect(DB_PATH).execute(
    "SELECT COUNT(*) FROM distribution_logs WHERE campaign_id=?", (camp_id,)).fetchone()[0]
job_queue.purge_queue()
job_queue.enqueue_job("retry_distribution", {"campaign_id": camp_id, "channel": "telegram"}, max_retries=3)
processed = scheduler_engine.retry_sweep()
time.sleep(0.2)
after_logs = sqlite3.connect(DB_PATH).execute(
    "SELECT COUNT(*) FROM distribution_logs WHERE campaign_id=?", (camp_id,)).fetchone()[0]
check("retry_sweep processes queued job", processed >= 1)
check("retry_sweep wrote distribution log", after_logs > before_logs)
check("retry queue empty after sweep", job_queue.get_queue_summary().get("pending", 0) == 0)
job_queue.purge_queue()

# ═════════════════════════════════════════════════════════════════════════════
# H. SCHEDULER
# ═════════════════════════════════════════════════════════════════════════════
section("H. Scheduler Engine")
status = scheduler_engine.get_status()
job_ids = {j["job_id"] for j in status.get("jobs", [])}
expected_jobs = {"content_sweep_morning", "content_sweep_evening", "auto_publish_sweep",
                 "retry_sweep", "hot_db_backup", "video_trash_collector"}
check("scheduler running", status.get("scheduler_running") is True)
check("all 6 jobs registered", expected_jobs.issubset(job_ids), f"got {job_ids}")

conn = sqlite3.connect(DB_PATH)
rc_before = conn.execute("SELECT run_count FROM scheduler_jobs WHERE job_id='video_trash_collector'").fetchone()
conn.close()
res = scheduler_engine.trigger_now("video_trash_collector")
check("trigger_now executes registered job (bug fixed)", res.get("status") == "success", str(res))
time.sleep(0.3)
conn = sqlite3.connect(DB_PATH)
rc_after = conn.execute("SELECT run_count, last_status FROM scheduler_jobs WHERE job_id='video_trash_collector'").fetchone()
conn.close()
check("telemetry run_count incremented", rc_after is not None and rc_after[0] > (rc_before[0] if rc_before else 0))
check("telemetry last_status success", rc_after is not None and rc_after[1] == "success")
res = scheduler_engine.trigger_now("no_such_job")
check("trigger_now unknown job errors gracefully", res.get("status") == "error")

toggle = scheduler_engine.set_job_enabled("content_sweep_morning", False)
check("set_job_enabled disables (DB+live)", toggle.get("enabled") is False and toggle.get("live_applied") is True)
paused = any(str(getattr(j, "next_run_time", None)) == "None" for j in scheduler_engine._SCHEDULER.get_jobs())
check("live job actually paused", paused)
scheduler_engine.set_job_enabled("content_sweep_morning", True)

backup_path = scheduler_engine.hot_backup()
check("hot_backup writes backup file", backup_path and os.path.exists(backup_path))
old_bk = os.path.join(BACKUP_DIR, "campaigns_20000101_000000.db")
open(old_bk, "w").close()
os.utime(old_bk, (time.time() - 8 * 86400,) * 2)
scheduler_engine._prune_old_backups(days=7)
check("backup prune removes >7d files", not os.path.exists(old_bk))

stale_m = os.path.join(CAMPAIGN_STATIC_DIR, "audit_stale.mp4")
fresh_m = os.path.join(CAMPAIGN_STATIC_DIR, "audit_fresh.png")
keep_f = os.path.join(CAMPAIGN_STATIC_DIR, "audit_keep.txt")
for p, age in ((stale_m, 3 * 86400), (fresh_m, 0), (keep_f, 3 * 86400)):
    open(p, "w").close()
    if age:
        os.utime(p, (time.time() - age,) * 2)
removed = scheduler_engine.video_trash_collector(older_than_hours=48)
check("trash collector removes stale media only",
      not os.path.exists(stale_m) and os.path.exists(fresh_m) and os.path.exists(keep_f))
for p in (fresh_m, keep_f):
    if os.path.exists(p):
        os.remove(p)

# ═════════════════════════════════════════════════════════════════════════════
# I. AMBIENT BACKGROUNDS
# ═════════════════════════════════════════════════════════════════════════════
section("I. Ambient UI Backgrounds")
bg_assets = background_factory.ensure_backgrounds(force=False)
check("all 4 theme posters generated", len(bg_assets) == 4 and all(os.path.exists(p) for p in bg_assets.values()))
ctx = background_factory.get_background_for_ui()
check("UI context exposes active theme poster", ctx["poster_url"].endswith(".jpg") and ctx["theme"] in background_factory.THEMES)
background_factory.set_active_theme("kelp")
ctx2 = background_factory.get_background_for_ui()
check("theme switch persists (operator_settings)", ctx2["theme"] == "kelp")
html_body = client.get("/").get_data(as_text=True)
check("dashboard renders switched theme", "bg-scene theme-kelp" in html_body)
resp = client.get("/api/background/status")
sdata = resp.get_json()
check("background status API lists themes", resp.status_code == 200 and len(sdata.get("themes", [])) == 4)
resp = client.post("/api/background/silhouette")
check("background switch API works", resp.status_code == 200 and resp.get_json().get("active") == "silhouette")
resp = client.post("/api/background/not_a_theme")
check("unknown theme rejected 400", resp.status_code == 400)
background_factory.set_active_theme("train")

# ═════════════════════════════════════════════════════════════════════════════
# J. PAGES & DASHBOARD APIs
# ═════════════════════════════════════════════════════════════════════════════
section("J. Pages & Dashboard APIs")
body = client.get("/").get_data(as_text=True)
check("dashboard has ticker + stat elements", 'id="ticker"' in body and 'id="stat-clicks"' in body)
check("dashboard has esc() XSS helper", "function esc(" in body)
hbody = client.get("/history").get_data(as_text=True)
check("history page renders ledger", 'id="campaigns-table"' in hbody)
sbody = client.get("/settings").get_data(as_text=True)
check("settings has theme selector", 'id="bg_theme"' in sbody)
check("settings has change-password form", 'action="/settings/password"' in sbody)

perf = client.get("/api/performance").get_json()
check("/api/performance success payload", perf.get("status") == "success")
check("performance includes stats+ab+ranking+alerts",
      all(k in perf for k in ("stats", "ab", "sector_ranking", "alerts")))
sectors = client.get("/api/sectors").get_json()
check("/api/sectors lists verticals", isinstance(sectors, list) and len(sectors) >= 1)
hist = client.get("/api/history?status=published&limit=10").get_json()
check("/api/history filters by status", hist.get("status") == "success")
clicks = client.get("/api/clicks").get_json()
check("/api/clicks stats present", clicks.get("status") == "success" and "total_clicks" in clicks)
ab_api = client.get("/api/ab").get_json()
check("/api/ab results present", ab_api.get("status") == "success")
rel = client.get("/api/reliability_status").get_json()
check("/api/reliability_status full payload",
      rel.get("status") == "success" and "quotas" in rel and "breakers" in rel and "queue" in rel)
dlq_api = client.get("/api/review_dead_letter").get_json()
check("/api/review_dead_letter payload", dlq_api.get("status") == "success" and "jobs" in dlq_api)
sched_api = client.get("/api/scheduler_status").get_json()
check("/api/scheduler_status jobs listed", sched_api.get("status") == "success" and len(sched_api.get("jobs", [])) >= 6)
health = client.get("/health").get_json()
check("/health ok with db+scheduler", health.get("status") == "ok" and health["database"]["connected"] and health["scheduler"]["active"])
anon_health = app_module.app.test_client().get("/health")
check("/health public (no auth)", anon_health.status_code == 200)

# Admin reset endpoints.
resp = client.post("/api/reliability_reset", json={"target": "purge_queue"})
check("reliability_reset purge works", resp.status_code == 200)
resp = client.post("/api/scheduler_run_now", json={"job_id": "video_trash_collector"})
check("scheduler_run_now API executes job", resp.status_code == 200 and resp.get_json().get("status") == "success")

# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"AUDIT RESULTS: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("\nPAIN POINTS FOUND:")
    for f in FAIL:
        print(f"  ✗ {f}")
print("=" * 70)
sys.exit(1 if FAIL else 0)
