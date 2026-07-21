#!/usr/bin/env python3
"""
scripts/calibrate_wp_model.py
─────────────────────────────
Calibrate the live MLB win-probability model against Kalshi in-game mids.

Motivation: the first live run of P-016 showed the model systematically
MORE EXTREME than the market in every observed state (fair pushed 8-17pp
away from 0.5 vs mid) — the signature of understated remaining variance
(real baseball has fat-tailed big innings; a tight normal approx does not).

The market is not ground truth, but the research (arXiv 2606.07811: Kalshi
in-play Brier ≈ a good benchmark model's) says it is well calibrated — so a
model that disagrees with it this systematically is the one that is wrong.

Method: sample live games, pair (game state, pregame anchor) with the Kalshi
mid, then grid-search SIGMA_PER_HALF_INNING minimising mean |model − mid|.
Reports the bias (signed mean) too, since the failure mode is directional.

Usage:
    python3 -m scripts.calibrate_wp_model [--samples-out PATH] [--load PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import mlb_win_prob as wp  # noqa: E402
from src.kalshi_public import KalshiPublic, fnum  # noqa: E402
from src.mlb_statsapi import MlbStatsApi  # noqa: E402
from src.pods.live_maker_pod import LiveMakerEngine  # noqa: E402


def collect(out_path: Path | None, anchors: dict | None = None) -> list:
    """Sample live (state, market mid) pairs.

    `anchors` maps ticker → TRUE pregame probability, normally harvested
    from a maker_quotes.jsonl ANCHOR record written before first pitch.
    Re-deriving an anchor from a live mid would double-count the current
    game state, which is the very bug this script exists to fix.
    """
    api, kal = MlbStatsApi(), KalshiPublic()
    eng = LiveMakerEngine(statsapi=api, kalshi=kal,
                          log_dir=Path("/tmp/calib_logs"))
    et = datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")
    games = api.schedule(et)
    eng.discover(games)
    status = {g.game_pk: g.status for g in games}

    samples = []
    for pk, book in eng.books.items():
        if status.get(pk) != "Live":
            continue
        if anchors is not None:
            if book.ticker not in anchors:
                continue
            book.pregame_prob = anchors[book.ticker]
        elif book.anchor_source != "kalshi_pregame_mid":
            continue
        ls = api.linescore(pk)
        ob = kal.orderbook(book.ticker)
        if not ls or not ob:
            continue
        b, a = ob.get("yes_bid"), ob.get("yes_ask")
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        mid = (b + a) / 2.0
        samples.append({
            "ticker": book.ticker, "pregame": book.pregame_prob, "mid": mid,
            "spread": a - b,
            "home_score": ls.home_score, "away_score": ls.away_score,
            "inning": ls.inning, "is_top": ls.is_top, "outs": ls.outs,
            "on_first": ls.on_first, "on_second": ls.on_second,
            "on_third": ls.on_third,
        })
    if out_path and samples:
        with open(out_path, "a") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")
    return samples


def evaluate(samples: list, sigma: float, extras_coef: float = None) -> tuple:
    old_sigma = wp.SIGMA_PER_HALF_INNING
    wp.SIGMA_PER_HALF_INNING = sigma
    try:
        errs = []
        for s in samples:
            snap = wp.MlbGameSnapshot(
                home_score=s["home_score"], away_score=s["away_score"],
                inning=s["inning"], is_top=s["is_top"], outs=s["outs"],
                on_first=s["on_first"], on_second=s["on_second"],
                on_third=s["on_third"],
            )
            fair = wp.home_win_probability(snap, s["pregame"]).home_wp
            errs.append(fair - s["mid"])
    finally:
        wp.SIGMA_PER_HALF_INNING = old_sigma
    if not errs:
        return None, None, None
    n = len(errs)
    mae = sum(abs(e) for e in errs) / n
    bias = sum(errs) / n
    # Extremeness: does the model push away from 0.5 relative to market?
    ext = []
    for s, e in zip(samples, errs):
        direction = 1.0 if s["mid"] >= 0.5 else -1.0
        ext.append(e * direction)
    extremeness = sum(ext) / n
    return mae, bias, extremeness


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples-out", default="data/wp_calib_samples.jsonl")
    ap.add_argument("--load", default=None,
                    help="skip collection; evaluate an existing sample file")
    ap.add_argument("--anchors", default=None,
                    help="JSON {ticker: pregame_prob} of TRUE pregame anchors")
    args = ap.parse_args()

    if args.load:
        samples = [json.loads(l) for l in Path(args.load).read_text().splitlines()
                   if l.strip()]
        print(f"loaded {len(samples)} samples from {args.load}")
    else:
        anchors = None
        if args.anchors:
            anchors = json.loads(Path(args.anchors).read_text())
            print(f"using {len(anchors)} supplied pregame anchors")
        samples = collect(
            Path(args.samples_out) if args.samples_out else None, anchors)
        print(f"collected {len(samples)} live pregame-anchored samples")

    if not samples:
        print("No usable samples (need live games with pregame anchors).")
        return

    print(f"\n{'sigma':>7} {'MAE':>9} {'bias':>9} {'extremeness':>12}")
    print("-" * 42)
    best = None
    s = 0.9
    while s <= 3.01:
        mae, bias, ext = evaluate(samples, s)
        flag = ""
        if best is None or mae < best[1]:
            best, flag = (s, mae), "  <-- best MAE"
        print(f"{s:7.2f} {mae:9.4f} {bias:+9.4f} {ext:+12.4f}{flag}")
        s += 0.1

    print(f"\nBest sigma by MAE: {best[0]:.2f} "
          f"(current default {wp.SIGMA_PER_HALF_INNING:.2f})")
    print("\nextremeness > 0 ⇒ model more extreme than market (variance too "
          "low)\nextremeness < 0 ⇒ model too timid (variance too high)")
    print("\nNOTE: this fits the model to the MARKET. It removes systematic "
          "disagreement;\nit does not create edge. Edge, if any, must come "
          "from state changes the\nmarket updates slowly on — which is the "
          "documented in-play inefficiency.")


if __name__ == "__main__":
    main()
