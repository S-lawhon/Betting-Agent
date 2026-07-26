"""
satellites_research/analyze_rt.py
─────────────────────────────────
Study B analysis — RT family.  READ-ONLY, no orders, no pod.

Two questions:
  (1) FALLBACK / NON-PARTITION.  RT.pdf: "If no data is available a week after
      that Monday, then all markets will resolve to No."  How often did that
      fire, and is the all-No state even IDENTIFIABLE from settlement data?
  (2) READ-TIME DRIFT.  Settlement reads the Tomatometer at 10:00 AM ET on the
      Monday after wide release, which is also the Last Trading Time.  Does the
      market's implied score on release-Friday differ systematically from the
      Monday read, by more than fee + half-spread?

Prices come ONLY from candlesticks (settled LIST endpoints null bid/ask and
`last_price` is settlement-contaminated).  Every anchor is measured backwards
from close_time.  Statistics are clustered by EVENT (title), never per strike:
strikes within one film are the same observation.
"""
from __future__ import annotations

import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CAND = os.path.join(DATA, "candles")

ANCHORS_H = [72, 48, 24, 6, 1]      # hours before the Monday 10:00 ET read


def load_events() -> dict[str, list[dict]]:
    ev = defaultdict(list)
    for fp in glob.glob(os.path.join(DATA, "rt_markets", "RT__*.json")):
        for m in json.load(open(fp)):
            if m.get("result"):
                ev[m["event_ticker"]].append(m)
    for e in ev:
        ev[e].sort(key=lambda m: m["floor_strike"])
    return ev


def realized_band(ms: list[dict]) -> tuple[int | None, int | None]:
    yes = [m["floor_strike"] for m in ms if m["result"] == "yes"]
    no = [m["floor_strike"] for m in ms if m["result"] == "no"]
    return (max(yes) + 1 if yes else None, min(no) if no else None)


def _iso(ts):
    from datetime import datetime
    return datetime.fromisoformat((ts or "").replace("Z", "+00:00")).timestamp()


def candle_at(candles, ts):
    best = None
    for c in candles:
        t = c.get("end_period_ts")
        if t is None or t > ts:
            continue
        if best is None or t > best.get("end_period_ts", -1):
            best = c
    return best


def quote_at(ticker: str, close_ts: float, h: int):
    p = os.path.join(CAND, f"{ticker}.json")
    if not os.path.exists(p):
        return None
    cs = (json.load(open(p)) or {}).get("candlesticks") or []
    c = candle_at(cs, close_ts - h * 3600)
    if not c:
        return None

    def g(side, key):
        v = (c.get(side) or {}).get(key)
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    bid, ask = g("yes_bid", "close_dollars"), g("yes_ask", "close_dollars")
    if bid is None or ask is None or ask <= bid:
        return None
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2,
            "half_spread": (ask - bid) / 2}


def implied_score(ms: list[dict], close_ts: float, h: int):
    """Market-implied EXPECTED score from the nested P(score > X) ladder.

    For a nested ladder over strikes x_1 < ... < x_k with survival probs
    p_i = P(S > x_i), E[S] is not identified without a tail assumption, so we
    report the survival-sum estimator over the listed grid, which is what a
    trader can actually replicate, plus the implied MEDIAN (the strike where
    the ladder crosses 0.5, linearly interpolated).  The median is the robust
    one and is what the drift test uses.
    """
    pts = []
    for m in ms:
        q = quote_at(m["ticker"], close_ts, h)
        if q:
            pts.append((m["floor_strike"], q["mid"], q["half_spread"]))
    if len(pts) < 3:
        return None
    pts.sort()
    med = None
    for (x0, p0, _), (x1, p1, _) in zip(pts, pts[1:]):
        if p0 >= 0.5 >= p1 and p0 != p1:
            med = x0 + (p0 - 0.5) / (p0 - p1) * (x1 - x0)
            break
    if med is None:
        med = pts[0][0] if pts[0][1] < 0.5 else pts[-1][0]
    return {"median": med, "n_legs": len(pts),
            "mean_half_spread": sum(p[2] for p in pts) / len(pts)}


def boot_ci(vals, n=10000, seed=7):
    if len(vals) < 3:
        return (None, None)
    r = random.Random(seed)
    out = []
    for _ in range(n):
        s = [vals[r.randrange(len(vals))] for _ in vals]
        out.append(sum(s) / len(s))
    out.sort()
    return (out[int(0.025 * n)], out[int(0.975 * n)])


def main() -> None:
    ev = load_events()
    print(f"settled RT events: {len(ev)}")

    # ---- (1) fallback census -----------------------------------------
    all_no = [e for e, ms in ev.items() if not any(m["result"] == "yes" for m in ms)]
    incoherent = []
    for e, ms in ev.items():
        yes = [m["floor_strike"] for m in ms if m["result"] == "yes"]
        no = [m["floor_strike"] for m in ms if m["result"] == "no"]
        if yes and no and max(yes) >= min(no):
            incoherent.append(e)
    print(f"  all-No settlements (fallback candidates): {len(all_no)} {all_no}")
    print(f"  monotonicity violations in settlement:    {len(incoherent)}")
    strike_types = {m.get("strike_type") for ms in ev.values() for m in ms}
    print(f"  strike types present: {strike_types}")

    # ---- (2) read-time drift -----------------------------------------
    print("\ndrift: market-implied MEDIAN score at T-h vs realized score")
    print(f"{'event':22s} {'real':>6} " +
          " ".join(f"{'T-'+str(h):>8s}" for h in ANCHORS_H))
    rows = {}
    for e, ms in sorted(ev.items()):
        close_ts = _iso(ms[0]["close_time"])
        lo, hi = realized_band(ms)
        if lo is None or hi is None:
            continue
        real = (lo + hi) / 2
        line, errs = [], {}
        for h in ANCHORS_H:
            imp = implied_score(ms, close_ts, h)
            if imp is None:
                line.append(f"{'-':>8s}")
                continue
            errs[h] = imp["median"] - real       # +ve = market too high
            line.append(f"{imp['median']:8.1f}")
        rows[e] = errs
        print(f"{e:22s} {real:6.1f} " + " ".join(line))

    print("\nsigned drift (implied median - realized), event-clustered:")
    print("  *** DIAGNOSTIC ONLY — see the note below; this estimator carries a")
    print("  *** built-in -0.5pt bias and must NOT be read as an edge.")
    for h in ANCHORS_H:
        vals = [r[h] for r in rows.values() if h in r]
        if len(vals) < 3:
            print(f"  T-{h:3d}h  n={len(vals):2d}  (too few)")
            continue
        mean = sum(vals) / len(vals)
        lo, hi = boot_ci(vals)
        pos = sum(1 for v in vals if v > 0)
        print(f"  T-{h:3d}h  n={len(vals):2d}  mean={mean:+6.2f} pts  "
              f"95%CI[{lo:+.2f},{hi:+.2f}]  above-realized {pos}/{len(vals)}")
    print("""
  NOTE (why the above is an artifact): 'realized' is the midpoint of the
  settlement bracket [max(yes)+1, min(no)], which for an exact bracket equals
  the integer score itself; the interpolated 0.5-crossing of a step survival
  function sits at the MIDPOINT of the two bracketing strikes, i.e. exactly
  0.5 grid-points lower.  The -0.49 at T-1h is that construction, not drift.
  The honest test is per-strike: does the quoted price differ from the realised
  0/1 payout by more than fee + half-spread?  That is the table below.""")

    # ---- (3) the test that actually matters: per-strike, net of costs --
    from src.kalshi_fees import fee_per_contract
    print("\nper-strike mispricing vs realised payout (event-clustered):")
    print(f"{'anchor':>7} {'strikes':>8} {'events':>7} {'mean(payout-mid)':>17} "
          f"{'95% CI (event-clustered)':>26} {'best-side net edge':>19}")
    for h in ANCHORS_H:
        per_event: dict[str, list[float]] = defaultdict(list)
        per_event_net: dict[str, list[float]] = defaultdict(list)
        n_strikes = 0
        for e, ms in ev.items():
            close_ts = _iso(ms[0]["close_time"])
            for m in ms:
                q = quote_at(m["ticker"], close_ts, h)
                if not q:
                    continue
                payout = 1.0 if m["result"] == "yes" else 0.0
                per_event[e].append(payout - q["mid"])
                # what a taker could actually have earned, best side, net
                buy_yes = payout - q["ask"] - fee_per_contract(q["ask"])
                buy_no = (1 - payout) - (1 - q["bid"]) - fee_per_contract(1 - q["bid"])
                per_event_net[e].append(max(buy_yes, buy_no))
                n_strikes += 1
        if len(per_event) < 3:
            continue
        ev_means = [sum(v) / len(v) for v in per_event.values()]
        lo, hi = boot_ci(ev_means)
        # NOTE: max(buy_yes, buy_no) is chosen WITH HINDSIGHT — it is an upper
        # bound on any strategy, not an achievable one.  Reported as a ceiling.
        ev_net = [sum(v) / len(v) for v in per_event_net.values()]
        ceiling = sum(ev_net) / len(ev_net)
        print(f"T-{h:3d}h {n_strikes:8d} {len(per_event):7d} "
              f"{sum(ev_means)/len(ev_means):+16.4f} "
              f"  [{lo:+.4f},{hi:+.4f}] {ceiling:+18.4f}")
    print("  (last column is an ORACLE ceiling — best side picked with hindsight.)")


if __name__ == "__main__":
    main()


def executable_test() -> None:
    """The tradeable version of the drift question.

    Strategy S+: at anchor h, BUY YES at the ask on every listed strike of the
    event (a long position on the film's score).
    Strategy S-: BUY NO at the ask on every strike (short the score).
    Both are pure takers, so both pay 0.07*P*(1-P) per contract.  Event-clustered
    bootstrap CI; each film is ONE observation.
    """
    from src.kalshi_fees import fee_per_contract
    ev = load_events()
    print("\nEXECUTABLE test — buy the whole ladder at the ask, net of taker fee")
    print(f"{'anchor':>7} {'legs':>6} {'ev':>4} {'half-spread':>12} "
          f"{'S+ net c/ct':>12} {'95% CI':>20} {'S- net c/ct':>12} {'95% CI':>20}")
    for h in ANCHORS_H:
        plus, minus, hs = defaultdict(list), defaultdict(list), []
        n = 0
        for e, ms in ev.items():
            close_ts = _iso(ms[0]["close_time"])
            for m in ms:
                q = quote_at(m["ticker"], close_ts, h)
                if not q:
                    continue
                payout = 1.0 if m["result"] == "yes" else 0.0
                plus[e].append(payout - q["ask"] - fee_per_contract(q["ask"]))
                no_ask = 1 - q["bid"]
                minus[e].append((1 - payout) - no_ask - fee_per_contract(no_ask))
                hs.append(q["half_spread"])
                n += 1
        if len(plus) < 3:
            continue
        pm = [sum(v) / len(v) for v in plus.values()]
        mm = [sum(v) / len(v) for v in minus.values()]
        plo, phi = boot_ci(pm)
        mlo, mhi = boot_ci(mm)
        print(f"T-{h:3d}h {n:6d} {len(plus):4d} {100*sum(hs)/len(hs):11.2f}c "
              f"{100*sum(pm)/len(pm):+11.2f}c [{100*plo:+6.2f},{100*phi:+6.2f}]c "
              f"{100*sum(mm)/len(mm):+11.2f}c [{100*mlo:+6.2f},{100*mhi:+6.2f}]c")


if __name__ == "__main__":
    executable_test()
