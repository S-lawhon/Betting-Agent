#!/usr/bin/env bash
# p022_post_round_readout.sh — read-only. Where did P-022's fills end up?
#
# WHY THIS EXISTS: MARKOUT_HORIZONS caps at 3600s (round_leader_fade_maker.py),
# so the log's last markout is one hour after the fill. The post-round picture —
# did the faded name hold the lead or drop back — has to come from the market
# itself. This pulls today's FILL rows from the droplet, derives the distinct
# tickers, and reads each one's live state from the Kalshi public API.
#
#   bash scripts/p022_post_round_readout.sh                      # run now
#   bash scripts/p022_post_round_readout.sh 2026-07-30T23:45:00Z # sleep until then
#   DAY=2026-07-30 bash scripts/p022_post_round_readout.sh       # non-today date
#
# Run it FROM THE MAC (it ssh'es to the droplet; Cowork sandboxes cannot).
# Pass a wake time PAST the ESPN-resolved round end plus slack — the schedule
# errs EARLY by design (src/golf_schedule.py), so the round often runs later.
#
# GOTCHA baked in below: on sub-penny golf series the integer-cent price
# fields (yes_bid, last_price, ...) are NULL on the single-market GET; the
# real prices are the *_dollars STRING fields. result stays "" for ~a day
# after settlement; status "closed" is NOT settled (see kalshi_golf_settler).
set -uo pipefail

DROPLET="${DROPLET:-root@129.212.176.202}"
FILLS_LOG="${FILLS_LOG:-/opt/betting-pod-shop/data/trade_logs/round_leader_fade_fills.jsonl}"
API="${API:-https://api.elections.kalshi.com/trade-api/v2}"
DAY="${DAY:-$(date -u '+%Y-%m-%d')}"

if [ "${1:-}" != "" ]; then
  # BSD date (macOS) first, GNU date fallback.
  TARGET=$(date -j -u -f '%Y-%m-%dT%H:%M:%SZ' "$1" '+%s' 2>/dev/null \
           || date -u -d "$1" '+%s')
  echo "waiting until $1 ($(( TARGET - $(date '+%s') ))s)"
  while [ "$(date '+%s')" -lt "$TARGET" ]; do
    REMAIN=$(( TARGET - $(date '+%s') ))
    [ "$REMAIN" -gt 600 ] && sleep 600 || sleep "$REMAIN"
  done
fi

echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') — P-022 post-round readout for $DAY ==="
echo
echo "── fills + markouts ($DAY, from droplet) ──"
ROWS=$(ssh -o ConnectTimeout=20 "$DROPLET" "grep '$DAY' '$FILLS_LOG' 2>/dev/null" || true)
if [ -z "$ROWS" ]; then
  echo "no rows for $DAY in $FILLS_LOG"
  exit 0
fi
printf '%s\n' "$ROWS"

echo
echo "── final market state (Kalshi public, *_dollars fields) ──"
printf '%s\n' "$ROWS" | python3 -c '
import json, sys
seen = []
for line in sys.stdin:
    try:
        r = json.loads(line)
    except ValueError:
        continue
    t = r.get("ticker")
    if r.get("type") == "FILL" and t and t not in seen:
        seen.append(t)
print("\n".join(seen))
' | while read -r T; do
  [ -z "$T" ] && continue
  echo "== $T =="
  curl -s "$API/markets/$T" | python3 -c '
import json, sys
m = json.load(sys.stdin).get("market", {})
if not m:
    print("  (no market in response — bad ticker or API error)")
for k in ("status", "yes_bid_dollars", "yes_ask_dollars", "last_price_dollars",
          "result", "settlement_value_dollars", "close_time"):
    print(f"  {k}: {m.get(k)}")'
done

echo
echo "── done. Read-only; nothing was changed. ──"
