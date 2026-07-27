#!/usr/bin/env python3
"""
scripts/p015_checkpoint.py
──────────────────────────
Evaluate P-015 (Tennis Qualifier Favorite) against its PRE-REGISTERED
decision rule.  Reads the pod's settled trades and prints a verdict.

    python3 -m scripts.p015_checkpoint [--log data/pods/P-015.jsonl]

The rule is fixed in advance (see P015_DECISION_RULE.md) precisely so it
cannot be renegotiated once results are in — the failure mode that cost
us P-013.  This script is the only sanctioned way to read the result.

Rule summary
────────────
  * Each settled trade contributes: won (1/0) and its own breakeven
    (fill_price + fee).  Aggregate edge = mean(won) - mean(breakeven).
  * n < 120   → NO DECISION (underpowered; a 3-month look cannot reach
                significance even if the edge is entirely real).
  * n >= 120  → KILL if realized edge <= 0 (hit rate at or below the
                price paid).  Otherwise CONTINUE.
  * n >= 240  → PROMOTE to live only if z >= 2.0 AND edge > 0.
  * Any n     → HARD KILL if z <= -2.0 (significantly negative).
"""

import argparse
import glob
import gzip
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

POD_ID = "P-015"
MIN_N_DECISION = 120
MIN_N_PROMOTE = 240
PROMOTE_Z = 2.0
HARD_KILL_Z = -2.0


# Where P-015 results actually live.
#
# This script previously defaulted to `data/pods/P-015.jsonl` — a path that
# HAS NEVER EXISTED on the droplet. `load_settled` returned [] for the missing
# file, so the reader reported `n = 0, NO DECISION` while five genuinely
# settled trades (4W/1L, 2026-07-25/26) sat in `trade_log.jsonl`, and
# `manager/collect.py` faithfully relayed the zero because delegating to the
# sanctioned reader is the correct house pattern. Found 2026-07-28 by the gate
# throughput audit; `research/REPORT_Gate_Throughput_2026-07.md`.
#
# The locked rule (`tennis_research/P015_DECISION_RULE.md`) names THIS SCRIPT
# as the only sanctioned reader. It does not name a log path, and it does not
# define an observation by which file the row is in. Pointing the reader at the
# file the pod actually writes is therefore a defect fix, not a rule change:
# the test statistic, the thresholds and the VOID exclusion are untouched.
#
# Archives are globbed because rotation TRUNCATES the live file — a reader that
# opens only `trade_log.jsonl` under-reports by however much has rotated, which
# is the same shape as the incident that hid 177 open positions.
DEFAULT_LOGS = [
    "data/trade_logs/trade_log*.jsonl",
    "data/trade_logs/trade_log*.jsonl.gz",
    "data/trade_logs/archive/*.jsonl",
    "data/trade_logs/archive/*.jsonl.gz",
    # Retained so that anything which ever did write here is still seen.
    # Harmless while absent; duplicates cannot survive the fingerprint dedupe.
    "data/pods/P-015.jsonl",
]


def taker_fee(price: float, rate: float = 0.07) -> float:
    return math.ceil(rate * price * (1.0 - price) * 100.0) / 100.0


def _open_any(path: str):
    return (gzip.open(path, "rt", encoding="utf-8", errors="replace")
            if path.endswith(".gz")
            else open(path, "r", encoding="utf-8", errors="replace"))


def load_settled(patterns, pod_id: str = POD_ID) -> List[Dict[str, Any]]:
    """Settled (WIN/LOSS) trades. VOIDs are excluded — no risk was taken.

    Deduplicated on `fingerprint`: a rotated log can carry the same settlement
    twice and would otherwise inflate n against a pre-registered threshold.
    """
    if isinstance(patterns, (str, Path)):
        patterns = [str(patterns)]
    files: List[str] = []
    for pat in patterns:
        files.extend(sorted(glob.glob(str(pat))))

    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(set(files)):
        try:
            with _open_any(path) as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("pod_id") != pod_id:
                        continue
                    if (rec.get("outcome") or "").upper() not in ("WIN", "LOSS"):
                        continue
                    key = (rec.get("fingerprint")
                           or f"{rec.get('market_id')}|{rec.get('settled_at_utc')}")
                    out[key] = rec
        except OSError:
            continue
    return list(out.values())


def entry_price(rec: Dict[str, Any]):
    """The rule's `fill_price`, or None.

    Deliberately NOT defaulted. The previous implementation read
    `fill_price or venue_prob or 0.9`, so a row missing both prices was
    silently assigned a 0.9 breakeven — a fabricated number feeding a
    pre-registered statistic. A row we cannot price is reported, not invented.
    """
    for field in ("fill_price", "venue_prob"):
        v = rec.get(field)
        if v is not None:
            try:
                p = float(v)
            except (TypeError, ValueError):
                continue
            if 0.0 < p < 1.0:
                return p
    return None


def evaluate(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "progress": 0, "threshold": MIN_N_DECISION,
                "verdict": "NO DECISION", "unpriced": 0,
                "reason": "no settled trades yet"}

    priced, unpriced = [], 0
    for t in trades:
        if entry_price(t) is None:
            unpriced += 1
        else:
            priced.append(t)
    if not priced:
        return {"n": 0, "progress": 0, "threshold": MIN_N_DECISION,
                "verdict": "NO DECISION", "unpriced": unpriced,
                "reason": f"{unpriced} settled row(s) carry no usable fill "
                          "price; refusing to invent a breakeven"}
    trades = priced
    n = len(trades)

    wins = sum(1 for t in trades if (t.get("outcome") or "").upper() == "WIN")
    hit = wins / n
    breakevens = [entry_price(t) + taker_fee(entry_price(t)) for t in trades]
    be = sum(breakevens) / n
    edge = hit - be
    se = math.sqrt(max(hit * (1 - hit), 1e-9) / n)
    z = edge / se if se > 0 else 0.0
    pnl = sum(float(t.get("pnl_usd") or 0) for t in trades)

    if z <= HARD_KILL_Z:
        verdict = "HARD KILL"
        reason = f"significantly negative (z={z:.2f})"
    elif n < MIN_N_DECISION:
        verdict = "NO DECISION"
        reason = (f"n={n} < {MIN_N_DECISION}; underpowered — "
                  "do not act on this yet")
    elif edge <= 0:
        verdict = "KILL"
        reason = f"realized edge {edge*100:+.2f}pp <= 0 at n={n}"
    elif n >= MIN_N_PROMOTE and z >= PROMOTE_Z:
        verdict = "PROMOTE"
        reason = f"edge {edge*100:+.2f}pp at z={z:.2f} with n={n}"
    else:
        need = MIN_N_PROMOTE - n
        verdict = "CONTINUE"
        reason = (f"edge {edge*100:+.2f}pp (z={z:.2f}); "
                  f"{'need z>=2.0' if n >= MIN_N_PROMOTE else f'{need} more trades to promotion checkpoint'}")

    # `progress` / `threshold` are what manager.checks._gate_progress reads.
    # Emitting only `n` left the gate unreadable by the manager even after the
    # reader itself was fixed — the reader found the trades and the gate
    # machinery still saw None.
    return {"n": n, "progress": n, "threshold": MIN_N_DECISION,
            "wins": wins, "hit": hit, "breakeven": be, "edge": edge,
            "z": z, "pnl": pnl, "verdict": verdict, "reason": reason,
            "unpriced": unpriced}


def main() -> int:
    ap = argparse.ArgumentParser(description="P-015 pre-registered checkpoint")
    ap.add_argument("--log", action="append", default=[],
                    help="log path or glob (repeatable); overrides the defaults")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    trades = load_settled(args.log or DEFAULT_LOGS)
    r = evaluate(trades)

    if args.json:
        print(json.dumps(r, indent=1))
        return 0

    print("P-015 Tennis Qualifier Favorite — pre-registered checkpoint")
    print("=" * 58)
    if r["n"] == 0:
        print(f"  settled trades: 0\n  VERDICT: {r['verdict']} — {r['reason']}")
        return 0
    print(f"  settled trades : {r['n']}  (wins {r['wins']})")
    if r.get("unpriced"):
        print(f"  UNPRICED       : {r['unpriced']} settled row(s) excluded — "
              "no usable fill price, and a breakeven is never invented")
    print(f"  realized hit   : {r['hit']*100:.1f}%")
    print(f"  breakeven hit  : {r['breakeven']*100:.1f}%  (mean fill + fee)")
    print(f"  realized edge  : {r['edge']*100:+.2f}pp   z = {r['z']:+.2f}")
    print(f"  paper P&L      : ${r['pnl']:+,.2f}")
    print(f"\n  VERDICT: {r['verdict']}")
    print(f"  {r['reason']}")
    print("\n  Rule: no decision <120 trades; kill if edge<=0 at >=120;")
    print("        promote only at >=240 trades AND z>=2.0.")
    print("        Reference: tennis_research/P015_DECISION_RULE.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
