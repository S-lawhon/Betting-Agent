#!/usr/bin/env python3
"""
golf_quirks_research/p022_rule_decision_evidence.py
───────────────────────────────────────────────────
Every number in `research/P022_RULE_DECISIONS_2026-07-29.md`, recomputed.

Read-only. Decides nothing; it exists so the four open rule questions are
answered from checkable arithmetic rather than from assertion, and so the
answers cannot later be re-derived differently.

    python3 golf_quirks_research/p022_rule_decision_evidence.py
    python3 golf_quirks_research/p022_rule_decision_evidence.py --cadence

`--cadence` hits Kalshi (settled markets across all 13 *LEAD series); every
other section runs offline from committed artefacts.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

Z95 = 1.959963985
Z90, Z80 = 1.281551566, 0.841621234

# The 6 competitions whose rounds trip BOTH weather criteria (see `weather`).
WEATHER = {"KLO26", "SOO26", "U.SWOPBA26", "USO26", "GESO26", "THMTPBW26"}


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def quant(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _widen():
    return json.loads((HERE / "widen_results.json").read_text())["unfiltered"]


def _validation():
    return json.loads(
        (HERE / "schedule_resolver_validation.json").read_text())["rows"]


# ── Item 2 ───────────────────────────────────────────────────────────

def weather() -> None:
    """Two independent criteria; they must select the same event-rounds.

    `lag_h` uses only the resolver's own prediction against Kalshi's rewritten
    close. `round_span_h` uses only ESPN tee times. Agreement is what makes
    the threshold a finding rather than a fit -- and both distributions have
    an empty gap around it, so any cut inside the gap gives the same answer.
    """
    rows = _validation()
    lag = {r["event"] for r in rows if r["live_err_h"] > 12.0}
    span = {r["event"] for r in rows
            if (r.get("round_span_h") or 0) > 24.0}
    print("== Item 2: weather detection ==")
    print(f"  lag_h > 12h        : {len(lag)}")
    print(f"  round_span_h > 24h : {len(span)}")
    print(f"  agreement          : {len(lag & span)} of {len(lag | span)}"
          f"   {'IDENTICAL' if lag == span else 'DISAGREE'}")
    e = sorted(r["live_err_h"] for r in rows)
    s = sorted(r["round_span_h"] for r in rows if r.get("round_span_h"))
    print(f"  lag gap            : ...{e[-9]:.1f} | {e[-8]:.1f}...")
    print(f"  span gap           : ...{s[-9]:.1f} | {s[-8]:.1f}...")

    w = _widen()
    for lab in ("ORIGINAL", "POOLED"):
        tc = w[lab]["per_tournament_cents"]
        keep = [v for k, v in tc.items() if k not in WEATHER]
        drop = {k: v for k, v in tc.items() if k in WEATHER}
        print(f"  {lab:9s} all n={len(tc):2d} edge={st.mean(list(tc.values())):+6.2f}c "
              f"z={_z(list(tc.values())):5.2f}   "
              f"ex-weather n={len(keep):2d} edge={st.mean(keep):+6.2f}c "
              f"z={_z(keep):5.2f}   dropped mean={st.mean(list(drop.values())):+6.2f}c")
    print()


def _z(xs):
    if len(xs) < 2:
        return float("nan")
    return st.mean(xs) / (st.stdev(xs) / math.sqrt(len(xs)))


# ── Item 3 ───────────────────────────────────────────────────────────

def above_24h() -> None:
    """True H = predicted H + resolver error, and the error is one-sided
    positive on 72 of 72 settled events."""
    rows = _validation()
    print("== Item 3: how far above H=24h the pod posts ==")
    for key, lab in (("err_tee_h", "tee_times"),
                     ("err_day_h", "tour_day_offset"),
                     ("live_err_h", "live (as resolved)")):
        e = [r[key] for r in rows if r.get(key) is not None]
        t = [24 + x for x in e]
        frac = [min(x, 12.0) / 12.0 for x in e]
        print(f"  {lab:20s} n={len(e):3d} trueH med {st.median(t):6.2f} "
              f"min {min(t):6.2f} p90 {quant(t, .9):6.2f} max {max(t):6.2f} "
              f"| >24h {sum(1 for x in t if x > 24)}/{len(t)} "
              f"| mean share of window above 24h true {st.mean(frac) * 100:5.1f}%")
    print()


# ── Item 4 ───────────────────────────────────────────────────────────

def power() -> None:
    """Two scales, because the backtest and the reader do not share one.

    `quirks_common.bootstrap_weighted` is contract-weighted across
    tournaments; DECISION_RULE §2 and `scripts/p022_checkpoint.py` weight
    every tournament equally. Both are printed rather than one being chosen.
    """
    w = _widen()
    print("== Item 4a: sigma and T from the bootstrap CI (§5's own method) ==")
    for lab in ("ORIGINAL", "POOLED"):
        v = w[lab]
        se = (v["ci_high_cents"] - v["ci_low_cents"]) / 2 / Z95
        sig = se * math.sqrt(v["tournaments"])
        d = v["net_cents_per_contract"]
        print(f"  {lab:9s} d={d:+5.2f} T={v['tournaments']:2d} SE={se:5.3f} "
              f"sigma={sig:6.3f}  T@90%={((2.0 + Z90) * sig / d) ** 2:6.1f}  "
              f"power@14={phi(d * math.sqrt(14) / sig - 2.0) * 100:5.1f}%")
    print("  cross-check, the queue's '~24': holding sigma at ORIGINAL's "
          f"3.870 with d=2.57 -> T={((2.0 + Z90) * 3.870 / 2.57) ** 2:5.1f}")

    print("== Item 4b: the same, under §2's equal-weight statistic ==")
    sets = {
        "ORIGINAL": list(w["ORIGINAL"]["per_tournament_cents"].values()),
        "POOLED": list(w["POOLED"]["per_tournament_cents"].values()),
        "ORIGINAL ex-weather": [v for k, v in
                                w["ORIGINAL"]["per_tournament_cents"].items()
                                if k not in WEATHER],
        "POOLED ex-weather": [v for k, v in
                              w["POOLED"]["per_tournament_cents"].items()
                              if k not in WEATHER],
    }
    for lab, xs in sets.items():
        d, sd, n = st.mean(xs), st.stdev(xs), len(xs)
        if d <= 0:
            print(f"  {lab:22s} n={n:2d} d={d:+6.2f} sigma={sd:6.2f} "
                  f"z={_z(xs):5.2f}  edge<=0 -> KILL at T>=14 by construction")
            continue
        print(f"  {lab:22s} n={n:2d} d={d:+6.2f} sigma={sd:6.2f} z={_z(xs):5.2f}"
              f"  T@90%={((2.0 + Z90) * sd / d) ** 2:6.1f}"
              f"  power@14={phi(d * math.sqrt(14) / sd - 2.0) * 100:5.1f}%"
              f"  @24={phi(d * math.sqrt(24) / sd - 2.0) * 100:5.1f}%"
              f"  @40={phi(d * math.sqrt(40) / sd - 2.0) * 100:5.1f}%")
    print()


def cadence() -> None:
    """The gate's unit is the GOLF EVENT, not the event x round ticker.

    DECISION_RULE §5 says 15-19/month and REPORT_P028 says 1.3/week; the
    second is KXPGAR1LEAD alone. Measured across all 13 series here.
    """
    from src.kalshi_public import KalshiPublic
    from src.round_leader_fade_maker import DEFAULT_SERIES
    k = KalshiPublic()
    firsts, per_series = {}, defaultdict(set)
    for s in DEFAULT_SERIES:
        page = k.get("/markets", {"series_ticker": s, "status": "settled",
                                  "limit": 1000}) or {}
        mk, cur = page.get("markets") or [], page.get("cursor")
        while cur and len(mk) < 40000:
            page = k.get("/markets", {"series_ticker": s, "status": "settled",
                                      "limit": 1000, "cursor": cur}) or {}
            mk += page.get("markets") or []
            cur = page.get("cursor")
        for m in mk:
            ev = m.get("event_ticker") or ""
            parts, ct = ev.split("-"), m.get("close_time")
            if len(parts) < 2 or not ct:
                continue
            per_series[s].add(ev)
            code = parts[1]
            if code not in firsts or ct < firsts[code]:
                firsts[code] = ct
    ds = sorted(datetime.fromisoformat(v.replace("Z", "+00:00"))
                for v in firsts.values())
    weeks = (ds[-1] - ds[0]).days / 7.0
    rounds = sum(len(v) for v in per_series.values())
    print("== Item 4c: cadence ==")
    print(f"  {len(firsts)} golf EVENT CODES, {rounds} event x round tickers, "
          f"{ds[0].date()} -> {ds[-1].date()} = {weeks:.2f} weeks")
    print(f"  event codes/week = {len(firsts) / weeks:.2f}   <- the gate's unit")
    print(f"  tickers/week     = {rounds / weeks:.2f}")
    print(f"  KXPGAR1LEAD alone: {len(per_series['KXPGAR1LEAD'])} events "
          f"-> {len(per_series['KXPGAR1LEAD']) / weeks:.2f}/week "
          "(this is REPORT_P028's 1.3)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cadence", action="store_true",
                    help="also measure cadence live from Kalshi")
    a = ap.parse_args()
    weather()
    above_24h()
    power()
    if a.cadence:
        cadence()
