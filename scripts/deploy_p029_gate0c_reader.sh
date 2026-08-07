#!/usr/bin/env bash
# Update only the sanctioned Gate 0c reader on the P-029 host.
# Deliberately does not touch or restart p029-shadow.service.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
HOST="${P029_HOST:-root@143.198.162.120}"
KEY="${P029_KEY:-$HOME/.ssh/betting_deploy}"
REMOTE_DIR="${P029_DIR:-/opt/p029}"
PYTHON="${PYTHON:-python3}"
READER="scripts/p029_gate0c_checkpoint.py"

cd "$ROOT"
[[ -f "$KEY" ]] || { echo "ERROR: SSH key not found: $KEY" >&2; exit 1; }
[[ -f "$READER" ]] || { echo "ERROR: reader missing: $READER" >&2; exit 1; }

"$PYTHON" -m pytest -q tests/test_p029_gate0c_checkpoint.py
"$PYTHON" -m scripts.p029_gate0c_checkpoint \
  --now 2026-08-22T23:59:59Z --json >/dev/null

RSYNC_RSH="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes" \
  rsync -az --relative --no-perms --no-owner --no-group \
    "$READER" "$HOST:$REMOTE_DIR/"
ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes "$HOST" \
  "chown p029:p029 '$REMOTE_DIR/$READER' && chmod 0755 '$REMOTE_DIR/$READER' && cd '$REMOTE_DIR' && '$REMOTE_DIR/venv/bin/python3' '$REMOTE_DIR/$READER' --now 2026-08-22T23:59:59Z --json"

echo "Gate 0c reader updated without restarting the shadow logger."
