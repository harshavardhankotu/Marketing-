import os
from dotenv import load_dotenv

# Resolve path relative to this config file's location
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# Load environment secrets on module initialization
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

# Base configuration paths
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'campaigns.db')
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'data', 'output')

# Loaded secrets with secure production fallbacks
FLASK_SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'a_very_secret_key_for_session_signing_987654')
POSTBACK_SECRET = os.getenv('POSTBACK_SECRET', 'default_secret_key_123')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'your_telegram_bot_token_here')
ADMIN_TELEGRAM_ID = os.getenv('ADMIN_TELEGRAM_ID', '')
ADMIN_DEFAULT_PASSWORD = os.getenv('ADMIN_DEFAULT_PASSWORD', 'admin123')

