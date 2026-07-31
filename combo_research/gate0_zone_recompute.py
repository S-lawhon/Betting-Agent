#!/usr/bin/env python3
"""
combo_research/gate0_zone_recompute.py
──────────────────────────────────────
The formal Gate 0 read: recompute zone membership UNIFORMLY, offline.

Why this exists
───────────────
``shadow_public.py`` sets ``in_zone`` at discovery::

    model   = copula if copula is not None else indep
    in_zone = MIN_LEGS <= n_legs <= MAX_LEGS and MIN_PX <= model <= MAX_PX

Before the copula landed (logger restarted 2026-07-29 21:13 UTC) ``copula`` was
None, so ``model`` was the INDEPENDENCE price; after, it was the COPULA price.
The stored column therefore mixes two different zone definitions, and the
copula runs ~+2.9c above independence — so the two bases do not select the same
combos. ``backfill_copula.py`` deliberately did not rewrite ``in_zone`` (that
would have silently redefined the sample), and both ``shadow_public.report()``
and ``gate0_margin_ab.py`` read the stored column. Neither is the gate read.

This script recomputes from scratch and is uniform on BOTH axes:

* **rho basis** — every row re-priced with the one fitted rho, so rows priced
  live under an older rho cannot skew the comparison.
* **zone basis** — membership recomputed from that price, one rule for all rows.

Read-only on the shadow DB (``mode=ro``); it never disturbs the running logger.
Prices come from ``leg_marks_json``, the raw leg books stored at discovery, so
nothing is re-collected and the result is exactly what the live path would have
computed.

Reported side by side:

* STORED zone   — reproduces the on-box number, for continuity only.
* UNIFORM copula zone — **THIS IS THE GATE.** The model price P-029 would quote
  on is the copula price, so the zone is defined on it.
* UNIFORM independence zone — sensitivity. Shows whether the verdict is an
  artefact of which basis defines the zone.

The median carries a **cluster bootstrap by event**: combos inside one event
share legs and outcomes, so treating them as independent overstates precision
(project convention — see CLAUDE.md on clustering backtest CIs by event).

Usage::

    python3 gate0_zone_recompute.py --db /var/lib/p029/shadow.sqlite \
        --rho combo_research/fitted_rho.json [--boot 2000]
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.combo_copula import (  # noqa: E402
    ComboCopula, correlation_matrix, load_rho, relation,
)

# The pre-registered zone (REPORT_Combo_MM_2026-07-28.md §4.6), restated here
# so the gate read does not depend on importing the collector.
MIN_LEGS, MAX_LEGS = 2, 4
MIN_PX, MAX_PX = 0.10, 0.35

# Pre-registered 2026-07-28. Not to be edited to fit a result.
GATE_CONTINUE_C, GATE_STOP_C, GATE_MIN_N = 2.0, 1.0, 500


def strongest(tickers: Sequence[str]) -> str:
    order = {"same_player": 3, "same_game": 2, "same_day": 1, "cross": 0}
    best = "cross"
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            r = relation(tickers[i], tickers[j])
            if order[r] > order[best]:
                best = r
    return best


def eff_marginals(legs, marks) -> Optional[List[float]]:
    """Discovery-time YES marginals, with each leg's side applied."""
    by: Dict[str, dict] = {}
    for m in marks or []:
        t = m.get("ticker")
        if t and t not in by:
            by[t] = m
    out: List[float] = []
    for tkr, side in legs:
        mid = (by.get(tkr) or {}).get("yes_mid")
        if mid is None:
            return None
        p = float(mid)
        if (side or "yes").lower() == "no":
            p = 1.0 - p
        if not (0.0 < p < 1.0):
            return None
        out.append(p)
    return out


def pct(v: Sequence[float], q: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(q * len(s)))]


def describe(label: str, vals: Sequence[float]) -> None:
    if len(vals) < 5:
        print(f"  {label:<26} n={len(vals):>6}  (too few)")
        return
    v = sorted(vals)
    n = len(v)
    print(f"  {label:<26} n={n:>6}  median {v[n // 2]:+7.2f}c  "
          f"p25 {pct(v, .25):+7.2f}c  p75 {pct(v, .75):+7.2f}c  "
          f"mean {sum(v) / n:+7.2f}c  "
          f"frac>0 {100 * sum(1 for x in v if x > 0) / n:.0f}%")


def cluster_bootstrap_median(pairs: Sequence[Tuple[str, float]],
                             iters: int, seed: int = 12345
                             ) -> Optional[Tuple[float, float]]:
    """95% CI for the median, resampling EVENTS (not rows) with replacement.

    Combos in one event share legs and settle together; resampling rows would
    treat them as independent observations and understate the interval.
    """
    by_ev: Dict[str, List[float]] = {}
    for ev, m in pairs:
        by_ev.setdefault(ev or "_none", []).append(m)
    evs = list(by_ev)
    if len(evs) < 5:
        return None
    rng = random.Random(seed)
    meds: List[float] = []
    for _ in range(iters):
        pool: List[float] = []
        for _ in range(len(evs)):
            pool.extend(by_ev[evs[rng.randrange(len(evs))]])
        if pool:
            pool.sort()
            meds.append(pool[len(pool) // 2])
    if not meds:
        return None
    meds.sort()
    return meds[int(.025 * len(meds))], meds[int(.975 * len(meds))]


def verdict_for(med: float, n: int) -> str:
    if med >= GATE_CONTINUE_C and n >= GATE_MIN_N:
        return "CONTINUE"
    if med < GATE_STOP_C:
        return "STOP"
    return "EXTEND one week"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--rho", required=True)
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()

    fitted = load_rho(a.rho)
    print(f"fitted rho: { {k: round(v, 4) for k, v in fitted.items()} }")

    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT market_ticker, event_ticker, legs_json, leg_marks_json,"
        " first_trade_px, n_legs, in_zone, copula_price, indep_price"
        " FROM combo WHERE traded=1 AND first_trade_px IS NOT NULL").fetchall()
    con.close()
    print(f"traded rows with a first print: {len(rows):,}")

    cop = ComboCopula(rho=fitted)
    recs = []
    unpriceable = 0
    for (tkr, ev, legs_j, marks_j, px, n_legs,
         stored_zone, stored_cop, stored_indep) in rows:
        try:
            legs = [(l[0], l[1]) for l in json.loads(legs_j or "[]")]
            marks = json.loads(marks_j or "[]")
        except (ValueError, TypeError, IndexError):
            unpriceable += 1
            continue
        eff = eff_marginals(legs, marks)
        if not eff or len(eff) != len(legs):
            unpriceable += 1
            continue
        tickers = [t for t, _ in legs]
        indep = 1.0
        for p in eff:
            indep *= p
        copula = cop.joint_yes(eff, correlation_matrix(tickers, fitted))
        nl = len(legs)
        legs_ok = MIN_LEGS <= nl <= MAX_LEGS
        recs.append({
            "event": ev, "px": float(px), "n_legs": nl,
            "indep": indep, "copula": copula,
            "rel": strongest(tickers),
            "stored_zone": int(stored_zone or 0),
            "stored_cop": stored_cop,
            "zone_copula": int(legs_ok and MIN_PX <= copula <= MAX_PX),
            "zone_indep": int(legs_ok and MIN_PX <= indep <= MAX_PX),
            "margin": (float(px) - copula) * 100.0,
        })

    print(f"re-priced: {len(recs):,}   unpriceable (no stored marks): {unpriceable:,}")
    if len(recs) < GATE_MIN_N:
        print("!! below the pre-registered n floor; not decidable")

    # --- did the live rho basis differ from the uniform one? ---------------
    drift = [abs(r["copula"] - r["stored_cop"]) * 100.0
             for r in recs if r["stored_cop"] is not None]
    if drift:
        big = sum(1 for d in drift if d > 0.05)
        print(f"\nrho-basis uniformity: {len(drift):,} rows carried a stored copula; "
              f"median |recomputed - stored| {sorted(drift)[len(drift) // 2]:.4f}c, "
              f"{big:,} rows differ by >0.05c")

    # --- how the zone definition moves the sample -------------------------
    a_in_b_out = sum(1 for r in recs if r["stored_zone"] and not r["zone_copula"])
    a_out_b_in = sum(1 for r in recs if not r["stored_zone"] and r["zone_copula"])
    print(f"\nzone migration (stored -> uniform copula):"
          f"  dropped {a_in_b_out:,}   added {a_out_b_in:,}")
    print(f"  stored in-zone {sum(r['stored_zone'] for r in recs):,}"
          f"   uniform-copula in-zone {sum(r['zone_copula'] for r in recs):,}"
          f"   uniform-indep in-zone {sum(r['zone_indep'] for r in recs):,}")

    # --- margin distributions ---------------------------------------------
    print("\n=== MARGIN vs COPULA (fitted rho, uniform) ===")
    describe("all", [r["margin"] for r in recs])
    describe("STORED zone (mixed)", [r["margin"] for r in recs if r["stored_zone"]])
    describe("UNIFORM copula zone", [r["margin"] for r in recs if r["zone_copula"]])
    describe("UNIFORM indep zone", [r["margin"] for r in recs if r["zone_indep"]])

    print("\n--- uniform copula zone, by leg count ---")
    for k in (2, 3, 4):
        describe(f"{k} legs", [r["margin"] for r in recs
                               if r["zone_copula"] and r["n_legs"] == k])
    print("--- uniform copula zone, by strongest relation ---")
    for rel in ("same_player", "same_game", "same_day", "cross"):
        describe(rel, [r["margin"] for r in recs
                       if r["zone_copula"] and r["rel"] == rel])

    # --- the gate ----------------------------------------------------------
    zone = [r for r in recs if r["zone_copula"]]
    print("\n=== GATE 0 (pre-registered 2026-07-28), UNIFORM ZONE ===")
    if len(zone) < 5:
        print("  sample too small")
        return 1
    m = sorted(r["margin"] for r in zone)
    med, n = m[len(m) // 2], len(m)
    ci = cluster_bootstrap_median([(r["event"], r["margin"]) for r in zone], a.boot)
    n_ev = len({r["event"] for r in zone})
    print(f"  in-zone median vs copula: {med:+.2f}c   n={n:,}   events={n_ev:,}")
    if ci:
        print(f"  cluster bootstrap 95% CI (by event, {a.boot} iters): "
              f"[{ci[0]:+.2f}c, {ci[1]:+.2f}c]")
    print(f"  thresholds: CONTINUE >= +{GATE_CONTINUE_C}c with n>={GATE_MIN_N}"
          f" | STOP < +{GATE_STOP_C}c")
    print(f"  -> {verdict_for(med, n)}")

    # Robustness: the verdict should not depend on which basis defines the zone.
    print("\n  robustness — same gate under the other zone definitions:")
    for label, key in (("stored (mixed)", "stored_zone"),
                       ("uniform independence", "zone_indep")):
        sub = sorted(r["margin"] for r in recs if r[key])
        if len(sub) >= 5:
            mm, nn = sub[len(sub) // 2], len(sub)
            print(f"    {label:<22} median {mm:+.2f}c  n={nn:,}  -> {verdict_for(mm, nn)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
