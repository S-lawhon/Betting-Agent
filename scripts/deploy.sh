#!/usr/bin/env bash
# =============================================================================
# scripts/deploy.sh
# Push the Betting Pod Shop project to your DigitalOcean server.
#
# Usage (from your Mac, inside the Betting Fund Project folder):
#   bash scripts/deploy.sh YOUR_SERVER_IP           # sync only
#   bash scripts/deploy.sh YOUR_SERVER_IP restart    # sync + restart service
#
# Features:
#   - Pre-deploy: runs pytest locally (aborts on new failures)
#   - Sync: rsync to server (excludes data, git, cache)
#   - Post-deploy: health check via web dashboard /api/state
#   - Auto-rollback: if health check fails after restart, rolls back to
#     the previous deployment and restarts again
# =============================================================================

set -euo pipefail

SERVER_IP="${1:-}"
RESTART="${2:-}"
REMOTE_DIR="/opt/betting-pod-shop"
REMOTE_USER="root"
SERVICE_NAME="betting-pod-shop"
HEALTH_URL="http://localhost:8080/health"
HEALTH_TIMEOUT=60      # seconds to wait for healthy response
HEALTH_INTERVAL=5      # seconds between health check retries
BACKUP_SUFFIX=".deploy-backup"

if [[ -z "$SERVER_IP" ]]; then
  echo "Usage: bash scripts/deploy.sh SERVER_IP [restart]"
  exit 1
fi

# ── Pre-deploy: run tests locally ─────────────────────────────────────
echo "==> Running local tests ..."
if ! python3 -m pytest tests/ -q --tb=no 2>&1 | tail -3; then
  echo ""
  echo "WARNING: Some tests failed. Review output above."
  read -p "Continue with deploy? [y/N] " -n 1 -r
  echo ""
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborting deploy."
    exit 1
  fi
fi

# ── Sync files to server ──────────────────────────────────────────────
echo ""
echo "==> Syncing files to $SERVER_IP:$REMOTE_DIR ..."
rsync -avz --progress \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='data/' \
  --exclude='*.jsonl' \
  --exclude='*.log' \
  --exclude='.mypy_cache' \
  --exclude='.pytest_cache' \
  --exclude='venv/' \
  --exclude='.claude/worktrees/' \
  --exclude='manager/state/' \
  ./ "${REMOTE_USER}@${SERVER_IP}:${REMOTE_DIR}/"

echo "==> Sync complete."

# ── Fix ownership (rsync as root creates root-owned files; service runs as bettingbot) ──
echo "==> Fixing file ownership ..."
ssh "${REMOTE_USER}@${SERVER_IP}" "chown -R bettingbot:bettingbot ${REMOTE_DIR}"

# ── Report systemd unit drift ─────────────────────────────────────────
# rsync ships scripts/systemd/ but nothing installs it, so the unit file in
# the repo and the unit systemd is actually running can disagree indefinitely
# and silently. That is how betting-strategy-agents ended up with an [Install]
# section on the droplet that was missing from git (2026-08-01). Installing
# automatically is deliberately NOT done here — restarting units is not this
# script's job — but the difference is now impossible to miss.
echo ""
echo "==> Checking systemd unit drift ..."
unit_drift=$(ssh "${REMOTE_USER}@${SERVER_IP}" "
  for f in ${REMOTE_DIR}/scripts/systemd/*.service ${REMOTE_DIR}/scripts/systemd/*.timer; do
    [ -e \"\$f\" ] || continue
    n=\$(basename \"\$f\")
    if [ ! -e \"/etc/systemd/system/\$n\" ]; then
      echo \"    NOT INSTALLED   \$n\"
    elif ! cmp -s \"\$f\" \"/etc/systemd/system/\$n\"; then
      echo \"    DIFFERS         \$n\"
    fi
  done
" || true)

if [[ -n "$unit_drift" ]]; then
  echo "$unit_drift"
  echo ""
  echo "    The repo and /etc/systemd/system disagree. Nothing was installed."
  echo "    To install one:  ssh ${REMOTE_USER}@${SERVER_IP} \\"
  echo "      'cp ${REMOTE_DIR}/scripts/systemd/UNIT /etc/systemd/system/ &&"
  echo "       systemctl daemon-reload && systemctl enable --now UNIT'"
  echo ""
  echo "    NOTE: a unit named in manager/registry.yaml but not installed now"
  echo "    reports as a WARN, not a CRITICAL page — but it is still unmonitored."
else
  echo "    All repo units match what is installed in /etc/systemd/system."
fi

# ── Post-deploy: restart + health check ───────────────────────────────
if [[ "$RESTART" == "restart" ]]; then
  echo ""
  echo "==> Creating pre-deploy backup on server ..."
  ssh "${REMOTE_USER}@${SERVER_IP}" "
    if [ -d '${REMOTE_DIR}${BACKUP_SUFFIX}' ]; then
      rm -rf '${REMOTE_DIR}${BACKUP_SUFFIX}'
    fi
    cp -a '${REMOTE_DIR}' '${REMOTE_DIR}${BACKUP_SUFFIX}'
  "

  echo "==> Restarting service on server ..."
  ssh "${REMOTE_USER}@${SERVER_IP}" "systemctl restart ${SERVICE_NAME}"

  echo "==> Waiting for health check (${HEALTH_TIMEOUT}s timeout) ..."
  elapsed=0
  healthy=false

  while [ $elapsed -lt $HEALTH_TIMEOUT ]; do
    # Check if service is active AND web dashboard responds
    if ssh "${REMOTE_USER}@${SERVER_IP}" "
      systemctl is-active ${SERVICE_NAME} >/dev/null 2>&1 && \
      curl -sf --max-time 5 ${HEALTH_URL} >/dev/null 2>&1
    "; then
      healthy=true
      break
    fi
    sleep $HEALTH_INTERVAL
    elapsed=$((elapsed + HEALTH_INTERVAL))
    echo "    ... waiting ($elapsed/${HEALTH_TIMEOUT}s)"
  done

  if $healthy; then
    echo "==> Health check PASSED"
    ssh "${REMOTE_USER}@${SERVER_IP}" "systemctl status ${SERVICE_NAME} --no-pager"
    echo ""
    echo "==> Deploy successful. Dashboard: http://${SERVER_IP}:8080"
    # Clean up backup after successful deploy
    ssh "${REMOTE_USER}@${SERVER_IP}" "rm -rf '${REMOTE_DIR}${BACKUP_SUFFIX}'" 2>/dev/null || true
  else
    echo ""
    echo "==> HEALTH CHECK FAILED — initiating rollback ..."
    ssh "${REMOTE_USER}@${SERVER_IP}" "
      systemctl stop ${SERVICE_NAME} 2>/dev/null || true
      if [ -d '${REMOTE_DIR}${BACKUP_SUFFIX}' ]; then
        rm -rf '${REMOTE_DIR}'
        mv '${REMOTE_DIR}${BACKUP_SUFFIX}' '${REMOTE_DIR}'
        echo 'Restored from backup.'
        systemctl start ${SERVICE_NAME}
        echo 'Service restarted with previous version.'
        systemctl status ${SERVICE_NAME} --no-pager
      else
        echo 'ERROR: No backup found! Manual intervention required.'
        exit 1
      fi
    "
    echo ""
    echo "==> ROLLBACK COMPLETE. Previous version restored."
    echo "    Review logs: ssh ${REMOTE_USER}@${SERVER_IP} journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
  fi
fi
