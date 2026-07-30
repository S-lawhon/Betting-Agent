#!/usr/bin/env bash
# p022_droplet_state_check.sh — read-only. What is P-022 actually running right now?
#
# WHY THIS EXISTS: Cowork cannot SSH (port 22 blocked outbound from the sandbox),
# so the droplet's running revision cannot be verified from a research session.
# Run this on the droplet itself, either over ssh from the Mac or pasted into the
# DigitalOcean web console. It changes nothing.
#
#   bash scripts/p022_droplet_state_check.sh
#
# Answers, in order:
#   1. which commit the running code is at, and whether 297ce2b (the size screen)
#      is in it
#   2. the EFFECTIVE min_top_size after config_multi_pod.yaml merges over config.yaml
#   3. today's QUOTE / REFUSED / FILL counts, and the size behind every refusal
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/betting-pod-shop}"
UNIT="${UNIT:-betting-round-leader-fade}"

echo "=============================================================="
echo " P-022 droplet state — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "=============================================================="

echo
echo "── 1. running code revision ──"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" log --oneline -1 | cat
  echo -n "size screen (297ce2b) present in this tree: "
  if git -C "$APP_DIR" merge-base --is-ancestor 297ce2b HEAD 2>/dev/null; then
    echo "YES"
  else
    echo "no"
  fi
else
  echo "no .git at $APP_DIR (deploy.sh rsyncs, so this is expected)"
  echo -n "min_top_size referenced in the deployed source: "
  grep -c "min_top_size" "$APP_DIR/src/round_leader_fade_maker.py" 2>/dev/null || echo "0 (pre-screen code)"
fi

echo
echo "── 2. effective config (after the multi-pod merge) ──"
cd "$APP_DIR" 2>/dev/null || { echo "cannot cd $APP_DIR"; exit 1; }
python3 - <<'PY'
try:
    from src.config_loader import load_config
    cfg = load_config()
except Exception as e:
    print(f"  load_config failed: {e}")
    raise SystemExit(0)
q = ((cfg.get("pods") or {}).get("P-022") or {}).get("quoting") or {}
r = ((cfg.get("pods") or {}).get("P-022") or {}).get("risk") or {}
mts = q.get("min_top_size", "<absent — code default 100.0 applies>")
print(f"  min_top_size      : {mts}")
print(f"  mid_band          : {q.get('mid_band')}")
print(f"  quote_offset      : {q.get('quote_offset')}")
print(f"  window [no_new, start] h: [{q.get('no_new_quote_h')}, {q.get('fade_start_h')}]")
print(f"  caps  name/event/total  : {r.get('pct_per_name')} / {r.get('pct_per_tournament')} / {r.get('pct_total')}")
print()
if mts in (0, 0.0):
    print("  => screen DISABLED. Registered population is being quoted.")
elif isinstance(mts, (int, float)):
    print(f"  => screen ACTIVE at {mts}. This is the §8.1 question in")
    print("     research/REPORT_P022_Size_Screen_Section8_2026-07-30.md — resolve before the window.")
else:
    print("  => key absent, so the code default 100.0 is ACTIVE. Same §8.1 question.")
PY

echo
echo "── 3. service ──"
systemctl is-active "$UNIT" 2>/dev/null || echo "  (unit $UNIT not active)"
systemctl show "$UNIT" -p ActiveEnterTimestamp --value 2>/dev/null | sed 's/^/  up since: /'

echo
echo "── 4. today's quote / refusal / fill counts ──"
LOGDIR="${LOGDIR:-$APP_DIR/data}"
TODAY="$(date -u '+%Y-%m-%d')"
python3 - "$LOGDIR" "$TODAY" <<'PY'
import json, os, sys, glob
from collections import Counter
logdir, today = sys.argv[1], sys.argv[2]
paths = [p for p in glob.glob(os.path.join(logdir, "**", "*p022*"), recursive=True)
         if p.endswith((".jsonl", ".log"))]
if not paths:
    print(f"  no P-022 logs under {logdir}")
    raise SystemExit(0)
kinds, refusals, quoted_sides = Counter(), [], Counter()
for p in paths:
    try:
        fh = open(p, errors="replace")
    except OSError:
        continue
    with fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if today not in str(r.get("ts", "")) + str(r.get("timestamp", "")):
                continue
            t = r.get("type")
            kinds[t] += 1
            if t == "REFUSED" and r.get("reason") == "thin_book":
                refusals.append(r)
            if t == "QUOTE":
                quoted_sides[r.get("book_side", "?")] += 1
for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
    print(f"  {k or '(none)':22} {v}")
if quoted_sides:
    print("  quotes by reference side:", dict(quoted_sides))
if refusals:
    print(f"\n  {len(refusals)} thin-book refusal(s) today. Size behind each:")
    for r in refusals[:40]:
        print(f"    {str(r.get('ticker'))[:34]:34} side={str(r.get('book_side'))[:14]:14} "
              f"bid_qty={r.get('bid_qty')} ask_qty={r.get('ask_qty')} "
              f"ask={r.get('yes_ask')}")
    print("\n  NOTE: ask_qty is TOP-OF-BOOK only. The fill-relevant quantity is the")
    print("  total size at every price strictly below the quote. On the AIGWO26")
    print("  census those differ by ~12x on exactly these books.")
PY

echo
echo "── done. Read-only; nothing was changed. ──"
