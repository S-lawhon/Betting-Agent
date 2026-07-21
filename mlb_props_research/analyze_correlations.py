"""Q1 foundation — measured MLB same-game correlations between contract classes.

From game logs: correlation matrix + joint-vs-independence lift tables for the
contract pairs a joint-distribution model would trade on Kalshi:
  ML winner x F5 winner, ML x total, starter Ks x total, starter Ks x ML,
  YRFI x total, HRs x total.

The 'lift' number is the MLB-measured analog of the +30-33% NFL illustrations
cited in the July research report.
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def load():
    rows = []
    for f in sorted(DATA.glob("gamelogs_*.jsonl")):
        with open(f) as fh:
            rows.extend(json.loads(l) for l in fh if l.strip())
    df = pd.DataFrame(rows).dropna(subset=["home_runs", "away_runs"])
    df["total"] = df["home_runs"] + df["away_runs"]
    df["home_win"] = (df["home_runs"] > df["away_runs"]).astype(float)
    df["f5_home_lead"] = np.sign(df["home_f5"] - df["away_f5"])
    df["yrfi"] = ((df["home_i1"] + df["away_i1"]) > 0).astype(float)
    df["total_hr"] = df["home_hr"] + df["away_hr"]
    return df


def lift(df, a, b, name):
    """P(A&B) / (P(A)P(B)) with binomial CI on the joint."""
    pa, pb = df[a].mean(), df[b].mean()
    pj = (df[a] * df[b]).mean()
    n = len(df)
    se = np.sqrt(pj * (1 - pj) / n)
    ind = pa * pb
    return {"pair": name, "P(A)": pa, "P(B)": pb, "P(A&B)": pj,
            "indep": ind, "lift": pj / ind if ind else np.nan,
            "lift_lo": (pj - 1.96 * se) / ind if ind else np.nan,
            "lift_hi": (pj + 1.96 * se) / ind if ind else np.nan}


def main():
    df = load()
    print(f"{len(df)} games loaded ({df['date'].min()} .. {df['date'].max()})\n")

    # -- headline correlation matrix (game level) --
    cols = {
        "home_win": df["home_win"],
        "f5_home_ahead": (df["f5_home_lead"] > 0).astype(float),
        "total_runs": df["total"],
        "total_hr": df["total_hr"],
        "yrfi": df["yrfi"],
        "home_sp_ks": df["home_sp_ks"],
        "away_sp_ks": df["away_sp_ks"],
        "home_f5_runs": df["home_f5"] + 0.0,
    }
    cm = pd.DataFrame(cols).corr(method="spearman")
    print("=== Spearman correlation matrix ===")
    print(cm.to_string(float_format=lambda x: f"{x:+.3f}"))

    # -- binary event pairs: joint vs independence --
    med_total = df["total"].median()
    med_ks = df["home_sp_ks"].median()
    ev = pd.DataFrame({
        "home_win": df["home_win"],
        "f5_home": (df["f5_home_lead"] > 0).astype(float),
        "over": (df["total"] > med_total).astype(float),
        "under": (df["total"] < med_total).astype(float),
        "hsp_ks_over": (df["home_sp_ks"] > med_ks).astype(float),
        "asp_ks_over": (df["away_sp_ks"] > med_ks).astype(float),
        "yrfi": df["yrfi"],
        "hr3plus": (df["total_hr"] >= 3).astype(float),
    }).dropna()
    pairs = [
        ("home_win", "f5_home", "ML home x F5 home ahead"),
        ("home_win", "under", "ML home x Under"),
        ("home_win", "hsp_ks_over", "ML home x home SP Ks over"),
        ("hsp_ks_over", "under", "home SP Ks over x Under"),
        ("hsp_ks_over", "asp_ks_over", "both SP Ks over"),
        ("yrfi", "over", "YRFI x Over"),
        ("hr3plus", "over", "3+ HR x Over"),
        ("f5_home", "under", "F5 home ahead x Under"),
    ]
    print(f"\n=== Joint vs independence (median splits: total>{med_total}, ks>{med_ks}) ===")
    out = pd.DataFrame([lift(ev, a, b, name) for a, b, name in pairs])
    print(out.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    # -- K-line specific: common Kalshi strike levels --
    print("\n=== Starter Ks x game total, common strikes ===")
    rows = []
    for k in (4, 5, 6, 7):
        for t in (7, 8, 9, 10):
            sub = df.dropna(subset=["home_sp_ks"])
            a = (sub["home_sp_ks"] >= k + 1).astype(float)   # over k.5
            b = (sub["total"] <= t).astype(float)            # under t.5
            r = lift(pd.DataFrame({"a": a, "b": b}), "a", "b",
                     f"SP Ks>{k}.5 x total<{t}.5")
            rows.append(r)
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
