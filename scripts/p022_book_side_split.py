#!/usr/bin/env python3
"""
scripts/p022_book_side_split.py
───────────────────────────────
The sanctioned reader for
``golf_quirks_research/P022_ONESIDED_PREREGISTRATION_2026-07-29.md``.

P-022's validated edge was never once measured off a one-sided ask (0 of 364
backtest anchors), and the live population is ~96% one-sided. This reads the
pod's own logs and answers, in the terms fixed BEFORE the first quote rested:

  * the EARLY diagnostic — live fill rate on one-sided-referenced quotes,
    against the backtest's 15% (bare one-sided) / 47-67% (traded) cells;
  * at T, the all-in net c/ct with a tournament-clustered CI, tested against
    the two pre-registered hypotheses (+3.41c validated, -1.48c one-sided).

**This is NOT the gate.** `scripts/p022_checkpoint.py` remains the only
sanctioned verdict (§10 of the decision rule). This qualifies what that
verdict measured; it cannot promote or rescue the pod.

Returns None rather than a number whenever it cannot measure. A failure to
run is not a pass — §7.5 of the registration.

    python3 -m scripts.p022_book_side_split
    python3 -m scripts.p022_book_side_split --json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
QUOTES = ROOT / "data/trade_logs/round_leader_fade_quotes.jsonl"
FILLS = ROOT / "data/trade_logs/round_leader_fade_fills.jsonl"

# Pre-registered in §4. Do not edit these to match a result.
H_VALIDATED_C = 3.41       # Phase-2 headline, H=12 offset +0.02, 364 markets
H_ONESIDED_C = -1.48       # bare-one-sided-at-T cell, 117 markets
# Pre-registered in §5.
EARLY_TOURNAMENTS = 5
FILL_RATE_OK = 0.40
FILL_RATE_ALARM = 0.25
BACKTEST_FILL_RATES = {"traded_two_sided": 0.67, "traded_one_sided_ask": 0.47,
                       "bare_one_sided_ask": 0.15}


def _read(path: Path) -> Optional[List[Dict[str, Any]]]:
    if not path.exists():
        return None
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None
    return out


def attribute(quotes: List[Dict[str, Any]], fills: List[Dict[str, Any]]
              ) -> Tuple[Dict[str, str], Dict[str, List[Dict[str, Any]]], int]:
    """§3: a FILL inherits the `book_side` of the last QUOTE for the same
    ticker with ts <= fill.ts. Exact — a fill can only hit the resting quote.

    Returns (ticker -> latest book_side seen, book_side -> fills, unattributed).
    """
    by_ticker: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for q in quotes:
        if q.get("type") != "QUOTE":
            continue
        tk, ts = q.get("ticker"), q.get("ts")
        if tk and ts is not None:
            by_ticker[tk].append(q)
    for tk in by_ticker:
        by_ticker[tk].sort(key=lambda r: r["ts"])

    strata: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    unattributed = 0
    for f in fills:
        if f.get("type") != "FILL":
            continue
        tk, ts = f.get("ticker"), f.get("ts")
        cands = [q for q in by_ticker.get(tk, [])
                 if ts is not None and q["ts"] <= ts]
        if not cands:
            unattributed += 1          # never silently assigned to a stratum
            continue
        side = cands[-1].get("book_side")
        if not side:
            # A quote written before the 2026-07-28 observability deploy.
            unattributed += 1
            continue
        strata[side].append(f)

    latest_side = {tk: (qs[-1].get("book_side") or "unknown")
                   for tk, qs in by_ticker.items() if qs}
    return latest_side, strata, unattributed


def cluster_ci(per: List[Tuple[str, float, float]], n_boot: int = 5000,
               seed: int = 4242) -> Dict[str, Any]:
    """Contract-weighted within tournament, equal weight across — the gate's
    own statistic (§3 of the decision rule), resampling TOURNAMENTS."""
    if not per:
        return {"n": 0, "tournaments": 0, "mean_c": None, "lo_c": None,
                "hi_c": None}
    num: Dict[str, float] = defaultdict(float)
    den: Dict[str, float] = defaultdict(float)
    for ev, pnl, qty in per:
        num[ev] += pnl * qty
        den[ev] += qty
    evs = [e for e in den if den[e] > 0]
    if not evs:
        return {"n": len(per), "tournaments": 0, "mean_c": None,
                "lo_c": None, "hi_c": None}
    per_ev = {e: num[e] / den[e] for e in evs}
    mean = sum(per_ev.values()) / len(evs)
    if len(evs) < 2:
        return {"n": len(per), "tournaments": len(evs),
                "mean_c": round(mean * 100, 2), "lo_c": None, "hi_c": None}
    rng = random.Random(seed)
    draws = []
    for _ in range(n_boot):
        s = [per_ev[evs[rng.randrange(len(evs))]] for _ in evs]
        draws.append(sum(s) / len(s))
    draws.sort()
    return {"n": len(per), "tournaments": len(evs),
            "mean_c": round(mean * 100, 2),
            "lo_c": round(draws[int(0.025 * len(draws))] * 100, 2),
            "hi_c": round(draws[int(0.975 * len(draws))] * 100, 2)}


def settled_pnl(fills: List[Dict[str, Any]],
                settles: List[Dict[str, Any]]) -> List[Tuple[str, float, float]]:
    """(tournament, pnl/contract, contracts) for SETTLED fills only."""
    by_id = {s.get("fill_id"): s for s in settles if s.get("type") == "SETTLE"}
    out = []
    for f in fills:
        s = by_id.get(f.get("fill_id"))
        if not s:
            continue
        qty = float(s.get("qty") or f.get("qty") or 0)
        if qty <= 0:
            continue
        pnl = s.get("pnl_usd")
        if pnl is None:
            continue
        out.append((s.get("event") or f.get("event") or "?",
                    float(pnl) / qty, qty))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    quotes, fills_all = _read(QUOTES), _read(FILLS)
    if quotes is None or fills_all is None:
        msg = ("cannot measure: missing "
               + ("quotes log " if quotes is None else "")
               + ("fills log" if fills_all is None else ""))
        res = {"available": False, "error": msg, "verdict": None}
        print(json.dumps(res, indent=1) if a.json else f"P-022 split: {msg}")
        return 0                      # None, not a pass

    fills = [f for f in fills_all if f.get("type") == "FILL"]
    settles = [f for f in fills_all if f.get("type") == "SETTLE"]
    latest_side, strata, unattributed = attribute(quotes, fills)

    # posted / filled per §5, same definition as quirks_common.replay
    posted_by_side: Dict[str, set] = defaultdict(set)
    for tk, side in latest_side.items():
        posted_by_side[side].add(tk)
    filled_tickers = {f.get("ticker") for f in fills}
    tournaments = {q.get("event") for q in quotes
                   if q.get("type") == "QUOTE" and q.get("event")}

    res: Dict[str, Any] = {
        "available": True,
        "quoted_tournaments": len(tournaments),
        "unattributed_fills": unattributed,
        "by_book_side": {},
        "early_diagnostic": None,
        "hypothesis_test": None,
    }
    for side in sorted(posted_by_side):
        posted = posted_by_side[side]
        filled = posted & filled_tickers
        sub = strata.get(side, [])
        res["by_book_side"][side] = {
            "posted_markets": len(posted),
            "filled_markets": len(filled),
            "fill_rate": (round(len(filled) / len(posted), 4)
                          if posted else None),
            "fills": len(sub),
            "settled": cluster_ci(settled_pnl(sub, settles)),
        }

    # ── §5 early diagnostic ────────────────────────────────────────────
    one = res["by_book_side"].get("one_sided_ask")
    if one and one["fill_rate"] is not None:
        fr = one["fill_rate"]
        if len(tournaments) < EARLY_TOURNAMENTS:
            state = (f"NOT YET — {len(tournaments)} of {EARLY_TOURNAMENTS} "
                     f"tournaments quoted")
        elif fr >= FILL_RATE_OK:
            state = f"OK — {fr:.0%} >= {FILL_RATE_OK:.0%}, resembles the traded cells"
        elif fr <= FILL_RATE_ALARM:
            state = (f"STOP AND REPORT — {fr:.0%} <= {FILL_RATE_ALARM:.0%}, "
                     f"resembles the excluded bare-one-sided cell (15%)")
        else:
            state = f"CONTINUE — {fr:.0%} between the thresholds"
        res["early_diagnostic"] = {"fill_rate": fr, "state": state,
                                   "backtest": BACKTEST_FILL_RATES}

    # ── §4 hypothesis test, on the ALL-IN number ───────────────────────
    allin = cluster_ci(settled_pnl(fills, settles))
    res["all_in"] = allin
    if allin["lo_c"] is not None:
        lo, hi = allin["lo_c"], allin["hi_c"]
        ex_val = not (lo <= H_VALIDATED_C <= hi)
        ex_one = not (lo <= H_ONESIDED_C <= hi)
        if ex_one and not ex_val:
            v = ("DISCHARGED — CI excludes the one-sided cell and contains "
                 "the validated headline")
        elif ex_val and not ex_one:
            v = ("DIFFERENT STRATEGY — CI excludes the validated headline. "
                 "The gate is not validating the Phase-2 thesis; re-register "
                 "under a new pod ID restricted to covered books")
        elif not ex_val and not ex_one:
            v = ("NO DECISION — CI contains BOTH hypotheses. Expected at this "
                 "T; NOT reassurance")
        else:
            v = "NEITHER — CI excludes both. Report and stop"
        res["hypothesis_test"] = {"ci_c": [lo, hi], "h_validated": H_VALIDATED_C,
                                  "h_onesided": H_ONESIDED_C, "verdict": v}

    if a.json:
        print(json.dumps(res, indent=1))
        return 0

    print("P-022 — book-side split (DIAGNOSTIC; the verdict is p022_checkpoint)")
    print("=" * 70)
    print(f"  tournaments quoted : {res['quoted_tournaments']}")
    print(f"  unattributed fills : {unattributed}"
          + ("  <- counted all-in, excluded from both strata"
             if unattributed else ""))
    print()
    print(f"  {'book_side':18s} {'posted':>7} {'filled':>7} {'fill%':>7} "
          f"{'settled c/ct':>13} {'95% CI':>20}")
    for side, d in sorted(res["by_book_side"].items()):
        s = d["settled"]
        ci = ("—" if s["lo_c"] is None
              else f"[{s['lo_c']:+.2f}, {s['hi_c']:+.2f}]")
        m = "—" if s["mean_c"] is None else f"{s['mean_c']:+.2f}"
        fr = "—" if d["fill_rate"] is None else f"{100*d['fill_rate']:.1f}"
        print(f"  {side:18s} {d['posted_markets']:>7} {d['filled_markets']:>7} "
              f"{fr:>7} {m:>13} {ci:>20}")
    if res["early_diagnostic"]:
        print(f"\n  EARLY (§5): {res['early_diagnostic']['state']}")
        print(f"    backtest fill rates: {BACKTEST_FILL_RATES}")
    if res["hypothesis_test"]:
        h = res["hypothesis_test"]
        print(f"\n  §4 all-in CI [{h['ci_c'][0]:+.2f}, {h['ci_c'][1]:+.2f}] "
              f"vs H_validated {H_VALIDATED_C:+.2f} / "
              f"H_onesided {H_ONESIDED_C:+.2f}")
        print(f"    -> {h['verdict']}")
    else:
        print("\n  §4: no settled fills yet — nothing to test. NOT a pass.")
    print("\n  Verdict reader (the only sanctioned one): "
          "python3 -m scripts.p022_checkpoint")
    return 0


if __name__ == "__main__":
    sys.exit(main())
