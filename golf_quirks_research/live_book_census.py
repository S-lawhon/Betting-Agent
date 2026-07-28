"""
golf_quirks_research/live_book_census.py
────────────────────────────────────────
What P-022's LIVE references actually look like, per market, at the moment it
would quote — and how much size rests between the market and its quote.

Two things the pre-flight recorded but did not quantify:

  * every in-band reference is a one-sided ask (`yes_bid: null`), so `_mid()`'s
    one-sided branch drives every placement;
  * the pod has NO size or depth screen.

`ask_qty` from `KalshiPublic.orderbook` is the depth of the best NO bid, i.e.
the size resting at a price BETTER than ours. For a resting YES ask that is
the queue barrier: a taker buying YES lifts the cheapest asks first, so every
contract offered below our quote must be consumed before we fill. That is the
number a depth screen would have to look at, and it is not `ask_qty` alone —
it is the sum over every level strictly better than the quote.

    python3 live_book_census.py --event KXLPGAR1LEAD-AIGWO26 --json census.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi_public import KalshiPublic, fnum  # noqa: E402

BAND = (0.03, 0.12)
OFFSET = 0.02


def raw_levels(k: KalshiPublic, ticker: str) -> Dict[str, List]:
    ob = k.get(f"/markets/{ticker}/orderbook", {"depth": 100}) or {}
    book = ob.get("orderbook_fp") or ob.get("orderbook") or {}
    return {"yes": book.get("yes_dollars") or book.get("yes") or [],
            "no": book.get("no_dollars") or book.get("no") or []}


def _norm(levels) -> List[Dict[str, float]]:
    out = []
    for lvl in levels or []:
        try:
            px, qty = fnum(lvl[0]), fnum(lvl[1]) or 0.0
        except (TypeError, IndexError):
            continue
        if px is None:
            continue
        if px > 1.5:
            px = px / 100.0
        out.append({"px": px, "qty": qty})
    return out


def census(tickers: Sequence[str]) -> List[Dict[str, Any]]:
    k = KalshiPublic()
    rows: List[Dict[str, Any]] = []
    for tk in tickers:
        ob = k.orderbook(tk)
        if not ob:
            rows.append({"ticker": tk, "class": "no_book"})
            continue
        b, a = ob.get("yes_bid"), ob.get("yes_ask")
        if a is not None and 0 < a < 1 and (b is None or b <= 0):
            mid, cls = a, "one_sided_ask"
        elif b is None or a is None or not (0 < b <= a < 1):
            rows.append({"ticker": tk, "class": "unpriceable",
                         "yes_bid": b, "yes_ask": a})
            continue
        else:
            mid, cls = (b + a) / 2.0, "two_sided"

        quote_px = round(mid + OFFSET, 2)
        lv = raw_levels(k, tk)
        # YES asks are NO bids: a NO bid at p is a YES ask at 1-p.
        yes_asks = [{"px": round(1.0 - x["px"], 4), "qty": x["qty"]}
                    for x in _norm(lv["no"])]
        ahead = sum(x["qty"] for x in yes_asks if x["px"] < quote_px - 1e-9)
        rows.append({
            "ticker": tk, "class": cls, "yes_bid": b, "yes_ask": a,
            "mid": round(mid, 4), "quote_px": quote_px,
            "in_band": BAND[0] <= mid <= BAND[1],
            "top_ask_qty": ob.get("ask_qty"), "top_bid_qty": ob.get("bid_qty"),
            "n_yes_bid_levels": len(_norm(lv["yes"])),
            "n_yes_ask_levels": len(yes_asks),
            "size_ahead_of_quote": round(ahead, 1),
        })
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--event", default="KXLPGAR1LEAD-AIGWO26")
    ap.add_argument("--series", default=None,
                    help="defaults to the event's leading segment")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    k = KalshiPublic()
    series = args.series or args.event.split("-")[0]
    tickers = [m["ticker"] for m in k.open_markets(series)
               if (m.get("event_ticker") or "") == args.event]
    print(f"{args.event}: {len(tickers)} open markets")

    rows = census(tickers)
    by_class: Dict[str, int] = {}
    for r in rows:
        by_class[r["class"]] = by_class.get(r["class"], 0) + 1
    print("\nbook class, ALL markets in the event")
    for kk, v in sorted(by_class.items(), key=lambda x: -x[1]):
        print(f"    {kk:16s} {v:4d}")

    band = [r for r in rows if r.get("in_band")]
    print(f"\nIN BAND {BAND}: {len(band)}")
    bc: Dict[str, int] = {}
    for r in band:
        bc[r["class"]] = bc.get(r["class"], 0) + 1
    for kk, v in sorted(bc.items(), key=lambda x: -x[1]):
        print(f"    {kk:16s} {v:4d}")

    if band:
        print(f"\n{'ticker':34s} {'cls':14s} {'bid':>6} {'ask':>6} {'quote':>6} "
              f"{'topAskQ':>8} {'aheadOfQuote':>13} {'#askLv':>7} {'#bidLv':>7}")
        for r in sorted(band, key=lambda x: x["ticker"]):
            print(f"{r['ticker']:34s} {r['class']:14s} "
                  f"{str(r['yes_bid']):>6} {r['yes_ask']:>6.2f} "
                  f"{r['quote_px']:>6.2f} {r['top_ask_qty']:>8.0f} "
                  f"{r['size_ahead_of_quote']:>13.0f} "
                  f"{r['n_yes_ask_levels']:>7d} {r['n_yes_bid_levels']:>7d}")
        ah = sorted(r["size_ahead_of_quote"] for r in band)
        print(f"\nsize resting AHEAD of the quote: min {ah[0]:.0f}  "
              f"median {ah[len(ah)//2]:.0f}  max {ah[-1]:.0f}")

    out = [r for r in rows if not r.get("in_band")]
    print(f"\nOUT OF BAND: {len(out)}")
    oc: Dict[str, int] = {}
    for r in out:
        key = r["class"]
        if r.get("mid") is not None:
            key += " >band" if r["mid"] > BAND[1] else " <band"
        oc[key] = oc.get(key, 0) + 1
    for kk, v in sorted(oc.items(), key=lambda x: -x[1]):
        print(f"    {kk:24s} {v:4d}")
    junk = [r for r in out if r.get("yes_bid") is not None
            and r.get("yes_ask") is not None
            and r["yes_bid"] <= 0.02 and r["yes_ask"] >= 0.90]
    print(f"    of which 'junk' (bid<=0.02 and ask>=0.90): {len(junk)}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
