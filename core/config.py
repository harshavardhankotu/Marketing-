"""
core/config.py
Configuration loader for Autonomous B2B Lead Generation, Cold Outreach & Meeting Booking Engine.
Loads environment variables via python-dotenv with safe production defaults and path resolutions.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Resolve project directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "outbound.db"
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Load .env file
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Gemini AI API Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Flask & Security Settings
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "b2b_outbound_secure_random_key_99381023")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@yourdomain.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin_password_123")

# SMTP Outbound Email Settings
SMTP_HOST = os.getenv("SMTP_HOST", "smtp-relay.brevo.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_NAME = os.getenv("FROM_NAME", "Harsha | Outbound Suite")

# Telegram Bot Alerting
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")

# Operational Mode ("self" = Dogfooding agency mode, "client" = Customer execution mode)
ENGINE_MODE = os.getenv("ENGINE_MODE", "self")

# Booking Gateway & Webhook Secret
PUBLIC_BOOKING_URL = os.getenv("PUBLIC_BOOKING_URL", "https://cal.com/your-username/15min")
BOOKING_WEBHOOK_SECRET = os.getenv("BOOKING_WEBHOOK_SECRET", "webhook_hmac_secret_key_12345")

# Deliverability Pacing & Hard Caps
DAILY_EMAIL_CAP = int(os.getenv("DAILY_EMAIL_CAP", "30"))
MIN_SEND_DELAY_SECONDS = int(os.getenv("MIN_SEND_DELAY_SECONDS", "120"))
MAX_SEND_DELAY_SECONDS = int(os.getenv("MAX_SEND_DELAY_SECONDS", "240"))
