#!/usr/bin/env bash
# ==============================================================================
# deploy.sh
# Automated Ubuntu VPS Provisioning & Deployment Script
# Autonomous B2B Lead Generation, Cold Outreach & Meeting Booking Engine
# ==============================================================================

set -euo pipefail

APP_DIR="/var/www/outbound"
LOG_DIR="/var/log/outbound"
SERVICE_NAME="outreach.service"

echo "=============================================================================="
echo "🚀 INITIATING AUTONOMOUS B2B OUTBOUND SUITE ZERO-COST DEPLOYMENT"
echo "=============================================================================="

# 1. Ensure Running as Root
if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Error: This deployment script must be run as root (sudo ./deploy.sh)."
    exit 1
fi

# 2. Install System Dependencies
echo "[1/7] Updating system packages and installing prerequisites..."
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    sqlite3 \
    curl \
    git \
    debian-keyring \
    debian-archive-keyring \
    apt-transport-https

# 3. Install Caddy Web Server (if not already installed)
if ! command -v caddy &> /dev/null; then
    echo "[2/7] Installing Caddy reverse proxy with automated SSL..."
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y
    apt-get install -y caddy
else
    echo "[2/7] Caddy is already installed."
fi

# 4. Prepare Application Directories & Virtual Environment
echo "[3/7] Setting up application workspace at ${APP_DIR}..."
mkdir -p "${APP_DIR}"
mkdir -p "${APP_DIR}/data/backups"
mkdir -p "${LOG_DIR}"

# If current folder contains source code, copy into APP_DIR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "${SCRIPT_DIR}" != "${APP_DIR}" ]; then
    echo "      Syncing codebase into ${APP_DIR}..."
    cp -ru "${SCRIPT_DIR}/." "${APP_DIR}/"
fi

cd "${APP_DIR}"

# 5. Create Python Virtual Environment & Install Dependencies
echo "[4/7] Configuring Python virtual environment and installing packages..."
if [ ! -d "${APP_DIR}/venv" ]; then
    python3 -m venv "${APP_DIR}/venv"
fi

"${APP_DIR}/venv/bin/pip" install --upgrade pip
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

# 6. Ensure Environment Variables File (.env)
echo "[5/7] Checking environment configuration..."
if [ ! -f "${APP_DIR}/.env" ]; then
    if [ -f "${APP_DIR}/.env.example" ]; then
        cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
        echo "⚠️  Created .env from .env.example. Update real credentials in ${APP_DIR}/.env."
    fi
fi

# Set proper ownership for www-data daemon
chown -R www-data:www-data "${APP_DIR}"
chown -R www-data:www-data "${LOG_DIR}"
chmod 750 "${APP_DIR}"
chmod 770 "${APP_DIR}/data"
chmod 770 "${APP_DIR}/data/backups"

# 7. Configure and Start Systemd Daemon Service
echo "[6/7] Installing Systemd daemon service (${SERVICE_NAME})..."
cp "${APP_DIR}/deploy/outreach.service" "/etc/systemd/system/${SERVICE_NAME}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

# Install Caddyfile if present
if [ -f "${APP_DIR}/deploy/Caddyfile" ]; then
    echo "      Configuring Caddy reverse proxy..."
    cp "${APP_DIR}/deploy/Caddyfile" "/etc/caddy/Caddyfile"
    systemctl reload caddy || systemctl restart caddy
fi

# 8. Liveness & Health Probe
echo "[7/7] Verifying service liveness and health endpoint..."
sleep 3

if curl -s -f http://127.0.0.1:5000/health > /dev/null; then
    echo "=============================================================================="
    echo "✅ DEPLOYMENT COMPLETE & SERVICE OPERATIONAL!"
    echo "   Local Health Check: http://127.0.0.1:5000/health [200 OK]"
    echo "   Systemd Service:    systemctl status ${SERVICE_NAME}"
    echo "   Application Logs:   tail -f ${LOG_DIR}/access.log"
    echo "=============================================================================="
else
    echo "⚠️  Warning: Service started, but /health endpoint did not immediately respond 200."
    echo "   Check service status: systemctl status ${SERVICE_NAME}"
    echo "   Check journal logs:   journalctl -u ${SERVICE_NAME} -n 50"
fi
