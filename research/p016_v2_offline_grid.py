#!/usr/bin/env python3
"""P-016 v2 offline suppress/widen grid + lag proxy.

Pure analysis over the existing maker_fills.jsonl tape, driven entirely by
the already-tested src/maker_challenger.py harness (Tier-2). No deployment,
no pod changes. Produces the numbers behind research/P016_v2_offline_grid_2026-07-21.md.

Usage:
    python3 research/p016_v2_offline_grid.py <maker_fills.jsonl>
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.maker_challenger import (  # noqa: E402
    CHAMPION_ARM, ArmSpec, build_tape, cluster_bootstrap_delta,
    detectable_effect_c, evaluate_arm, intracluster_correlation, verify_subset,
    _fee_adj_cents,
)

# exclude_before contamination cutoff from manager/registry.yaml P-016 gate block
CUT_ISO = "2026-07-20T01:26:00Z"
CUT_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()  # placeholder
CUT_EPOCH = datetime(2026, 7, 20, 1, 26, 0, tzinfo=timezone.utc).timestamp()

T_GRID = [5, 10, 15, 20, 30]
WIDEN_LARGE_C = 0.05   # "large offset" for Route B (5c half-width add). Sensitivity below.


def load_records(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def fill_epoch(rec):
    return rec.get("fill_epoch") or rec.get("ts")


def main(path):
    records = load_records(path)
    # Build tape via the harness (excludes shadow, pairs +5m markout).
    tape = build_tape(records)  # GATE_HORIZON_S = 300 by default
    # Apply exclude_before defensively on the paired tape (FILL fields carry fill_epoch).
    tape = [r for r in tape if (fill_epoch(r) or 0) >= CUT_EPOCH]

    champ = evaluate_arm(tape, CHAMPION_ARM)
    icc, deff = intracluster_correlation(champ)
    sd_c = _pstdev([f.markout_c for f in champ.fills])

    print("=" * 78)
    print("CHAMPION (v1) baseline on the analysis tape")
    print("=" * 78)
    print(f"tape rows (paired +5m markout, real, post-cutoff): {len(tape)}")
    print(f"champion fills: {champ.n}   games: {champ.games}")
    print(f"mean +5m markout (fee-adj): {champ.mean_markout_c:+.3f} c/ct")
    total_c = sum(f.markout_c for f in champ.fills)
    total_ct = sum(f.qty for f in champ.fills)
    total_dollars = sum(f.markout_c * f.qty for f in champ.fills) / 100.0
    print(f"total contracts: {total_ct:.0f}   total markout $: {total_dollars:+.2f}")
    print(f"per-fill sd: {sd_c:.2f} c   ICC(game): {icc:.4f}   design effect: {deff:.2f}")
    mdd = detectable_effect_c(champ.n, deff, sd_c, n_arms=len(T_GRID) * 2)
    print(f"min detectable effect @80% power (deff-adj, {len(T_GRID)*2} arms Bonferroni): {mdd:.2f} c")
    print()

    # Spread capture at fill (t=0 markout proxy): sign*(mid_at_fill - price), fee-adj.
    # This is the "+4.08c" the postmortem cites; it's what a suppressed fill forgoes.
    def spread_capture_c(rec):
        mid = rec.get("mid_at_fill")
        if mid is None:
            return None
        sign = 1.0 if rec["side"] == "buy" else -1.0
        return _fee_adj_cents(sign * (mid - float(rec["price"])), float(rec["price"]))
    champ_sc = [spread_capture_c(r) for r in tape]
    champ_sc = [x for x in champ_sc if x is not None]
    print(f"champion spread capture at fill (fee-adj mean): "
          f"{sum(champ_sc)/len(champ_sc):+.3f} c  (n={len(champ_sc)})")
    print()

    n_arms_total = len(T_GRID) * 2
    rows = []
    for route in ("suppress", "widen"):
        for T in T_GRID:
            if route == "suppress":
                spec = ArmSpec(name=f"suppress_{T}s", description="",
                               event_window_s=float(T), event_action="suppress")
            else:
                spec = ArmSpec(name=f"widen_{T}s", description="",
                               event_window_s=float(T), event_action="widen",
                               event_widen_cents=WIDEN_LARGE_C)
            arm = evaluate_arm(tape, spec)
            verify_subset(champ, arm)  # enforce L5 observability
            bs = cluster_bootstrap_delta(champ, arm, n_arms=n_arms_total)
            arm_total_dollars = sum(f.markout_c * f.qty for f in arm.fills) / 100.0
            rows.append({
                "route": route, "T": T,
                "n": arm.n, "retention": arm.retention,
                "mean_markout_c": arm.mean_markout_c,
                "mean_dropped_c": arm.mean_dropped_markout_c,
                "n_dropped": len(arm.dropped),
                "total_dollars": arm_total_dollars,
                "delta_c": bs["delta_c"],
                "ci_lo": bs["ci_lo_c"], "ci_hi": bs["ci_hi_c"],
                "p_improve": bs["p_improve"], "games": arm.games,
            })

    print("=" * 78)
    print(f"GRID (widen offset = {WIDEN_LARGE_C*100:.0f}c; CIs game-clustered, "
          f"Bonferroni over {n_arms_total} arms)")
    print("=" * 78)
    hdr = (f"{'arm':<14}{'n':>5}{'ret%':>7}{'mkout':>8}{'drop_mk':>9}"
           f"{'tot$':>9}{'Δc':>8}{'CI95':>18}{'p_imp':>7}")
    print(hdr)
    for r in rows:
        ci = f"[{r['ci_lo']:+.2f},{r['ci_hi']:+.2f}]"
        print(f"{r['route']+'_'+str(r['T'])+'s':<14}{r['n']:>5}"
              f"{r['retention']*100:>6.1f}%{r['mean_markout_c']:>+8.2f}"
              f"{r['mean_dropped_c']:>+9.2f}{r['total_dollars']:>+9.2f}"
              f"{r['delta_c']:>+8.2f}{ci:>18}{r['p_improve']:>7.2f}")
    print()
    print(f"champion mean markout {champ.mean_markout_c:+.2f}c  total ${total_dollars:+.2f}")

    # ---- Lag proxy: markout curve vs secs_since_state_change (observed) ----
    print()
    print("=" * 78)
    print("LAG PROXY: +5m markout by secs_since_state_change bucket (observed)")
    print("=" * 78)
    buckets = [(0, 5), (5, 10), (10, 15), (15, 30), (30, 60), (60, 1e9)]
    bybuck = defaultdict(list)
    none_bucket = []
    for f in champ.fills:
        ssc = f.secs_since_state_change
        if ssc is None:
            none_bucket.append(f.markout_c)
            continue
        for lo, hi in buckets:
            if lo <= ssc < hi:
                bybuck[(lo, hi)].append(f.markout_c)
                break
    print(f"{'bucket(s)':<12}{'n':>6}{'mean_mkout_c':>14}{'share%':>9}")
    for lo, hi in buckets:
        xs = bybuck[(lo, hi)]
        label = f"{lo}-{'inf' if hi > 1e8 else int(hi)}"
        if xs:
            print(f"{label:<12}{len(xs):>6}{sum(xs)/len(xs):>+14.2f}"
                  f"{100*len(xs)/champ.n:>8.1f}%")
        else:
            print(f"{label:<12}{0:>6}{'--':>14}{0:>8.1f}%")
    if none_bucket:
        print(f"{'None(no chg)':<12}{len(none_bucket):>6}"
              f"{sum(none_bucket)/len(none_bucket):>+14.2f}"
              f"{100*len(none_bucket)/champ.n:>8.1f}%")

    # ---- Poll cadence: consecutive observation timestamps per game ----
    print()
    print("=" * 78)
    print("POLL CADENCE (consecutive fill_epoch gaps within a game, proxy for cycle)")
    print("=" * 78)
    by_game_epochs = defaultdict(list)
    for r in tape:
        e = fill_epoch(r)
        if e:
            by_game_epochs[r.get("game_pk")].append(e)
    gaps = []
    for g, es in by_game_epochs.items():
        es.sort()
        for a, b in zip(es, es[1:]):
            d = b - a
            if 0 < d < 300:  # ignore cross-inning/idle gaps
                gaps.append(d)
    gaps.sort()
    if gaps:
        import statistics
        print(f"n gaps: {len(gaps)}  median: {statistics.median(gaps):.1f}s  "
              f"p10: {gaps[len(gaps)//10]:.1f}s  p90: {gaps[len(gaps)*9//10]:.1f}s")
    # secs_since_state_change distribution (how fine is the observed window)
    sscs = sorted(f.secs_since_state_change for f in champ.fills
                  if f.secs_since_state_change is not None)
    if sscs:
        print(f"secs_since_state_change: min {sscs[0]:.1f}s  "
              f"p10 {sscs[len(sscs)//10]:.1f}s  median {sscs[len(sscs)//2]:.1f}s  "
              f"share ssc<12s (< one cycle): "
              f"{100*sum(1 for x in sscs if x<12)/len(sscs):.1f}%")


def _pstdev(xs):
    import math
    if len(xs) < 2:
        return float("nan")
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


if __name__ == "__main__":
    main(sys.argv[1])
