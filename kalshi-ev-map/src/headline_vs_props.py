"""Test: are headline sports markets efficient while props are soft?

Compares, at matched pre-close horizons and price levels:
  - Brier score of executable mid vs settlement
  - bucket calibration deviation
  - fee-aware naive taker backtests (both sides)
between headline series and prop series.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calibration as cal

import numpy as np
import pandas as pd

HEADLINE = ["KXMLBGAME", "KXWNBAGAME", "KXATPMATCH", "KXATPCHALLENGERMATCH",
            "KXT20MATCH", "KXUFCFIGHT", "KXLIGAMXGAME", "KXWCGAME",
            "KXNASCARRACE", "KXF1RACE", "KXBOXING"]
PROPS = ["KXMLBTOTAL", "KXMLBSPREAD", "KXWCSCORE", "KXWCGOAL", "KXWCAST",
         "KXUFCMOV", "KXWC1HSCORE", "KXWCBTTS", "KXPGATOP20", "KXWC2H",
         "KXWCFINISHINGORDER", "KXWCGOALLEADER", "KXWCMESSIMBAPPE",
         "KXWCGAMEGOALS"]


def group_stats(df, label):
    p, y = df["mid"], df["won"]
    brier = ((p - y) ** 2).mean()
    # reliability-in-the-small: mean absolute bucket miscalibration, weighted
    tab = cal.calibration_table(df)
    w = tab["n"] / tab["n"].sum()
    mac = (tab["miscal"].abs() * w).sum()
    return {"group": label, "n": len(df), "brier": round(brier, 4),
            "wgt_abs_miscal": round(mac, 4),
            "n_signif_buckets": int(tab["signif"].sum()),
            "buckets": len(tab)}


def main(horizon=24):
    df = cal.load(horizon)
    df = df[df["volume"] >= 100]  # only markets someone actually traded
    h = df[df["series_ticker"].isin(HEADLINE)]
    p = df[df["series_ticker"].isin(PROPS)]
    print(f"horizon T-{horizon}h | headline n={len(h)} props n={len(p)}")
    print(pd.DataFrame([group_stats(h, "headline"), group_stats(p, "props")])
          .to_string(index=False))
    for label, g in [("HEADLINE", h), ("PROPS", p)]:
        print(f"\n--- {label} calibration ---")
        print(cal.calibration_table(g).to_string(index=False))
        print(f"--- {label} naive taker backtest (buy YES) ---")
        print(cal.naive_backtest(g, "yes").to_string(index=False))
        print(f"--- {label} naive taker backtest (buy NO) ---")
        print(cal.naive_backtest(g, "no").to_string(index=False))
    # per-series detail for the props group
    print("\nper-series props detail:")
    rows = []
    for st, g in p.groupby("series_ticker"):
        if len(g) < 50:
            continue
        rows.append({"series": st, **group_stats(g, st)})
    if rows:
        print(pd.DataFrame(rows)[["series","n","brier","wgt_abs_miscal","n_signif_buckets"]]
              .to_string(index=False))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 24)
