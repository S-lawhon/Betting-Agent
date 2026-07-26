"""
satellites_research/analyze_awards.py
─────────────────────────────────────
Study A analysis — award tie regimes.  READ-ONLY, no orders, no pod.

Verified rule (verbatim, GLOBES.pdf / CRITICS.pdf / AMAS.pdf / TONYS.pdf /
BILLBOARDAWARDS.pdf):
    "If there is a tie, the strike for 'tie' will resolve to Yes and the strikes
     for the shared winners alone will resolve to No."
and, in the unified AWARDS rulebook (AWARDS/SAGAWARDS/BETAWARDS/AWARDSCMA/
LATINGRAMMY(S)/IHEARTRADIOMUSICAWARDS/PRODUCERSGUILDAWARDS/
FILMINDEPENDENTSPIRITAWARDS):
    "If there is a tie, a strike for a tie will resolve Yes and all others will
     resolve No."
OSCARS / EMMYS / GRAMMY / ACMA / BAFTA / GAMEAWARDS are SILENT on ties.

This differs from the golf/KXLEADER dead-heat rule (pro-rata $1/n): a shared
winner here pays the holder ZERO.  So the questions are:
  (a) does every event actually LIST a tie strike (if not, a tie pays nobody)?
  (b) what does the tie strike cost, and does 1c clear the base rate?
  (c) is the favourite's YES haircut visible, i.e. bigger than one tick?

Prices come from candlesticks only (settled LIST endpoints null bid/ask;
`last_price` is settlement-contaminated).
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi_fees import fee_per_contract  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CAND = os.path.join(DATA, "candles")

GROUP = {}
for g, tpls in [
    ("G1 explicit tie strike", ["GLOBES", "CRITICS", "AMAS", "TONYS", "BILLBOARDAWARDS"]),
    ("G2 unified AWARDS rulebook", ["AWARDS", "SAGAWARDS", "BETAWARDS", "AWARDSCMA",
                                    "LATINGRAMMY", "LATINGRAMMYS",
                                    "IHEARTRADIOMUSICAWARDS", "PRODUCERSGUILDAWARDS",
                                    "FILMINDEPENDENTSPIRITAWARDS"]),
    ("G3 silent on ties", ["OSCARS", "EMMYS", "GRAMMY", "ACMA", "BAFTA", "GAMEAWARDS"]),
]:
    for t in tpls:
        GROUP[t] = g

ANCHORS_H = [168, 72, 24, 6, 1]


def _iso(ts):
    from datetime import datetime
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def quote_at(ticker, close_ts, h):
    p = os.path.join(CAND, f"{ticker}.json")
    if not os.path.exists(p):
        return None
    cs = (json.load(open(p)) or {}).get("candlesticks") or []
    best = None
    for c in cs:
        t = c.get("end_period_ts")
        if t is None or t > close_ts - h * 3600:
            continue
        if best is None or t > best["end_period_ts"]:
            best = c
    if not best:
        return None

    def g(side, key):
        try:
            return float((best.get(side) or {}).get(key))
        except (TypeError, ValueError):
            return None
    bid, ask = g("yes_bid", "close_dollars"), g("yes_ask", "close_dollars")
    if bid is None or ask is None:
        return None
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}


def is_tie(m):
    return (m.get("yes_sub_title") or "").strip().lower() == "tie"


def main() -> None:
    ev = defaultdict(list)
    for fp in glob.glob(os.path.join(DATA, "award_markets", "*.json")):
        tpl = os.path.basename(fp)[:-5].split("__")[0]
        for m in json.load(open(fp)):
            ev[(GROUP[tpl], tpl, m["event_ticker"])].append(m)

    # ---- (a) tie-strike listing census -------------------------------
    print("TIE-STRIKE LISTING CENSUS (all listed events with markets on the API)")
    print(f"{'regime':28s} {'template':14s} {'events':>7} {'with tie strike':>16} "
          f"{'settled events':>15} {'ties observed':>14}")
    agg = defaultdict(lambda: [0, 0, 0, 0])
    for (g, tpl, e), ms in ev.items():
        a = agg[(g, tpl)]
        a[0] += 1
        if any(is_tie(m) for m in ms):
            a[1] += 1
        settled = [m for m in ms if m.get("result")]
        if settled and len(settled) == len(ms):
            a[2] += 1
            if any(is_tie(m) and m["result"] == "yes" for m in settled):
                a[3] += 1
    for (g, tpl), a in sorted(agg.items()):
        print(f"{g:28s} {tpl:14s} {a[0]:7d} {a[1]:16d} {a[2]:15d} {a[3]:14d}")
    tot = [sum(a[i] for a in agg.values()) for i in range(4)]
    print(f"{'TOTAL':28s} {'':14s} {tot[0]:7d} {tot[1]:16d} {tot[2]:15d} {tot[3]:14d}")

    # ---- (b) what the tie strike costs -------------------------------
    print("\nTIE-STRIKE PRICING (candlestick quotes, settled events only)")
    print(f"{'event':30s} {'tmpl':10s} " +
          " ".join(f"{'T-'+str(h)+'h':>14s}" for h in ANCHORS_H) + "  result")
    tie_asks = []
    for (g, tpl, e), ms in sorted(ev.items()):
        ties = [m for m in ms if is_tie(m)]
        if not ties or not all(m.get("result") for m in ms):
            continue
        t = ties[0]
        close_ts = _iso(t.get("close_time"))
        cells = []
        for h in ANCHORS_H:
            q = quote_at(t["ticker"], close_ts, h) if close_ts else None
            cells.append(f"{100*q['bid']:5.1f}/{100*q['ask']:5.1f}c" if q
                         else f"{'-':>14s}")
            if h == 1 and q:
                tie_asks.append(q["ask"])
        print(f"{e:30s} {tpl:10s} " + " ".join(cells) + f"  {t.get('result')}")
    if tie_asks:
        n = len(tie_asks)
        srt = sorted(tie_asks)
        med = srt[n // 2]
        at_tick = sum(1 for a in tie_asks if a <= 0.0101)
        # NOTE: mean is quoted for completeness but is contaminated by a handful
        # of stale one-lot candles printing 97-98c asks on an empty book — the
        # exact artifact this task is warned about.  Median + share-at-tick are
        # the honest summary.
        fee = fee_per_contract(med, maker=False)
        print(f"\n  tie ASK at T-1h over {n} settled events: "
              f"median {100*med:.2f}c, mean {100*sum(tie_asks)/n:.2f}c, "
              f"{at_tick}/{n} sitting AT the 1c minimum tick")
        print(f"  taker fee at the median ask = {100*fee:.3f}c/ct")
        print(f"  break-even tie base rate    = {100*(med+fee):.2f}% per category-year")
        print(f"  observed ties in this sample = {tot[3]} / {tot[2]} settled events")
        print("  (the 1c Minimum Tick is a hard floor: a tie strike CANNOT be")
        print("   bought below 1c, so any base rate under ~1.1% makes the long")
        print("   side -EV no matter how 'ignored' the strike is.)")

    # ---- (c) favourite haircut / event overround ---------------------
    print("\nEVENT OVERROUND AND FAVOURITE PRICE (T-24h, settled events)")
    print(f"{'event':30s} {'legs':>5} {'sum bid':>8} {'sum ask':>8} {'fav bid/ask':>14} {'fav won':>8}")
    for (g, tpl, e), ms in sorted(ev.items()):
        if not all(m.get("result") for m in ms) or len(ms) < 3:
            continue
        close_ts = _iso(ms[0].get("close_time"))
        qs = {m["ticker"]: quote_at(m["ticker"], close_ts, 24) for m in ms}
        got = [(m, qs[m["ticker"]]) for m in ms if qs.get(m["ticker"])]
        if len(got) < len(ms) - 1 or not got:
            continue
        sb = sum(q["bid"] for _, q in got)
        sa = sum(q["ask"] for _, q in got)
        fav, fq = max(got, key=lambda x: x[1]["mid"])
        print(f"{e:30s} {len(got):5d} {sb:8.3f} {sa:8.3f} "
              f"{100*fq['bid']:6.1f}/{100*fq['ask']:5.1f}c "
              f"{('Y' if fav.get('result') == 'yes' else 'n'):>8s}")


if __name__ == "__main__":
    main()


def anchor_table() -> None:
    """Robust tie-strike quote summary by anchor, plus the information caveat.

    IMPORTANT: for these award templates the Last Trading Time is the 10:00 AM ET
    *after* the ceremony, so T-6h and T-1h are POST-INFORMATION — the winner is
    already public and the tie strike collapses to the 1c tick.  Only the T-168h
    / T-72h / T-24h anchors are ex-ante tradeable prices.
    """
    ev = defaultdict(list)
    for fp in glob.glob(os.path.join(DATA, "award_markets", "*.json")):
        tpl = os.path.basename(fp)[:-5].split("__")[0]
        for m in json.load(open(fp)):
            ev[(tpl, m["event_ticker"])].append(m)
    print("\nTIE-STRIKE QUOTE BY ANCHOR (settled events; median across events)")
    print(f"{'anchor':>8} {'n':>4} {'med bid':>9} {'med ask':>9} {'med mid':>9}  tradeable ex ante?")
    for h in ANCHORS_H:
        bids, asks = [], []
        for (tpl, e), ms in ev.items():
            ties = [m for m in ms if is_tie(m)]
            if not ties or not all(m.get("result") for m in ms):
                continue
            q = quote_at(ties[0]["ticker"], _iso(ties[0].get("close_time")), h)
            if q:
                bids.append(q["bid"])
                asks.append(q["ask"])
        if not asks:
            continue
        med = lambda v: sorted(v)[len(v) // 2]
        flag = "YES (pre-ceremony)" if h >= 24 else "NO - winner already public"
        print(f"T-{h:4d}h {len(asks):4d} {100*med(bids):8.1f}c {100*med(asks):8.1f}c "
              f"{100*(med(bids)+med(asks))/2:8.1f}c  {flag}")


if __name__ == "__main__":
    anchor_table()
