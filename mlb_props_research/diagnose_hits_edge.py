"""Is the +8c cheap-YES hits edge real, or a stale-price artifact?

The Phase 2 baseline priced entries at the LAST PREGAME TRADE. In an illiquid
prop book that trade can be hours old; if the market drifts up before first
pitch, "realized vs last trade" measures the drift, not a tradeable edge —
you could never have bought at that stale price at game time.

Tests:
  1. Join correctness: does Kalshi's settlement agree with box-score hits?
  2. Staleness: edge bucketed by trade-age (minutes before first pitch).
     A real edge survives when the entry price is FRESH.
  3. Same, using only trades within 30/60 min of first pitch.
"""
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from validate_hits_model import parse_ticker, player_code

DATA = Path(__file__).resolve().parent / "data"
FEE = lambda p: 0.07 * p * (1 - p)
COST = 0.015


def load_hit_trades():
    frames = []
    for f in ("trades.parquet", "trades_45_90.parquet"):
        p = DATA / f
        if p.exists():
            d = pd.read_parquet(p)
            frames.append(d[d["series"] == "KXMLBHIT"])
    df = pd.concat(frames, ignore_index=True)
    df["created_time"] = pd.to_datetime(df["created_time"], utc=True,
                                        format="ISO8601")
    info = {t: parse_ticker(t) for t in df["ticker"].unique()}
    info = {k: v for k, v in info.items() if v}
    df = df[df["ticker"].isin(info)]
    df["start"] = df["ticker"].map(
        lambda t: info[t]["start_et"].tz_convert("UTC"))
    df["n"] = df["ticker"].map(lambda t: info[t]["n"])
    df["code"] = df["ticker"].map(lambda t: info[t]["code"])
    df["game_date"] = df["ticker"].map(lambda t: info[t]["game_date"])
    df["mins_before"] = (df["start"] - df["created_time"]).dt.total_seconds() / 60
    return df[df["mins_before"] > 5]


def pnl(sub, price_col="yes_price"):
    p = sub[price_col].values
    y = (sub["result"] == "yes").astype(float).values
    return 100 * (y - p - FEE(p) - COST).mean()


def main():
    tr = load_hit_trades()
    print(f"{len(tr)} pregame HIT trades on {tr['ticker'].nunique()} markets\n")

    # ---------- 1. join correctness ----------
    preds = pd.read_parquet(DATA / "hits_model_preds.parquet")
    preds["game_date"] = pd.to_datetime(preds["date"]).dt.date
    preds["code"] = [player_code(n, t, j) for n, t, j
                     in zip(preds["name"], preds["team"], preds["jersey"])]
    box = preds[["game_date", "code", "h"]].dropna()
    last = tr.sort_values("created_time").groupby("ticker").tail(1)
    m = last.merge(box, on=["game_date", "code"], how="inner")
    m["kalshi_yes"] = (m["result"] == "yes").astype(int)
    m["box_yes"] = (m["h"] >= m["n"]).astype(int)
    agree = (m["kalshi_yes"] == m["box_yes"]).mean()
    print("=== 1. JOIN CORRECTNESS ===")
    print(f"  {len(m)} joined; Kalshi settlement agrees with box score: {agree:.2%}")
    print(pd.crosstab(m["kalshi_yes"], m["box_yes"],
                      rownames=["kalshi"], colnames=["boxscore"]).to_string())

    # ---------- 2. staleness ----------
    print("\n=== 2. STALENESS: edge by age of the entry price ===")
    cheap = last[(last["yes_price"] >= 0.15) & (last["yes_price"] <= 0.45)].copy()
    cheap["age_bucket"] = pd.cut(
        cheap["mins_before"], [5, 30, 60, 120, 240, 480, 10000],
        labels=["5-30m", "30-60m", "1-2h", "2-4h", "4-8h", ">8h"])
    rows = []
    for b, g in cheap.groupby("age_bucket", observed=True):
        rows.append({"trade_age": b, "n": len(g),
                     "mean_price": g["yes_price"].mean(),
                     "hit_rate": (g["result"] == "yes").mean(),
                     "net_c": pnl(g)})
    print(pd.DataFrame(rows).to_string(index=False,
                                       float_format=lambda x: f"{x:.3f}"))

    # ---------- 3. fresh-price-only entries ----------
    print("\n=== 3. FRESH ENTRY PRICES ONLY (last trade within X of first pitch) ===")
    for window, label in [(30, "<=30m"), (60, "<=60m"), (120, "<=2h")]:
        fresh = tr[tr["mins_before"] <= window]
        f_last = fresh.sort_values("created_time").groupby("ticker").tail(1)
        c = f_last[(f_last["yes_price"] >= 0.15) & (f_last["yes_price"] <= 0.45)]
        if len(c) < 50:
            continue
        print(f"  entry = last trade {label} before pitch: n={len(c):5d}  "
              f"price={c['yes_price'].mean():.3f}  "
              f"hit={(c['result']=='yes').mean():.3f}  net={pnl(c):+.2f}c")

    # ---------- 4. drift: does the price move up after a stale quote? ----------
    print("\n=== 4. PRICE DRIFT from early trade to final pregame trade ===")
    first_early = tr[tr["mins_before"] > 120].sort_values("created_time") \
                    .groupby("ticker").tail(1)[["ticker", "yes_price"]] \
                    .rename(columns={"yes_price": "early_price"})
    final = tr[tr["mins_before"] <= 60].sort_values("created_time") \
              .groupby("ticker").tail(1)[["ticker", "yes_price", "result"]] \
              .rename(columns={"yes_price": "late_price"})
    d = first_early.merge(final, on="ticker")
    d = d[(d["early_price"] >= 0.15) & (d["early_price"] <= 0.45)]
    print(f"  {len(d)} markets traded both >2h out and <=1h out")
    print(f"  mean price >2h out : {d['early_price'].mean():.3f}")
    print(f"  mean price <=1h out: {d['late_price'].mean():.3f}")
    print(f"  mean drift         : {100*(d['late_price']-d['early_price']).mean():+.2f}c")
    print(f"  realized hit rate  : {(d['result']=='yes').mean():.3f}")
    print(f"  net buying at EARLY price: {pnl(d,'early_price'):+.2f}c")
    print(f"  net buying at LATE  price: {pnl(d,'late_price'):+.2f}c")


if __name__ == "__main__":
    main()
