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
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

MIN_N_DECISION = 120
MIN_N_PROMOTE = 240
PROMOTE_Z = 2.0
HARD_KILL_Z = -2.0


def taker_fee(price: float, rate: float = 0.07) -> float:
    return math.ceil(rate * price * (1.0 - price) * 100.0) / 100.0


def load_settled(log_path: Path, pod_id: str = "P-015") -> List[Dict[str, Any]]:
    """Settled (WIN/LOSS) trades. VOIDs are excluded — no risk was taken."""
    if not log_path.exists():
        return []
    out = []
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if rec.get("pod_id") != pod_id:
            continue
        if (rec.get("outcome") or "").upper() in ("WIN", "LOSS"):
            out.append(rec)
    return out


def evaluate(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "verdict": "NO DECISION", "reason": "no settled trades yet"}

    wins = sum(1 for t in trades if (t.get("outcome") or "").upper() == "WIN")
    hit = wins / n
    breakevens = []
    for t in trades:
        price = float(t.get("fill_price") or t.get("venue_prob") or 0.9)
        breakevens.append(price + taker_fee(price))
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

    return {"n": n, "wins": wins, "hit": hit, "breakeven": be, "edge": edge,
            "z": z, "pnl": pnl, "verdict": verdict, "reason": reason}


def main() -> int:
    ap = argparse.ArgumentParser(description="P-015 pre-registered checkpoint")
    ap.add_argument("--log", default="data/pods/P-015.jsonl")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    trades = load_settled(Path(args.log))
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
