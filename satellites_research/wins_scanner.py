"""
satellites_research/wins_scanner.py
───────────────────────────────────
Study C — WINS / EXACTWINS partition-coherence scanner.  READ-ONLY.
This script CANNOT place orders and must never be wired into a pod.

Why this is well-founded: NFLWINS.pdf, NFLEXACTWINS.pdf and MLBWINS.pdf are the
SAME certified rulebook ("WINTOTAL"), verbatim identical, whose Payout Criterion
is `<above/below/between/exactly/at least> <count>` on one integer — regular
season wins.  So within a team-season:
      P(>=N) is monotone non-increasing in N
      P(>=N) - P(>=N+1) = P(exactly N)
      sum_N P(exactly N) = 1
Any violation of those is an internal-consistency violation, no forecast needed.

The scanner is deliberately paranoid, because the failure mode here is
fabricating an arb out of a one-lot resting order (the P-022 lesson):

  F0  candidate            structural relation exists (all legs listed)
  F1  two-sided            EVERY leg has yes_bid > 0 AND yes_ask < 1 simultaneously
  F2  executable sign      the violation survives crossing the spread
                           (buy at ask / sell at bid), not on mids
  F3  depth                min executable size across legs >= MIN_SIZE contracts
  F4  net of fees          profit/ct clears the Kalshi taker fee on every leg
                           (src.kalshi_fees, series-aware) plus a slippage buffer
  F5  persistence          still there on a re-scan >= 1h later

Only an F5 survivor is a tradeable dutch book.  Everything else is an artifact
and is reported as such — the attrition table is the deliverable.

Usage:
    python3 satellites_research/wins_scanner.py --scan          # take a snapshot
    python3 satellites_research/wins_scanner.py --report        # analyse snapshots
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi_fees import fee_per_contract  # noqa: E402
from src.kalshi_public import KalshiPublic  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SNAPS = os.path.join(DATA, "wins_snapshots")
os.makedirs(SNAPS, exist_ok=True)

# Every template that is the WINTOTAL rulebook (see contract_terms/*.txt).
WIN_TEMPLATES = [
    "NFLWINS", "NFLEXACTWINS", "MLBWINS", "NFLXWINS",
    "NBAWINS", "NHLWINS", "WNBAWINS", "NCAAFWINS", "NCAAMBWINS",
    "NFLDIVISIONWINS", "NFLDIVISIONTOTALWINS",
]

MIN_SIZE = 20.0          # contracts that must be available on EVERY leg
SLIPPAGE = 0.005         # $/ct buffer on top of fees (half a tick)


def fnum(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


# ───────────────────────── snapshot ──────────────────────────────────

def scan(tag: str | None = None) -> str:
    templates = json.load(open(os.path.join(DATA, "templates.json")))
    k = KalshiPublic(min_interval=0.7)
    out: list[dict] = []
    for tpl in WIN_TEMPLATES:
        for ser in templates.get(tpl, {}).get("tickers", []):
            params = {"series_ticker": ser, "limit": 200}
            for _ in range(20):
                d = k.get("/markets", params)
                if not d:
                    break
                for m in d.get("markets") or []:
                    if m.get("status") not in ("active", "open"):
                        continue
                    out.append({
                        "template": tpl, "series": ser,
                        "ticker": m["ticker"], "event": m.get("event_ticker"),
                        "sub": m.get("yes_sub_title"),
                        "strike_type": m.get("strike_type"),
                        "floor": fnum(m.get("floor_strike")),
                        "cap": fnum(m.get("cap_strike")),
                        "bid": fnum(m.get("yes_bid_dollars")),
                        "ask": fnum(m.get("yes_ask_dollars")),
                        "bid_sz": fnum(m.get("yes_bid_size_fp")) or 0.0,
                        "ask_sz": fnum(m.get("yes_ask_size_fp")) or 0.0,
                        "oi": fnum(m.get("open_interest_fp")) or 0.0,
                        "vol": fnum(m.get("volume_fp")) or 0.0,
                    })
                cur = d.get("cursor")
                if not cur:
                    break
                params["cursor"] = cur
    ts = tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = os.path.join(SNAPS, f"snap_{ts}.json")
    with open(p, "w") as fh:
        json.dump({"ts": ts, "markets": out}, fh)
    print(f"snapshot {p}: {len(out)} live markets")
    return p


# ───────────────────────── analysis ──────────────────────────────────

def _ladder(markets: list[dict]) -> dict[str, list[dict]]:
    """Group `>= N wins` markets into ladders keyed by event."""
    by_event = defaultdict(list)
    for m in markets:
        if m["strike_type"] in ("greater_or_equal", "greater") and m["floor"] is not None:
            by_event[m["event"]].append(m)
    for e in by_event:
        by_event[e].sort(key=lambda m: m["floor"])
    return by_event


def _two_sided(m: dict) -> bool:
    return (m["bid"] or 0) > 0 and (m["ask"] or 1) < 1.0 \
        and m["bid_sz"] > 0 and m["ask_sz"] > 0


def analyse(snap: dict) -> dict:
    markets = snap["markets"]
    att = defaultdict(int)
    findings: list[dict] = []
    inversions: set[tuple[str, str]] = set()

    # ---- Check 1: ladder monotonicity, P(>=N) >= P(>=N+1) -------------
    for event, legs in _ladder(markets).items():
        for a, b in zip(legs, legs[1:]):          # a.floor < b.floor
            att["F0_pairs"] += 1
            # a mid-level inversion is only a *candidate*
            mid_a = ((a["bid"] or 0) + (a["ask"] or 0)) / 2
            mid_b = ((b["bid"] or 0) + (b["ask"] or 0)) / 2
            if mid_b <= mid_a:
                continue
            att["F0_mid_inversions"] += 1
            inversions.add((a["ticker"], b["ticker"]))
            if not (_two_sided(a) and _two_sided(b)):
                continue
            att["F1_two_sided"] += 1
            # executable form: BUY the lower strike at ask, SELL the higher
            # strike at bid.  Payoff >= 0 always, > 0 when the team lands in
            # [floor_a, floor_b).  Locked profit = bid_b - ask_a.
            gross = (b["bid"] or 0) - (a["ask"] or 0)
            if gross <= 0:
                continue
            att["F2_executable"] += 1
            size = min(a["ask_sz"], b["bid_sz"])
            if size < MIN_SIZE:
                att["F3_thin"] += 1
                continue
            att["F3_depth"] += 1
            fees = fee_per_contract(a["ask"], maker=False, series_ticker=a["series"]) \
                + fee_per_contract(1 - (b["bid"] or 0), maker=False, series_ticker=b["series"])
            net = gross - fees - 2 * SLIPPAGE
            if net <= 0:
                continue
            att["F4_net_positive"] += 1
            findings.append({
                "kind": "ladder_monotonicity", "event": event,
                "legs": [a["ticker"], b["ticker"]],
                "gross": round(gross, 4), "fees": round(fees, 4),
                "net_per_ct": round(net, 4), "size": size,
                "locked_usd": round(net * size, 2),
            })

    # ---- Check 2: exact-N partition sums to 1 -------------------------
    exact_by_event = defaultdict(list)
    for m in markets:
        if m["strike_type"] == "equal" or (m["template"].endswith("EXACTWINS")):
            exact_by_event[m["event"]].append(m)
    for event, legs in exact_by_event.items():
        att["F0_partitions"] += 1
        if not all(_two_sided(m) for m in legs):
            continue
        att["F1_partitions_two_sided"] += 1
        sum_ask = sum(m["ask"] for m in legs)
        sum_bid = sum(m["bid"] for m in legs)
        # NOTE: the buy-all-legs direction is only risk-free if the listed
        # strikes are EXHAUSTIVE.  Truncated ladders make sum_ask < 1
        # meaningless; sum_bid > 1 stays safe either way.
        if sum_bid > 1.0:
            att["F2_partitions_sell_side"] += 1
            size = min(m["bid_sz"] for m in legs)
            fees = sum(fee_per_contract(1 - m["bid"], maker=False,
                                        series_ticker=m["series"]) for m in legs)
            net = sum_bid - 1.0 - fees - len(legs) * SLIPPAGE
            if size >= MIN_SIZE and net > 0:
                att["F4_partitions_net"] += 1
                findings.append({
                    "kind": "partition_sum_bid>1", "event": event,
                    "legs": [m["ticker"] for m in legs],
                    "sum_bid": round(sum_bid, 4), "fees": round(fees, 4),
                    "net_per_ct": round(net, 4), "size": size,
                    "locked_usd": round(net * size, 2),
                })

    # ---- Check 3: ladder increment vs exact bucket --------------------
    # P(>=N) - P(>=N+1) must equal P(exactly N).  Only computable where both
    # families are listed for the same team-season.
    att["events_with_exact_family"] = len(exact_by_event)

    # ---- Check 4: league adding-up constraint -------------------------
    # A COMPLETE unit-increment ladder 1..G for a team is a synthetic on the
    # win count itself: one contract of every ">= N" leg pays exactly W dollars.
    # Summed over a whole league the payoff is a structural constant
    # (games played, less tie games), so the basket is a true dutch-book test
    # that needs no forecast.  NFL: 32 teams x 17 games / 2 = 272 wins, minus
    # one per tie game (ties are ~0-2 per season).
    for lg, prefix, games, teams in [("NFL", "KXNFLWINS-27", 17, 32)]:
        legs = [m for m in markets
                if m["event"].startswith(prefix) and m["strike_type"] == "greater_or_equal"]
        by_ev = defaultdict(dict)
        for m in legs:
            by_ev[m["event"]][int(m["floor"])] = m
        complete = {e: d for e, d in by_ev.items() if set(d) == set(range(1, games + 1))}
        if len(complete) != teams:
            att[f"{lg}_league_basket_incomplete"] = len(complete)
            continue
        cost = sum(d[n]["ask"] if d[n]["ask"] is not None else 1.0
                   for d in complete.values() for n in range(1, games + 1))
        proceeds = sum(d[n]["bid"] or 0.0
                       for d in complete.values() for n in range(1, games + 1))
        fee_buy = sum(fee_per_contract(d[n]["ask"] if d[n]["ask"] is not None else 1.0)
                      for d in complete.values() for n in range(1, games + 1))
        fee_sell = sum(fee_per_contract(1 - (d[n]["bid"] or 0.0))
                       for d in complete.values() for n in range(1, games + 1))
        payoff_max = teams * games / 2                    # no tie games
        payoff_min = payoff_max - 3                       # generous tie allowance
        att[f"{lg}_basket_cost_ask"] = round(cost, 2)
        att[f"{lg}_basket_proceeds_bid"] = round(proceeds, 2)
        att[f"{lg}_basket_mid"] = round((cost + proceeds) / 2, 2)
        att[f"{lg}_basket_true_payoff"] = payoff_max
        buy_net = payoff_min - cost - fee_buy
        sell_net = proceeds - payoff_max - fee_sell
        att[f"{lg}_basket_buy_net"] = round(buy_net, 2)
        att[f"{lg}_basket_sell_net"] = round(sell_net, 2)
        if buy_net > 0 or sell_net > 0:
            findings.append({
                "kind": "league_adding_up", "event": prefix,
                "legs": [prefix], "net_per_ct": round(max(buy_net, sell_net), 4),
                "size": min(min(d[n]["ask_sz"] if buy_net > 0 else d[n]["bid_sz"]
                                for n in range(1, games + 1))
                            for d in complete.values()),
                "locked_usd": round(max(buy_net, sell_net), 2),
            })

    return {"attrition": dict(att), "findings": findings,
            "inversions": sorted(inversions), "n_markets": len(markets)}


def report() -> None:
    snaps = sorted(glob.glob(os.path.join(SNAPS, "snap_*.json")))
    if not snaps:
        print("no snapshots; run --scan first")
        return
    results = []
    for p in snaps:
        s = json.load(open(p))
        r = analyse(s)
        r["ts"] = s["ts"]
        results.append(r)
        print(f"\n=== {s['ts']}  ({r['n_markets']} live markets)")
        for kk, vv in sorted(r["attrition"].items()):
            print(f"    {kk:32s} {vv}")
        for f in r["findings"]:
            print("    FINDING", json.dumps(f))
    if len(results) >= 2:
        first = {tuple(sorted(f["legs"])) for f in results[0]["findings"]}
        last = {tuple(sorted(f["legs"])) for f in results[-1]["findings"]}
        print(f"\nF5 — executable violations persisting first->last snapshot: "
              f"{len(first & last)}")
        for legs in sorted(first & last):
            print("   ", legs)
        # Stability diagnostic: even though nothing is executable, are the SAME
        # pairs mid-inverted in both snapshots?  Persistent inversions would mean
        # a stale quoting artifact; churn means noise on empty books.
        i0 = {tuple(x) for x in results[0]["inversions"]}
        i1 = {tuple(x) for x in results[-1]["inversions"]}
        both = i0 & i1
        print(f"mid inversions: {len(i0)} in first, {len(i1)} in last, "
              f"{len(both)} in both ({100*len(both)/max(1, len(i0 | i1)):.0f}% overlap)")
    with open(os.path.join(DATA, "wins_scanner_results.json"), "w") as fh:
        json.dump(results, fh, indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--tag", default=None)
    a = ap.parse_args()
    if a.scan:
        scan(a.tag)
    if a.report or not a.scan:
        report()
