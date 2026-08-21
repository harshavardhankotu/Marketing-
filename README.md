# Autonomous Affiliate Marketing & ML Optimization Suite

A production-ready, 100% Amazon Associates / ASCI / CPA-compliant affiliate
marketing automation platform built with Python + Flask + SQLite.

The suite sources real products (PA-API v5 with RSS fallback), composes
compliant multilingual ad copy, renders social posters/reels, holds campaigns
in a manual preview gate, and broadcasts approved campaigns to Telegram,
X/Twitter, and Instagram. A multi-armed bandit (A/B) optimizer and an
expected-value vertical ranker continuously improve which creatives and
verticals are used.

## Compliance guarantees

- No wallets, no payouts, no CPA telephony pools: the schema contains no
  `user_wallets`, `payout_transactions`, or `cpa_phone_pool` tables.
- No incentivized or fabricated traffic; every distribution is organic.
- Every marketing asset carries the ASCI disclosure:
  `*Affiliate link — I may earn a commission at no extra cost to you.`
- Click tracking filters bot user-agents so analytics are human-only.
- Open-redirect protection restricts redirect targets to a trusted-domain
  whitelist (Amazon Associates domains).

## Architecture

```
scrapers/product_scraper.py     PA-API v5 (SigV4) + RSS + offline mock fallback
generators/ai_copywriter.py     Gemini 2.0 Flash copy + ASCI disclosure guard
generators/video_script_engine.py  Poster/reel renderer (Ken Burns via MoviePy)
bots/db_manager.py              SQLite WAL schema (13 tables) + migrations
bots/ab_engine.py               Epsilon-Greedy multi-armed bandit (e=0.20)
bots/affiliate_tracker.py       Human-only click tracking + bot UA filtering
bots/revenue_ranker.py          EV vertical ranking (EV = commission x CR)
bots/quota_manager.py           Daily API caps + stateful circuit breakers
bots/idempotency.py             HMAC-SHA256 webhook validation + dedup
bots/job_queue.py               Background queue + dead-letter retry pool
bots/alert_engine.py            Telegram alerting on trips/blocks/DLQs
bots/distributor.py             Telegram / X / Instagram adapters (mock-safe)
bots/scheduler_engine.py        APScheduler (Asia/Kolkata) + SQLite hot backups
generators/background_factory.py  Procedural ambient UI backdrops (4 themes)
app.py                          Flask app: auth, /go/ redirects, postback, APIs
```

## Scheduler

- 08:15 IST morning content sweep
- 18:30 IST evening content sweep
- Every 5 min: auto-publish due pending campaigns
- 02:00 IST: zero-cost SQLite hot backup (`source.backup(target)`), 7-day prune

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
python setup_wizard.py            # guided .env setup with live validation
python app.py                     # http://127.0.0.1:5000
```

The wizard generates rotated secrets, prompts for every credential, and
live-validates Telegram/SMTP (free endpoints). `python setup_wizard.py --check`
re-validates an existing `.env` anytime.

## Ambient UI backgrounds

The console ships with four procedural, zero-cost animated backdrops inspired
by cinematic AI-video aesthetics, rendered offline with Pillow:

- `kelp` — turquoise kelp forest with pulsing golden god-rays
- `pavilion` — bone-white bio-limestone pavilion with a slow light sweep
- `train` — 90s-anime golden-hour train carriage, countryside gliding past
- `silhouette` — double-exposure silhouette with drifting PNW fog & pines

Set `BACKGROUND_THEME` in `.env` or switch live under **Settings → UI Ambient
Theme**. When MoviePy is installed, `generators/background_factory.py
--loop --theme <name>` compiles a true seamless-loop MP4; otherwise the CSS
motion layer animates the still poster (no video required).

## Tests

```bash
python tests/verify_local_backend.py
python tests/verify_attribution.py
python tests/verify_security_hardened.py
python tests/final_e2e_check.py
```

All suites run against an isolated temporary database and require no live APIs.
