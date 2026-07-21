"""Evaluate paper-maker quotes against the public trades tape + settlements.

Fill rule (conservative): a resting paper bid at price b is considered filled
if, after the quote snapshot and before market close, a trade printed at
yes_price <= b (someone sold into the bid level); symmetric for asks. At-price
prints count as half fills (queue position unknown). P&L: filled positions
held to settlement, maker fee = 0 (weather series are default maker-free).

Run for a given day after its markets settle (next morning):
  /usr/bin/python3 src/weather_paper_eval.py --day=2026-07-20
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc

import pandas as pd

kc.MIN_INTERVAL = 0.3
QUOTE_LIFE_S = 1800  # quotes are pulled/replaced at the next 30-min cycle


def trades_for(ticker, min_ts):
    return list(kc.paginate("/markets/trades", {
        "ticker": ticker, "limit": 1000, "min_ts": min_ts},
        list_key="trades", max_pages=10))


def main():
    day = None
    for a in sys.argv[1:]:
        if a.startswith("--day"):
            day = a.split("=")[1]
    if day is None:
        day = date.today().isoformat()
    q = pd.read_parquet(kc.DATA_DIR / "paper_quotes.parquet")
    q = q[q.target_day == day]
    if q.empty:
        print(f"no quotes logged for {day}")
        return
    # latest quote intent per ticker per snapshot; evaluate each snapshot's
    # quotes independently, then aggregate
    results = []
    for ticker, g in q.groupby("ticker"):
        try:
            mkt = kc.get(f"/markets/{ticker}")["market"]
        except Exception as e:
            print(f"[warn] {ticker}: {e}")
            continue
        result = mkt.get("result")
        if result not in ("yes", "no"):
            continue
        won = 1 if result == "yes" else 0
        first_snap = g.snap_utc.min()
        min_ts = int(datetime.fromisoformat(first_snap)
                     .replace(tzinfo=timezone.utc).timestamp())
        try:
            tape = trades_for(ticker, min_ts)
        except Exception as e:
            print(f"[warn] tape {ticker}: {e}")
            continue
        for r in g.itertuples():
            snap_ts = int(datetime.fromisoformat(r.snap_utc)
                          .replace(tzinfo=timezone.utc).timestamp())
            # quote lives until the next requote cycle, not until close
            after = [t for t in tape
                     if snap_ts < pd.Timestamp(t["created_time"]).timestamp()
                     <= snap_ts + QUOTE_LIFE_S]
            for side, px in (("bid", r.my_bid), ("ask", r.my_ask)):
                if px is None or pd.isna(px):
                    continue
                if side == "bid":
                    through = sum(kc.fnum(t["count_fp"]) or 0 for t in after
                                  if (kc.fnum(t["yes_price_dollars"]) or 1) < px)
                    at = sum(kc.fnum(t["count_fp"]) or 0 for t in after
                             if abs((kc.fnum(t["yes_price_dollars"]) or 1) - px) < 1e-9)
                    fill = min(r.size, through + 0.5 * at)
                    pnl = fill * (won - px)
                else:
                    through = sum(kc.fnum(t["count_fp"]) or 0 for t in after
                                  if (kc.fnum(t["yes_price_dollars"]) or 0) > px)
                    at = sum(kc.fnum(t["count_fp"]) or 0 for t in after
                             if abs((kc.fnum(t["yes_price_dollars"]) or 0) - px) < 1e-9)
                    fill = min(r.size, through + 0.5 * at)
                    pnl = fill * (px - won)
                results.append({
                    "ticker": ticker, "snap_utc": r.snap_utc, "side": side,
                    "price": px, "fair": r.fair, "won": won,
                    "filled": round(fill, 1), "pnl": round(pnl, 2),
                })
    out = pd.DataFrame(results)
    if out.empty:
        print("nothing to evaluate")
        return
    out.to_parquet(kc.DATA_DIR / f"paper_eval_{day}.parquet", index=False)
    filled = out[out.filled > 0]
    collat = (filled.apply(lambda r: r.filled * (r.price if r.side == "bid"
                                                 else 1 - r.price), axis=1))
    print(f"{day}: {len(out)} quote-sides, {len(filled)} with fills")
    print(f"total paper P&L: ${out.pnl.sum():,.0f} on ~${collat.sum():,.0f} "
          f"collateral")
    print(out.groupby("side").agg(n=("pnl", "size"), fills=("filled", "sum"),
                                  pnl=("pnl", "sum")).round(1).to_string())


if __name__ == "__main__":
    main()
