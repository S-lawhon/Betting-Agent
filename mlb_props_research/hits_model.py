"""Phase 3 — batter hits-prop probability model.

P(H >= N) for a batter-game, built from three pieces:

  1. per-PA hit probability via Log5 (Bill James odds-ratio):
         p = (b/lg * q/lg) ... in odds form:
         odds_combined = odds(b) * odds(q) / odds(lg), park-adjusted
     where b = batter hit rate/PA, q = opposing starter hits allowed/BF,
     lg = league hit rate/PA.
  2. PA distribution by lineup slot and home/away (empirical, prior seasons).
  3. P(H>=N) = sum_pa P(PA=pa) * P(Binom(pa, p) >= N)

NO LOOKAHEAD: every batter/pitcher rate for a game on date d uses only
(prior full seasons, decayed) + (current season strictly before d).
PA distributions are estimated from prior seasons only.

Rates are empirical-Bayes shrunk toward the league mean.
"""
import json
from math import comb
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / "data"

K_BAT = 150.0     # shrinkage strength, PA
K_PIT = 300.0     # shrinkage strength, BF
K_PARK = 8000.0   # shrinkage strength, PA (park effects are small)
PRIOR_W = 0.5     # weight on prior-season totals


def load_batters():
    rows = []
    for f in sorted(DATA.glob("batter_logs_*.jsonl")):
        with open(f) as fh:
            rows.extend(json.loads(l) for l in fh if l.strip())
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["season"] = df["date"].dt.year
    return df.sort_values(["date", "pk"]).reset_index(drop=True)


def load_pitchers():
    rows = []
    for f in sorted(DATA.glob("pitcher_logs_*.jsonl")):
        with open(f) as fh:
            rows.extend(json.loads(l) for l in fh if l.strip())
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["season"] = df["date"].dt.year
    return df.sort_values(["date", "pk"]).reset_index(drop=True)


def expanding_prior(df, key, num_col, den_col):
    """Cumulative num/den for `key` strictly BEFORE each row, within season,
    plus prior-season totals. Returns (num_before, den_before) arrays."""
    d = df.sort_values("date")
    g = d.groupby([key, "season"])
    cum_n = g[num_col].cumsum() - d[num_col]
    cum_d = g[den_col].cumsum() - d[den_col]
    # prior-season full totals
    season_tot = (d.groupby([key, "season"])[[num_col, den_col]].sum()
                   .reset_index())
    season_tot["season"] += 1  # shift so it applies to the NEXT season
    season_tot = season_tot.rename(columns={num_col: "pn", den_col: "pd"})
    merged = d[[key, "season"]].merge(season_tot, on=[key, "season"], how="left")
    pn = merged["pn"].fillna(0).values
    pd_ = merged["pd"].fillna(0).values
    return (cum_n.values + PRIOR_W * pn, cum_d.values + PRIOR_W * pd_)


def shrunk(num, den, lg, k):
    return (num + k * lg) / (den + k)


def log5(b, q, lg, park_mult=1.0):
    """Odds-ratio combination of batter rate b and pitcher rate q vs league."""
    eps = 1e-6
    b = np.clip(b, eps, 1 - eps)
    q = np.clip(q, eps, 1 - eps)
    lg = np.clip(lg, eps, 1 - eps)
    odds = (b / (1 - b)) * (q / (1 - q)) / (lg / (1 - lg)) * park_mult
    return odds / (1 + odds)


def pa_distribution(bat, train_seasons):
    """P(PA = k | lineup slot, home/away) from prior seasons only.
    Returns {(slot, side): {pa: prob}}."""
    t = bat[bat["season"].isin(train_seasons) & bat["starter_slot"]].copy()
    t["slot"] = (t["order"] // 100).clip(1, 9).astype(int)
    t["pa_c"] = t["pa"].fillna(0).clip(0, 7).astype(int)
    out = {}
    for (slot, side), g in t.groupby(["slot", "side"]):
        counts = g["pa_c"].value_counts()
        total = counts.sum()
        out[(int(slot), side)] = {int(k): v / total for k, v in counts.items()}
    return out


def binom_tail(pa, p, n):
    """P(Binomial(pa, p) >= n)."""
    if n <= 0:
        return 1.0
    if n > pa:
        return 0.0
    return float(sum(comb(pa, k) * p ** k * (1 - p) ** (pa - k)
                     for k in range(n, pa + 1)))


def build(train_seasons=(2024, 2025), target_season=2026):
    bat = load_batters()
    pit = load_pitchers()

    lg_bat = bat["h"].sum() / max(bat["pa"].sum(), 1)
    lg_pit = pit["h"].sum() / max(pit["bf"].sum(), 1)

    # --- batter rates (no lookahead) ---
    bn, bd = expanding_prior(bat, "pid", "h", "pa")
    bat = bat.sort_values("date").reset_index(drop=True)
    bat["b_rate"] = shrunk(bn, bd, lg_bat, K_BAT)
    bat["b_pa_seen"] = bd

    # --- pitcher rates (starters only, no lookahead) ---
    st = pit[pit["gs"]].sort_values("date").reset_index(drop=True)
    pn, pdn = expanding_prior(st, "pid", "h", "bf")
    st["q_rate"] = shrunk(pn, pdn, lg_pit, K_PIT)
    st["q_bf_seen"] = pdn
    pmap = st.set_index(["pk", "pid"])["q_rate"].to_dict()

    # --- park factors from prior seasons only ---
    tr = bat[bat["season"].isin(train_seasons)]
    park_tab = tr.groupby("park").agg(h=("h", "sum"), pa=("pa", "sum"))
    park_rate = shrunk(park_tab["h"], park_tab["pa"], lg_bat, K_PARK)
    park_mult = ((park_rate / (1 - park_rate))
                 / (lg_bat / (1 - lg_bat))).to_dict()

    padist = pa_distribution(bat, train_seasons)

    # --- assemble target season ---
    tgt = bat[(bat["season"] == target_season) & bat["starter_slot"]].copy()
    tgt["q_rate"] = [pmap.get((pk, sp), lg_pit)
                     for pk, sp in zip(tgt["pk"], tgt["opp_sp"])]
    tgt["park_mult"] = tgt["park"].map(park_mult).fillna(1.0)
    tgt["p_hit"] = log5(tgt["b_rate"].values, tgt["q_rate"].values,
                        lg_bat, tgt["park_mult"].values)
    tgt["slot"] = (tgt["order"] // 100).clip(1, 9)

    # --- P(H >= N) for N = 1,2,3 ---
    tgt["slot"] = tgt["slot"].astype(int)
    # cache tail probs per (slot, side, rounded p) — p varies continuously but
    # the PA mixture is shared, so cache on the mixture key only
    for n in (1, 2, 3):
        probs = []
        for slot, side, p in zip(tgt["slot"], tgt["side"], tgt["p_hit"]):
            dist = padist.get((int(slot), side))
            if not dist:
                probs.append(np.nan)
                continue
            tot = sum(w * binom_tail(pa, p, n) for pa, w in dist.items())
            probs.append(tot)
        tgt[f"p_{n}plus"] = probs

    meta = {"lg_bat": lg_bat, "lg_pit": lg_pit}
    return tgt, meta


if __name__ == "__main__":
    tgt, meta = build()
    print(f"league hit/PA {meta['lg_bat']:.4f}, pitcher hit/BF {meta['lg_pit']:.4f}")
    print(f"{len(tgt)} batter-games modeled in target season\n")
    print(tgt[["date", "name", "team", "slot", "b_rate", "q_rate",
               "p_hit", "p_1plus", "p_2plus", "p_3plus", "h"]].head(10)
          .to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("\n--- model calibration vs realized (target season) ---")
    for n in (1, 2, 3):
        p = tgt[f"p_{n}plus"].values
        y = (tgt["h"].values >= n).astype(float)
        ok = ~np.isnan(p)
        print(f"  {n}+ hits: mean_pred={p[ok].mean():.4f} "
              f"realized={y[ok].mean():.4f} "
              f"bias={100*(y[ok].mean()-p[ok].mean()):+.2f}pp "
              f"brier={np.mean((p[ok]-y[ok])**2):.4f}")
    tgt.to_parquet(DATA / "hits_model_preds.parquet", index=False)
    print(f"\nsaved -> {DATA/'hits_model_preds.parquet'}")
