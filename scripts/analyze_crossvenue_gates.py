#!/usr/bin/env python
"""P-030 gate analyzer -- reads data/crossvenue_basis/*.jsonl, applies the four
PRE-REGISTERED kill gates from research/SPEC_P030_CrossVenue_Kalshi_ProphetX.md.

The thresholds below are CONSTANTS, not arguments, and they are not recomputed
from src/prophetx_fees.py. That is deliberate: a gate whose threshold is derived
at analysis time can be loosened by a later "refinement" to the fee model, which
is how a pre-registered test quietly stops being one. If a threshold is wrong,
amend the spec and say so -- do not edit it here silently.

  GATE 1  depth      KILL if median ProphetX depth < $2,000
  GATE 2  gap        KILL if P(executable gap > 3.85c) < 2%, restricted to
                     Kalshi mid in [0.40, 0.65]
  GATE 3  persistence KILL if median qualifying-gap survival < 30s
  GATE 4  capacity   KILL if implied capturable < $5,000/day

All four must PASS. CIs are EVENT-CLUSTERED (bootstrap over game_id), because
snapshots within one game are heavily autocorrelated -- treating 60s samples as
independent is the single easiest way to manufacture significance here, and it
is the error that made P-020's lead-lag beta collapse from +0.300 to +0.045 when
the cluster count went from 5 to 15.

Usage:
  python3 scripts/analyze_crossvenue_gates.py
  python3 scripts/analyze_crossvenue_gates.py --data-dir DIR --json OUT.json
  python3 scripts/analyze_crossvenue_gates.py --self-test   # synthetic sanity check
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import random
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(ROOT, "data", "crossvenue_basis")
log = logging.getLogger("crossvenue_gates")

# ── pre-registered thresholds (see module docstring before touching) ─────────
GATE1_MIN_DEPTH_USD = 2_000.0
GATE2_MIN_GAP_CENTS = 3.85       # after-tax breakeven at P=0.50, gambling char.
GATE2_MIN_FREQ = 0.02
GATE2_BAND = (0.40, 0.65)
GATE3_MIN_SURVIVAL_SECS = 30.0
GATE4_MIN_DAILY_USD = 5_000.0

MIN_CLUSTERS = 15                # below this, CIs are not trustworthy (P-020)


def load_rows(data_dir: str) -> list:
    rows = []
    for path in sorted(glob.glob(os.path.join(data_dir, "*.jsonl"))):
        with open(path) as fh:
            for ln, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("%s:%d unparseable, skipped", path, ln)
    return rows


def _ts(r) -> float:
    try:
        return datetime.fromisoformat(r["ts_utc"]).timestamp()
    except (KeyError, ValueError):
        return float("nan")


def cluster_bootstrap(clusters: dict, stat_fn, n: int = 2000,
                      seed: int = 20260729) -> tuple:
    """(point, lo95, hi95) resampling whole CLUSTERS with replacement."""
    keys = list(clusters)
    if not keys:
        return (float("nan"),) * 3
    point = stat_fn([v for k in keys for v in clusters[k]])
    rng = random.Random(seed)
    draws = []
    for _ in range(n):
        pick = [rng.choice(keys) for _ in keys]
        vals = [v for k in pick for v in clusters[k]]
        if vals:
            draws.append(stat_fn(vals))
    if not draws:
        return point, float("nan"), float("nan")
    draws.sort()
    return point, draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws))]


def gate1_depth(rows) -> dict:
    """Median ProphetX depth in dollars at the price we would lift.

    NOTE -- MEASUREMENT CAVEAT. The collector records TOP-OF-BOOK size only;
    the spec's gate is written on top-3-level depth. Top-of-book is a LOWER
    BOUND, so a PASS here is real but a FAIL may be an artifact of the
    instrument. A gate-1 KILL must be re-checked against full depth before it
    is accepted. Flagged in the output.
    """
    clusters = defaultdict(list)
    for r in rows:
        size, px = r.get("px_other_ask_size"), r.get("px_other_ask_prob")
        if size is None or px is None:
            continue
        clusters[r.get("game_id", "?")].append(float(size) * float(px))
    point, lo, hi = cluster_bootstrap(clusters, st.median)
    return {"gate": 1, "name": "depth", "metric": "median PX depth (USD)",
            "value": point, "ci95": [lo, hi], "threshold": GATE1_MIN_DEPTH_USD,
            "n_obs": sum(len(v) for v in clusters.values()),
            "n_clusters": len(clusters),
            "pass": bool(point >= GATE1_MIN_DEPTH_USD) if point == point else False,
            "caveat": "top-of-book only; a FAIL needs re-check on full depth"}


def gate2_gap(rows) -> dict:
    """P(executable gap > 3.85c) inside the spec band."""
    clusters = defaultdict(list)
    for r in rows:
        if not r.get("in_spec_band"):
            continue
        gap = r.get("gap")
        if gap is None:
            continue
        clusters[r.get("game_id", "?")].append(
            1.0 if gap * 100.0 > GATE2_MIN_GAP_CENTS else 0.0)
    point, lo, hi = cluster_bootstrap(
        clusters, lambda v: sum(v) / len(v) if v else 0.0)
    return {"gate": 2, "name": "gap",
            "metric": f"P(gap > {GATE2_MIN_GAP_CENTS}c) in band {GATE2_BAND}",
            "value": point, "ci95": [lo, hi], "threshold": GATE2_MIN_FREQ,
            "n_obs": sum(len(v) for v in clusters.values()),
            "n_clusters": len(clusters),
            "pass": bool(point >= GATE2_MIN_FREQ) if point == point else False}


def gate3_persistence(rows) -> dict:
    """Median wall-clock survival of a qualifying gap.

    An episode is measured as (last qualifying observation - first qualifying
    observation). That is the OBSERVED LOWER BOUND: the true end lies somewhere
    in the interval between the last qualifying sample and the first
    non-qualifying one, and crediting the full interval would award survival
    time we did not see. Since gate 3 kills on SHORT survival, the lower bound
    is the conservative direction -- it can produce a false KILL but never a
    false PASS.

    A consequence worth reading carefully: a gap seen in exactly ONE snapshot
    scores 0s, not one sample interval. So survival is only resolvable above
    the sample interval (60s default). If the median lands at 0, the honest
    statement is "shorter than the instrument can resolve," and the fix is a
    faster sample rate, not a softer gate.
    """
    seq = defaultdict(list)
    for r in rows:
        gap = r.get("gap")
        if gap is None:
            continue
        seq[(r.get("game_id"), r.get("team"))].append(
            (_ts(r), gap * 100.0 > GATE2_MIN_GAP_CENTS))
    clusters = defaultdict(list)
    for (gid, _), pts in seq.items():
        pts.sort()
        start = last = None
        for ts, ok in pts:
            if ok:
                start = ts if start is None else start
                last = ts
            elif start is not None:
                clusters[gid].append(last - start)
                start = last = None
        if start is not None:                       # right-censored episode
            clusters[gid].append(last - start)
    point, lo, hi = cluster_bootstrap(clusters, st.median)
    return {"gate": 3, "name": "persistence",
            "metric": "median qualifying-gap survival (s)",
            "value": point, "ci95": [lo, hi],
            "threshold": GATE3_MIN_SURVIVAL_SECS,
            "n_obs": sum(len(v) for v in clusters.values()),
            "n_clusters": len(clusters),
            "pass": bool(point >= GATE3_MIN_SURVIVAL_SECS) if point == point else False}


def gate4_capacity(rows, g1, g2) -> dict:
    """Implied $/day = P(qualifying) x median depth x distinct markets/day."""
    days = {r["ts_utc"][:10] for r in rows if r.get("ts_utc")}
    mkts = {(r.get("ts_utc", "")[:10], r.get("game_id"), r.get("team"))
            for r in rows}
    per_day = len(mkts) / max(len(days), 1)
    depth, freq = g1["value"], g2["value"]
    val = (depth * freq * per_day
           if depth == depth and freq == freq else float("nan"))
    return {"gate": 4, "name": "capacity", "metric": "implied capturable USD/day",
            "value": val, "ci95": [float("nan")] * 2,
            "threshold": GATE4_MIN_DAILY_USD,
            "n_obs": len(rows), "n_clusters": len(days),
            "markets_per_day": per_day,
            "pass": bool(val >= GATE4_MIN_DAILY_USD) if val == val else False,
            "caveat": "product of two point estimates; inherits gate-1's "
                      "top-of-book caveat and is an UPPER bound on what a "
                      "single participant captures"}


def bust_exposure(rows) -> dict:
    """Not a gate -- diagnostic. Share of qualifying fills that would sit
    outside ProphetX Rule 4.6's No-Cancellation Range and be bustable."""
    q = [r for r in rows
         if r.get("gap") is not None and r["gap"] * 100 > GATE2_MIN_GAP_CENTS]
    flagged = [r for r in q if r.get("rule46_bustable")]
    return {"qualifying": len(q), "bustable": len(flagged),
            "share": (len(flagged) / len(q)) if q else None}


def run(rows) -> dict:
    g1 = gate1_depth(rows)
    g2 = gate2_gap(rows)
    g3 = gate3_persistence(rows)
    g4 = gate4_capacity(rows, g1, g2)
    gates = [g1, g2, g3, g4]
    thin = [g["gate"] for g in gates if g["n_clusters"] < MIN_CLUSTERS]
    return {"gates": gates, "verdict": "PASS" if all(g["pass"] for g in gates)
            else "KILL", "rule46": bust_exposure(rows),
            "n_rows": len(rows), "underpowered_gates": thin}


def report(res: dict) -> str:
    out = [f"P-030 GATE REPORT   rows={res['n_rows']}   "
           f"VERDICT: {res['verdict']}", ""]
    for g in res["gates"]:
        mark = "PASS" if g["pass"] else "KILL"
        v, lo, hi = g["value"], *g["ci95"]
        ci = f"  CI95[{lo:.4g}, {hi:.4g}]" if lo == lo else ""
        out.append(f"  [{mark}] gate {g['gate']} {g['name']:<12} "
                   f"{g['metric']}")
        out.append(f"         value={v:.4g}  threshold={g['threshold']:.4g}"
                   f"{ci}  n={g['n_obs']} clusters={g['n_clusters']}")
        if g.get("caveat"):
            out.append(f"         caveat: {g['caveat']}")
    r46 = res["rule46"]
    if r46["share"] is not None:
        out += ["", f"  Rule 4.6 diagnostic: {r46['bustable']}/{r46['qualifying']}"
                    f" qualifying fills ({r46['share']:.1%}) sit OUTSIDE the "
                    f"No-Cancellation Range and are bustable."]
    if res["underpowered_gates"]:
        out += ["", f"  ⚠ UNDERPOWERED: gates {res['underpowered_gates']} have "
                    f"<{MIN_CLUSTERS} event clusters. Do not act on these -- "
                    f"collect more days first."]
    if res["verdict"] == "KILL":
        out += ["", "  A KILL here retires P-030. Record it in the run queue "
                    "with the failing gate; do not re-run with a widened "
                    "universe or a loosened threshold."]
    return "\n".join(out)


def _self_test() -> int:
    """Synthetic data with a KNOWN answer, so the analyzer is verifiable with
    no venue access. Builds 40 games x 20 snapshots where exactly 10% of
    in-band observations clear the gap and depth is $3,000."""
    rows, t0 = [], 1785200000.0
    for g in range(40):
        for s in range(20):
            gap = 0.05 if s < 2 else 0.01          # 2/20 = 10% qualifying
            rows.append({
                "ts_utc": datetime.utcfromtimestamp(t0 + s * 60).isoformat(),
                "game_id": f"G{g}", "team": "A", "in_spec_band": True,
                "gap": gap, "px_other_ask_size": 6000.0,
                "px_other_ask_prob": 0.5,
                "px_leg_edge_vs_fmv": 0.05, "rule46_ceiling": 0.075,
                "rule46_bustable": False})
    res = run(rows)
    print(report(res))
    g1, g2, g3 = res["gates"][0], res["gates"][1], res["gates"][2]
    ok = True
    for label, got, want in (("depth", g1["value"], 3000.0),
                             ("freq", g2["value"], 0.10),
                             ("survival", g3["value"], 60.0)):
        if abs(got - want) > 1e-6:
            print(f"SELF-TEST FAIL: {label}={got} expected {want}")
            ok = False
    if res["verdict"] != "PASS":
        print(f"SELF-TEST FAIL: verdict={res['verdict']} expected PASS")
        ok = False
    print("\nself-test:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=DEFAULT_DIR)
    ap.add_argument("--json", metavar="PATH")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.self_test:
        return _self_test()
    rows = load_rows(args.data_dir)
    if not rows:
        log.error("no rows in %s -- nothing to analyze. An empty sample is NOT "
                  "a KILL.", args.data_dir)
        return 2
    res = run(rows)
    print(report(res))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(res, fh, indent=2)
        log.info("wrote %s", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
