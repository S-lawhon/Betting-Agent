#!/usr/bin/env bash
# sync_env_to_vps.sh — push .env and the Kalshi PEM from this Mac to a VPS.
#
# WHY THIS EXISTS
# ---------------
# Rotating a credential locally and forgetting the server is a silent outage:
# the engine keeps running and every API call fails. That happened on
# 2026-07-29 — keys were rotated after a leak, the main VPS kept the dead ones,
# and nothing announced it. **Run this every single time a credential changes.**
#
# The deploy rsync deliberately EXCLUDES .env (secrets should not ride along on
# every code push), so this is the one path that updates them. That is a
# deliberate split: code deploys often and automatically, secrets move rarely
# and on purpose.
#
# Run FROM YOUR MAC (Cowork cannot SSH — port 22 is blocked from the sandbox).
#
# Usage:
#   bash scripts/sync_env_to_vps.sh                      # main pod-shop VPS
#   bash scripts/sync_env_to_vps.sh --host 1.2.3.4 --dir /opt/p029
#   bash scripts/sync_env_to_vps.sh --dry-run

set -euo pipefail

HOST="${HOST:-129.212.176.202}"
USER_AT="root"
REMOTE_DIR="${REMOTE_DIR:-/opt/betting-pod-shop}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/betting_deploy}"
SERVICE="${SERVICE:-betting-pod-shop}"
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --dir) REMOTE_DIR="$2"; shift 2 ;;
    --key) SSH_KEY="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --no-restart) SERVICE=""; shift ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$HERE/.env"
PEM_FILE="$HERE/kalshi_private_key.pem"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*"; exit 1; }

say "preflight"
[ -f "$ENV_FILE" ] || fail "no .env at $ENV_FILE"
[ -f "$SSH_KEY" ]  || fail "no ssh key at $SSH_KEY (pass --key)"
echo "  local .env : $ENV_FILE"
echo "  target     : $USER_AT@$HOST:$REMOTE_DIR"

# The PEM is optional -- some hosts may not need Kalshi auth.
if [ -f "$PEM_FILE" ]; then
  echo "  pem        : $PEM_FILE"
else
  echo "  pem        : (none found — skipping)"
fi

# A machine-specific absolute path is the trap: it resolves on the Mac and
# silently fails on the server. Rewrite it to a repo-relative path in transit;
# src/kalshi_private.py resolves relative paths against the repo root.
say "checking KALSHI_PRIVATE_KEY_PATH portability"
TMP_ENV="$(mktemp)"
trap 'rm -f "$TMP_ENV"' EXIT
if grep -qE '^\s*(export\s+)?KALSHI_PRIVATE_KEY_PATH\s*=\s*/' "$ENV_FILE"; then
  echo "  absolute path found — rewriting to repo-relative for the server"
  sed -E 's#^([[:space:]]*(export[[:space:]]+)?KALSHI_PRIVATE_KEY_PATH[[:space:]]*=[[:space:]]*).*$#\1kalshi_private_key.pem#' \
      "$ENV_FILE" > "$TMP_ENV"
else
  echo "  already portable"
  cp "$ENV_FILE" "$TMP_ENV"
fi

if grep -qE '^\s*(export\s+)?KALSHI_PRIVATE_KEY\s*=' "$TMP_ENV"; then
  fail "the inline KALSHI_PRIVATE_KEY variable is back in .env — remove it and
     use KALSHI_PRIVATE_KEY_PATH. A multi-line PEM inline is how a key leaked."
fi

if [ "$DRY" = "1" ]; then
  say "dry run — nothing sent"
  echo "  would copy .env (with a portable key path) and the PEM to"
  echo "  $USER_AT@$HOST:$REMOTE_DIR/, then restart ${SERVICE:-<no service>}"
  exit 0
fi

SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=accept-new)

say "backing up the remote .env"
ssh "${SSH_OPTS[@]}" "$USER_AT@$HOST" \
  "cd $REMOTE_DIR && [ -f .env ] && cp .env .env.prev-\$(date +%Y%m%d%H%M%S) || true"

say "copying"
scp "${SSH_OPTS[@]}" "$TMP_ENV" "$USER_AT@$HOST:$REMOTE_DIR/.env"
[ -f "$PEM_FILE" ] && scp "${SSH_OPTS[@]}" "$PEM_FILE" "$USER_AT@$HOST:$REMOTE_DIR/kalshi_private_key.pem"

say "locking down permissions"
ssh "${SSH_OPTS[@]}" "$USER_AT@$HOST" \
  "cd $REMOTE_DIR && chmod 600 .env kalshi_private_key.pem 2>/dev/null; \
   chown bettingbot:bettingbot .env kalshi_private_key.pem 2>/dev/null || true; \
   ls -l .env kalshi_private_key.pem 2>/dev/null | sed 's/^/  /'"

say "verifying the credentials actually work ON THE SERVER"
ssh "${SSH_OPTS[@]}" "$USER_AT@$HOST" \
  "cd $REMOTE_DIR && (python3 scripts/check_kalshi_auth.py 2>&1 | grep -E 'authenticated|balance|✗' | sed 's/^/  /')" \
  || echo "  (checker not present or failed — inspect manually)"

if [ -n "$SERVICE" ]; then
  say "restarting $SERVICE"
  ssh "${SSH_OPTS[@]}" "$USER_AT@$HOST" \
    "systemctl restart $SERVICE && sleep 8 && systemctl is-active $SERVICE"
fi

say "done"
cat <<EOF
Credentials on $HOST are now the same as your local .env.

Remember: run this EVERY time a credential rotates. The code deploy excludes
.env by design, so nothing else will carry the change across — and a stale key
on a server fails silently.
EOF
