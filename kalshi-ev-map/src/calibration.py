"""Calibration analysis + fee-aware naive backtest over settled candles.

Buckets contracts by executable price at a fixed pre-close horizon, computes
realized YES settlement frequency with Wilson intervals, and simulates
buy-at-ask / hold-to-settlement in each bucket net of taker fees.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fees

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

BUCKETS = [(0.01, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.30),
           (0.30, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70),
           (0.70, 0.80), (0.80, 0.90), (0.90, 0.95), (0.95, 0.99)]


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, center - half, center + half


def load(horizon=24):
    df = pd.read_parquet(DATA_DIR / "settled_candles.parquet")
    df = df[df["result"].isin(["yes", "no"])].copy()
    df["won"] = (df["result"] == "yes").astype(int)
    df["mid"] = (df[f"bid_t{horizon}"] + df[f"ask_t{horizon}"]) / 2
    df["ask"] = df[f"ask_t{horizon}"]
    df["bid"] = df[f"bid_t{horizon}"]
    # drop unquoted or degenerate books
    df = df[df["ask"].notna() & df["bid"].notna()]
    df = df[(df["ask"] > 0.0) & (df["ask"] < 1.0) & (df["bid"] > 0.0)]
    df = df[(df["ask"] - df["bid"]) <= 0.20]
    return df


def calibration_table(df, price_col="mid", group=None):
    """Realized YES freq by price bucket. group: optional column to slice."""
    out = []
    slices = df.groupby(group) if group else [("all", df)]
    for gname, g in slices:
        for lo, hi in BUCKETS:
            b = g[(g[price_col] >= lo) & (g[price_col] < hi)]
            n, k = len(b), b["won"].sum()
            if n == 0:
                continue
            p_hat, lo_ci, hi_ci = wilson(k, n)
            mean_price = b[price_col].mean()
            out.append({
                "slice": gname, "bucket": f"{lo:.2f}-{hi:.2f}",
                "n": n, "mean_price": round(mean_price, 4),
                "realized": round(p_hat, 4),
                "ci_lo": round(lo_ci, 4), "ci_hi": round(hi_ci, 4),
                "miscal": round(p_hat - mean_price, 4),
                "signif": (lo_ci > mean_price) or (hi_ci < mean_price),
            })
    return pd.DataFrame(out)


def naive_backtest(df, side="yes"):
    """Buy at ask (taker), hold to settlement, one contract per market.

    Returns per-bucket net P&L per $1 collateral. side='no' buys NO at
    (1 - bid_yes).
    """
    out = []
    for lo, hi in BUCKETS:
        b = df[(df["mid"] >= lo) & (df["mid"] < hi)]
        if len(b) == 0:
            continue
        if side == "yes":
            cost = b["ask"] + [
                fees.fee_per_contract(a, s) for a, s in
                zip(b["ask"], b["series_ticker"])]
            payoff = b["won"]
        else:
            no_price = 1 - b["bid"]
            cost = no_price + [
                fees.fee_per_contract(p, s) for p, s in
                zip(no_price, b["series_ticker"])]
            payoff = 1 - b["won"]
        pnl = payoff - cost
        out.append({
            "bucket": f"{lo:.2f}-{hi:.2f}", "side": side, "n": len(b),
            "avg_cost": round(cost.mean(), 4),
            "net_roi": round(pnl.sum() / cost.sum(), 4),
            "tstat": round(pnl.mean() / (pnl.std() / math.sqrt(len(b)) + 1e-12), 2),
        })
    return pd.DataFrame(out)


if __name__ == "__main__":
    horizon = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    df = load(horizon)
    print(f"n = {len(df)} settled markets with T-{horizon}h quotes")
    print(calibration_table(df).to_string(index=False))
    print()
    print(naive_backtest(df, "yes").to_string(index=False))
    print(naive_backtest(df, "no").to_string(index=False))
