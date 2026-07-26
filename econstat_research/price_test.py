"""P-027 step 5 — pre-close price / liquidity test on the prelim-labelled set.

For every SETTLED event of a series whose <econ stat> is a labelled
preliminary/flash/advance print, pull 1-minute candlesticks for the 6h before
Last Trading Time and record, per strike:

  * last candle with a GENUINE TWO-SIDED quote (bid>0, ask<1, spread<=25c)
    -- bare asks / empty books are discarded, never used as a price;
  * last executed print (candle with volume>0) in that window;
  * top-of-book depth is not in the candle payload, so traded volume over the
    window is the capacity proxy.

The final candle at/after close is always dropped: the book collapses to
bid 0 / ask 1.00 on halt, which would fabricate a 100c spread.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from src.kalshi_public import KalshiPublic, fnum  # noqa: E402

DATA = os.path.join(HERE, "data")
CAND = os.path.join(DATA, "candles")
os.makedirs(CAND, exist_ok=True)

PRELIM_SERIES = [
    "KXUSMICHCSP", "KXDECPIPREL", "KXFRCPIPREL", "KXITCPIPREL",
    "KXEZCPIYOYF", "KXEZGDPYOYF", "KXEZGDPQOQF", "KXDEGDPQOQF",
    "KXDEGDPYOYF", "KXESGDPQOQF", "KXESGDPYOYF", "KXFRGDPQOQP",
    "KXFRGDPYOYP", "KXITGDPQOQA", "KXITGDPYOYA",
]
LOOKBACK_H = 6
MAX_SPREAD = 0.25


def iso(t):
    return dt.datetime.fromisoformat(t.replace("Z", "+00:00"))


def candles(kp, series, ticker, close_iso):
    cache = os.path.join(CAND, f"{ticker}.json")
    if os.path.exists(cache):
        return json.load(open(cache))
    ct = iso(close_iso)
    s = int((ct - dt.timedelta(hours=LOOKBACK_H)).timestamp())
    e = int(ct.timestamp())
    d = kp.get(f"/series/{series}/markets/{ticker}/candlesticks",
               {"start_ts": s, "end_ts": e, "period_interval": 1})
    cs = (d or {}).get("candlesticks") or []
    json.dump(cs, open(cache, "w"))
    return cs


def main():
    kp = KalshiPublic(min_interval=0.8)
    rows = []
    for ser in PRELIM_SERIES:
        p = os.path.join(DATA, "markets", f"{ser}.json")
        if not os.path.exists(p):
            continue
        mkts = json.load(open(p))
        for m in mkts:
            if not m.get("settlement_ts") or not m.get("close_time"):
                continue
            if float(m.get("volume_fp") or 0) <= 0:
                continue
            ct = iso(m["close_time"])
            cs = candles(kp, ser, m["ticker"], m["close_time"])
            two_sided, last_trade = None, None
            for c in cs:
                if c.get("end_period_ts", 0) > ct.timestamp():
                    continue                      # never use post-halt candles
                b = fnum((c.get("yes_bid") or {}).get("close_dollars"))
                a = fnum((c.get("yes_ask") or {}).get("close_dollars"))
                if (b is not None and a is not None and b > 0 and a < 1.0
                        and (a - b) <= MAX_SPREAD):
                    two_sided = {"ts": c["end_period_ts"], "bid": b, "ask": a}
                v = fnum(c.get("volume_fp")) or 0.0
                px = fnum((c.get("price") or {}).get("close_dollars")) \
                    or fnum((c.get("price") or {}).get("previous_dollars"))
                if v > 0 and px is not None:
                    last_trade = {"ts": c["end_period_ts"], "px": px, "vol": v}
            rows.append({
                "series": ser, "event": m["event_ticker"], "ticker": m["ticker"],
                "strike": m.get("floor_strike"),
                "expiration_value": m.get("expiration_value"),
                "result": m.get("result"),
                "volume": float(m.get("volume_fp") or 0),
                "open_interest": float(m.get("open_interest_fp") or 0),
                "n_candles": len(cs),
                "two_sided": two_sided, "last_trade": last_trade,
            })
    json.dump(rows, open(os.path.join(DATA, "price_test.json"), "w"), indent=1)

    n = len(rows)
    ts = sum(1 for r in rows if r["two_sided"])
    lt = sum(1 for r in rows if r["last_trade"])
    print(f"traded settled markets in prelim-labelled set: {n}")
    print(f"  with a genuine two-sided quote (bid>0, ask<1, spread<={MAX_SPREAD*100:.0f}c) "
          f"in the {LOOKBACK_H}h pre-close window: {ts} ({100.0*ts/max(n,1):.1f}%)")
    print(f"  with an executed print in that window: {lt} "
          f"({100.0*lt/max(n,1):.1f}%)")
    sp = sorted(r["two_sided"]["ask"] - r["two_sided"]["bid"]
                for r in rows if r["two_sided"])
    if sp:
        print(f"  spread: median={sp[len(sp)//2]*100:.1f}c  "
              f"p75={sp[int(len(sp)*0.75)]*100:.1f}c  max={sp[-1]*100:.1f}c")
    vols = sorted((r["volume"] for r in rows), reverse=True)
    print(f"  per-market lifetime volume: median={vols[len(vols)//2]:.0f} "
          f"max={vols[0]:.0f} total={sum(vols):,.0f} contracts")


if __name__ == "__main__":
    main()
