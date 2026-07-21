"""Q2 — Calibration of Kalshi MLB market prices vs realized outcomes, by series.

For every settled market with real trading volume: implied prob = last_price,
outcome = result ('yes'/'no'). Measures, per series and price bucket:
  bias = mean(outcome) - mean(price)   (positive => YES underpriced)
plus Brier vs a calibrated-price baseline, and a favorite-longshot profile.

last_price is the final trade, not a close mid — treat wide-spread props with
care (the fill-sim study measures maker/taker economics directly).
"""
import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

MIN_VOL = 10  # ignore books with no meaningful trading

BUCKETS = [0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, 1.0001]


def load():
    df = pd.read_parquet(DATA / "settled_markets.parquet")
    df = df[df["result"].isin(["yes", "no"])].copy()
    df["y"] = (df["result"] == "yes").astype(float)
    df = df[(df["volume"] >= MIN_VOL) & df["last_price"].notna()
            & (df["last_price"] > 0) & (df["last_price"] < 1)]
    return df


def per_series(df):
    rows = []
    for s, g in df.groupby("series"):
        p, y = g["last_price"].values, g["y"].values
        rows.append({
            "series": s, "n": len(g),
            "total_vol": g["volume"].sum(),
            "mean_price": p.mean(), "hit_rate": y.mean(),
            "bias_pp": 100 * (y.mean() - p.mean()),
            "brier": np.mean((p - y) ** 2),
            "brier_clairvoyant_price": np.mean(p * (1 - p)),  # if prices were true probs
        })
    return pd.DataFrame(rows).sort_values("total_vol", ascending=False)


def flb_profile(df, series=None):
    g = df if series is None else df[df["series"] == series]
    g = g.copy()
    g["bucket"] = pd.cut(g["last_price"], BUCKETS)
    out = g.groupby("bucket", observed=True).agg(
        n=("y", "count"), price=("last_price", "mean"), hit=("y", "mean"),
        vol=("volume", "sum"))
    out["bias_pp"] = 100 * (out["hit"] - out["price"])
    # binomial se on the hit rate
    out["se_pp"] = 100 * np.sqrt(out["hit"] * (1 - out["hit"]) / out["n"].clip(lower=1))
    return out


def main():
    df = load()
    print(f"{len(df)} settled markets with volume >= {MIN_VOL}\n")
    print("=== Per-series calibration ===")
    print(per_series(df).to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("\n=== Favorite-longshot profile: ALL series pooled ===")
    print(flb_profile(df).to_string(float_format=lambda x: f"{x:.3f}"))
    for s in ["KXMLBGAME", "KXMLBTOTAL", "KXMLBKS", "KXMLBHR", "KXMLBHIT",
              "KXMLBTB", "KXMLBHRR", "KXMLBRFI", "KXMLBF5"]:
        sub = df[df["series"] == s]
        if len(sub) < 200:
            continue
        print(f"\n=== FLB profile: {s} (n={len(sub)}) ===")
        print(flb_profile(df, s).to_string(float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
