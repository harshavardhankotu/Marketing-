"""
core/config.py
Core configuration module for the Autonomous B2B Lead Generation & Call-Booking Engine.
Loads environment variables via python-dotenv with robust, self-healing fallbacks.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Resolve project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from root if available
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Database Configuration
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "outbound.db"

# Application & Security
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "b2b_outbound_dev_secret_key_84719204")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@agency.ai")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# Gemini API Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Cold Email Dispatcher (SMTP)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_NAME = os.getenv("FROM_NAME", "GrowthOps Outbound")

# Telegram Alerts
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "")

# Operation Mode & Booking Gateway
ENGINE_MODE = os.getenv("ENGINE_MODE", "self")  # "self" or "client"
PUBLIC_BOOKING_URL = os.getenv("PUBLIC_BOOKING_URL", "https://cal.com/growthops/strategy-session")

# Dispatcher Safeguards & Limits
DAILY_EMAIL_CAP = int(os.getenv("DAILY_EMAIL_CAP", "30"))
MIN_SEND_DELAY_SECONDS = int(os.getenv("MIN_SEND_DELAY_SECONDS", "120"))
MAX_SEND_DELAY_SECONDS = int(os.getenv("MAX_SEND_DELAY_SECONDS", "240"))
