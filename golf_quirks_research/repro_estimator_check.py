#!/usr/bin/env python3
"""Which statistic is the P-022 backtest headline actually reporting?

Run:  python3 golf_quirks_research/repro_estimator_check.py

WHY THIS EXISTS
───────────────
P022_DECISION_RULE.md §3 defines the gate statistic as

    x_t  = contract-weighted mean pnl_per_contract WITHIN tournament t
    edge = mean(x_t)                     # EQUAL weight per tournament
    se   = stdev(x_t) / sqrt(T)

`quirks_common.bootstrap_weighted` returns, as its point estimate,

    mean = sum(p * w) / total_w          # POOLED across every filled contract

Those are different estimators. The bootstrap does resample TOURNAMENTS with
replacement, so the CI is clustered correctly — it is only the point estimate
that is not the rule's. A tournament with many filled contracts dominates the
pooled figure and counts once in the rule's figure.

This script computes both from the SAME per-tournament table the harness
itself builds, so nothing here depends on re-deriving fills.

Sizing is held at the harness default on purpose. A separate finding is that
the default quote_size=25 breaches the registered $5 per-name collateral cap
at these prices; folding that in here would stop this from being a clean test
of the estimator alone.

RESULT, 2026-08-02 (404-market cache)
─────────────────────────────────────
  PUBLISHED universe (pinned, 364 markets, T=19)
      harness headline  +3.41 c/ct
      pooled            +3.41 c/ct   <- identical, confirming the headline
                                        IS the pooled estimator
      equal-weight §3   +3.80 c/ct   z = 3.29   SIGNIFICANT

  WIDENED universe (full cache, 404 markets, T=22)
      harness headline  +2.57 c/ct
      pooled            +2.57 c/ct   <- identical
      equal-weight §3   +1.45 c/ct   z = 0.65   NOT significant

WHAT THIS DOES AND DOES NOT OVERTURN
────────────────────────────────────
Does NOT overturn the Phase-2 GREEN-LIGHT. On the published sample the two
estimators agree in conclusion: +3.41 vs +3.80, both comfortably significant.
The decision made on that sample stands on either statistic.

DOES undermine the WIDENED evidence. Amendment 1 (2026-07-28) cites +2.57c/ct
for the widened universe and sets T = 24 for "90% power vs the widened
+2.57c effect". That +2.57 is the pooled number. Under the rule's own
statistic the widened effect is +1.45c/ct with sd(x_t) = 10.44c (against
5.04c on the published sample), so the power calculation behind T = 24 rests
on an effect size that is too large and a dispersion that is too small.

The forward gate reader is NOT affected: scripts/p022_checkpoint.py computes
statistics.mean(xs) — the rule's statistic — and MIN_T_DECISION = 24.

CHANGING THE GATE IS NOT THIS SCRIPT'S CALL, and it is not the reader's
either. T = 24 was set by a written amendment; moving it needs another one.
This script reports; a human decides.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "golf_quirks_research"))

import quirks_common as qc            # noqa: E402
import backtest_fade_fills as bf      # noqa: E402

ANCHOR_H = 12.0
OFFSET = 0.02


def estimators(head: Dict[str, Any]) -> Tuple[float, float, int, float, float, float]:
    """Both statistics, from the per-tournament table the harness built."""
    pt = head["per_tournament"]
    xs: List[float] = [v["net_per_contract"] for v in pt.values()]
    ws: List[float] = [v["contracts"] for v in pt.values()]
    pooled = sum(x * w for x, w in zip(xs, ws)) / sum(ws)
    equal = statistics.mean(xs)
    T = len(xs)
    sd = statistics.stdev(xs) if T > 1 else float("nan")
    se = sd / (T ** 0.5) if T > 1 else float("nan")
    z = equal / se if se else float("nan")
    return pooled, equal, T, sd, se, z


def show(label: str, recs: Sequence[Dict[str, Any]]) -> None:
    head = qc.replay(recs, "sell_yes", ANCHOR_H, OFFSET)
    pooled, equal, T, sd, se, z = estimators(head)
    headline = head["net_per_contract"]
    print(f"\n=== {label} ===")
    print(f"  markets in universe      : {len(recs)}")
    print(f"  tournaments with fills T : {T}")
    print(f"  harness net_per_contract : {headline * 100:+.2f} c/ct")
    print(f"  pooled  sum(p*w)/sum(w)  : {pooled * 100:+.2f} c/ct"
          f"   <- matches headline: {abs(pooled - headline) < 5e-4}")
    print(f"  equal   mean(x_t)  [RULE]: {equal * 100:+.2f} c/ct")
    print(f"  divergence               : {(pooled - equal) * 100:+.2f} c/ct")
    print(f"  sd(x_t)={sd * 100:.2f}c  se={se * 100:.2f}c  z={z:.2f}"
          f"   -> {'z>=2.0 SIGNIFICANT' if z >= 2.0 else 'z<2.0 NOT significant'}")
    print(f"  harness CI95             : [{head['ci95_lo'] * 100:+.2f}, "
          f"{head['ci95_hi'] * 100:+.2f}] c/ct  (bootstrap, tournament-clustered)")


def main() -> int:
    all_recs = bf.load()
    pinned, n_excluded, missing = bf.restrict_to_pinned(all_recs)
    print(f"cache: {len(all_recs)} markets total; pinned published universe: "
          f"{len(pinned)} (expected {bf.PUBLISHED_UNIVERSE}), "
          f"excluded {n_excluded}, missing {len(missing)}")
    if missing:
        print(f"  WARNING: {len(missing)} pinned markets MISSING from the cache — "
              f"the published sample has been lost, not merely grown.")
    show("PUBLISHED universe (pinned, the Phase-2 sample)", pinned)
    show("WIDENED universe (full current cache)", all_recs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
