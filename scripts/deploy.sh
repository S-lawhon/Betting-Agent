#!/usr/bin/env bash
# =============================================================================
# scripts/deploy.sh
# Push the Betting Pod Shop project to your DigitalOcean server.
#
# Usage (local shell or CI checkout, inside the repository root):
#   bash scripts/deploy.sh YOUR_SERVER_IP           # sync only
#   bash scripts/deploy.sh YOUR_SERVER_IP restart    # sync + restart service
#
# Features:
#   - Pre-deploy: runs pytest locally (aborts on new failures)
#   - Sync: rsync to server (excludes data, git, cache)
#   - Post-deploy: health check via standalone dashboard /healthz (:8081)
#   - Auto-rollback: if health check fails after restart, rolls back to
#     the previous deployment and restarts again
# =============================================================================

set -euo pipefail

SERVER_IP="${1:-}"
RESTART="${2:-}"
REMOTE_DIR="/opt/betting-pod-shop"
REMOTE_USER="root"
SERVICE_NAME="betting-pod-shop"
# The dashboard must restart with every code deploy: it imports checks.py /
# dashboard_api.py at startup and computes alarms from the modules in memory,
# so a long-lived process serves stale check logic no matter what is on disk.
# Measured 2026-08-11: a process from 08-03 kept paging two alarms whose
# checks had been fixed on disk for days (evmap UTC-day false positive,
# research-audit oneshot handling).
DASHBOARD_SERVICE="betting-dashboard"
# Phase 3 cutover (2026-08-05): the gate targets the standalone dashboard on
# :8081, not the engine — the engine no longer serves HTTP. /healthz returns
# JSON with HTTP 200 (the curl -sf below asserts only on status). This line
# and the --web removal in betting-pod-shop.service must revert TOGETHER.
HEALTH_URL="http://localhost:8081/healthz"
HEALTH_TIMEOUT=60      # seconds to wait for healthy response
HEALTH_INTERVAL=5      # seconds between health check retries
BACKUP_SUFFIX=".deploy-backup"
PYTHON="${PYTHON:-python3}"
DEPLOY_TOKEN="$(date -u +%Y%m%dT%H%M%SZ)-$$"
REMOTE_LOCK_DIR="/run/lock/betting-pod-shop-deploy"
REMOTE_BACKUP_DIR="${REMOTE_DIR}${BACKUP_SUFFIX}.${DEPLOY_TOKEN}"
LOCK_HELD=false

release_deploy_lock() {
  if [[ "$LOCK_HELD" != "true" ]]; then
    return
  fi
  ssh "${REMOTE_USER}@${SERVER_IP}" "
    owner=\$(cat '${REMOTE_LOCK_DIR}/owner' 2>/dev/null || true)
    if [ \"\$owner\" = '${DEPLOY_TOKEN}' ]; then
      rm -f '${REMOTE_LOCK_DIR}/owner'
      rmdir '${REMOTE_LOCK_DIR}'
    fi
  " >/dev/null 2>&1 || true
  LOCK_HELD=false
}

if [[ -z "$SERVER_IP" ]]; then
  echo "Usage: bash scripts/deploy.sh SERVER_IP [restart]"
  exit 1
fi

# ── Pre-deploy: prove the test gate can actually measure ──────────────
# Several test modules open with `pytest.importorskip(...)`, so a missing
# third-party dependency skips the WHOLE module and the run still reports
# green. Measured 2026-08-01: this script's shell resolved python3 to a
# 3.14 framework build with no numpy/scipy, silently dropping 47 tests —
# every test of the P-029 combo correlation pricer, the module whose output
# Gate 0's verdict depends on — and the summary showed it only as "4
# skipped" instead of 2. A gate that quietly stops measuring is worse than
# no gate, so a missing dependency now aborts the deploy instead.
echo "==> Checking the test environment ..."
echo "    interpreter: $("$PYTHON" -c 'import sys; print(sys.executable)')"
missing=$("$PYTHON" - <<'PY'
import importlib.util
# Anything a test module importorskips on. Add to this list, never remove:
# a name dropping off is exactly the silent-shrinkage this check exists for.
required = ("pytest", "yaml", "requests", "numpy", "scipy", "cryptography")
print(" ".join(m for m in required if importlib.util.find_spec(m) is None))
PY
)
if [[ -n "$missing" ]]; then
  echo ""
  echo "ABORT: the test interpreter is missing: $missing"
  echo ""
  echo "  Test modules importorskip on these, so the suite would report green"
  echo "  while silently not running them."
  echo ""
  # Name a concrete interpreter that works rather than a placeholder. Note
  # `bash` re-orders PATH relative to an interactive zsh, which is how the
  # deploy came to use a different python3 than the one you get by typing
  # python3 at a prompt — so "just fix your PATH" is not actionable advice.
  for candidate in /usr/bin/python3 \
                   /Library/Developer/CommandLineTools/usr/bin/python3 \
                   /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    [[ -x "$candidate" ]] || continue
    if "$candidate" - <<'PY' 2>/dev/null
import importlib.util, sys
required = ("pytest", "yaml", "requests", "numpy", "scipy", "cryptography")
sys.exit(0 if all(importlib.util.find_spec(m) for m in required) else 1)
PY
    then
      echo "  This interpreter has everything:"
      echo "    PYTHON=$candidate bash scripts/deploy.sh $SERVER_IP ${RESTART}"
      echo ""
      break
    fi
  done
  echo "  Or install into the current one:"
  echo "    $PYTHON -m pip install $missing"
  echo ""
  echo "  Nothing was deployed."
  exit 1
fi

# ── Pre-deploy: run tests locally ─────────────────────────────────────
echo "==> Running local tests ..."
if ! "$PYTHON" -m pytest tests/ -q --tb=no 2>&1 | tail -3; then
  echo ""
  echo "WARNING: Some tests failed. Review output above."
  if [[ "${CI:-}" == "true" || ! -t 0 ]]; then
    echo "CI/non-interactive mode detected; aborting deploy on test failure."
    exit 1
  fi
  read -p "Continue with deploy? [y/N] " -n 1 -r
  echo ""
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborting deploy."
    exit 1
  fi
fi

# ── Serialize deploys before either one can rsync production ─────────
# A shared backup directory is not a lock. Measured 2026-08-16: two deploys
# both passed tests, interleaved rsync, and one removed the other's backup
# parent while `cp -a` was still populating it. The code reached production,
# but the deploy aborted before its own restart/health gate. An atomic remote
# mkdir covers sync through health/rollback; a token prevents one caller from
# releasing another's lock. /run is cleared by reboot. A stale lock is removed
# manually only after proving no deploy owns it -- guessing by age could delete
# a valid lock during a slow test or backup.
echo ""
echo "==> Acquiring deployment lock ..."
if ! ssh "${REMOTE_USER}@${SERVER_IP}" "
  if mkdir '${REMOTE_LOCK_DIR}' 2>/dev/null; then
    printf '%s\n' '${DEPLOY_TOKEN}' > '${REMOTE_LOCK_DIR}/owner'
    exit 0
  fi
  echo 'DEPLOY LOCKED: owner='\"\$(cat '${REMOTE_LOCK_DIR}/owner' 2>/dev/null || echo unknown)\" >&2
  exit 73
"; then
  echo "ABORT: another deployment owns ${REMOTE_LOCK_DIR}."
  echo "Verify the owner before removing a stale lock; nothing was synced."
  exit 1
fi
LOCK_HELD=true
trap release_deploy_lock EXIT INT TERM
echo "    acquired: ${DEPLOY_TOKEN}"

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
    if [ -e '${REMOTE_BACKUP_DIR}' ]; then
      echo 'ERROR: unique backup path already exists: ${REMOTE_BACKUP_DIR}' >&2
      exit 1
    fi
    cp -a '${REMOTE_DIR}' '${REMOTE_BACKUP_DIR}'
  "

  echo "==> Restarting services on server ..."
  # Dashboard first: the health gate below reads ${HEALTH_URL}, which the
  # dashboard serves, so the gate then validates BOTH the new engine and the
  # new dashboard code.
  ssh "${REMOTE_USER}@${SERVER_IP}" "systemctl restart ${DASHBOARD_SERVICE} ${SERVICE_NAME}"

  echo "==> Waiting for health check (${HEALTH_TIMEOUT}s timeout) ..."
  elapsed=0
  healthy=false

  while [ $elapsed -lt $HEALTH_TIMEOUT ]; do
    # Check if service is active AND web dashboard responds
    if ssh "${REMOTE_USER}@${SERVER_IP}" "
      systemctl is-active ${SERVICE_NAME} >/dev/null 2>&1 && \
      systemctl is-active ${DASHBOARD_SERVICE} >/dev/null 2>&1 && \
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
    echo "==> Deploy successful. Dashboard: https://dashboard.htxtrades.org (standalone, :8081)"
    # Clean up backup after successful deploy
    ssh "${REMOTE_USER}@${SERVER_IP}" "rm -rf '${REMOTE_BACKUP_DIR}'" 2>/dev/null || true
  else
    echo ""
    echo "==> HEALTH CHECK FAILED — initiating rollback ..."
    ssh "${REMOTE_USER}@${SERVER_IP}" "
      systemctl stop ${SERVICE_NAME} 2>/dev/null || true
      systemctl stop ${DASHBOARD_SERVICE} 2>/dev/null || true
      if [ -d '${REMOTE_BACKUP_DIR}' ]; then
        rm -rf '${REMOTE_DIR}'
        mv '${REMOTE_BACKUP_DIR}' '${REMOTE_DIR}'
        echo 'Restored from backup.'
        # Both services must reload the RESTORED code — leaving the dashboard
        # down (or running rolled-back-from code) recreates the stale-process
        # drift this restart step exists to prevent.
        systemctl start ${DASHBOARD_SERVICE}
        systemctl start ${SERVICE_NAME}
        echo 'Services restarted with previous version.'
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
