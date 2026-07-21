#!/usr/bin/env bash
# =============================================================================
# scripts/server_setup.sh
# One-time setup script for a fresh Ubuntu 22.04/24.04 DigitalOcean Droplet.
#
# Run this ON THE SERVER after SSH-ing in:
#   ssh root@YOUR_SERVER_IP
#   bash /opt/betting-pod-shop/scripts/server_setup.sh
# =============================================================================

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

APP_DIR="/opt/betting-pod-shop"
SERVICE_NAME="betting-pod-shop"
PYTHON="python3.12"
SERVICE_USER="bettingbot"
VENV_DIR="$APP_DIR/venv"          # existing venv on the server

echo "==> Updating system packages ..."
apt-get update -qq
apt-get upgrade -y -qq -o Dpkg::Options::="--force-confold"

echo "==> Installing Python 3.12 and pip ..."
apt-get install -y -qq python3.12 python3.12-venv python3-pip python3-full

# ── Create dedicated service account ────────────────────────────────────────
echo "==> Creating service user '$SERVICE_USER' ..."
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "    Created system user: $SERVICE_USER"
else
    echo "    User $SERVICE_USER already exists"
fi

# ── Set up venv and install dependencies ────────────────────────────────────
echo "==> Setting up Python virtual environment ..."
cd "$APP_DIR"
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r requirements.txt 2>/dev/null || \
    "$VENV_DIR/bin/pip" install --quiet pyyaml py-clob-client httpx

VENV_PYTHON="$VENV_DIR/bin/python"

# ── Set ownership ───────────────────────────────────────────────────────────
echo "==> Setting file ownership ..."
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
# Ensure data directory exists with proper permissions
mkdir -p "$APP_DIR/data/trade_logs" "$APP_DIR/data/trade_logs/archive" "$APP_DIR/data/pods"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/data"

echo "==> Configuring firewall (UFW) ..."
ufw --force enable
ufw allow 22/tcp   comment 'SSH'
ufw allow 8080/tcp comment 'Betting Pod Shop dashboard'
ufw status

# ── Install systemd service ─────────────────────────────────────────────────
echo "==> Installing systemd service ..."
cp "$APP_DIR/scripts/betting-pod-shop.service" \
   "/etc/systemd/system/${SERVICE_NAME}.service"

# Patch the service file with the venv Python path
sed -i "s|__PYTHON__|${VENV_PYTHON}|g" \
    "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start  "$SERVICE_NAME"

# ── Set up trade log rotation cron ──────────────────────────────────────────
echo "==> Installing trade log rotation cron job ..."
CRON_CMD="0 3 1 * * ${VENV_PYTHON} ${APP_DIR}/scripts/rotate_trade_logs.py --log-path ${APP_DIR}/data/trade_logs/trade_log.jsonl --archive-dir ${APP_DIR}/data/trade_logs/archive --days 30"
(crontab -u "$SERVICE_USER" -l 2>/dev/null | grep -v rotate_trade_logs; echo "$CRON_CMD") | crontab -u "$SERVICE_USER" -

# ── Configure journald log size cap ─────────────────────────────────────────
echo "==> Configuring journald log rotation ..."
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/betting-pod-shop.conf <<'EOF'
# Cap journal storage for the betting-pod-shop service
[Journal]
SystemMaxUse=500M
SystemMaxFileSize=50M
MaxRetentionSec=30day
EOF
systemctl restart systemd-journald

echo ""
echo "==> Setup complete!"
echo "    Service status:"
systemctl status "$SERVICE_NAME" --no-pager
echo ""
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo "    Dashboard: http://${SERVER_IP}:8080"
echo ""
echo "    Useful commands:"
echo "      systemctl status  $SERVICE_NAME   # check health"
echo "      systemctl restart $SERVICE_NAME   # restart engine"
echo "      journalctl -u $SERVICE_NAME -f    # live logs"
echo "      sudo -u $SERVICE_USER ${VENV_PYTHON} ${APP_DIR}/scripts/rotate_trade_logs.py --dry-run  # preview log rotation"
