"""
golf_quirks_research/backtest_makecut_fills.py
──────────────────────────────────────────────
P-023 Phase 2 — make-the-cut, maker-fill realism replay.

Phase 1 (REPORT_Golf_Quirks_2026-07.md §4.2) found the one unpriced residual
in the golf quirk basket: PGA make-cut bubble names (48h anchor in 40-60c)
realize ~56% vs a ~51c anchor, a +4.8c/ct buy edge over 10 tournaments with a
tournament-clustered CI excluding zero. The mechanic is three stacked YES
inflators written into GOLFCUT.pdf — cut-line ties ("top 65 AND TIES"), MDF,
and post-cut WD/DQ all resolve YES.

Phase 2 asks the only question that matters next: a maker RESTING A YES BID
only trades when a seller hits it, and on a make-cut market sellers hit
disproportionately on names whose R1 went badly. That is the mirror image of
P-022's adverse selection, and it is the failure mode that would turn +4.8c
into nothing.

Mirrors `backtest_fade_fills.py`, flipped:
    rest a YES BID at anchor - offset
    fill ONLY on a taker_side="no" print strictly THROUGH the bid
    PnL/ct = settlement_value - fill_px,   maker fee 0 (quadratic series)
    CIs bootstrap-clustered by TOURNAMENT

Cohorts are reported SEPARATELY and never pooled: PGA make-cut is the
10-tournament ADVANCE candidate; DP World make-cut and LIV top-N were only
MARGINAL in Phase 1 (4 and 2 tournaments) and pooling them would launder
their thinness into the headline.

Settlement: `settlement_value_dollars` is read directly from the cache, NOT
via src/kalshi_golf_settler.py — that settler still treats result="scalar"
as a withdrawal void, and make-cut markets do produce scalar rows.

Usage
  python3 backtest_makecut_fills.py --out makecut_fill_results.json
  python3 backtest_makecut_fills.py --skip-validate     # not recommended
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))   # repo root, for src.kalshi_fees
import quirks_common as qc  # noqa: E402
import backtest_fade_fills as fade  # noqa: E402

MAKECUT_DIR = os.path.join(qc.DATA, "makecut_trades")
LIVTOPN_DIR = os.path.join(qc.DATA, "livtopn_trades")

COHORTS: Dict[str, Dict[str, Any]] = {
    "pga_makecut": {"dir": MAKECUT_DIR, "series": qc.MAKECUT_SERIES_PGA,
                    "label": "PGA make-cut (Phase-1 ADVANCE)"},
    "dpw_makecut": {"dir": MAKECUT_DIR, "series": qc.MAKECUT_SERIES_DPW,
                    "label": "DP World make-cut (Phase-1 MARGINAL)"},
    "liv_topn": {"dir": LIVTOPN_DIR, "series": qc.LIV_TOPN_SERIES,
                 "label": "LIV top-N (Phase-1 MARGINAL)"},
}

# (H hours before close, offset below anchor, quote lifetime end in hours
#  before close, post_at_anchor).
#
# Timing, from the observed close times: a PGA make-cut market closes when
# that player's cut status is settled, ~Friday evening. So close-48h is
# Wednesday evening (pre-tournament), close-36h is roughly R1 tee-off, and
# close-24h is roughly the end of R1. `cancel36h` is therefore the only fill
# window that is entirely pre-tournament and information-clean.
#
# post_at_anchor=True is the honest execution model on this surface: make-cut
# books only print during tour business hours, so the "48h anchor" is on
# median a 68h-old price. Posting at close-48h off that price quotes 20h
# stale. See quirks_common.replay().
GRID: Tuple[Tuple[float, float, float, bool], ...] = (
    (48.0, 0.00, 0.0, True), (48.0, 0.02, 0.0, True), (48.0, 0.04, 0.0, True),
    (48.0, 0.02, 36.0, True), (48.0, 0.02, 24.0, True), (48.0, 0.00, 36.0, True),
    (24.0, 0.02, 0.0, True),
    # Literal published-P-022 convention (post at close-H off a stale anchor),
    # retained so the staleness effect is visible rather than assumed away.
    (48.0, 0.00, 0.0, False), (48.0, 0.02, 0.0, False), (48.0, 0.04, 0.0, False),
    (48.0, 0.02, 36.0, False), (48.0, 0.02, 24.0, False),
    (24.0, 0.00, 0.0, False), (24.0, 0.02, 0.0, False),
)

GATE_NET = 0.02          # ADVANCE needs >= +2c/ct ...
QUOTE_SIZE = 25.0        # ... research cap, NOT a live sizing recommendation


def load_cohort(name: str) -> List[Dict[str, Any]]:
    cfg = COHORTS[name]
    want = set(cfg["series"])
    return [r for r in qc.load_trade_recs(cfg["dir"])
            if qc.series_of(r["ticker"]) in want]


def describe(recs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    res = Counter(r.get("result") for r in recs)
    svs = [qc.settlement_value(r) for r in recs]
    svs = [s for s in svs if s is not None]
    tourns = sorted({r["tournament"] for r in recs})
    return {
        "n_markets": len(recs),
        "n_tournaments": len(tourns),
        "tournaments": tourns,
        "results": dict(res),
        "mean_settlement_value": qc.r4(sum(svs) / len(svs)) if svs else None,
        "n_prints": sum(len(r["trades"]) for r in recs),
    }


def run_cohort(recs: Sequence[Dict[str, Any]],
               quote_size: float = QUOTE_SIZE) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for h, off, we, paa in GRID:
        key = (f"H{h:g}_off{off:.2f}" + (f"_cancel{we:g}h" if we else "")
               + ("" if paa else "_staleT"))
        out[key] = qc.replay(recs, "buy_yes", h, off, quote_size=quote_size,
                             window_end_h=we, post_at_anchor=paa)
    # friendlier at-or-through variant at the headline setting, for contrast
    out["H48_off0.02_atOrThrough"] = qc.replay(
        recs, "buy_yes", 48.0, 0.02, quote_size=quote_size, strict=False,
        post_at_anchor=True)
    return out


def taker_reference(recs: Sequence[Dict[str, Any]],
                    hours: float = 48.0) -> Dict[str, Any]:
    """What a TAKER would net buying YES at the 48h ask, fee included.

    Phase 1 measured its +4.8c against the anchor (last trade / tight mid).
    Nobody transacts at a mid. This restates the same population at the price
    a buyer actually pays — the ask on the anchor candle — net of the taker
    fee 0.07*P*(1-P). The gap between the two is the execution cost Phase 1
    did not charge, and it is reported so the maker KILL is not mistaken for
    a statement about the taker route.
    """
    from src.kalshi_fees import fee_per_contract  # local: keeps import cheap
    entries: List[Tuple[str, float, float]] = []
    asks: List[float] = []
    spreads: List[float] = []
    for r in recs:
        close = qc.iso_epoch(r["close_time"])
        if close is None:
            continue
        target = close - hours * 3600.0
        best = None
        for c in r["_candles"].get("candles") or []:
            e = c.get("end_period_ts")
            if e is None or e > target:
                continue
            bid = qc.fnum((c.get("yes_bid") or {}).get("close_dollars"))
            ask = qc.fnum((c.get("yes_ask") or {}).get("close_dollars"))
            if bid is None or ask is None:
                continue
            if bid <= 0 or ask >= 1 or ask < bid or (ask - bid) > qc.ANCHOR_MAX_SPREAD:
                continue
            if best is None or e > best[0]:
                best = (e, ask, bid)
        if best is None:
            continue
        _, ask, bid = best
        sv = qc.settlement_value(r)
        if sv is None:
            continue
        entries.append((r["tournament"],
                        sv - ask - fee_per_contract(ask, maker=False), 1.0))
        asks.append(ask)
        spreads.append(ask - bid)
    # Every entry is weighted 1.0 here, so "contract-weighted within a
    # tournament" collapses to the tournament's simple mean — but the
    # ACROSS-tournament aggregation still matters: pooling lets a
    # many-market tournament dominate a figure this module reports as a
    # "+4.8c/ct edge over 10 tournaments" with a tournament-clustered CI.
    # That is the same defect fixed in the P-022 headline on 2026-08-02,
    # and this module's own header warns that pooling "would launder" a
    # marginal cohort into a strong one.
    mean, lo, hi = qc.bootstrap_gate(entries)
    pooled, _, _ = qc.bootstrap_pooled(entries)
    T_g, sd_g, se_g, z_g = qc.gate_dispersion(entries)
    pt = qc.per_tournament(entries)
    return {
        "n_markets": len(entries), "n_tournaments": len(pt),
        "mean_ask": qc.r4(sum(asks) / len(asks)) if asks else None,
        "mean_spread": qc.r4(sum(spreads) / len(spreads)) if spreads else None,
        "net_per_contract": qc.r4(mean),
        "ci95_lo": qc.r4(lo), "ci95_hi": qc.r4(hi),
        "estimator": "equal_weight_per_tournament",
        "gate_T": T_g, "gate_sd_per_tournament": qc.r4(sd_g),
        "gate_se": qc.r4(se_g), "gate_z": qc.r4(z_g),
        "net_per_contract_pooled": qc.r4(pooled),
        "tournaments_positive": sum(1 for v in pt.values()
                                    if v["net_per_contract"] > 0),
        "per_tournament": pt,
    }


def verdict(row: Dict[str, Any]) -> str:
    """KILL / ADVANCE / MARGINAL per the pre-registered Phase-2 gate."""
    net = row.get("net_per_contract")
    lo = row.get("ci95_lo")
    hi = row.get("ci95_hi")
    if net is None or lo is None or hi is None:
        return "NO DECISION (insufficient data)"
    if hi < 0:
        return "KILL"
    if net >= GATE_NET and lo > 0:
        return "ADVANCE"
    if net <= 0:
        return "KILL"
    return "MARGINAL"


def fmt_table(res: Dict[str, Any]) -> str:
    hdr = (f"{'setting':<30} {'posted':>7} {'filled':>7} {'fill%':>6} "
           f"{'cts':>7} {'net c/ct':>9} {'95% CI':>18} {'trn':>4} "
           f"{'+trn':>5} {'E[s|post]':>10} {'E[s|fill]':>10}")
    lines = [hdr, "-" * len(hdr)]
    for k, v in res.items():
        if v["posted_markets"] == 0 or not v["contracts_filled"]:
            lines.append(f"{k:<30} {v['posted_markets']:>7} (no fills)")
            continue
        ci = ("[  n/a (1 tournament) ]" if v["ci95_lo"] is None
              else f"[{v['ci95_lo'] * 100:+.1f}, {v['ci95_hi'] * 100:+.1f}]")
        lines.append(
            f"{k:<30} {v['posted_markets']:>7} {v['filled_markets']:>7} "
            f"{(v['fill_rate'] or 0) * 100:>5.0f}% {v['contracts_filled']:>7.0f} "
            f"{v['net_per_contract'] * 100:>+9.1f} {ci:>18} "
            f"{v['n_fill_tournaments']:>4} "
            f"{v['tournaments_positive']:>5} "
            f"{v['e_settle_posted']:>10.3f} {v['e_settle_filled']:>10.3f}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="makecut_fill_results.json")
    ap.add_argument("--params-out", default="p022_p023_params.json")
    ap.add_argument("--quote-size", type=float, default=QUOTE_SIZE)
    ap.add_argument("--skip-validate", action="store_true")
    args = ap.parse_args(argv)

    if not args.skip_validate:
        print("=== harness validation vs published P-022 Phase 2 ===")
        if not fade.validate(fade.load()):
            print("\nABORT: the rebuilt harness does not reproduce P-022's "
                  "published figures. Do not trust make-cut results from it.")
            return 1
        print()

    payload: Dict[str, Any] = {
        "generated": "backtest_makecut_fills.py",
        "quote_size": args.quote_size,
        "gate_net_per_contract": GATE_NET,
        "maker_fee": 0.0,
        "maker_fee_source": "live GET /series?category=Sports 2026-07-26: "
                            "KXPGAMAKECUT, KXDPWORLDTOURMAKECUT, KXLIVTOP5, "
                            "KXLIVTOP10 all fee_type=quadratic -> maker 0",
        "cohorts": {},
    }

    for name, cfg in COHORTS.items():
        recs = load_cohort(name)
        print(f"=== {cfg['label']} [{name}] ===")
        if not recs:
            print(f"  no cached ticks in {cfg['dir']} — "
                  f"run pull_trades.py first\n")
            payload["cohorts"][name] = {"label": cfg["label"], "universe": None}
            continue
        uni = describe(recs)
        print(f"  {uni['n_markets']} markets, {uni['n_tournaments']} tournaments, "
              f"{uni['n_prints']} prints, results={uni['results']}, "
              f"E[settle]={uni['mean_settlement_value']}")
        res = run_cohort(recs, args.quote_size)
        print(fmt_table(res))
        head = res["H48_off0.02"]
        v = verdict(head)
        print(f"  headline (H=48, offset -0.02): {v}")
        entries = [(ev, d["net_per_contract"], d["contracts"])
                   for ev, d in head["per_tournament"].items()]
        loo = qc.leave_one_out(entries)
        if loo:
            print(f"  leave-one-out range: "
                  f"[{min(loo.values()) * 100:+.1f}, {max(loo.values()) * 100:+.1f}] c/ct "
                  f"({sum(1 for x in loo.values() if x > 0)}/{len(loo)} jackknives positive)")
        tk = taker_reference(recs)
        if tk["n_markets"]:
            print(f"  taker reference (buy YES at the 48h ask, net of fee): "
                  f"n={tk['n_markets']} mean_ask={tk['mean_ask']:.3f} "
                  f"spread={tk['mean_spread']:.3f} -> "
                  f"{tk['net_per_contract'] * 100:+.1f}c/ct "
                  f"CI [{tk['ci95_lo'] * 100:+.1f}, {tk['ci95_hi'] * 100:+.1f}] "
                  f"{tk['tournaments_positive']}/{tk['n_tournaments']} positive")
        print()
        payload["cohorts"][name] = {
            "label": cfg["label"], "universe": uni, "grid": res,
            "headline_key": "H48_off0.02", "verdict": v,
            "leave_one_out": loo, "taker_reference": tk,
        }

    out = args.out if os.path.isabs(args.out) else os.path.join(qc.HERE, args.out)
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"wrote {out}")

    # Params file consumed by any future pod spec.
    pga = payload["cohorts"].get("pga_makecut") or {}
    head = (pga.get("grid") or {}).get("H48_off0.02") or {}
    params = {
        "generated_utc": "2026-07-26",
        "source": "golf_quirks_research/backtest_makecut_fills.py",
        "p022_round_leader_fade": {
            "status": "Phase-2 GREEN-LIGHT (validated, reproduced 2026-07-26)",
            "series": list(qc.LEADER_SERIES),
            "band": [0.03, 0.12], "anchor_hours_before_close": 12,
            "side": "sell_yes", "quote_offset": 0.02,
            "net_per_contract": 0.034, "ci95": [0.017, 0.051],
            "tournaments_positive": "16/19", "maker_fee": 0.0,
            "research_quote_size": 25,
        },
        "p023_makecut": {
            "status": (pga.get("verdict") or "NO DECISION"),
            "series": list(qc.MAKECUT_SERIES_PGA),
            "band": [0.40, 0.60], "anchor_hours_before_close": 48,
            "side": "buy_yes", "quote_offset": 0.02,
            "net_per_contract": head.get("net_per_contract"),
            "ci95": [head.get("ci95_lo"), head.get("ci95_hi")],
            "tournaments_positive":
                f"{head.get('tournaments_positive')}/{head.get('n_fill_tournaments')}",
            "maker_fee": 0.0, "research_quote_size": args.quote_size,
        },
    }
    pout = (args.params_out if os.path.isabs(args.params_out)
            else os.path.join(qc.HERE, args.params_out))
    with open(pout, "w") as fh:
        json.dump(params, fh, indent=2)
    print(f"wrote {pout}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
