# Test Report — Autonomous Affiliate Marketing Suite

**Generated:** 2026-08-22 · **Code under test:** `main` @ `acf6c52` (+ hermetic-test fixes landed with this report)
**Platform:** Python 3.11.9 · Windows 11 · SQLite WAL · all external services mocked/sandboxed

---

## 1. Executive Summary

| | |
|---|---|
| **Total assertions** | **323 executed per full battery** |
| **Result** | **323 passed · 0 failed** |
| Suites | 5 (4 regression + 1 deep audit) |
| Isolation | Every suite runs against a fresh temp database; zero live API calls; operator `.env` credentials cannot affect results |
| CI | GitHub Actions runs the four regression suites on every push to `main` |

---

## 2. Test Suites

| Suite | Focus | Checks | Latest |
|---|---|---:|---|
| `tests/verify_local_backend.py` | Schema integrity (13 tables, WAL, FKs), compliance table bans, auth flow, dashboard APIs | 39 | ✅ 39/39 |
| `tests/verify_attribution.py` | `/go/` redirects, open-redirect whitelist, bot-UA filtering, variant storage | 9 | ✅ 9/9 |
| `tests/verify_security_hardened.py` | HMAC-SHA256 webhook forgery, idempotent conversions, CSRF enforcement | 10 | ✅ 10/10 |
| `tests/final_e2e_check.py` | Closed loop: source → compose → approve → distribute → click → postback → bandit/EV → scheduler | 16 | ✅ 16/16 |
| `tests/full_feature_audit.py` | Deep audit across 18 domains (below) | 249 executed | ✅ 249/249 |
| **Total** | | **323** | **✅ 323/323** |

---

## 3. Full-Feature Audit Breakdown (`full_feature_audit.py`, sections A–R)

| Section | Assertions |
|---|---:|
| A. Database & Schema | 8 |
| B. Auth & Security | 24 |
| C. Tracked Redirect & Attribution | 8 |
| D. HMAC Postback & Idempotency | 6 |
| E. ML — Bandit & EV Ranker | 6 |
| F. Pipeline & Campaign Lifecycle | 12 |
| G. Resilience Shield | 17 |
| H. Scheduler Engine | 11 |
| I. Ambient UI Backgrounds | 7 |
| J. Pages & Dashboard APIs | 18 |
| K. Revenue Engine (deal scoring/format/cards/public SEO/clock) | 27 |
| L. Audience Engine (growth telemetry/share kit/channel card) | 13 |
| M. Legal & Compliance Pages | 12 |
| N. Integration Sandbox (mocked network) | 14 |
| O. Business Logic & Revenue Controls | 15 |
| P. Multi-Network, Repurposing, Auto-Pin, Newsletter | 26 |
| Q. Operator Duties Console | 12 |
| R. Full Automation Layer | 17 |

*253 assertions are defined; a handful are dual-path (e.g. variant-link rendering), so a standard run executes 249.*

---

## 4. Feature Coverage Matrix

| Feature area | Verified behaviors |
|---|---|
| Sourcing | PA-API SigV4 header shape · RSS XML parsing (ASIN + ₹ price) · mock/offline flag · quota/breaker guards |
| Deal intelligence | Price history recording · lowest-ever detection · MRP/discount math · 0–100 score · badges · best-first ranking |
| Creative | Deal-format captions (price/MRP strike/%OFF/disclosure) · forwardable cards (3 sizes) · channel pitch card · Shorts/Pin repackaging |
| Distribution | Telegram live success + 5xx→breaker→mock degradation · Instagram container/publish · Twitter credential-less mock · WhatsApp deep-links · retry queue drain · dead-letter after max retries · requeue/purge |
| Attribution & ML | Trusted-domain allow/subdomain/rejects · bot-UA exclusion · click dedupe · HMAC postbacks · forged/missing signature rejects · idempotent duplicates · epsilon-greedy structure · EV ordering · variant wiring into creatives and public links |
| Resilience | Daily caps → BLOCKED → exception · breaker trip/raise/reset · `breaker_call` · stale-job recovery · FIFO claim/complete |
| Scheduler | 10 jobs registered · synchronous `trigger_now` · run telemetry (run_count/last_status) · live pause/resume · hot backup + 7-day prune · trash collector (48h media) |
| Public site | Anonymous `/deals` listing + detail · JSON-LD Product schema · sponsored-rel CTAs · sitemap.xml · robots.txt · 404 handling |
| Legal | Amazon earning statement · PA-API "AS IS" clause · price-accuracy disclaimer · ASCI line · DPDP notice (collection/purpose/retention/children §7/grievance §8) · no-price-warranty terms · footer links everywhere |
| Audience | Growth snapshots (Bot API success/error/manual) · summaries with deltas · tracked share links per channel · cross-promo kit assets |
| Money ops | Commission CSV export (CA-ready totals) · conversion import (idempotent, admin-gated) · 180-day clock math · GST threshold watch |
| Duties console | Readiness conjunction (8 checks) · grievance officer rendered onto /privacy + /terms · auto-upgrade secure cookies on HTTPS URL · spot-check due/log flow |
| Automation | Setup wizard validators (Telegram/SMTP/Gemini/tag/secrets) · auto spot-check via mocked live prices · honest manual fallback (never stamps completion) · duty-watch one-shot alerts |
| Web security | CSRF token-less reject · login rate-limit trip (429) · role gates (viewer 403s) · CSP/XFO/nosniff/Referrer-Policy headers · HttpOnly + SameSite=Lax cookies · stored-XSS neutralization in JSON · HTML-safe JSON escaping |

---

## 5. Bugs Caught by Testing (and fixed)

| # | Bug | Caught by |
|---|---|---|
| 1 | `affiliate_clicks` lacked `session_id` → `/go/` crashed with 500 (bandit attribution broken) | verify_attribution |
| 2 | `caption`/`graphic_path` never persisted — mock fallbacks masked it; live posts would ship empty captions | final_e2e + direct probe |
| 3 | `_set_breaker_state` emitted invalid SQL (`?, ,`) → **every successful live API call crashed** `record_breaker_success` | full_feature_audit G |
| 4 | `trigger_now` instantiated a fresh empty scheduler — manual job runs were impossible | full_feature_audit H |
| 5 | Retry queue never drained; `recover_stale_jobs` never invoked; job telemetry never written | code audit → fixed, locked by H |
| 6 | `set_job_enabled` didn't pause/resume the live scheduler | full_feature_audit H |
| 7 | `SESSION_COOKIE_SECURE=True` silently broke plain-HTTP logins (cookie never sent back) | live-server smoke test → made opt-in/auto-upgrade |
| 8 | Channel P&L read a consumed cursor + duplicate dict key → always empty | business audit → fixed with real session join |
| 9 | Distributor 5xx `RuntimeError` escaped the `RequestException` handler → sweeps could crash instead of degrading to mock | sandbox N |
| 10 | Bandit was disconnected from creatives (variants never assigned anywhere) | business audit → closed loop via compose_service |
| 11 | No dedupe / quality gate / posting cap → channel-spam risk | business audit → enforced in compose_service + sweeps |
| 12 | Direct Amazon sends no postbacks → revenue invisible | strategy review → idempotent import endpoint |
| 13 | `newsletter_list_confirmed` omitted tokens → KeyError crash on every send | sandbox-style audit P |
| 14 | Orphaned CPA-era Swagger template shipped in repo | GitHub sync audit (removed) |
| 15 | Operator `.env` (real wizard password) leaked into hardcoded-credential tests → mass failures | this report's rerun → hermetic env pinning added |

---

## 6. Hermetic Guarantees

Every suite pins its environment **before importing the app**, and since `load_dotenv(override=False)` never overrides pre-set variables, operator configuration cannot leak into results:

```python
os.environ["DB_PATH"] = <fresh temp dir>          # isolated SQLite per run
os.environ["FLASK_SECRET_KEY"] = "test_secret"
os.environ["POSTBACK_SECRET"] = "test_postback_secret"
os.environ["ADMIN_DEFAULT_PASSWORD"] = "admin123"  # deterministic login creds
for _k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GEMINI_API_KEY",
           "SMTP_HOST", "AMAZON_PAAPI_ACCESS_KEY", "AMAZON_PAAPI_SECRET_KEY"):
    os.environ[_k] = ""                            # no live external calls
```

Additional guarantees: `MOCK_SOURCING=True` + `FAST_VIDEO_RENDER=True` for offline speed; outbound network is only ever exercised through mocks; each run gets its own temp directory.

---

## 7. Running the Tests

```bash
python tests/verify_local_backend.py       # 39 checks
python tests/verify_attribution.py         #  9 checks
python tests/verify_security_hardened.py   # 10 checks
python tests/final_e2e_check.py            # 16 checks
python tests/full_feature_audit.py         # 249 checks (deep audit)
```

Exit codes are CI-friendly (`0` green, `1` red). On Windows consoles set
`$env:PYTHONIOENCODING='utf-8'` first (emoji in output). GitHub Actions runs the
four regression suites automatically on pushes/PRs to `main`.

---

## 8. Known Limits

* Live network paths (PA-API GetItems, Telegram sendPhoto/pin, SMTP delivery,
  Gemini generation) are verified through **mocked transports** — first
  real-credential runs should be watched once via `journalctl -u affiliate.service -f`.
* Playwright X-posting requires an interactively logged-in browser profile;
  only the guard/mock path is testable headlessly.
* The deep audit mutates provider state (quotas/breakers/DLQ) inside its own
  temp database and resets it — safe to run repeatedly.
