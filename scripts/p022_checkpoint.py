#!/usr/bin/env python3
"""
scripts/p022_checkpoint.py
──────────────────────────
Evaluate P-022 (Round-Leader Dead-Heat Fade) against its PRE-REGISTERED
decision rule.  **This is the only sanctioned way to read P-022's
results.**  A P&L chart, a dashboard tile, or a tail of the jsonl is not
a verdict.

    python3 -m scripts.p022_checkpoint
    python3 -m scripts.p022_checkpoint --json

The rule is fixed in advance in `golf_quirks_research/P022_DECISION_RULE.md`
precisely so it cannot be renegotiated once results are in — the failure
mode that cost us P-013 ($2,094).

Rule summary (see the doc for the derivation)
─────────────────────────────────────────────
  * **One tournament = one observation.**  Within a tournament, the
    contract-weighted mean net ¢/ct; across tournaments, equal weight.
    Rounds of the same golf event pool into ONE observation.
  * edge = mean(x_t), se = sd(x_t)/sqrt(T), z = edge/se.
  * any T  -> HARD KILL if z <= -2.0.
  * T < 14 -> NO DECISION (underpowered: 91% power at T=14 vs the
              measured +3.4c effect, only 71% at T=8).
  * T >= 14 -> KILL if edge <= 0; PASS if z >= 2.0; else CONTINUE, the
              single extension, to T = 40 at UNCHANGED parameters.
  * T >= 40 -> KILL if still z < 2.0.  The extension is spent.

PASS authorises more paper allocation within the caps and a written
promotion proposal.  It does NOT authorise live money.

STATUS: live.  The pod was reconciled against the rule and the runner
restarted 2026-07-26 22:36 UTC, which is when T started counting.  The
earlier "stub / pod does not exist" note was wrong even when written --
betting-round-leader-fade.service had been running since 2026-07-23, but
it could never quote (see rule §11b), so T was genuinely 0.

Historical note: this script is
expected to print "NO DECISION — no settled trades".  It reads the row
shape the pod will write; if the pod ships a different shape, fix the
LOADER here rather than the RULE.
"""

import argparse
import glob
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

POD_ID = "P-022"
MIN_T_DECISION = 14          # 90% power vs the measured +3.4c effect
MIN_T_EXTENSION = 40         # 80% power vs half that effect
PASS_Z = 2.0
HARD_KILL_Z = -2.0

DEFAULT_LOGS = [
    "data/pods/P-022.jsonl",
    "data/round_leader_fade/*.jsonl",
    "data/trade_logs/trade_log.jsonl",
    "data/trade_logs/archive/*.jsonl",
]

SETTLED = ("WIN", "LOSS")    # VOIDs excluded — no risk taken


# ── loading ──────────────────────────────────────────────────────────

def load_settled(patterns: List[str], pod_id: str = POD_ID) -> List[Dict[str, Any]]:
    """Settled P-022 rows, deduplicated on fingerprint.

    Deduplication matters: a rotated log can carry the same settlement
    twice and would double-weight that tournament.
    """
    out: Dict[str, Dict[str, Any]] = {}
    files: List[str] = []
    for pat in patterns:
        files.extend(sorted(glob.glob(pat)))
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as fh:
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
                    if (rec.get("outcome") or "").upper() not in SETTLED:
                        continue
                    key = (rec.get("fingerprint")
                           or f"{rec.get('market_id')}|{rec.get('settled_at_utc')}")
                    out[key] = rec
        except OSError:
            continue
    return list(out.values())


def tournament_key(rec: Dict[str, Any]) -> str:
    """One golf EVENT = one observation, pooling its rounds.

    KXPGAR1LEAD-MEMTOUR26 and KXPGAR2LEAD-MEMTOUR26 are two rounds of
    one event and share a leaderboard; they must not count twice.
    """
    extra = rec.get("extra") or {}
    for field in ("event_code", "event_ticker", "tournament"):
        v = extra.get(field) or rec.get(field)
        if v:
            return str(v).rsplit("-", 1)[-1] if "-" in str(v) else str(v)
    ticker = str(rec.get("market_id") or rec.get("market_ticker") or "")
    parts = ticker.split("-")
    return parts[1] if len(parts) > 1 else ticker or "UNKNOWN"


# ── the rule ─────────────────────────────────────────────────────────

def per_contract_cents(rec: Dict[str, Any]) -> float:
    """Net ¢/contract on a settled fade row.

    Prefers the pod's own recorded per-contract number; otherwise
    derives it from net P&L over contracts.  Both are net of the maker
    fee (zero on `quadratic` *LEAD series — the checkpoint asserts this
    rather than assuming it, see `--check-fees`).
    """
    extra = rec.get("extra") or {}
    for field in ("net_cents_per_contract", "pnl_cents_per_contract"):
        if extra.get(field) is not None:
            return float(extra[field])
    contracts = float(extra.get("contracts") or rec.get("contracts") or 0)
    if contracts <= 0:
        return 0.0
    return 100.0 * float(rec.get("pnl_usd") or 0) / contracts


def evaluate(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"T": 0, "progress": 0, "threshold": MIN_T_DECISION,
                "n_contracts": 0, "verdict": "NO DECISION",
                "reason": "no settled P-022 trades yet",
                "tournaments": {}}

    # within-tournament: contract-weighted mean ¢/ct
    buckets: Dict[str, List] = {}
    for rec in trades:
        extra = rec.get("extra") or {}
        cts = float(extra.get("contracts") or rec.get("contracts") or 0) or 1.0
        buckets.setdefault(tournament_key(rec), []).append(
            (per_contract_cents(rec), cts))

    per_t = {}
    for key, rows in buckets.items():
        w = sum(c for _, c in rows)
        per_t[key] = sum(v * c for v, c in rows) / w if w else 0.0

    xs = list(per_t.values())
    T = len(xs)
    n_contracts = int(sum(
        float((r.get("extra") or {}).get("contracts") or r.get("contracts") or 0)
        for r in trades))
    edge = statistics.mean(xs)
    sd = statistics.stdev(xs) if T > 1 else float("nan")
    se = sd / math.sqrt(T) if T > 1 and sd > 0 else float("nan")
    z = edge / se if se and not math.isnan(se) and se > 0 else 0.0
    pnl = sum(float(r.get("pnl_usd") or 0) for r in trades)
    positive = sum(1 for x in xs if x > 0)

    if not math.isnan(z) and z <= HARD_KILL_Z:
        verdict, reason = "HARD KILL", f"significantly negative (z={z:.2f})"
    elif T < MIN_T_DECISION:
        verdict = "NO DECISION"
        reason = (f"T={T} < {MIN_T_DECISION} tournaments; underpowered — "
                  f"do not act, do not raise caps "
                  f"({MIN_T_DECISION - T} more needed)")
    elif edge <= 0:
        verdict, reason = "KILL", f"edge {edge:+.2f}c/ct <= 0 at T={T}"
    elif z >= PASS_Z:
        verdict = "PASS"
        reason = (f"edge {edge:+.2f}c/ct at z={z:.2f} with T={T} — "
                  "forward test replicated. Authorises more PAPER "
                  "allocation within caps + a written promotion "
                  "proposal. NOT live money.")
    elif T >= MIN_T_EXTENSION:
        verdict = "KILL"
        reason = (f"edge {edge:+.2f}c/ct but z={z:.2f} < {PASS_Z} at "
                  f"T={T} >= {MIN_T_EXTENSION} — the single extension is "
                  "spent. No second extension.")
    else:
        verdict = "CONTINUE"
        reason = (f"edge {edge:+.2f}c/ct, z={z:.2f} — positive but not "
                  f"separable. Single extension to T={MIN_T_EXTENSION} at "
                  f"UNCHANGED parameters ({MIN_T_EXTENSION - T} more).")

    return {"T": T, "progress": T, "threshold": MIN_T_DECISION,
            "n_contracts": n_contracts, "edge_cents": edge,
            "sd_cents": sd, "se_cents": se, "z": z, "pnl_usd": pnl,
            "tournaments_positive": positive,
            "tournaments": per_t, "verdict": verdict, "reason": reason}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="P-022 pre-registered checkpoint (the only sanctioned "
                    "reader of P-022 results)")
    ap.add_argument("--log", action="append", default=[],
                    help="log path or glob (repeatable)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check-fees", action="store_true",
                    help="assert every *LEAD series is still maker-fee-free")
    args = ap.parse_args()

    if args.check_fees:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from src.kalshi_fees import fee_per_contract
        bad = [s for s in ("KXPGAR1LEAD", "KXPGAR2LEAD", "KXPGAR3LEAD",
                           "KXDPWORLDTOURR1LEAD", "KXLIVR1LEAD",
                           "KXLPGAR1LEAD", "KXCHAMPTOURR1LEAD")
               if fee_per_contract(0.08, maker=True, series_ticker=s) > 0]
        print("maker-fee check: " + ("OK — all *LEAD series free"
                                     if not bad else f"DRIFTED: {bad}"))
        if bad:
            return 3

    trades = load_settled(args.log or DEFAULT_LOGS)
    r = evaluate(trades)

    if args.json:
        print(json.dumps(r, indent=1, default=str))
        return 0

    print("P-022 Round-Leader Dead-Heat Fade — pre-registered checkpoint")
    print("=" * 62)
    if r["T"] == 0:
        print("  settled trades : 0   tournaments: 0")
        print(f"\n  VERDICT: {r['verdict']} — {r['reason']}")
        print("\n  (T counts from 2026-07-26 22:36 UTC, when the reconciled"
              "\n   runner restarted. Golf events accrue ~15-19/month.)")
        print("  Rule: golf_quirks_research/P022_DECISION_RULE.md")
        return 0

    print(f"  tournaments (T): {r['T']}   contracts: {r['n_contracts']:,}")
    print(f"  tournaments +ve: {r['tournaments_positive']}/{r['T']}")
    print(f"  edge           : {r['edge_cents']:+.2f} c/contract "
          f"(equal-weighted across tournaments)")
    print(f"  between-tourn sd: {r['sd_cents']:.2f} c   "
          f"se: {r['se_cents']:.2f} c   z = {r['z']:+.2f}")
    print(f"  paper P&L      : ${r['pnl_usd']:+,.2f}")
    print(f"\n  VERDICT: {r['verdict']}")
    print(f"  {r['reason']}")
    print(f"\n  Rule: no decision < {MIN_T_DECISION} tournaments; kill if "
          f"edge<=0; pass only at z>=2.0;")
    print(f"        ONE extension to T={MIN_T_EXTENSION} at unchanged "
          f"parameters; hard kill at z<=-2.0.")
    print("        Reference: golf_quirks_research/P022_DECISION_RULE.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
