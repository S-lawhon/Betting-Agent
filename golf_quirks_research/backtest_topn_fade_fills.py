"""
golf_quirks_research/backtest_topn_fade_fills.py
────────────────────────────────────────────────
P-023c Phase 2 — maker-fill realism for the top-N FADE.

Phase 1 (`backtest_topn_fade.py`) reproduces the published P-023a buy-side
cells exactly and then decomposes the mirrored fade. On the liquid control the
raw +3.2c/ct fade gross is fully accounted for by an anchor print-side bias
(+1.2c) plus half-spread (+1.3c) plus the taker fee (+0.9c) — i.e. the TAKER
route is dead on arrival, and the settlement mechanic (ties pay YES in FULL,
YES/N = 1.07-1.46) runs AGAINST the fade rather than for it.

That leaves exactly one route worth measuring: a MAKER fade — rest a YES offer
and wait to be lifted, fee 0 on these `quadratic` series. Phase 1 says that
offer earns +3.8c/ct on the control IF every posted quote filled at its price.
This script measures what it actually earns once only the prints that come to
you fill you.

The quantity that decides it is `E[settle | filled]` vs `E[settle | posted]`.
On make-cut (Task 7) that gap was 0.361 vs 0.558 and it flipped +6.7c into
-13.8c. A resting YES OFFER on a market that trades through its own
determining period is a one-way option written to the informed side: you get
lifted precisely on the names climbing the leaderboard. This cohort sits on
the opposite side of the book from make-cut, so the sign is measured, never
assumed.

Anchor contemporaneity (the Task-7 lesson, now mandatory): full-tournament
top-N candles are DAILY, so the "48h anchor" is a median 68h-old price — 19.7h
stale, 96% of markets. Headline rows therefore post at the moment the anchor
was observed (`post_at_anchor=True`). The literal post-at-T rows are retained
as `_staleT` so the artifact is visible rather than assumed away.

Usage
  python3 backtest_topn_fade_fills.py
  python3 backtest_topn_fade_fills.py --skip-validate
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import quirks_common as qc  # noqa: E402
import backtest_fade_fills as fade  # noqa: E402
import backtest_topn_fade as p1  # noqa: E402

ROUND_DIR = os.path.join(qc.DATA, "topn_round_trades")
FULL_DIR = os.path.join(qc.DATA, "topn_full_trades")

# cohort -> dir, series, anchor H, grid of (offset, cancel-at hours-before-close)
COHORTS: Dict[str, Dict[str, Any]] = {
    "pga_top1020": dict(dir=FULL_DIR, series=("KXPGATOP10", "KXPGATOP20"),
                        h=48.0, label="CONTROL PGA top-10/20",
                        cancels=(0.0, 36.0, 24.0)),
    "pga_top5": dict(dir=FULL_DIR, series=("KXPGATOP5",), h=48.0,
                     label="PGA top-5", cancels=(0.0, 36.0)),
    "pga_top40": dict(dir=FULL_DIR, series=("KXPGATOP40",), h=48.0,
                      label="PGA top-40", cancels=(0.0, 36.0)),
    "r1top5": dict(dir=ROUND_DIR, series=("KXPGAR1TOP5",), h=12.0,
                   label="R1 top-5", cancels=(0.0, 6.0)),
    "r1top10": dict(dir=ROUND_DIR, series=("KXPGAR1TOP10",), h=12.0,
                    label="R1 top-10", cancels=(0.0, 6.0)),
    "r2top10": dict(dir=ROUND_DIR, series=("KXPGAR2TOP10",), h=12.0,
                    label="R2 top-10", cancels=(0.0, 6.0)),
    "r3top10": dict(dir=ROUND_DIR, series=("KXPGAR3TOP10",), h=12.0,
                    label="R3 top-10", cancels=(0.0, 6.0)),
}

OFFSETS = (0.00, 0.02, 0.04)
QUOTE_SIZE = 25.0        # research cap, NOT a live sizing recommendation
GATE_NET = 0.02


def load_cohort(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    want = set(cfg["series"])
    return [r for r in qc.load_trade_recs(cfg["dir"])
            if qc.series_of(r["ticker"]) in want]


def run(recs: Sequence[Dict[str, Any]], cfg: Dict[str, Any],
        quote_size: float) -> Dict[str, Any]:
    h = cfg["h"]
    out: Dict[str, Any] = {}
    for off in OFFSETS:
        for we in cfg["cancels"]:
            key = f"H{h:g}_off+{off:.2f}" + (f"_cancel{we:g}h" if we else "")
            out[key] = qc.replay(recs, "sell_yes", h, off,
                                 quote_size=quote_size, window_end_h=we,
                                 post_at_anchor=True)
    # literal post-at-T (stale anchor) contrast at the headline offset
    out[f"H{h:g}_off+0.02_staleT"] = qc.replay(
        recs, "sell_yes", h, 0.02, quote_size=quote_size, post_at_anchor=False)
    # friendlier at-or-through fill convention at the headline offset
    out[f"H{h:g}_off+0.02_atOrThrough"] = qc.replay(
        recs, "sell_yes", h, 0.02, quote_size=quote_size, strict=False,
        post_at_anchor=True)
    return out


def verdict(row: Dict[str, Any]) -> str:
    net, lo, hi = (row.get("net_per_contract"), row.get("ci95_lo"),
                   row.get("ci95_hi"))
    if net is None or lo is None or hi is None:
        return "NO DECISION (insufficient data)"
    if hi < 0:
        return "KILL"
    if net >= GATE_NET and lo > 0:
        return "ADVANCE"
    if net <= 0:
        return "KILL"
    return "MARGINAL"


def fmt(res: Dict[str, Any]) -> str:
    hdr = (f"{'setting':<30} {'posted':>7} {'filled':>7} {'fill%':>6} "
           f"{'cts':>7} {'net c/ct':>9} {'95% CI':>18} {'trn':>4} {'+trn':>5} "
           f"{'E[s|post]':>10} {'E[s|fill]':>10} {'naive c':>8}")
    lines = [hdr, "-" * len(hdr)]
    for k, v in res.items():
        if not v["posted_markets"] or not v["contracts_filled"]:
            lines.append(f"{k:<30} {v['posted_markets']:>7} (no fills)")
            continue
        ci = ("[  n/a (1 tourn) ]" if v["ci95_lo"] is None
              else f"[{v['ci95_lo'] * 100:+.1f}, {v['ci95_hi'] * 100:+.1f}]")
        lines.append(
            f"{k:<30} {v['posted_markets']:>7} {v['filled_markets']:>7} "
            f"{(v['fill_rate'] or 0) * 100:>5.0f}% {v['contracts_filled']:>7.0f} "
            f"{v['net_per_contract'] * 100:>+9.1f} {ci:>18} "
            f"{v['n_fill_tournaments']:>4} {v['tournaments_positive']:>5} "
            f"{v['e_settle_posted']:>10.3f} {v['e_settle_filled']:>10.3f} "
            f"{(v['naive_edge_if_all_filled'] or 0) * 100:>+8.1f}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="topn_fade_fill_results.json")
    ap.add_argument("--quote-size", type=float, default=QUOTE_SIZE)
    ap.add_argument("--only", default=None, help="comma-separated cohort keys")
    ap.add_argument("--skip-validate", action="store_true")
    args = ap.parse_args(argv)

    if not args.skip_validate:
        print("=== harness validation vs published P-022 Phase 2 ===")
        if not fade.validate(fade.load()):
            print("\nABORT: harness does not reproduce P-022. Trust nothing.")
            return 1
        print()

    only = set(args.only.split(",")) if args.only else None
    payload: Dict[str, Any] = {
        "generated": "backtest_topn_fade_fills.py",
        "quote_size": args.quote_size,
        "side": "sell_yes (maker fade: rest a YES offer at anchor + offset)",
        "maker_fee": 0.0,
        "maker_fee_source": "live GET /series?category=Sports 2026-07-26: "
                            "KXPGATOP5/10/20/40 and KXPGAR{1,2,3}TOP{5,10,20} "
                            "all fee_type=quadratic -> maker fee 0. NOTE "
                            "src/kalshi_fees.py is MISSING the round-based "
                            "KXPGAR*TOP* prefixes and so charges a maker fee "
                            "on them (4th recorded drift).",
        "cohorts": {},
    }

    for key, cfg in COHORTS.items():
        if only and key not in only:
            continue
        recs = load_cohort(cfg)
        print(f"=== {cfg['label']} [{key}] anchor H={cfg['h']:g}h ===")
        if not recs:
            print(f"  no cached ticks in {cfg['dir']} — run pull_trades.py\n")
            payload["cohorts"][key] = {"label": cfg["label"], "universe": None}
            continue
        tourns = sorted({r["tournament"] for r in recs})
        prints_ = sum(len(r["trades"]) for r in recs)
        lifts = sum(1 for r in recs for t in r["trades"]
                    if t.get("taker_side") == "yes")
        print(f"  {len(recs)} markets, {len(tourns)} tournaments, "
              f"{prints_} prints ({lifts} taker=yes lifts = "
              f"{100 * lifts / prints_ if prints_ else 0:.0f}%)")
        res = run(recs, cfg, args.quote_size)
        print(fmt(res))
        head = res[f"H{cfg['h']:g}_off+0.02"]
        v = verdict(head)
        ent = [(e, d["net_per_contract"], d["contracts"])
               for e, d in head["per_tournament"].items()]
        l = qc.leave_one_out(ent)
        print(f"  headline (offset +0.02, contemporaneous post): {v}")
        if l:
            print(f"  leave-one-out [{min(l.values()) * 100:+.1f}, "
                  f"{max(l.values()) * 100:+.1f}] c/ct "
                  f"({sum(1 for x in l.values() if x > 0)}/{len(l)} positive)")
        print(f"  capacity: {head['contracts_filled']:.0f} contracts, "
              f"${head['collateral_usd']:.0f} collateral, "
              f"${head['pnl_usd']:+.0f} P&L "
              f"({(head['return_on_collateral'] or 0) * 100:+.1f}% on collateral) "
              f"over {len(tourns)} tournaments")
        print(f"  worst single market: {head['worst_market']}")
        print()
        payload["cohorts"][key] = {
            "label": cfg["label"], "anchor_h": cfg["h"],
            "n_markets": len(recs), "n_tournaments": len(tourns),
            "n_prints": prints_, "pct_taker_yes": round(
                100 * lifts / prints_, 1) if prints_ else None,
            "tournaments": tourns, "grid": res,
            "headline_key": f"H{cfg['h']:g}_off+0.02", "verdict": v,
            "leave_one_out": l,
        }

    out = (args.out if os.path.isabs(args.out)
           else os.path.join(qc.HERE, args.out))
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
