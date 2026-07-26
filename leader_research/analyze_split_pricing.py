"""
leader_research/analyze_split_pricing.py
────────────────────────────────────────
P-026 Step 1 analysis — is the KXLEADER book pricing the $1/n dead-heat split?

THE TEST, STATED PRECISELY
--------------------------
Let L be the set of participants tied at the season maximum, n = |L|.
Certified LEAGUELEADER terms: YES on i pays floor(100/n)/100 if i in L, else 0.

    fair_i = E[ 1{i in L} * floor(100/n)/100 ]

Summing over the COMPLETE eligible field:

    SUM_i fair_i = E[ n * floor(100/n)/100 ]  <=  $1.00

with equality when n = 1 (or n divides 100), and strictly less otherwise
because the split rounds DOWN (3-way -> 0.33*3 = 0.99; 6-way -> 0.16*6 = 0.96).
So a correctly-priced COMPLETE field sums to at most 100c.

If the market instead ignores the split and prices each name as if a share of
the title pays a full $1, it is pricing q_i = P(i in L), and

    SUM_i q_i = E[n] = 1 + s*(E[n|tie] - 1) > 1

    MLB pitcher wins (s=0.30, E[n|tie]~2.33) -> ~140c
    NFL interceptions (s=0.47, E[n|tie]~2.33) -> ~162c

TWO BIASES, BOTH LARGE, IN OPPOSITE DIRECTIONS
----------------------------------------------
(1) HALF-SPREAD ACCUMULATION -- inflates the sum.  mid = (bid+ask)/2, so
    SUM(mid) = SUM(bid) + SUM(half-spread).  On a 40-leg book quoting 5-6c
    wide, that is +100c of pure spread.  A mid-sum over 100c is therefore NOT
    by itself evidence of anything: it is what a wide multi-leg book always
    produces.  This is the single biggest threat to the test and it is why the
    bid / mid / ask decomposition below is the headline output, not the mid.

(2) INCOMPLETE FIELD -- deflates the sum.  Kalshi lists a named subset with no
    "Field/Other" strike, and many listed names carry no two-sided quote at
    all.  Probability mass outside the quoted set is simply missing.

THE ONLY MODEL-FREE STATEMENT AVAILABLE
---------------------------------------
Shorting YES on a SUBSET S of the field has maximum liability $1.00 exactly
(payouts partition at most a dollar and we hold only part of it).  So:

    SUM_{i in S} bid_i  >  100c   =>   strict dutch book, model-free.
    SUM_{i in S} bid_i  <= 100c   =>   nothing is proven either way.

Everything else requires a model of q_i, which we do not have.
"""
from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

E_INV_N = 0.43          # E[floor(100/n)/100 | tie], round-4 assumption
TAKER = 0.07            # Kalshi taker fee coefficient: 0.07*P*(1-P)


def tier(ob):
    """Quote-quality tier.  T1/T2/T3 are all GENUINE two-sided quotes;
    X has no usable mid at all (one-sided or empty) and is discarded."""
    if not ob:
        return "X", None, None
    b, a = ob.get("yes_bid"), ob.get("yes_ask")
    if b is None or a is None:
        return "X", None, None
    sp = round(a - b, 4)
    if sp <= 0.0301 and (ob.get("bid_qty") or 0) >= 25 and (ob.get("ask_qty") or 0) >= 25:
        return "T1", (a + b) / 2.0, sp
    if sp <= 0.0501:
        return "T2", (a + b) / 2.0, sp
    return "T3", (a + b) / 2.0, sp


def analyse(snap_path: str) -> dict:
    snap = json.load(open(snap_path))
    rep = {"pulled_utc": snap["pulled_utc"], "series": {}}
    for series, blk in snap["series"].items():
        rows = blk["markets"]
        if not rows:
            continue
        by_event = {}
        for r in rows:
            by_event.setdefault(r["event_ticker"], []).append(r)
        ev_out = {}
        for ev, legs in by_event.items():
            recs = []
            for r in legs:
                ob = r["orderbook"] or {}
                t, mid, sp = tier(ob)
                recs.append({
                    "ticker": r["ticker"], "name": r["name"], "tier": t,
                    "mid": mid, "spread": sp,
                    "bid": ob.get("yes_bid"), "ask": ob.get("yes_ask"),
                    "bid_qty": ob.get("bid_qty"), "ask_qty": ob.get("ask_qty"),
                    "oi": r.get("open_interest"),
                    "n_trades_30d": r.get("n_trades_30d"),
                    "vol_30d": r.get("vol_30d"),
                    "last_print": r.get("last_print"),
                })
            live = [x for x in recs if x["tier"] != "X"]
            dead = [x for x in recs if x["tier"] == "X"]
            sb = sum(x["bid"] for x in live)
            sa = sum(x["ask"] for x in live)
            sm = sum(x["mid"] for x in live)
            # fee to SELL the whole quoted field as a taker, at the bid
            fees = sum(TAKER * x["bid"] * (1 - x["bid"]) for x in live)
            # how much probability mass sits on discarded legs?  bound it by
            # their standing bid (a real, executable price) where one exists.
            dead_bid = sum((x["bid"] or 0.0) for x in dead)
            dead_with_bid = sum(1 for x in dead if x["bid"])
            ev_out[ev] = {
                "n_legs": len(recs),
                "tier_counts": {k: sum(1 for x in recs if x["tier"] == k)
                                for k in ("T1", "T2", "T3", "X")},
                "n_two_sided": len(live),
                "sum_bid": round(sb, 4),
                "sum_mid": round(sm, 4),
                "sum_ask": round(sa, 4),
                "half_spread_total": round(sm - sb, 4),
                "taker_fee_to_sell_field": round(fees, 4),
                "sum_bid_net_of_fee": round(sb - fees, 4),
                "median_spread": (sorted(x["spread"] for x in live)[len(live)//2]
                                  if live else None),
                "discarded_legs": len(dead),
                "discarded_legs_with_a_bid": dead_with_bid,
                "discarded_bid_mass": round(dead_bid, 4),
                "sum_bid_T1_only": round(sum(x["bid"] for x in recs
                                             if x["tier"] == "T1"), 4),
                "sum_mid_T1_only": round(sum(x["mid"] for x in recs
                                             if x["tier"] == "T1"), 4),
                "legs": sorted(recs, key=lambda x: -(x["mid"] or 0)),
            }
        rep["series"][series] = {
            "label": blk["label"], "tie_rate": blk["tie_rate"],
            "naive_field_sum_if_split_ignored":
                round(1 + blk["tie_rate"] * (1 / E_INV_N - 1), 3),
            "events": ev_out,
        }
    return rep


def main():
    snaps = sorted(glob.glob(os.path.join(DATA, "snapshot_*.json")))
    if not snaps:
        sys.exit("no snapshot; run fetch_leader_books.py first")
    rep = analyse(snaps[-1])
    json.dump(rep, open(os.path.join(DATA, "split_pricing_analysis.json"),
                        "w"), indent=1)

    print(f"snapshot {os.path.basename(snaps[-1])}  ({rep['pulled_utc']})\n")
    print("All figures in CENTS.  Correctly-split COMPLETE field <= 100.0c.")
    print("SUM(bid) > 100 is the only model-free evidence of mispricing.\n")
    h = (f"{'event':26s} {'legs':>4s} {'2sd':>4s} {'medsp':>5s} | "
         f"{'SUMbid':>7s} {'SUMmid':>7s} {'SUMask':>7s} {'halfsp':>7s} | "
         f"{'dead':>4s} {'deadbid':>7s} | {'naive':>6s}")
    print(h)
    print("-" * len(h))
    for s, blk in rep["series"].items():
        for ev, e in blk["events"].items():
            ms = e["median_spread"]
            print(f"{ev:26s} {e['n_legs']:4d} {e['n_two_sided']:4d} "
                  f"{(100*ms if ms is not None else 0):5.1f} | "
                  f"{100*e['sum_bid']:7.1f} {100*e['sum_mid']:7.1f} "
                  f"{100*e['sum_ask']:7.1f} {100*e['half_spread_total']:7.1f} | "
                  f"{e['discarded_legs']:4d} {100*e['discarded_bid_mass']:7.1f} | "
                  f"{100*blk['naive_field_sum_if_split_ignored']:5.0f}")


if __name__ == "__main__":
    main()
