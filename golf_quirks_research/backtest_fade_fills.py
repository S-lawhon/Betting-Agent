"""
golf_quirks_research/backtest_fade_fills.py
───────────────────────────────────────────
P-022 Phase 2 — round-leader dead-heat fade, maker-fill realism replay.

REBUILT 2026-07-26 (original lost 2026-07-25, never committed).

Sell YES / make NO on cheap round-leader names, then replay the real public
tape against the resting quote. The question Phase 1 could not answer is not
"is the price wrong" (the $1/n dead-heat rulebook guarantees it is) but
"does a maker get filled on anything other than the names that go on to
lead" — the adverse selection that killed P-016 and left P-017's fade leg
underpowered.

Run `--validate` FIRST. It checks the rebuilt harness against the figures
published in REPORT_Golf_Quirks_Phase2_P022_2026-07.md §2. A harness that
cannot reproduce a known result must not be trusted to produce a new one
(the P-016 v2 lesson). `backtest_makecut_fills.py` refuses to run unless
this passes.

**`--validate` is PINNED to the 364 tickers the published cells rest on**
(`published_universe_364.json`), NOT to whatever happens to be in `data/`.
The 2026-07-28 widening added 40 markets to the cache and broke every cell of
a whole-cache validator, which made the reproduction test un-runnable for a
reason that had nothing to do with the harness (see REPORT_P022_Widened §5 —
it called this out and pinned the ticker list, but never wired it in). A
reproduction test must be independent of cache growth or it decays into a
cache-fingerprint test. If a pinned ticker is MISSING from the cache the
validator fails loudly: that is real data loss, not growth.

The analysis path (`--out`, no `--validate`) still runs the FULL cache by
default; `--universe pinned` restricts it, which is how the n=364 vs n=404
rows in the Phase-2 report §2.1 were produced.

Usage
  python3 backtest_fade_fills.py --validate
  python3 backtest_fade_fills.py --out fade_fill_results.json
  python3 backtest_fade_fills.py --universe pinned      # published universe only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quirks_common as qc  # noqa: E402

TRADE_DIR = os.path.join(qc.DATA, "leader_trades")

# The published Phase-2 grid (H hours, offset).
GRID: Tuple[Tuple[float, float], ...] = (
    (12.0, 0.00), (12.0, 0.02), (12.0, 0.04),
    (6.0, 0.00), (6.0, 0.02),
    (24.0, 0.00), (24.0, 0.02),
)

# REPORT_Golf_Quirks_Phase2_P022_2026-07.md §2, transcribed verbatim.
# (H, offset) -> posted, filled, contracts, net_c, ci_lo_c, ci_hi_c,
#                tournaments, E[settle|posted], E[settle|filled]
PUBLISHED: Dict[Tuple[float, float], Dict[str, Any]] = {
    (12.0, 0.00): dict(posted=364, filled=211, contracts=5160, net=2.1,
                       ci=(0.8, 3.5), tourn=19, esp=0.018, esf=0.032),
    (12.0, 0.02): dict(posted=364, filled=171, contracts=4061, net=3.4,
                       ci=(1.7, 5.1), tourn=19, esp=0.018, esf=0.041),
    (12.0, 0.04): dict(posted=364, filled=147, contracts=3506, net=4.7,
                       ci=(2.7, 6.7), tourn=19, esp=0.018, esf=0.048),
    (6.0, 0.00): dict(posted=342, filled=112, contracts=2765, net=0.5,
                      ci=(-1.9, 3.0), tourn=15, esp=0.020, esf=0.060),
    (6.0, 0.02): dict(posted=364, filled=97, contracts=2385, net=1.7,
                      ci=(-1.0, 4.6), tourn=15, esp=0.018, esf=0.070),
    (24.0, 0.00): dict(posted=272, filled=172, contracts=4234, net=2.4,
                       ci=(0.0, 4.3), tourn=19, esp=0.017, esf=0.028),
    (24.0, 0.02): dict(posted=274, filled=138, contracts=3323, net=3.5,
                       ci=(0.5, 5.9), tourn=19, esp=0.017, esf=0.035),
}
# Tolerances. Counts must match exactly (they are deterministic); the CI
# endpoints are bootstrap draws, so 0.3c of slack absorbs RNG-order drift.
TOL_NET_C = 0.2
TOL_CI_C = 0.3

PUBLISHED_UNIVERSE = 364
PUBLISHED_POS_TOURN = (16, 19)          # 16 of 19 positive at H=12/+0.02
PUBLISHED_LOO_USO26 = 0.030             # dropping USO26: +3.4c -> +3.0c

# The exact ticker list the published cells rest on. Committed 2026-07-28 by
# the widening run precisely so the published universe could be restored;
# wired into --validate 2026-07-28.
PINNED_UNIVERSE_PATH = os.path.join(qc.HERE, "published_universe_364.json")


def load() -> List[Dict[str, Any]]:
    recs = qc.load_trade_recs(TRADE_DIR)
    if not recs:
        raise SystemExit(f"no cached tick data in {TRADE_DIR} — "
                         f"run: python3 pull_trades.py --mode leader")
    return recs


def pinned_tickers() -> List[str]:
    """The 364 tickers of the published Phase-2 universe."""
    try:
        with open(PINNED_UNIVERSE_PATH) as fh:
            blob = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read {PINNED_UNIVERSE_PATH}: {exc}\n"
                         f"the published universe pin is required to validate")
    tickers = blob.get("tickers") or []
    if len(tickers) != PUBLISHED_UNIVERSE:
        raise SystemExit(f"{PINNED_UNIVERSE_PATH} holds {len(tickers)} tickers, "
                         f"expected {PUBLISHED_UNIVERSE}")
    return list(tickers)


def restrict_to_pinned(recs: Sequence[Dict[str, Any]]
                       ) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    """`recs` cut down to the published universe.

    Returns (pinned_recs, n_excluded, missing_tickers). `missing` is the real
    failure mode — a pinned ticker absent from the cache means the sample the
    published cells rest on has been LOST, which no amount of cache growth can
    explain. Extra cached markets are expected and are merely counted.
    """
    want = set(pinned_tickers())
    have = {r.get("ticker"): r for r in recs}
    kept = [have[t] for t in sorted(want) if t in have]
    missing = sorted(want - set(have))
    return kept, len(recs) - len(kept), missing


def run_grid(recs: Sequence[Dict[str, Any]], quote_size: float = 25.0
             ) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for h, off in GRID:
        out[f"H{h:g}_off{off:.2f}"] = qc.replay(
            recs, "sell_yes", h, off, quote_size=quote_size)
    return out


def validate(recs: Sequence[Dict[str, Any]], verbose: bool = True) -> bool:
    ok = True
    lines: List[str] = []

    n_cached = len(recs)
    recs, n_excluded, missing = restrict_to_pinned(recs)

    n_uni = len(recs)
    uni_ok = n_uni == PUBLISHED_UNIVERSE and not missing
    ok &= uni_ok
    lines.append(f"{'PASS' if uni_ok else 'FAIL'}  universe: "
                 f"{n_uni} of the {PUBLISHED_UNIVERSE} pinned markets present "
                 f"({n_cached} cached, {n_excluded} outside the published "
                 f"universe and excluded)")
    if missing:
        lines.append(f"      MISSING from cache — the published sample has been "
                     f"LOST, not merely grown: {', '.join(missing[:10])}"
                     + (f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""))

    header = (f"{'H':>4} {'off':>5} | {'posted':>13} {'filled':>11} "
              f"{'contracts':>13} {'net c/ct':>15} {'CI lo':>13} {'CI hi':>13} "
              f"{'tourn':>9} {'E[s|post]':>15} {'E[s|fill]':>15}  result")
    lines.append(header)
    lines.append("-" * len(header))

    for h, off in GRID:
        got = qc.replay(recs, "sell_yes", h, off)
        exp = PUBLISHED[(h, off)]
        net_c = got["net_per_contract"] * 100.0
        lo_c = got["ci95_lo"] * 100.0
        hi_c = got["ci95_hi"] * 100.0
        checks = [
            got["posted_markets"] == exp["posted"],
            got["filled_markets"] == exp["filled"],
            abs(got["contracts_filled"] - exp["contracts"]) <= 1,
            abs(net_c - exp["net"]) <= TOL_NET_C,
            abs(lo_c - exp["ci"][0]) <= TOL_CI_C,
            abs(hi_c - exp["ci"][1]) <= TOL_CI_C,
            got["n_fill_tournaments"] == exp["tourn"],
            abs(got["e_settle_posted"] - exp["esp"]) <= 0.002,
            abs(got["e_settle_filled"] - exp["esf"]) <= 0.002,
        ]
        row_ok = all(checks)
        ok &= row_ok
        lines.append(
            f"{h:>4g} {off:>5.2f} | "
            f"{got['posted_markets']:>6d}/{exp['posted']:<6d} "
            f"{got['filled_markets']:>5d}/{exp['filled']:<5d} "
            f"{got['contracts_filled']:>6.0f}/{exp['contracts']:<6d} "
            f"{net_c:>+7.1f}/{exp['net']:<+7.1f} "
            f"{lo_c:>+6.1f}/{exp['ci'][0]:<+6.1f} "
            f"{hi_c:>+6.1f}/{exp['ci'][1]:<+6.1f} "
            f"{got['n_fill_tournaments']:>4d}/{exp['tourn']:<4d} "
            f"{got['e_settle_posted']:>7.3f}/{exp['esp']:<7.3f} "
            f"{got['e_settle_filled']:>7.3f}/{exp['esf']:<7.3f}  "
            f"{'PASS' if row_ok else 'FAIL'}")

    head = qc.replay(recs, "sell_yes", 12.0, 0.02)
    pos = head["tournaments_positive"]
    tot = head["n_fill_tournaments"]
    pos_ok = (pos, tot) == PUBLISHED_POS_TOURN
    ok &= pos_ok
    lines.append(f"{'PASS' if pos_ok else 'FAIL'}  per-tournament sign: "
                 f"{pos} of {tot} positive "
                 f"(published {PUBLISHED_POS_TOURN[0]} of {PUBLISHED_POS_TOURN[1]})")

    entries: List[Tuple[str, float, float]] = []
    for ev, v in head["per_tournament"].items():
        entries.append((ev, v["net_per_contract"], v["contracts"]))
    loo = qc.leave_one_out(entries)
    loo_uso = loo.get("USO26")
    loo_ok = loo_uso is not None and abs(loo_uso - PUBLISHED_LOO_USO26) <= 0.003
    all_pos = all(v > 0 for v in loo.values())
    ok &= loo_ok and all_pos
    lines.append(f"{'PASS' if (loo_ok and all_pos) else 'FAIL'}  leave-one-out: "
                 f"drop USO26 -> {loo_uso:+.4f} "
                 f"(published {PUBLISHED_LOO_USO26:+.3f}); "
                 f"all {len(loo)} jackknives positive = {all_pos}")

    lines.append("")
    lines.append(f"HARNESS VALIDATION: {'PASS' if ok else 'FAIL'}")
    if verbose:
        print("\n".join(lines))
    return ok


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--validate", action="store_true",
                    help="reproduce the published Phase-2 table and exit")
    ap.add_argument("--out", default=None, help="write full results JSON here")
    ap.add_argument("--quote-size", type=float, default=25.0)
    ap.add_argument("--universe", choices=("all", "pinned"), default="all",
                    help="'all' = every cached market (default); 'pinned' = "
                         "only the 364 markets the published cells rest on. "
                         "Ignored by --validate, which is always pinned.")
    args = ap.parse_args(argv)

    recs = load()
    if args.validate:
        return 0 if validate(recs) else 1

    if args.universe == "pinned":
        recs, n_excluded, missing = restrict_to_pinned(recs)
        if missing:
            print(f"WARNING: {len(missing)} pinned markets missing from the "
                  f"cache — this is NOT the published universe", file=sys.stderr)
        print(f"universe: pinned, {len(recs)} markets "
              f"({n_excluded} cached markets excluded)")
    else:
        print(f"universe: all, {len(recs)} cached markets "
              f"(published cells rest on {PUBLISHED_UNIVERSE} — "
              f"use --universe pinned to reproduce them)")

    res = run_grid(recs, args.quote_size)
    head = res["H12_off0.02"]
    print(json.dumps({k: {kk: vv for kk, vv in v.items()
                          if kk != "per_tournament"}
                      for k, v in res.items()}, indent=2))
    print(f"\nheadline H=12 offset +0.02: "
          f"{head['net_per_contract'] * 100:+.1f}c/ct "
          f"CI [{head['ci95_lo'] * 100:+.1f}, {head['ci95_hi'] * 100:+.1f}] "
          f"{head['tournaments_positive']}/{head['n_fill_tournaments']} positive")
    if args.out:
        path = args.out if os.path.isabs(args.out) else os.path.join(qc.HERE, args.out)
        with open(path, "w") as fh:
            json.dump(res, fh, indent=2)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
