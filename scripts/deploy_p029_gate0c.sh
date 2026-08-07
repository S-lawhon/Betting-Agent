#!/usr/bin/env bash
# Transfer and apply only the P-029 Gate 0c artifacts.
#
# Run from the repository root on a machine that can SSH to the P-029 VPS.
# No data directory or credential file is transferred.

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
HOST="${P029_HOST:-root@143.198.162.120}"
KEY="${P029_KEY:-$HOME/.ssh/betting_deploy}"
REMOTE_DIR="${P029_DIR:-/opt/p029}"
PYTHON="${PYTHON:-python3}"
EXPECTED_MODEL_SHA="01411d863de04075a38f02b40b7e0c4a7e21463a96b42d8194e8ccd8325956af"

FILES=(
  scripts/p029_gate0c_checkpoint.py
  scripts/upgrade_p029_gate0c.sh
  src/combo_copula.py
  combo_research/recalib/recalibrated_model.json
)

say() { printf '\n== %s ==\n' "$*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

cd "$ROOT"

say "local preflight"
[[ -f "$KEY" ]] || fail "SSH key not found: $KEY"
for path in "${FILES[@]}"; do
  [[ -f "$path" ]] || fail "required artifact is missing: $path"
done

actual_sha=$("$PYTHON" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' \
  combo_research/recalib/recalibrated_model.json)
[[ "$actual_sha" == "$EXPECTED_MODEL_SHA" ]] || \
  fail "frozen model hash mismatch: expected $EXPECTED_MODEL_SHA, got $actual_sha"

"$PYTHON" -m pytest -q \
  tests/test_p029_gate0c_checkpoint.py \
  tests/test_p029_gate0c_deploy_scripts.py
"$PYTHON" -m scripts.p029_gate0c_checkpoint --now 2026-08-22T23:59:59Z --json

say "transfer selected artifacts"
RSYNC_RSH="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes" \
  rsync -az --relative --no-perms --no-owner --no-group \
    "${FILES[@]}" "$HOST:$REMOTE_DIR/"

say "apply and verify on P-029 VPS"
ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes "$HOST" \
  "chown p029:p029 '$REMOTE_DIR' '$REMOTE_DIR/scripts' '$REMOTE_DIR/src' '$REMOTE_DIR/combo_research' '$REMOTE_DIR/combo_research/recalib' '$REMOTE_DIR/scripts/p029_gate0c_checkpoint.py' '$REMOTE_DIR/src/combo_copula.py' '$REMOTE_DIR/combo_research/recalib/recalibrated_model.json' && chmod 0755 '$REMOTE_DIR' '$REMOTE_DIR/scripts' '$REMOTE_DIR/src' '$REMOTE_DIR/combo_research' '$REMOTE_DIR/combo_research/recalib' && bash '$REMOTE_DIR/scripts/upgrade_p029_gate0c.sh'"

say "done"
echo "Gate 0c artifacts are installed and the P-029 forward tape is healthy."
