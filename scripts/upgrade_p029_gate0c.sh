#!/usr/bin/env bash
# Apply the P-029 Gate 0c operational prerequisites on the P-029 VPS.
#
# Run as root after the four Gate 0c code/model artifacts have been copied to
# /opt/p029. This script never fetches git, rewrites the main unit, reads
# credentials, or modifies /var/lib/p029 data.

set -euo pipefail

APP="${APP:-/opt/p029}"
DATA="${DATA:-/var/lib/p029}"
UNIT="p029-shadow.service"
ARCHIVE_TIMER="p029-archive.timer"
EXPECTED_MODEL_SHA="01411d863de04075a38f02b40b7e0c4a7e21463a96b42d8194e8ccd8325956af"
EXPECTED_MEMORY_BYTES="805306368"
BACKLOG_WAIT_S="${BACKLOG_WAIT_S:-900}"
BACKLOG_POLL_S="${BACKLOG_POLL_S:-30}"

say() { printf '\n== %s ==\n' "$*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

if [[ "${EUID}" -ne 0 ]]; then
  fail "run this script as root on the P-029 VPS"
fi

PY="$APP/venv/bin/python3"
READER="$APP/scripts/p029_gate0c_checkpoint.py"
MODEL="$APP/combo_research/recalib/recalibrated_model.json"
DB="$DATA/shadow.sqlite"
ARCHIVE="$DATA/archive"

say "preflight"
for path in "$PY" "$READER" "$MODEL" "$APP/src/combo_copula.py" "$DB" "$ARCHIVE"; do
  [[ -e "$path" ]] || fail "required path is missing: $path"
done
systemctl cat "$UNIT" >/dev/null || fail "$UNIT is not installed"
systemctl cat "$ARCHIVE_TIMER" >/dev/null || fail "$ARCHIVE_TIMER is not installed"

actual_sha=$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$MODEL")
[[ "$actual_sha" == "$EXPECTED_MODEL_SHA" ]] || \
  fail "frozen model hash mismatch: expected $EXPECTED_MODEL_SHA, got $actual_sha"
(cd "$APP" && "$PY" -c 'import numpy; from src.combo_copula import ComboCopula') \
  2>/dev/null || fail "the P-029 virtualenv cannot import numpy/src.combo_copula"

blind_json=$(cd "$APP" && "$PY" "$READER" --now 2026-08-22T23:59:59Z --json)
"$PY" -c 'import json,sys; r=json.loads(sys.argv[1]); assert r["verdict"] == "NO DECISION" and r["progress"] is None' \
  "$blind_json" || fail "Gate 0c reader did not remain blind before the registered read date"
echo "frozen reader/model verified; pre-read verdict is NO DECISION"

say "install 768M systemd override"
dropin_dir="/etc/systemd/system/${UNIT}.d"
mkdir -p "$dropin_dir"
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<'EOF'
[Service]
MemoryMax=768M
EOF
install -m 0644 "$tmp" "$dropin_dir/20-gate0c-memory.conf"
rm -f "$tmp"
trap - EXIT

SHADOW_LOG="$DATA/shadow.log"
[[ -f "$SHADOW_LOG" ]] || fail "shadow log is missing: $SHADOW_LOG"
log_lines_before=$(wc -l <"$SHADOW_LOG")
systemctl daemon-reload
systemctl restart "$UNIT"

deadline=$((SECONDS + 90))
until [[ "$(systemctl show "$UNIT" --property=ActiveState --value)" == "active" \
      && "$(systemctl show "$UNIT" --property=SubState --value)" == "running" \
      && "$(systemctl show "$UNIT" --property=MainPID --value)" != "0" ]]; do
  (( SECONDS < deadline )) || fail "$UNIT did not become active within 90 seconds"
  sleep 3
done

# Restart=always can produce a momentary "active" result between crashes. A
# stable PID across this dwell proves the process is running, not crash-looping.
steady_pid=$(systemctl show "$UNIT" --property=MainPID --value)
steady_restarts=$(systemctl show "$UNIT" --property=NRestarts --value)
sleep 10
[[ "$(systemctl show "$UNIT" --property=ActiveState --value)" == "active" \
   && "$(systemctl show "$UNIT" --property=SubState --value)" == "running" \
   && "$(systemctl show "$UNIT" --property=MainPID --value)" == "$steady_pid" \
   && "$(systemctl show "$UNIT" --property=NRestarts --value)" == "$steady_restarts" ]] || \
  fail "$UNIT did not remain steadily running after restart"

effective_memory=$(systemctl show "$UNIT" --property=MemoryMax --value)
[[ "$effective_memory" == "$EXPECTED_MEMORY_BYTES" ]] || \
  fail "effective MemoryMax is $effective_memory bytes, expected $EXPECTED_MEMORY_BYTES"
echo "$UNIT is active with effective MemoryMax=768M"

say "verify forward-tape health"
systemctl is-active --quiet "$ARCHIVE_TIMER" || fail "$ARCHIVE_TIMER is not active"

health_json=$("$PY" - "$DB" "$ARCHIVE" <<'PY'
import json
import sqlite3
import sys
import time
from pathlib import Path

db = Path(sys.argv[1])
archive = Path(sys.argv[2])
files = [p for p in archive.rglob("*.jsonl.gz") if p.is_file()]
newest = max(files, key=lambda p: p.stat().st_mtime) if files else None
with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30) as con:
    row_count = con.execute("SELECT COUNT(*) FROM combo").fetchone()[0]
print(json.dumps({
    "db_age_s": time.time() - db.stat().st_mtime,
    "row_count": row_count,
    "archive_newest": newest.name if newest else None,
    "archive_age_s": time.time() - newest.stat().st_mtime if newest else None,
}))
PY
)
"$PY" -c '
import json,sys
r=json.loads(sys.argv[1])
assert r["db_age_s"] <= 600, "shadow database is over 10 minutes stale"
assert r["row_count"] > 0, "shadow database has no combo rows"
assert r["archive_age_s"] is not None, "settlement archive is empty"
assert r["archive_age_s"] <= 30*3600, "newest settlement archive is over 30 hours old"
' "$health_json" || fail "forward-tape freshness check failed: $health_json"
echo "$health_json"

# The queue is continuously receiving rows as they cross 40 hours old, so the
# live SQL count is normally nonzero between resolver cycles. The logger's
# contract is that a cycle DRAINS the priority queue: require a post-restart
# cycle whose own terminal measurement says `due 0 in-zone`.
deadline=$((SECONDS + BACKLOG_WAIT_S))
while true; do
  latest_resolve=$(tail -n "+$((log_lines_before + 1))" "$SHADOW_LOG" \
    | grep 'resolve: checked' | tail -1 || true)
  if [[ "$latest_resolve" == *"due 0 in-zone"* ]]; then
    break
  fi
  (( SECONDS < deadline )) || \
    fail "no post-restart resolver cycle drained the in-zone backlog within ${BACKLOG_WAIT_S}s"
  echo "waiting for a post-restart resolver cycle to report due 0 in-zone"
  sleep "$BACKLOG_POLL_S"
done
echo "in-zone due backlog: 0 at the latest resolver cycle"

say "result"
systemctl --no-pager --full status "$UNIT" | sed -n '1,12p'
systemctl list-timers "$ARCHIVE_TIMER" --no-pager | sed -n '1,4p'
echo "P-029 Gate 0c host prerequisites PASS"
