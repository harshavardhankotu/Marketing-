#!/usr/bin/env bash
# ==============================================================================
# CPA LEAD ARBITRAGE & UPI PAYOUT SUITE - UBUNTU VPS MASTER DEPLOYMENT SCRIPT
# ==============================================================================
# Supported OS: Ubuntu 20.04 LTS / 22.04 LTS / 24.04 LTS
# Enforce execution as root operator.
# ==============================================================================

# Exit immediately if a command exits with a non-zero status
set -e

# ANSI Color Codes for premium audit logs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1${NC}"
}

log_success() {
    echo -e "${GREEN}[SUCCESS] $(date '+%Y-%m-%d %H:%M:%S') - $1${NC}"
}

log_warn() {
    echo -e "${YELLOW}[WARN] $(date '+%Y-%m-%d %H:%M:%S') - $1${NC}"
}

log_error() {
    echo -e "${RED}[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1${NC}"
}

# ------------------------------------------------------------------------------
# 1. Enforce ROOT Privileges
# ------------------------------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
    log_error "This script must be executed as root. Please run: sudo ./deploy.sh"
    exit 1
fi

log_info "Starting Production Deployment Setup..."

# ------------------------------------------------------------------------------
# 2. Establish Installation Directory
# ------------------------------------------------------------------------------
DEPLOY_DIR="/var/www/affiliate"
if [ "$PWD" != "$DEPLOY_DIR" ]; then
    log_info "Creating production target directory at $DEPLOY_DIR..."
    mkdir -p "$DEPLOY_DIR"
    log_info "Copying project codebase into $DEPLOY_DIR..."
    # Copy files (excluding venv if present)
    rsync -aq --exclude 'venv' --exclude '.git' . "$DEPLOY_DIR/"
    cd "$DEPLOY_DIR"
fi

# ------------------------------------------------------------------------------
# 3. System Packages Installation
# ------------------------------------------------------------------------------
log_info "Updating system package list..."
apt-get update -y

log_info "Upgrading existing system packages (resilient-safe)..."
apt-get upgrade -y

log_info "Installing core system libraries (Python, SQLite, ffmpeg, git, curl, rsync)..."
apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    sqlite3 \
    ffmpeg \
    git \
    curl \
    rsync \
    debian-keyring \
    debian-archive-keyring \
    apt-transport-https

# ------------------------------------------------------------------------------
# 4. Install Caddy Server (Preferred Auto-SSL Reverse Proxy)
# ------------------------------------------------------------------------------
log_info "Installing Caddy Reverse Proxy..."
if ! command -v caddy &> /dev/null; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y
    apt-get install caddy -y
    log_success "Caddy Server successfully installed."
else
    log_info "Caddy Server is already installed."
fi

# ------------------------------------------------------------------------------
# 5. Build Python Virtual Environment & Install Requirements
# ------------------------------------------------------------------------------
log_info "Creating secure Python virtual environment..."
python3 -m venv venv

log_info "Activating virtual environment & updating pip..."
source venv/bin/activate
pip install --upgrade pip

log_info "Installing Python dependencies (Flask, APScheduler, Pillow, MoviePy, etc.)..."
pip install -r requirements.txt

log_info "Installing Gunicorn production server..."
pip install gunicorn

# ------------------------------------------------------------------------------
# 6. Playwright Headless Browser Installation
# ------------------------------------------------------------------------------
log_info "Installing Playwright system library dependencies..."
venv/bin/playwright install-deps

log_info "Installing Chromium browser binaries for lead scraping..."
venv/bin/playwright install chromium

# ------------------------------------------------------------------------------
# 7. Configure and Initialize Daemon Service
# ------------------------------------------------------------------------------
log_info "Registering Systemd Daemon service (deploy/affiliate.service)..."
if [ ! -f "deploy/affiliate.service" ]; then
    log_error "Systemd configuration file deploy/affiliate.service not found!"
    exit 1
fi

cp deploy/affiliate.service /etc/systemd/system/affiliate.service
systemctl daemon-reload

log_info "Enabling affiliate.service on boot..."
systemctl enable affiliate.service

# ------------------------------------------------------------------------------
# 8. Set Up File Ownership and Permissions (Crucial for SQLite WAL Mode)
# ------------------------------------------------------------------------------
log_info "Setting secure directory permissions..."
# Ensure the database data directory exists
mkdir -p data data/output assets

# Change ownership of the entire directory to www-data (system user running Gunicorn & Caddy)
chown -R www-data:www-data "$DEPLOY_DIR"

# Allow group write access to let SQLite manage WAL files safely without permission deadlocks
chmod -R 775 "$DEPLOY_DIR"
find "$DEPLOY_DIR" -type d -exec chmod 775 {} \;
find "$DEPLOY_DIR" -type f -exec chmod 664 {} \;
chmod +x "$DEPLOY_DIR/venv/bin/gunicorn"

# ------------------------------------------------------------------------------
# 9. Start the Service Daemon
# ------------------------------------------------------------------------------
log_info "Starting Gunicorn daemon service..."
systemctl restart affiliate.service

# ------------------------------------------------------------------------------
# 10. Configure Caddy reverse proxy
# ------------------------------------------------------------------------------
log_info "Configuring Caddy reverse proxy..."
if [ ! -f "deploy/Caddyfile" ]; then
    log_error "Caddy configuration file deploy/Caddyfile not found!"
    exit 1
fi

# Copy default Caddyfile
cp deploy/Caddyfile /etc/caddy/Caddyfile

# Ask user for their domain name to configure SSL, or default to IP-based port 80
echo -e "\n${YELLOW}=== HTTPS PROVISIONING CONFIGURATION ===${NC}"
read -p "Enter your registered domain (e.g. affiliate.marketing.ai) or press ENTER to test on HTTP IP port 80: " DOMAIN_NAME

if [ -z "$DOMAIN_NAME" ]; then
    DOMAIN_NAME=":80"
    log_warn "No domain name entered. Configuring reverse proxy to listen on public HTTP port 80 (Automatic SSL disabled)."
else
    log_success "Domain entered: $DOMAIN_NAME. Automatic Let's Encrypt / ZeroSSL TLS will be provisioned."
fi

# Dynamically inject the domain/port into the production Caddyfile
sed -i "s/yourdomain.com/$DOMAIN_NAME/g" /etc/caddy/Caddyfile

# Verify the Caddyfile configuration syntax is correct
log_info "Validating Caddy reverse proxy configuration..."
caddy validate --config /etc/caddy/Caddyfile

log_info "Starting and enabling Caddy Server reverse proxy..."
systemctl enable caddy
systemctl restart caddy

# ------------------------------------------------------------------------------
# 11. Run Local API Health Verification
# ------------------------------------------------------------------------------
log_info "Waiting for Gunicorn daemon to start up (sleeping 5s)..."
sleep 5

log_info "Performing internal API health-check loop..."
HEALTH_CHECK_URL="http://127.0.0.1:5000/login"
STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_CHECK_URL" || echo "000")

if [ "$STATUS_CODE" -eq 200 ]; then
    log_success "Internal Gunicorn daemon health check PASSED (HTTP 200 OK)."
else
    log_error "Internal Gunicorn daemon health check FAILED (HTTP Status: $STATUS_CODE)."
    log_warn "Please check server logs by running: journalctl -u affiliate.service -n 50"
    exit 1
fi

# ------------------------------------------------------------------------------
# 12. Final Diagnostics & Success Notice
# ------------------------------------------------------------------------------
echo -e "\n"
print_line="================================================================================"
echo -e "${GREEN}$print_line"
echo -e "   CPA LEAD GENERATION & UPI PAYOUT SUITE DEPLOYED SUCCESSFULLY!"
echo -e "$print_line${NC}"
echo -e "Your application daemon is now fully isolated, secure, and running 24/7."
echo -e ""
echo -e "  [Gunicorn Service Status] : $(systemctl is-active affiliate.service)"
echo -e "  [Caddy Reverse Proxy State] : $(systemctl is-active caddy)"
echo -e "  [Internal Gateway URL]      : http://127.0.0.1:5000"
if [ "$DOMAIN_NAME" = ":80" ]; then
    echo -e "  [Public Application URL]    : http://<YOUR_VPS_PUBLIC_IP>"
else
    echo -e "  [Public Application URL]    : https://$DOMAIN_NAME"
fi
echo -e ""
echo -e "${YELLOW}Useful Administrative Commands:${NC}"
echo -e "  - View live app logs       : journalctl -u affiliate.service -f"
echo -e "  - View reverse proxy logs  : journalctl -u caddy -f"
echo -e "  - Restart the application  : systemctl restart affiliate.service"
echo -e "  - Restart proxy web server  : systemctl restart caddy"
echo -e "${GREEN}$print_line${NC}\n"
