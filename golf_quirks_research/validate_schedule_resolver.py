"""
golf_quirks_research/validate_schedule_resolver.py
──────────────────────────────────────────────────
Validate ``src/golf_schedule.GolfScheduleResolver`` against ground truth.

For every settled round-leader event (where Kalshi HAS rewritten
``close_time`` to the real early-close stamp), reconstruct what the resolver
would have said and report the error distribution.

The prompt's acceptance test, stated in the terms that matter:

    error e = true_close − predicted_close

    e > 0  ->  the pod posts EARLIER than the validated [12h, 24h] window.
               Realised H at placement = [12+e, 24+e].  Untested, but always
               further from the round.
    e < 0  ->  the pod posts LATER.  Realised H = [12−|e|, 24−|e|], and since
               the measured round span (close − first tee) is 12.5h median,
               |e| > ~0.5h puts quotes INSIDE a live round — the regime
               Phase 2 measured at H=6h and found the edge does not survive.

So the tolerance is asymmetric and the resolver is calibrated to make the
error one-sided positive.  This script measures whether it succeeded.

A settled leaderboard carries tee times for every round, so the live resolver
always picks ``tee_times`` here.  The two fallbacks are therefore evaluated
explicitly rather than left untested — they are the paths that actually run
at listing time, when ESPN has published no tee times yet.

Run:  python3 -m golf_quirks_research.validate_schedule_resolver
"""
from __future__ import annotations

import collections
import json
import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from src.golf_schedule import (LAG_DAY_H, LAG_R1_ANCHOR_H, LAG_TEE_H,
                               TOUR_LEAGUE, GolfScheduleResolver, round_of,
                               tour_of)

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "schedule_probe.json")
OUT = os.path.join(HERE, "schedule_resolver_validation.json")

# The pod's locked window.  Read, never written.
WINDOW_LO_H, WINDOW_HI_H = 12.0, 24.0


def iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def summarize(label: str, vals: List[float], width: int = 26) -> Dict:
    if not vals:
        print(f"  {label:<{width}} n=0")
        return {}
    v = sorted(vals)
    d = {
        "n": len(v), "min": v[0], "max": v[-1],
        "median": statistics.median(v),
        "mean": statistics.fmean(v),
        "n_negative": sum(1 for x in v if x < 0),
        "n_below_-0.5h": sum(1 for x in v if x < -0.5),
    }
    print(f"  {label:<{width}} n={d['n']:<4} min={d['min']:8.2f} "
          f"med={d['median']:7.2f} max={d['max']:8.2f} "
          f"e<0: {d['n_negative']}  e<-0.5h: {d['n_below_-0.5h']}")
    return d


def main() -> None:
    if not os.path.exists(PROBE):
        raise SystemExit("run golf_quirks_research.schedule_probe first")
    rows = json.load(open(PROBE))

    resolver = GolfScheduleResolver()
    out: List[Dict] = []

    print(f"Replaying the resolver over {len(rows)} settled events...\n")
    for r in rows:
        tour, rnd = r.get("tour"), r.get("round")
        if not tour or not rnd:
            continue
        true_close = iso(r["true_close"])
        rec = {
            "event": r["event"], "tour": tour, "round": rnd,
            "competition": r.get("competition"),
            "true_close": r["true_close"],
        }

        # (a) what the LIVE resolver returns, end to end
        rc = resolver.resolve(r.get("competition") or "", r["series"],
                              near=true_close)
        if rc is not None:
            rec["live_source"] = rc.source
            rec["live_pred"] = rc.close_iso
            rec["live_err_h"] = (
                true_close.timestamp() - rc.close_epoch) / 3600.0
        else:
            rec["live_source"] = None

        # (b) each source forced, so the fallbacks are measured too
        if r.get("last_tee_time"):
            p = iso(r["last_tee_time"]) + timedelta(hours=LAG_TEE_H)
            rec["err_tee_h"] = (true_close - p).total_seconds() / 3600.0
        if r.get("resid_r1anchor_h") is not None:
            # resid_r1anchor_h = true − (R1 last tee + (n−1)·24h)
            rec["err_r1_h"] = r["resid_r1anchor_h"] - LAG_R1_ANCHOR_H
        if r.get("espn_start") and tour in LAG_DAY_H:
            p = (iso(r["espn_start"]) + timedelta(days=rnd - 1,
                                                  hours=LAG_DAY_H[tour]))
            rec["err_day_h"] = (true_close - p).total_seconds() / 3600.0
        rec["round_span_h"] = r.get("round_span_h")
        out.append(rec)

    print("── (a) LIVE resolver, end to end ──")
    live = [r for r in out if r.get("live_err_h") is not None]
    print(f"  resolved {len(live)}/{len(out)} events "
          f"({len(out) - len(live)} would NOT have quoted — fail-closed)")
    bysrc = collections.Counter(r["live_source"] for r in live)
    print(f"  source mix: {dict(bysrc)}")
    stats = {"live": summarize("ALL", [r["live_err_h"] for r in live])}
    bt = collections.defaultdict(list)
    for r in live:
        bt[r["tour"]].append(r["live_err_h"])
    for t, v in sorted(bt.items()):
        summarize(t, v)

    print("\n── (b) each source forced ──")
    for key, label in (("err_tee_h", "per-round tee times"),
                       ("err_r1_h", "R1 tee anchor + (n−1)d"),
                       ("err_day_h", "tour day offset")):
        vals = [r[key] for r in out if r.get(key) is not None]
        stats[key] = summarize(label, vals)
        bt = collections.defaultdict(list)
        for r in out:
            if r.get(key) is not None:
                bt[r["tour"]].append(r[key])
        for t, v in sorted(bt.items()):
            summarize(f"    {t}", v)

    print("\n── What the pod would actually have done ──")
    print("  realised H at placement = [12+e, 24+e] hours before the TRUE close")
    for key, label in (("err_tee_h", "per-round tee times"),
                       ("err_r1_h", "R1 tee anchor"),
                       ("err_day_h", "tour day offset")):
        vals = [r[key] for r in out if r.get(key) is not None]
        if not vals:
            continue
        lo = [WINDOW_LO_H + e for e in vals]
        hi = [WINDOW_HI_H + e for e in vals]
        into_round = sum(1 for r in out
                         if r.get(key) is not None
                         and r.get("round_span_h") is not None
                         and WINDOW_LO_H + r[key] < r["round_span_h"])
        n = len(vals)
        print(f"  {label:<24} latest placement H: med {statistics.median(lo):6.2f} "
              f"min {min(lo):6.2f} | earliest H: med {statistics.median(hi):6.2f} "
              f"max {max(hi):7.2f} | placements reaching into a live round: "
              f"{into_round}/{n}")

    spans = [r["round_span_h"] for r in out if r.get("round_span_h")]
    if spans:
        print(f"\n  round span (close − first tee): n={len(spans)} "
              f"med={statistics.median(spans):.2f}h min={min(spans):.2f}h "
              f"— the 12h window edge sits at the first tee")

    json.dump({"rows": out, "stats": stats}, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
