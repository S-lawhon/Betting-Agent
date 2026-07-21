"""Kill-test #2: fill realism for the MLB totals/spread favorites fade.

For each fade-candidate market (mid 70-90c at the T-1h candle in the original
close-anchored sample), pull the public trades tape and measure how many
contracts actually printed within +/-15 min of the quote timestamp, and at
what prices. If almost nothing trades near the sampled quotes, the backtest
was quoting a ghost.

Output: data/mlb_fill_check.parquet + console summary.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc

import pandas as pd

kc.MIN_INTERVAL = 0.3


def main():
    df = pd.read_parquet(kc.DATA_DIR / "settled_candles.parquet")
    df = df[df.series_ticker.isin(["KXMLBTOTAL", "KXMLBSPREAD"])]
    df = df[df.bid_t1.notna() & df.ask_t1.notna()]
    df["mid"] = (df.bid_t1 + df.ask_t1) / 2
    cand = df[(df.mid >= 0.70) & (df.mid < 0.90) & (df.volume >= 500)
              & ((df.ask_t1 - df.bid_t1) <= 0.05)]
    print(f"{len(cand)} fade candidates", flush=True)
    rows = []
    for r in cand.itertuples():
        t_anchor = r.close_ts - 3600
        try:
            trades = list(kc.paginate("/markets/trades", {
                "ticker": r.ticker, "limit": 1000,
                "min_ts": t_anchor - 900, "max_ts": t_anchor + 900},
                list_key="trades", max_pages=5))
        except Exception as e:
            print(f"[warn] {r.ticker}: {e}", flush=True)
            continue
        n_contracts = sum(kc.fnum(t.get("count_fp")) or 0 for t in trades)
        # size printed at prices where the fade could fill (selling YES =
        # someone buying YES from us at >= bid; approximate with trades at
        # yes_price >= bid - 1c)
        near = [t for t in trades
                if (kc.fnum(t.get("yes_price_dollars")) or 0) >= r.bid_t1 - 0.01]
        rows.append({
            "ticker": r.ticker, "series_ticker": r.series_ticker,
            "mid": r.mid, "bid_t1": r.bid_t1, "result": r.result,
            "trades_30min": len(trades), "contracts_30min": n_contracts,
            "contracts_near_bid": sum(kc.fnum(t.get("count_fp")) or 0 for t in near),
        })
    out = pd.DataFrame(rows)
    out.to_parquet(kc.DATA_DIR / "mlb_fill_check.parquet", index=False)
    print(out[["contracts_30min", "contracts_near_bid"]].describe()
          .round(1).to_string())
    med = out.contracts_near_bid.median()
    print(f"\nmedian contracts printed near bid in the +/-15min window: {med:.0f}")
    print(f"share of candidates with >=100 contracts near bid: "
          f"{(out.contracts_near_bid >= 100).mean():.2f}")


if __name__ == "__main__":
    main()
