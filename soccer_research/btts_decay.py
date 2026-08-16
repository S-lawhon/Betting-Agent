#!/usr/bin/env python3
"""First-half BTTS — Phase 2 late-half maker fade (READ-ONLY, cache-first).

Governing rule: soccer_research/BTTS_1H_DECAY_RULE.md (committed 032b15d),
with Amendment 1 (f9fc788) fixing the close_time outcome leak. Both landed
before this file existed.

Offers YES every open in-play minute, one tick inside the prevailing ask,
fills ONLY when the minute's trade range prints strictly through the offer,
and books the result against realised settlement. Match-clustered, equal
weight per match.

    python3 soccer_research/btts_decay.py --pull      # cache candles
    python3 soccer_research/btts_decay.py --replay    # verdict
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.kalshi_fees import fee_per_contract  # noqa: E402
from src.kalshi_public import KalshiPublic  # noqa: E402

HERE = Path(__file__).resolve().parent
CACHE = HERE / "data" / "decay_cache"
FIXTURE = ROOT / "src" / "fixtures" / "kalshi_series_fees.json"

TICK = 0.01
BOOTSTRAP_REPS = 5000
BOOTSTRAP_SEED = 20260816
MIN_MATCHES_KILL = 20      # rule section 7
MIN_MATCHES_PASS = 40
FILL_FLOOR_PASS = 0.10
FILL_FLOOR_KILL = 0.02
LOOKBACK_H = 4             # candles pulled before close; window is NOT anchored to it


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def series_list(arm: str = "first_half") -> list[str]:
    fx = json.loads(FIXTURE.read_text())
    alls = [s for s in fx["all_series"] if "BTTS" in str(s).upper()]
    if arm == "first_half":
        return sorted(s for s in alls if "1HBTTS" in s.upper())
    # placebo: full-match BTTS -- not a longshot (median mid 0.525), same
    # venue, fee regime, tick and settlement rulebook (Phase 1 proved the
    # contract-terms document is literally identical).
    return sorted(s for s in alls
                  if "1HBTTS" not in s.upper() and "2HBTTS" not in s.upper())


def pull(arm: str = "first_half") -> None:
    cache = CACHE / arm
    cache.mkdir(parents=True, exist_ok=True)
    kp = KalshiPublic(min_interval=0.4)
    for ser in series_list(arm):
        d = kp.get("/markets", {"series_ticker": ser, "status": "settled",
                                "limit": 200})
        markets = [m for m in ((d or {}).get("markets") or [])
                   if m.get("close_time")
                   and m.get("result") in ("yes", "no", "scalar")]
        if not markets:
            continue
        print(f"{ser:26} settled={len(markets)}", flush=True)
        for m in markets:
            out = cache / f"{m['ticker']}.json"
            if out.exists():
                continue
            close = dt.datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            start = int((close - dt.timedelta(hours=LOOKBACK_H)).timestamp())
            cs = (kp.get(f"/series/{ser}/markets/{m['ticker']}/candlesticks",
                         {"start_ts": start, "end_ts": int(close.timestamp()),
                          "period_interval": 1}) or {}).get("candlesticks") or []
            sv = m.get("settlement_value_dollars")
            if m["result"] == "scalar" and sv is None:
                # Never default a scalar. Re-read the single-market endpoint,
                # which carries settlement_value_dollars when LIST does not.
                sv = (kp.get_market(m["ticker"]) or {}).get(
                    "settlement_value_dollars")
            out.write_text(json.dumps(
                {"ticker": m["ticker"], "series": ser, "result": m["result"],
                 "settlement_value_dollars": sv,
                 "close_time": m["close_time"], "candlesticks": cs}))


def replay_market(blob: dict, side: str = "sell") -> dict | None:
    """Offer YES one tick inside the ask, every open in-play minute.

    Fill ONLY on a strictly-through print: the minute's trade HIGH must exceed
    the offer. A minute with no volume can never fill (rule section 4).
    """
    ser, result = blob["series"], blob["result"]
    if result == "scalar":
        sv = f(blob.get("settlement_value_dollars"))
        if sv is None:
            return {"ticker": blob["ticker"], "series": ser, "result": result,
                    "excluded": "scalar_without_settlement_value",
                    "offers": 0, "fills": 0, "net_c_total": 0.0,
                    "net_c_per_fill": None}
        settle = sv
    else:
        settle = 1.0 if result == "yes" else 0.0
    offers = fills = 0
    pnl_ct = 0.0
    for c in blob["candlesticks"]:
        ask_open = f((c.get("yes_ask") or {}).get("open_dollars"))
        bid_open = f((c.get("yes_bid") or {}).get("open_dollars"))
        # Require a live two-sided quote: a bare ask on an empty book is not a
        # market (the artifact the satellites census warns about).
        if ask_open is None or bid_open is None or bid_open <= 0.0 or ask_open >= 1.0:
            continue
        if side == "sell":
            offer = round(ask_open - TICK, 4)
            if offer <= bid_open:      # would cross; we are a maker, not a taker
                continue
        else:                          # second placebo: the BUY side
            offer = round(bid_open + TICK, 4)
            if offer >= ask_open:
                continue
        offers += 1
        vol = f(c.get("volume_fp")) or 0.0
        if vol <= 0:
            continue
        px = c.get("price") or {}
        if side == "sell":
            through = f(px.get("high_dollars"))
            hit = through is not None and through > offer
        else:
            through = f(px.get("low_dollars"))
            hit = through is not None and through < offer
        if not hit:                    # strictly through, never at touch
            continue
        fills += 1
        # Short YES at `offer`; settles $1 if yes, $0 if no.
        gross = (offer - settle) if side == "sell" else (settle - offer)
        fee = fee_per_contract(offer, maker=True, series_ticker=ser)
        pnl_ct += (gross - fee) * 100.0
    if offers == 0:
        return None
    return {"ticker": blob["ticker"], "series": ser, "result": result,
            "offers": offers, "fills": fills,
            "net_c_total": pnl_ct,
            "net_c_per_fill": (pnl_ct / fills) if fills else None}


def match_key(ticker: str) -> str:
    """One fixture = one cluster (rule section 5). KXMLS1HBTTS-26AUG16SEAVAN-BTTS
    -> 26AUG16SEAVAN, which is shared by both halves of the same match."""
    parts = ticker.split("-")
    return parts[1] if len(parts) > 1 else ticker


def bootstrap(xs: list[float]) -> tuple[float, float, float, float]:
    """Equal weight per match; NEVER the contract-weighted pooled mean
    (the 2026-08-02 estimator correction)."""
    mean = statistics.fmean(xs)
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(xs)
    means = []
    for _ in range(BOOTSTRAP_REPS):
        means.append(statistics.fmean([xs[rng.randrange(n)] for _ in range(n)]))
    means.sort()
    lo = means[int(0.025 * BOOTSTRAP_REPS)]
    hi = means[int(0.975 * BOOTSTRAP_REPS)]
    se = statistics.pstdev(means) or float("inf")
    return mean, lo, hi, mean / se


def replay(arm: str = "first_half", side: str = "sell", quiet: bool = False):
    blobs = [json.loads(p.read_text())
             for p in sorted((CACHE / arm).glob("*.json"))]
    if not blobs:
        print(f"no cache for arm={arm}; run --pull --arm {arm} first")
        return None

    raw = [r for r in (replay_market(b, side) for b in blobs) if r]
    excluded = [r for r in raw if r.get("excluded")]
    per_market = [r for r in raw if not r.get("excluded")]
    by_match: dict[str, list[dict]] = {}
    for r in per_market:
        by_match.setdefault(match_key(r["ticker"]), []).append(r)

    tot_off = sum(r["offers"] for r in per_market)
    tot_fill = sum(r["fills"] for r in per_market)
    fill_rate = tot_fill / tot_off if tot_off else 0.0

    # Equal weight per MATCH: average net per fill within a match, then across.
    xs, filled_matches = [], 0
    for _, rows in sorted(by_match.items()):
        fills = sum(r["fills"] for r in rows)
        if fills == 0:
            continue
        filled_matches += 1
        xs.append(sum(r["net_c_total"] for r in rows) / fills)

    print(f"\n########## arm={arm}  side={side} ##########")
    if excluded:
        print(f"EXCLUDED (never defaulted): {len(excluded)} "
              f"{sorted({r['excluded'] for r in excluded})}")
    print(f"markets replayed      : {len(per_market)}")
    print(f"matches (clusters)    : {len(by_match)}  | carrying fills: {filled_matches}")
    print(f"offers / fills        : {tot_off} / {tot_fill}")
    print(f"FILL RATE             : {fill_rate:.2%}   "
          f"(rule: PASS needs >= {FILL_FLOOR_PASS:.0%}, KILL below {FILL_FLOOR_KILL:.0%})")

    if not xs:
        print("\nno filled matches — no edge statistic is computable")
        return None

    mean, lo, hi, z = bootstrap(xs)
    print(f"\nnet c/contract (equal weight per match): {mean:+.2f}")
    print(f"  95% CI [{lo:+.2f}, {hi:+.2f}]   z = {z:+.2f}")
    print(f"  estimator: gate (mean over matches), NOT pooled")

    M = filled_matches
    if z <= -2.0 and M >= MIN_MATCHES_KILL:
        v = "HARD KILL — final, no re-parameterisation (rule section 7)"
    elif fill_rate < FILL_FLOOR_KILL and M >= MIN_MATCHES_PASS:
        v = "KILL — unfillable, as P-017A was, regardless of edge"
    elif z >= 2.0 and fill_rate >= FILL_FLOOR_PASS and M >= MIN_MATCHES_PASS:
        v = "PASS — authorises a capacity study only"
    else:
        v = "NO DECISION"
    if not quiet:
        print(f"\n===== VERDICT (rule section 7) =====\n  {v}")

    out = {"arm": arm, "side": side, "per_market": per_market,
           "excluded": excluded, "fill_rate": fill_rate, "matches": M,
           "net_c_per_contract": mean, "ci": [lo, hi], "z": z,
           "xs": xs, "estimator": "gate", "verdict": v}
    (HERE / "data" / f"decay_results_{arm}_{side}.json").write_text(
        json.dumps(out, indent=2, sort_keys=True))
    return out


def gate_p(test: dict, placebo: dict) -> None:
    """BTTS_PLACEBO_RULE.md section 3, applied mechanically."""
    print("\n===== GATE P (placebo) =====")
    t, p_ = test["net_c_per_contract"], placebo["net_c_per_contract"]
    print(f"  first-half (test) : {t:+.2f}c  CI [{test['ci'][0]:+.2f}, {test['ci'][1]:+.2f}]")
    print(f"  full-match (placebo): {p_:+.2f}c  CI [{placebo['ci'][0]:+.2f}, {placebo['ci'][1]:+.2f}]")
    overlap = not (test["ci"][0] > placebo["ci"][1] or placebo["ci"][0] > test["ci"][1])
    ratio = (p_ / t) if t else float("inf")
    print(f"  placebo / test = {ratio:.0%}   CIs overlap: {overlap}")
    if t > 0 and p_ >= 0.5 * t and overlap:
        print("  KILL — no first-half effect; this is a generic short-longshot premium")
    elif not overlap and t > p_:
        print("  PASS Gate P — the first-half effect is separable")
    else:
        print("  NO DECISION — Gate P unresolved; Phase 2 does not advance")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--arm", default="first_half",
                    choices=("first_half", "full_match"))
    ap.add_argument("--side", default="sell", choices=("sell", "buy"))
    ap.add_argument("--gate-p", action="store_true",
                    help="run both arms + the buy-side control, apply Gate P")
    a = ap.parse_args()
    if a.pull:
        pull(a.arm)
        return 0
    if a.gate_p:
        test = replay("first_half", "sell")
        placebo = replay("full_match", "sell")
        buy = replay("first_half", "buy", quiet=True)
        if test and placebo:
            gate_p(test, placebo)
        if buy:
            print(f"\n  second placebo (BUY side, first-half): "
                  f"{buy['net_c_per_contract']:+.2f}c  z={buy['z']:+.2f}  "
                  f"fill {buy['fill_rate']:.2%}")
            print("  (if BOTH directions earn, the harness is measuring spread "
                  "capture or a fill artifact, not a directional edge)")
        return 0
    replay(a.arm, a.side)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
