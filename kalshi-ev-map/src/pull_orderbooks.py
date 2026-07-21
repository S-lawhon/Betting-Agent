"""Sample top-3-level orderbook depth for a stratified sample of markets.

Picks the top-N markets by 24h volume within each series category (plus a
random tail sample), pulls /markets/{ticker}/orderbook, and computes spread
and $ depth at the top 3 levels each side.

Output: data/orderbook_sample.parquet
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc

import pandas as pd

kc.MIN_INTERVAL = 0.15

PER_CATEGORY_TOP = 40
PER_CATEGORY_RANDOM = 15


def top3_depth(levels):
    """levels: [[price_dollars, size_fp], ...] sorted best-first? Kalshi
    returns ascending price for yes side; best bid is the max price."""
    if not levels:
        return None, 0.0
    lv = sorted(([kc.fnum(p), kc.fnum(s)] for p, s in levels),
                key=lambda x: -(x[0] or 0))[:3]
    best = lv[0][0]
    dollars = sum((p or 0) * (s or 0) for p, s in lv)
    return best, dollars


def main():
    df = pd.read_parquet(kc.DATA_DIR / "kalshi_universe.parquet")
    active = df[df["volume_24h"].fillna(0) > 0]
    sample = []
    for cat, g in active.groupby("family"):
        top = g.nlargest(PER_CATEGORY_TOP, "volume_24h")
        rest = g.drop(top.index)
        rnd = rest.sample(min(PER_CATEGORY_RANDOM, len(rest)), random_state=7)
        sample.append(pd.concat([top, rnd]))
    sample = pd.concat(sample).drop_duplicates(subset="ticker")
    print(f"[ob] sampling {len(sample)} markets", flush=True)

    rows = []
    for i, r in enumerate(sample.itertuples()):
        try:
            ob = kc.get(f"/markets/{r.ticker}/orderbook", {"depth": 10})
        except Exception as e:
            print(f"[warn] {r.ticker}: {e}", flush=True)
            continue
        book = ob.get("orderbook_fp") or ob.get("orderbook") or {}
        yes_levels = book.get("yes_dollars") or book.get("yes") or []
        no_levels = book.get("no_dollars") or book.get("no") or []
        # 'yes' side = resting bids for YES; 'no' side = resting bids for NO.
        # Best YES ask = 1 - best NO bid.
        yes_bid, yes_bid_depth3 = top3_depth(yes_levels)
        no_bid, no_bid_depth3 = top3_depth(no_levels)
        yes_ask = (1 - no_bid) if no_bid is not None else None
        rows.append({
            "ticker": r.ticker,
            "category": r.category,
            "series_ticker": r.series_ticker,
            "volume_24h": r.volume_24h,
            "ob_yes_bid": yes_bid,
            "ob_yes_ask": yes_ask,
            "ob_spread": (yes_ask - yes_bid)
                if yes_bid is not None and yes_ask is not None else None,
            "bid_depth3_dollars": yes_bid_depth3,
            "ask_depth3_dollars": no_bid_depth3,
        })
        if (i + 1) % 100 == 0:
            print(f"[ob] {i+1}/{len(sample)}", flush=True)
    out = pd.DataFrame(rows)
    out.to_parquet(kc.DATA_DIR / "orderbook_sample.parquet", index=False)
    print(f"[done] {len(out)} books -> orderbook_sample.parquet", flush=True)


if __name__ == "__main__":
    main()
