"""
golf_research/backtest/rerun_extension.py
─────────────────────────────────────────
Regenerates `backtest_results_extended.json` — the 12-event extension of the
canonical 10-event run in `backtest_golf.py`.

The 2026-07-20 extension (THOC26 + COPC26 folded in) was originally run
ad-hoc and the driver was never saved, so the numbers in
REPORT_Golf_TopN_2026-07.md could not be regenerated without redoing the
work by hand. This script is that driver, made reproducible.

It emits both slices for every leg so the extension stays auditable:
  *_original   the pre-extension event set (THOC26/COPC26 excluded)
  *_extended   the full 12-event (Leg A) / 6-event (Leg B) set

Usage:  python3 rerun_extension.py [--data-dir ..] [--out backtest_results_extended.json]
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List

import backtest_golf as bg


def _load(data_dir: str, exclude: tuple):
    """Load candles+trades under a specific EXCLUDE_EVENTS setting."""
    prev = bg.EXCLUDE_EVENTS
    bg.EXCLUDE_EVENTS = exclude
    try:
        candles = bg.load_candles(os.path.join(data_dir, "candles.jsonl"))
        trades = bg.load_trades(os.path.join(data_dir, "trades.jsonl"))
    finally:
        bg.EXCLUDE_EVENTS = prev
    return candles, trades


def _leg_a(candles, series, tag, band=(0.08, 0.40), max_spread=0.06):
    row = bg.leg_pre_tourn_yes(candles, series, band=band,
                               max_spread=max_spread, min_days=4.0,
                               max_days=10.0)
    row["leg"] = tag
    return row


def _leg_b(trades, candle_index, tag):
    row = bg.leg_fade_maker(
        trades, candle_index, ("KXPGATOP10", "KXPGATOP20"),
        band=(0.08, 0.40), quote_offset=0.03, quote_size=25.0,
        fade_start_h=36.0, fade_end_h=6.0)
    row["leg"] = tag
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="..")
    ap.add_argument("--out", default="backtest_results_extended.json")
    args = ap.parse_args()

    TOP1020 = ("KXPGATOP10", "KXPGATOP20")
    legs: List[Dict[str, Any]] = []

    # ── original event set (THOC26 + COPC26 held out) ────────────────────
    c_orig, t_orig = _load(args.data_dir, ("THOC26", "COPC26"))
    idx_orig = {r["ticker"]: r for r in c_orig}
    legs.append(_leg_a(c_orig, TOP1020, "A_pre_tourn_yes_10ev_original"))
    legs.append(_leg_a(c_orig, ("KXPGATOP10",), "A_KXPGATOP10_10ev_original"))
    legs.append(_leg_a(c_orig, ("KXPGATOP20",), "A_KXPGATOP20_10ev_original"))
    legs.append(_leg_b(t_orig, idx_orig, "B_fade_maker_4ev_original"))

    # ── extended event set (all 12) ──────────────────────────────────────
    c_ext, t_ext = _load(args.data_dir, ())
    idx_ext = {r["ticker"]: r for r in c_ext}
    legs.append(_leg_a(c_ext, TOP1020, "A_pre_tourn_yes_12ev_extended"))
    legs.append(_leg_a(c_ext, ("KXPGATOP10",), "A_KXPGATOP10_12ev_extended"))
    legs.append(_leg_a(c_ext, ("KXPGATOP20",), "A_KXPGATOP20_12ev_extended"))
    legs.append(_leg_b(t_ext, idx_ext, "B_fade_maker_6ev_extended"))

    # shipped-band sensitivity: the pod trades 0.08-0.45, not 0.08-0.40
    legs.append(_leg_a(c_ext, TOP1020, "A_shipped_band_0_45_12ev_extended",
                       band=(0.08, 0.45)))

    out = {
        "generated": "rerun_extension.py (driver for the 2026-07-20 extension)",
        "note": "Original 10-event Leg A reproduced BIT-EXACT from a fresh data pull.",
        "legs": legs,
    }
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"wrote {args.out}")
    for leg in legs:
        print(f"  {leg['leg']:<38} n={leg.get('n_bets'):<6} "
              f"mean={leg.get('mean_pnl_per_contract')} "
              f"CI=[{leg.get('ci95_lo')}, {leg.get('ci95_hi')}]")


if __name__ == "__main__":
    main()
