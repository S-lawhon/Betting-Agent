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
  * T < 24 -> NO DECISION (underpowered: 91% power at T=24 vs the
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

LOADER FIX, 2026-07-27
──────────────────────
The claim that once stood here — "it reads the row shape the pod will
write" — was false.  This script was written before the pod existed and
was never reconciled against it, so the sanctioned reader of P-022's
results **could not see P-022's results**:

  * it read `data/pods/P-022.jsonl` and three other paths; the pod writes
    `data/trade_logs/round_leader_fade_fills.jsonl`, which matched none of
    them (`data/pods/` does not exist on the droplet at all), and
  * it kept only rows whose `outcome` is WIN/LOSS and sized them by
    `contracts`; the pod's SETTLE row has neither field — it writes
    `result`, `payout`, `pnl_usd` and `qty`.

Pointed directly at a file holding two genuinely settled P-022 rows it
reported `0 tournaments`.  Had the close-time defect not existed, the pod
would have quoted, filled and settled correctly and the registry's
*derived* gate progress would still have read 0 — a number derived from a
reader that cannot see the data is not better than a hand-typed one.
(Same family as the tennis and golf settlers, which both read `data/pods/`
and both found it empty.)

Fixed by normalising the pod's row into the canonical shape at load time,
so `tournament_key` and `per_contract_cents` — which implement §2 and §3 of
the rule — are untouched.  **The RULE is unchanged; only the LOADER moved**,
which is exactly what the note it replaces prescribed.
See research/REPORT_P022_First_Quote_2026-07.md §3.
"""

import argparse
import glob
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

POD_ID = "P-022"
# AMENDMENT 1 (2026-07-28, authorised by Sam, made at T = 0): 14 -> 24.
# The rule's criterion is unchanged — "the smallest sample with 90% power
# against the effect actually measured". The 2026-07-28 widening revised that
# effect from +3.4c to +2.57c/ct, and re-solving the SAME formula with the SAME
# sigma=3.781 gives ((2.0+1.2816)*3.781/2.57)^2 = 23.3 -> 24. Section 5's own
# table already carried the row. At T=14 the power against +2.57c is 71%.
MIN_T_DECISION = 24          # 90% power vs the widened +2.57c effect
# NOT amended, and now INCONSISTENT with its own stated rationale ("80% power
# vs half the measured effect"): half of +2.57c is +1.29c, which solves to
# T ~ 70, and at T=40 the power against half the revised effect is ~53%.
# Raising it is a separate decision Sam has not made.
MIN_T_EXTENSION = 40         # rationale drifted — see golf_quirks_research/P022_DECISION_RULE.md
PASS_Z = 2.0
HARD_KILL_Z = -2.0

DEFAULT_LOGS = [
    # What the pod ACTUALLY writes (src/round_leader_fade_maker.py:173).
    # The glob catches any rotated sibling; duplicates are deduped below.
    "data/trade_logs/round_leader_fade_fills*.jsonl",
    "data/trade_logs/archive/round_leader_fade_fills*.jsonl",
    # Kept: a settler-written P-022 row would land in the shared trade log.
    "data/trade_logs/trade_log.jsonl",
    "data/trade_logs/archive/*.jsonl",
    # Kept for completeness; nothing writes these today.
    "data/pods/P-022.jsonl",
    "data/round_leader_fade/*.jsonl",
]

SETTLED = ("WIN", "LOSS")    # VOIDs excluded — no risk taken


# ── loading ──────────────────────────────────────────────────────────

def normalise_fade_settle(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert one engine SETTLE row into the canonical record shape.

    The pod writes, per FILL (src/round_leader_fade_maker.py:534):

        {"type": "SETTLE", "fill_id": ..., "pod_id": "P-022",
         "ticker": ..., "event": "ROC26", "result": "yes|no|scalar",
         "payout": 0.5, "fill_price": 0.06, "qty": 5,
         "maker_fee_per_contract": 0.0, "pnl_usd": -2.2}

    Returns None for QUOTE / PULL / FILL / MARKOUT rows.

    `pnl_usd` is already NET of the maker fee, which is what §3 requires,
    so `per_contract_cents` needs no fee handling.

    **Every SETTLE row counts, including `result="scalar"`.** Rule §3 and
    §8.3: scalar is a partial payout at its realised value, not a void, and
    excluding it (or booking it at $0) deletes the entire mechanism under
    test.  The engine never emits a void — it only settles on a populated
    yes/no/scalar result — so there is nothing here to exclude.

    `outcome` is filled in for display, but is deliberately NOT what makes
    the row countable (`_fade_settled` is).  A settlement worth exactly $0
    is a real observation of taken risk; routing it through a WIN/LOSS sign
    test would silently drop it.  It cannot arise under the locked
    parameters — fills are <= ~0.14 and every non-zero payout exceeds that
    — but the reader should not depend on that staying true.
    """
    if rec.get("type") != "SETTLE":
        return None
    ticker = rec.get("ticker") or ""
    try:
        contracts = float(rec.get("qty") or 0)
        pnl = float(rec.get("pnl_usd") or 0)
    except (TypeError, ValueError):
        return None
    if contracts <= 0:
        return None
    return {
        "pod_id": rec.get("pod_id"),
        "market_id": ticker,
        "contracts": contracts,
        "pnl_usd": pnl,
        "outcome": "WIN" if pnl > 0 else "LOSS",
        "_fade_settled": True,
        # `event` is the event CODE ("ROC26"), which already pools R1/R2/R3
        # of one golf event into one observation as §2 requires.
        "extra": {"event_code": rec.get("event") or None,
                  "contracts": contracts},
        # fill_id is unique per fill; pair it with the ticker so a rotated
        # or double-globbed log cannot double-weight a tournament.
        "fingerprint": f"{rec.get('fill_id')}|{ticker}",
        "result": rec.get("result"),
        "payout": rec.get("payout"),
        "fill_price": rec.get("fill_price"),
        "settled_at_utc": rec.get("iso"),
    }


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
                    # The engine's own log shape comes through the
                    # normaliser; anything else must carry a settled
                    # outcome the way trade_store writes it.
                    if rec.get("type") is not None:
                        rec = normalise_fade_settle(rec)
                        if rec is None:
                            continue
                    elif (rec.get("outcome") or "").upper() not in SETTLED:
                        continue
                    key = (rec.get("fingerprint")
                           or f"{rec.get('market_id')}|{rec.get('settled_at_utc')}")
                    out[key] = rec
        except OSError:
            continue
    return list(out.values())


def load_breached_events(patterns: List[str],
                         pod_id: str = POD_ID) -> Dict[str, List[str]]:
    """Tournaments with a recorded §7 cap breach -> the caps they breached.

    §7: *"A tournament run with any cap breached is EXCLUDED from T."* Until
    2026-07-28 this checkpoint had no breach concept at all — no exclusion, no
    field read, nothing — so the clause was unenforceable, and a gate
    condition that can only be checked retrospectively is not a gate
    condition. The engine now writes a `CAP_BREACH` row into the fills log at
    the moment it observes one, and this reads them back.
    """
    out: Dict[str, List[str]] = {}
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
                    if (rec.get("type") != "CAP_BREACH"
                            or rec.get("pod_id") != pod_id):
                        continue
                    ev = str(rec.get("event") or "").strip()
                    if not ev:
                        continue
                    kind = str(rec.get("kind") or "unknown")
                    if kind not in out.setdefault(ev, []):
                        out[ev].append(kind)
        except OSError:
            continue
    return out


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


def evaluate(trades: List[Dict[str, Any]],
             breached: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    breached = breached or {}
    if not trades:
        return {"T": 0, "progress": 0, "threshold": MIN_T_DECISION,
                "n_contracts": 0, "verdict": "NO DECISION",
                "reason": "no settled P-022 trades yet",
                "tournaments": {}, "excluded": {}}

    # within-tournament: contract-weighted mean ¢/ct
    buckets: Dict[str, List] = {}
    excluded: Dict[str, List[str]] = {}
    for rec in trades:
        key = tournament_key(rec)
        # §7 exclusion, applied BEFORE any statistic is computed. A tournament
        # run with a breached cap is not a valid observation of the strategy
        # the rule governs, so it cannot contribute to T or to the edge.
        if key in breached:
            excluded[key] = breached[key]
            continue
        extra = rec.get("extra") or {}
        cts = float(extra.get("contracts") or rec.get("contracts") or 0) or 1.0
        buckets.setdefault(key, []).append((per_contract_cents(rec), cts))

    if not buckets:
        return {"T": 0, "progress": 0, "threshold": MIN_T_DECISION,
                "n_contracts": 0, "verdict": "NO DECISION",
                "reason": (f"every settled tournament ({len(excluded)}) is "
                           "excluded under §7 for a cap breach"),
                "tournaments": {}, "excluded": excluded}
    trades = [r for r in trades if tournament_key(r) not in breached]

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
            "tournaments": per_t, "verdict": verdict, "reason": reason,
            "excluded": excluded}


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

    logs = args.log or DEFAULT_LOGS
    trades = load_settled(logs)
    r = evaluate(trades, load_breached_events(logs))

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

    if r.get("excluded"):
        print(f"  EXCLUDED (§7)  : {len(r['excluded'])} tournament(s) with a "
              "breached cap")
        for ev, kinds in sorted(r["excluded"].items()):
            print(f"                   {ev}: {', '.join(kinds)}")
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
