"""
Central configuration & secret management.

Loads environment variables from ``.env`` (via python-dotenv) and exposes
secure, production-aware fallbacks for every external integration used by
the Autonomous Affiliate Marketing & ML Optimization Suite.

SECURITY NOTE:
    - Never commit a real ``.env``. Only ``.env.example`` is versioned.
    - Secrets are read once at import time. Restart the process after
      rotating any credential.
"""

import os
from dotenv import load_dotenv

# Resolve the project root relative to this file's location (bots/..)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# Load environment secrets on module initialization
load_dotenv(os.path.join(PROJECT_ROOT, '.env'), override=False)

# ─────────────────────────────────────────────────────────────────────────────
# FILESYSTEM LAYOUT
# ─────────────────────────────────────────────────────────────────────────────
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
OUTPUT_DIR = os.path.join(DATA_DIR, 'output')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')
STATIC_DIR = os.path.join(PROJECT_ROOT, 'static')
CAMPAIGN_STATIC_DIR = os.path.join(STATIC_DIR, 'campaigns')
BACKGROUND_DIR = os.path.join(STATIC_DIR, 'backgrounds')
ASSETS_DIR = os.path.join(PROJECT_ROOT, 'assets')

# Primary SQLite database file (overridable for tests via DB_PATH env var)
DB_PATH = os.getenv('DB_PATH', os.path.join(DATA_DIR, 'campaigns.db'))

# List of directories that must exist before any subsystem touches disk.
REQUIRED_DIRS = [DATA_DIR, OUTPUT_DIR, BACKUP_DIR, STATIC_DIR, CAMPAIGN_STATIC_DIR, BACKGROUND_DIR, ASSETS_DIR]


def ensure_directories():
    """Create every required filesystem directory idempotently."""
    for directory in REQUIRED_DIRS:
        os.makedirs(directory, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# APPLICATION SECURITY
# ─────────────────────────────────────────────────────────────────────────────
FLASK_SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'a_very_secret_key_for_session_signing_987654')
POSTBACK_SECRET = os.getenv('POSTBACK_SECRET', 'change_me_postback_secret_987654')
ADMIN_DEFAULT_PASSWORD = os.getenv('ADMIN_DEFAULT_PASSWORD', 'admin123')

# Trusted redirect whitelist (Open Redirect prevention for the /go/ router).
# Only these domains (and their subdomains) may be used as affiliate targets.
TRUSTED_DOMAINS = os.getenv(
    'TRUSTED_DOMAINS',
    'amazon.in,amazon.com,amzn.in,amzn.to,geni.us'
).split(',')

# ─────────────────────────────────────────────────────────────────────────────
# AMAZON ASSOCIATES & PA-API v5
# ─────────────────────────────────────────────────────────────────────────────
AMAZON_ASSOCIATE_TAG = os.getenv('AMAZON_ASSOCIATE_TAG', '')
AMAZON_PAAPI_ACCESS_KEY = os.getenv('AMAZON_PAAPI_ACCESS_KEY', '')
AMAZON_PAAPI_SECRET_KEY = os.getenv('AMAZON_PAAPI_SECRET_KEY', '')
AMAZON_PAAPI_REGION = os.getenv('AMAZON_PAAPI_REGION', 'eu-west-1')
AMAZON_PAAPI_HOST = os.getenv('AMAZON_PAAPI_HOST', 'webservices.amazon.in')
AMAZON_MARKETPLACE = os.getenv('AMAZON_MARKETPLACE', 'amazon.in')

# ─────────────────────────────────────────────────────────────────────────────
# GENERATIVE AI (Gemini 2.0 Flash via google-genai)
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')

# ─────────────────────────────────────────────────────────────────────────────
# SOCIAL DISTRIBUTION
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
ADMIN_TELEGRAM_ID = os.getenv('ADMIN_TELEGRAM_ID', '')

TWITTER_API_KEY = os.getenv('TWITTER_API_KEY', '')
TWITTER_API_SECRET = os.getenv('TWITTER_API_SECRET', '')
TWITTER_ACCESS_TOKEN = os.getenv('TWITTER_ACCESS_TOKEN', '')
TWITTER_ACCESS_SECRET = os.getenv('TWITTER_ACCESS_SECRET', '')

# Zero-cost X/Twitter posting via Playwright browser automation (used only when
# the paid Twitter API credentials above are absent AND this flag is enabled).
# A persistent browser profile under data/playwright/ must be logged in first.
PLAYWRIGHT_X_ENABLED = os.getenv('PLAYWRIGHT_X_ENABLED', 'False').strip().lower() in ('1', 'true', 'yes')
PLAYWRIGHT_HEADLESS = os.getenv('PLAYWRIGHT_HEADLESS', 'True').strip().lower() in ('1', 'true', 'yes')

INSTAGRAM_ACCOUNT_ID = os.getenv('INSTAGRAM_ACCOUNT_ID', '')
META_ACCESS_TOKEN = os.getenv('META_ACCESS_TOKEN', '')

# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONAL TUNING
# ─────────────────────────────────────────────────────────────────────────────
# Epsilon-Greedy exploration factor used by the A/B multi-armed bandit.
AB_EPSILON = float(os.getenv('AB_EPSILON', '0.20'))

# Minimum impressions before the bandit may exploit (rather than explore).
AB_MIN_SAMPLES = int(os.getenv('AB_MIN_SAMPLES', '20'))

# When truthy, the video factory renders 1-second mock clips (no audio encode).
FAST_VIDEO_RENDER = os.getenv('FAST_VIDEO_RENDER', 'False').strip().lower() in ('1', 'true', 'yes')

# When truthy, the scraper skips all network I/O and returns the offline mock
# offer set (used by CI / tests / offline demos).
MOCK_SOURCING = os.getenv('MOCK_SOURCING', 'False').strip().lower() in ('1', 'true', 'yes')

# Timezone for scheduler jobs.
SCHEDULER_TZ = os.getenv('SCHEDULER_TZ', 'Asia/Kolkata')

# Ambient UI background theme: kelp | pavilion | train | silhouette.
BACKGROUND_THEME = os.getenv('BACKGROUND_THEME', 'train').strip().lower()


def _credential_ok(value):
    """True when a credential was actually supplied (not a placeholder)."""
    return bool(value) and 'your_' not in value and 'xxx' not in value and 'here' not in value


def has_gemini():
    """True when a usable Gemini API key is configured."""
    return _credential_ok(GEMINI_API_KEY)


def has_amazon_paapi():
    """True when full Amazon PA-API credentials are configured."""
    return _credential_ok(AMAZON_PAAPI_ACCESS_KEY) and _credential_ok(AMAZON_PAAPI_SECRET_KEY)


def has_telegram():
    """True when a usable Telegram bot is configured."""
    return _credential_ok(TELEGRAM_BOT_TOKEN) and bool(TELEGRAM_CHAT_ID)
