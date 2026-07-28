#!/usr/bin/env python3
"""
inplay_research/gate1_redesign.py
─────────────────────────────────
P-018 gate #1, redesigned. Implements EXACTLY the three tests pre-registered
in ``research/P018_GATE1_REDESIGN.md``, which was committed before this file
existed — check the git history.

  1R-A  PLACEBO (primary kill). The identical fade quote construction rested
        at NON-EVENT ticks with a comparable |fair - mid|. One difference
        from the fade arm: no surprising event just happened.
  1R-B  Mechanical decomposition: realized minus model-PREDICTED edge.
        Cannot kill; reframes.
  1R-C  Dose-response in surprise WITHIN terciles of predicted edge, i.e.
        with quote distance held fixed.

Plus one descriptive by-product, explicitly NOT a gate: the surprise-bucket
table with `surprise_hi` widened so the low buckets populate.

Both arms are replayed by the SAME code path in the SAME run, per §5 of the
pre-registration. Clustering is on the GAME (`ticker.split("-")[1]`), never
the ticker — see `research/P018_DECISION_RULE.md` §2.

    python3 inplay_research/gate1_redesign.py --out gate1_redesign.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_inplay_fade as bt  # noqa: E402
from src.inplay_surprise import (  # noqa: E402
    Regime, SurpriseParams, SurpriseState, build_quote, fills_through,
    select_regime, settle_pnl, update_on_event,
)
from src.mlb_win_prob import home_win_probability  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# Pre-registered in P018_GATE1_REDESIGN.md §5.
MIN_PLACEBO_CLUSTERS = 30
# §2: KILL if placebo >= this fraction of the fade AND the CIs overlap.
PLACEBO_KILL_FRAC = 0.50
# Descriptive by-product only.
WIDE_SURPRISE_BUCKETS = [(0.0, 0.02), (0.02, 0.06), (0.06, 0.12), (0.12, 1.01)]


def game_of(ticker: str) -> str:
    """The CLUSTER. `KXMLBGAME-26JUL211840LADPHI-LAD` -> `26JUL211840LADPHI`.

    Both tickers of a game share this segment. They resolve on the same event
    with perfectly anti-correlated outcomes, so they are ONE observation.
    """
    parts = ticker.split("-")
    return parts[1] if len(parts) > 1 else ticker


def cluster_bootstrap(per: Sequence[Tuple[str, float]], n_boot: int = 5000,
                      seed: int = 12345) -> Dict[str, Any]:
    """Mean, 95% CI and z, resampling GAMES with replacement."""
    if not per:
        return {"n": 0, "clusters": 0, "mean": None, "lo": None, "hi": None,
                "se": None, "z": None}
    by: Dict[str, List[float]] = defaultdict(list)
    for g, v in per:
        by[g].append(v)
    keys = list(by.keys())
    mean = sum(v for _, v in per) / len(per)
    if len(keys) < 2:
        return {"n": len(per), "clusters": len(keys), "mean": mean,
                "lo": None, "hi": None, "se": None, "z": None}
    rng = random.Random(seed)
    means: List[float] = []
    for _ in range(n_boot):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by[keys[rng.randrange(len(keys))]])
        if pool:
            means.append(sum(pool) / len(pool))
    means.sort()
    m2 = sum(means) / len(means)
    se = (sum((x - m2) ** 2 for x in means) / max(len(means) - 1, 1)) ** 0.5
    return {"n": len(per), "clusters": len(keys), "mean": mean,
            "lo": means[int(0.025 * len(means))],
            "hi": means[int(0.975 * len(means))],
            "se": se, "z": (mean / se) if se else None}


def paired_diff(a: Sequence[Tuple[str, float]], b: Sequence[Tuple[str, float]],
                n_boot: int = 5000, seed: int = 999) -> Dict[str, Any]:
    """Game-clustered CI for mean(a) - mean(b), resampling the SAME games in
    both arms so the pairing is preserved."""
    ga: Dict[str, List[float]] = defaultdict(list)
    gb: Dict[str, List[float]] = defaultdict(list)
    for g, v in a:
        ga[g].append(v)
    for g, v in b:
        gb[g].append(v)
    keys = sorted(set(ga) | set(gb))
    if len(keys) < 2:
        return {"clusters": len(keys), "diff": None, "lo": None, "hi": None}
    obs = ((sum(v for _, v in a) / len(a)) if a else 0.0) - \
          ((sum(v for _, v in b) / len(b)) if b else 0.0)
    rng = random.Random(seed)
    diffs: List[float] = []
    for _ in range(n_boot):
        pa: List[float] = []
        pb: List[float] = []
        for _ in range(len(keys)):
            k = keys[rng.randrange(len(keys))]
            pa.extend(ga.get(k, []))
            pb.extend(gb.get(k, []))
        if pa and pb:
            diffs.append(sum(pa) / len(pa) - sum(pb) / len(pb))
    if len(diffs) < 100:
        return {"clusters": len(keys), "diff": obs, "lo": None, "hi": None}
    diffs.sort()
    return {"clusters": len(keys), "diff": obs,
            "lo": diffs[int(0.025 * len(diffs))],
            "hi": diffs[int(0.975 * len(diffs))]}


# ══════════════════════════════════════════════════════════════════════
# Replay — fade arm and placebo arm, same walk, same code
# ══════════════════════════════════════════════════════════════════════

def replay_both(ticker: str, ticks: List[Dict[str, Any]],
                timeline, params: SurpriseParams, series: str,
                placebo_gap_s: float, rng: random.Random
                ) -> Tuple[List[Dict], List[Dict]]:
    """One walk of one market producing BOTH arms.

    Mirrors ``backtest_inplay_fade.replay_market`` exactly for the fade arm —
    same detection, same regime call, same quote build, same fill rule — and
    adds a second, independent resting quote for the placebo arm at ticks
    that are far from any event.
    """
    st = SurpriseState()
    pregame = None
    fade_fills: List[Dict[str, Any]] = []
    plac_fills: List[Dict[str, Any]] = []
    open_quote = None
    plac_quote = None          # (side, px, size, start_ts, pred_edge)
    result = 1.0 if timeline.final_home_won else 0.0
    game = game_of(ticker)

    for t in ticks:
        epoch = t.get("epoch")
        mid = t.get("mid")
        if epoch is None:
            continue
        snap = timeline.state_at(epoch)
        if snap is None:
            continue
        if pregame is None:
            pregame = mid if mid is not None else 0.54
        fv = home_win_probability(snap, pregame)
        state_key = (f"{snap.away_score}-{snap.home_score}|i{snap.inning}"
                     f"{'T' if snap.is_top else 'B'}|o{snap.outs}"
                     f"|b{int(snap.on_first)}{int(snap.on_second)}"
                     f"{int(snap.on_third)}")

        # ── fills against this tick's prints ──
        for label, q, sink in (("fade", open_quote, fade_fills),
                               ("placebo", plac_quote, plac_fills)):
            if q is None:
                continue
            side, px, size, ev_ts, surp, underr, pred = q
            for pr in (t.get("trades") or []):
                got = fills_through(side, px, size, pr.get("yes_price"),
                                    pr.get("count") or 0)
                if got <= 0:
                    continue
                sd = "buy" if side == "bid" else "sell"
                sink.append({
                    "ticker": ticker, "game": game, "arm": label, "side": sd,
                    "price": px, "qty": got, "surprise": surp,
                    "underreaction": underr,
                    "secs_since_event": (pr.get("epoch") or epoch) - ev_ts,
                    "predicted_edge": pred,
                    "pnl": settle_pnl(sd, px, result, got, series),
                })
                size -= got
            if label == "fade":
                open_quote = ((side, px, size, ev_ts, surp, underr, pred)
                              if size > 0 else None)
            else:
                plac_quote = ((side, px, size, ev_ts, surp, underr, pred)
                              if size > 0 else None)

        # ── event detection + fade arm (identical to the committed harness) ──
        update_on_event(st, state_key, fv.home_wp, mid, now=epoch)
        regime = select_regime(st, now=epoch, params=params,
                               killed=False, event_risk=False)
        if regime is Regime.FADE and st.last_event is not None:
            secs = epoch - (st.event_ts or epoch)
            spec = build_quote(Regime.FADE, fv.home_wp, mid, fv.sensitivity,
                               params, secs_since_event=secs,
                               underreaction=st.last_event.underreaction)
            if spec.bid is not None:
                open_quote = ("bid", spec.bid, spec.bid_size, st.event_ts,
                              st.last_event.surprise,
                              st.last_event.underreaction,
                              abs(fv.home_wp - spec.bid))
            elif spec.ask is not None:
                open_quote = ("ask", spec.ask, spec.ask_size, st.event_ts,
                              st.last_event.surprise,
                              st.last_event.underreaction,
                              abs(fv.home_wp - spec.ask))
        elif regime is not Regime.FADE:
            open_quote = None

        # ── placebo arm: the SAME construction at a NON-event tick ──
        # Eligible iff no observed event within `placebo_gap_s` (so the fade
        # window and its neighbourhood are excluded outright) and the model
        # disagrees with the market by at least `min_underreaction`, matching
        # the fade arm on the mechanical driver of the quote distance.
        since_ev = (epoch - st.event_ts) if st.event_ts is not None else 1e9
        eligible = (since_ev >= placebo_gap_s and mid is not None
                    and abs(fv.home_wp - mid) >= params.min_underreaction)
        if plac_quote is None and eligible:
            pseudo = fv.home_wp - mid          # stands in for `underreaction`
            spec = build_quote(Regime.FADE, fv.home_wp, mid, fv.sensitivity,
                               params, secs_since_event=0.0,
                               underreaction=pseudo)
            if spec.bid is not None:
                plac_quote = ("bid", spec.bid, spec.bid_size, epoch,
                              abs(pseudo), pseudo, abs(fv.home_wp - spec.bid))
            elif spec.ask is not None:
                plac_quote = ("ask", spec.ask, spec.ask_size, epoch,
                              abs(pseudo), pseudo, abs(fv.home_wp - spec.ask))
        elif plac_quote is not None:
            # same 240s life and taper as the fade arm
            if epoch - plac_quote[3] > params.fade_window_s:
                plac_quote = None

    return fade_fills, plac_fills


def per_ct(fills: Sequence[Dict[str, Any]]) -> List[Tuple[str, float]]:
    return [(f["game"], f["pnl"] / max(f["qty"], 1e-9)) for f in fills]


def c(x: Optional[float]) -> str:
    return "—" if x is None else f"{100.0 * x:+.2f}"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="data/book_capture")
    ap.add_argument("--series", default="KXMLBGAME")
    ap.add_argument("--placebo-gap-s", type=float, default=600.0,
                    help="a placebo tick must be this far from any observed "
                         "event (>= 2.5x the 240s fade window)")
    ap.add_argument("--out", default="gate1_redesign.json")
    ap.add_argument("--surprise-hi", type=float, default=None,
                    help="override surprise_hi for the DESCRIPTIVE widened "
                         "run only (P018_GATE1_REDESIGN.md §3). This is not a "
                         "gate: §0 shows the resulting table slopes upward by "
                         "arithmetic and therefore cannot kill.")
    args = ap.parse_args(argv)

    params = SurpriseParams.from_config(
        json.load(open(os.path.join(HERE, "p018_params.json"))))
    if args.surprise_hi is not None:
        params.surprise_hi = args.surprise_hi
        print(f"*** DESCRIPTIVE RUN: surprise_hi overridden to "
              f"{params.surprise_hi} — NOT a gate ***")

    ticks = bt.load_book_ticks(args.data_dir)
    by_ticker: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in ticks:
        tk = t.get("ticker") or ""
        if tk.startswith(args.series):
            by_ticker[tk].append(t)
    print(f"markets: {len(by_ticker)}  games: "
          f"{len({game_of(t) for t in by_ticker})}")

    rng = random.Random(4242)
    fade: List[Dict[str, Any]] = []
    plac: List[Dict[str, Any]] = []
    n_tl = 0
    import requests
    sess = requests.Session()
    pk_cache: Dict[str, Any] = {}
    for i, (tk, tks) in enumerate(sorted(by_ticker.items())):
        tks.sort(key=lambda x: x.get("epoch") or 0)
        gk = game_of(tk)
        if gk not in pk_cache:
            pk_cache[gk] = bt.resolve_game_pk(tk, sess)
        pk = pk_cache[gk]
        if pk is None:
            continue
        tl = bt.MlbStateTimeline.from_statsapi(pk, sess)
        if tl is None:
            continue
        n_tl += 1
        f, p = replay_both(tk, tks, tl, params, args.series,
                           args.placebo_gap_s, rng)
        fade.extend(f)
        plac.extend(p)
        if (i + 1) % 40 == 0:
            print(f"  ...{i+1}/{len(by_ticker)} markets, "
                  f"{len(fade)} fade / {len(plac)} placebo fills")

    print(f"\ntimelines resolved: {n_tl}")
    fade_pc, plac_pc = per_ct(fade), per_ct(plac)
    A_f = cluster_bootstrap(fade_pc)
    A_p = cluster_bootstrap(plac_pc)
    diff = paired_diff(fade_pc, plac_pc)

    print("\n" + "=" * 70)
    print("1R-A  PLACEBO — the primary kill")
    print("=" * 70)
    for lbl, r in (("fade", A_f), ("placebo", A_p)):
        print(f"  {lbl:8s} fills {r['n']:6d}  games {r['clusters']:4d}  "
              f"net {c(r['mean'])}c  CI [{c(r['lo'])}, {c(r['hi'])}]  "
              f"z {r['z'] if r['z'] is None else round(r['z'], 2)}")
    print(f"  paired difference (fade - placebo): {c(diff['diff'])}c  "
          f"CI [{c(diff['lo'])}, {c(diff['hi'])}]")

    # Pre-registered decision, applied mechanically.
    verdict_a, why_a = "NO DECISION", ""
    if A_p["clusters"] < MIN_PLACEBO_CLUSTERS and A_p["mean"] is not None:
        pass  # under-powered for PASS; KILL still reachable below
    if A_f["mean"] and A_p["mean"] is not None and A_f["mean"] > 0:
        frac = A_p["mean"] / A_f["mean"]
        overlap = (A_p["hi"] is not None and A_f["lo"] is not None
                   and A_p["hi"] >= A_f["lo"] and A_f["hi"] >= A_p["lo"])
        if frac >= PLACEBO_KILL_FRAC and overlap:
            verdict_a = "KILL"
            why_a = (f"placebo is {frac:.0%} of the fade "
                     f"(>= {PLACEBO_KILL_FRAC:.0%}) and the CIs overlap")
    if verdict_a == "NO DECISION" and diff["lo"] is not None and diff["lo"] > 0:
        if A_p["clusters"] >= MIN_PLACEBO_CLUSTERS:
            verdict_a, why_a = "PASS", "difference CI excludes 0, positive"
        else:
            why_a = (f"difference CI excludes 0 but only {A_p['clusters']} "
                     f"placebo clusters (< {MIN_PLACEBO_CLUSTERS}) — "
                     f"under-powered for PASS (§5)")
    if not why_a:
        why_a = "neither the KILL nor the PASS condition is met"
    print(f"  >>> 1R-A VERDICT: {verdict_a} — {why_a}")

    print("\n" + "=" * 70)
    print("1R-B  realized minus model-PREDICTED edge (cannot kill)")
    print("=" * 70)
    resid = [(f["game"], (f["pnl"] / max(f["qty"], 1e-9)) - f["predicted_edge"])
             for f in fade]
    pred = [(f["game"], f["predicted_edge"]) for f in fade]
    B_r, B_p = cluster_bootstrap(resid), cluster_bootstrap(pred)
    print(f"  model-predicted  {c(B_p['mean'])}c")
    print(f"  realized         {c(A_f['mean'])}c")
    print(f"  residual         {c(B_r['mean'])}c  "
          f"CI [{c(B_r['lo'])}, {c(B_r['hi'])}]")

    print("\n" + "=" * 70)
    print("1R-C  surprise dose-response WITHIN terciles of predicted edge")
    print("=" * 70)
    pe = sorted(f["predicted_edge"] for f in fade)
    cuts = ((pe[len(pe) // 3], pe[2 * len(pe) // 3]) if len(pe) >= 3
            else (0.0, 0.0))
    strata: Dict[str, Any] = {}
    for si, (slo, shi) in enumerate(((-1e9, cuts[0]), cuts, (cuts[1], 1e9))):
        sub = [f for f in fade if slo <= f["predicted_edge"] < shi]
        row: Dict[str, Any] = {"n": len(sub), "range": [slo, shi], "buckets": {}}
        print(f"  stratum {si+1}  predicted edge [{c(slo) if si else '  -inf'},"
              f" {c(shi)}]c   n={len(sub)}")
        for blo, bhi in WIDE_SURPRISE_BUCKETS:
            fs = [f for f in sub if blo <= f["surprise"] < bhi]
            r = cluster_bootstrap(per_ct(fs))
            row["buckets"][f"[{blo},{bhi})"] = r
            print(f"      surprise [{blo:.2f},{bhi:.2f})  n {r['n']:5d}  "
                  f"games {r['clusters']:4d}  net {c(r['mean'])}c  "
                  f"CI [{c(r['lo'])}, {c(r['hi'])}]")
        strata[f"stratum_{si+1}"] = row

    out = {
        "params": {"surprise_hi": params.surprise_hi,
                   "min_underreaction": params.min_underreaction,
                   "fade_window_s": params.fade_window_s,
                   "fade_edge_frac": params.fade_edge_frac,
                   "placebo_gap_s": args.placebo_gap_s},
        "markets": len(by_ticker), "timelines": n_tl,
        "gate_1R_A": {"fade": A_f, "placebo": A_p, "paired_diff": diff,
                      "verdict": verdict_a, "why": why_a},
        "gate_1R_B": {"predicted": B_p, "residual": B_r},
        "gate_1R_C": strata,
    }
    path = args.out if os.path.isabs(args.out) else os.path.join(HERE, args.out)
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
