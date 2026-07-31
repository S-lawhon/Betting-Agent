#!/usr/bin/env bash
# p022_settle_poll_droplet.sh — runs ON THE DROPLET. Poll a set of Kalshi
# markets until all are finalized, then write a settlement readout (market
# results + the P-022 SETTLE/FILL rows) to $OUT. Read-only apart from $OUT.
#
# WHY DROPLET-SIDE: a Mac-side watcher dies when the laptop sleeps. The
# droplet is always on, and the fills log lives there, so the timing-critical
# capture runs here; a session on the Mac picks $OUT up whenever it's awake:
#
#   ssh root@129.212.176.202 cat /root/p022_settle_readout.out
#
# Launch as a transient unit (survives the ssh session; --collect cleans up):
#
#   scp scripts/p022_settle_poll_droplet.sh root@129.212.176.202:/root/
#   ssh root@129.212.176.202 "systemd-run --unit p022-settle-poll --collect \
#     bash /root/p022_settle_poll_droplet.sh 2026-08-01T06:00:00Z \
#     KXPGAR1LEAD-ROC26-XSCH KXLPGAR2LEAD-AIGWO26-CELBOU"
#
#   $1   backstop deadline, UTC ISO (writes whatever it has at that point)
#   $2.. tickers to wait on (default: tickers of every open FILL today —
#        rows with a FILL but no SETTLE for the same fill_id)
#
# Settlement lore this leans on (CLAUDE.md): status "closed" is NOT settled —
# wait for "finalized"; result stays "" ~a day; sub-penny golf series null the
# integer-cent price fields, only *_dollars carries prices.
set -uo pipefail

API="${API:-https://api.elections.kalshi.com/trade-api/v2}"
FILLS_LOG="${FILLS_LOG:-/opt/betting-pod-shop/data/trade_logs/round_leader_fade_fills.jsonl}"
OUT="${OUT:-/root/p022_settle_readout.out}"

DEADLINE_ISO="${1:?usage: p022_settle_poll_droplet.sh <deadline-utc-iso> [ticker ...]}"
DEADLINE=$(date -u -d "$DEADLINE_ISO" '+%s')
shift || true

if [ "$#" -gt 0 ]; then
  TICKERS="$*"
else
  TICKERS=$(python3 - "$FILLS_LOG" <<'PY'
import json, sys
fills, settled = {}, set()
for line in open(sys.argv[1], errors="replace"):
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if r.get("type") == "FILL":
        fills[r.get("fill_id")] = r.get("ticker")
    elif r.get("type") == "SETTLE":
        settled.add(r.get("fill_id"))
open_tickers = sorted({t for f, t in fills.items() if t and f not in settled})
print(" ".join(open_tickers))
PY
  )
fi
[ -n "$TICKERS" ] || { echo "no open fills to wait on" > "$OUT"; exit 0; }
echo "waiting on: $TICKERS (deadline $DEADLINE_ISO)"

while [ "$(date '+%s')" -lt "$DEADLINE" ]; do
  ALL_DONE=1
  for T in $TICKERS; do
    S=$(curl -s --max-time 20 "$API/markets/$T" | python3 -c '
import json, sys
try: print(json.load(sys.stdin).get("market", {}).get("status", "?"))
except Exception: print("?")')
    [ "$S" = "finalized" ] || ALL_DONE=0
  done
  [ "$ALL_DONE" = "1" ] && break
  sleep 1800
done

{
  echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') — settlement readout (deadline hit if not all finalized) ==="
  for T in $TICKERS; do
    echo "== $T =="
    curl -s --max-time 20 "$API/markets/$T" | python3 -c '
import json, sys
m = json.load(sys.stdin).get("market", {})
for k in ("status", "result", "settlement_value_dollars", "last_price_dollars", "close_time"):
    print(f"  {k}: {m.get(k)}")'
  done
  echo "--- SETTLE + FILL rows (whole log) ---"
  grep -E '"type": "(SETTLE|FILL)"' "$FILLS_LOG"
  echo "=== COMPLETE ==="
} > "$OUT.tmp" 2>&1
mv "$OUT.tmp" "$OUT"
