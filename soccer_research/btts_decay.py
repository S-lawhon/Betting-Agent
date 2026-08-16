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


def series_list() -> list[str]:
    fx = json.loads(FIXTURE.read_text())
    return sorted(s for s in fx["all_series"] if "1HBTTS" in str(s).upper())


def pull() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    kp = KalshiPublic(min_interval=0.4)
    for ser in series_list():
        d = kp.get("/markets", {"series_ticker": ser, "status": "settled",
                                "limit": 200})
        markets = [m for m in ((d or {}).get("markets") or [])
                   if m.get("close_time") and m.get("result") in ("yes", "no")]
        if not markets:
            continue
        print(f"{ser:26} settled={len(markets)}", flush=True)
        for m in markets:
            out = CACHE / f"{m['ticker']}.json"
            if out.exists():
                continue
            close = dt.datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            start = int((close - dt.timedelta(hours=LOOKBACK_H)).timestamp())
            cs = (kp.get(f"/series/{ser}/markets/{m['ticker']}/candlesticks",
                         {"start_ts": start, "end_ts": int(close.timestamp()),
                          "period_interval": 1}) or {}).get("candlesticks") or []
            out.write_text(json.dumps(
                {"ticker": m["ticker"], "series": ser, "result": m["result"],
                 "close_time": m["close_time"], "candlesticks": cs}))


def replay_market(blob: dict) -> dict | None:
    """Offer YES one tick inside the ask, every open in-play minute.

    Fill ONLY on a strictly-through print: the minute's trade HIGH must exceed
    the offer. A minute with no volume can never fill (rule section 4).
    """
    ser, result = blob["series"], blob["result"]
    offers = fills = 0
    pnl_ct = 0.0
    for c in blob["candlesticks"]:
        ask_open = f((c.get("yes_ask") or {}).get("open_dollars"))
        bid_open = f((c.get("yes_bid") or {}).get("open_dollars"))
        # Require a live two-sided quote: a bare ask on an empty book is not a
        # market (the artifact the satellites census warns about).
        if ask_open is None or bid_open is None or bid_open <= 0.0 or ask_open >= 1.0:
            continue
        offer = round(ask_open - TICK, 4)
        if offer <= bid_open:          # would cross; we are a maker, not a taker
            continue
        offers += 1
        vol = f(c.get("volume_fp")) or 0.0
        if vol <= 0:
            continue
        high = f((c.get("price") or {}).get("high_dollars"))
        if high is None or high <= offer:   # strictly through, never at touch
            continue
        fills += 1
        # Short YES at `offer`; settles $1 if yes, $0 if no.
        gross = offer - (1.0 if result == "yes" else 0.0)
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


def replay() -> int:
    blobs = [json.loads(p.read_text()) for p in sorted(CACHE.glob("*.json"))]
    if not blobs:
        print("no cache; run --pull first")
        return 1

    per_market = [r for r in (replay_market(b) for b in blobs) if r]
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

    print(f"markets replayed      : {len(per_market)}")
    print(f"matches (clusters)    : {len(by_match)}  | carrying fills: {filled_matches}")
    print(f"offers / fills        : {tot_off} / {tot_fill}")
    print(f"FILL RATE             : {fill_rate:.2%}   "
          f"(rule: PASS needs >= {FILL_FLOOR_PASS:.0%}, KILL below {FILL_FLOOR_KILL:.0%})")

    if not xs:
        print("\nno filled matches — no edge statistic is computable")
        return 0

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
    print(f"\n===== VERDICT (rule section 7) =====\n  {v}")

    (HERE / "data" / "decay_results.json").write_text(json.dumps(
        {"per_market": per_market, "fill_rate": fill_rate, "matches": M,
         "net_c_per_contract": mean, "ci": [lo, hi], "z": z,
         "estimator": "gate", "verdict": v}, indent=2, sort_keys=True))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--replay", action="store_true")
    a = ap.parse_args()
    if a.pull:
        pull()
    if a.replay or not a.pull:
        return replay()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
