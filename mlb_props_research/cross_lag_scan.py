"""Q1 statistical layer — do prop books lag total-book repricing?

For each game across snapshot cycles:
  x_t = implied P(total >= 9) from the KXMLBTOTAL-9 mid (or nearest strike)
  y_t = each starter's KS-prop mid (per strike)

Dependence model: odds-ratio-preserving update. From game logs the (Ks>=k,
total>=9) odds ratio is ~0.6-0.7 (negative dependence). Predicted prop move
when the total book moves dx:
  dy_pred = dP(A|B-mix) where P(A) anchored at the prop's own mid,
  via  P(A|B) with fixed OR, mixed by P(B)=x.

We then regress observed prop mid changes dy on predicted dy at cycle lags
0..3. Slope ~1 at lag 0 = props reprice instantly (no edge). Slope mass at
lags >=1 = measurable lag a maker can trade ahead of.

Usage: python cross_lag_scan.py [snapshots.jsonl] [--or=0.65]
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / "data" / "live"
TKRE = re.compile(r"^(KXMLB[A-Z]+)-([0-9]{2}[A-Z]{3}[0-9]{6}[A-Z]+)-?(.*)$")


def joint_prob(pa, pb, orr):
    """P(A&B) given marginals and odds ratio (Plackett copula), vectorized."""
    pa, pb = np.asarray(pa, float), np.asarray(pb, float)
    if orr == 1:
        return pa * pb
    s = 1 + (pa + pb) * (orr - 1)
    disc = s * s - 4 * orr * (orr - 1) * pa * pb
    return (s - np.sqrt(np.maximum(disc, 0))) / (2 * (orr - 1))


def predicted_shift(pa0, pb0, pb1, orr):
    """Predicted new P(A) when P(B) moves pb0 -> pb1, conditionals fixed."""
    pab = joint_prob(pa0, pb0, orr)
    p_a_given_b = pab / np.maximum(pb0, 1e-9)
    p_a_given_nb = (pa0 - pab) / np.maximum(1 - pb0, 1e-9)
    pa1 = np.clip(p_a_given_b * pb1 + p_a_given_nb * (1 - pb1), 0, 1)
    return pa1 - pa0


def load_mids(path):
    cyc = defaultdict(dict)
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r["t"] != "mkt":
                continue
            yb, ya = r.get("yb"), r.get("ya")
            ybs, yas = r.get("ybs") or 0, r.get("yas") or 0
            if not yb or not ya or ybs <= 0 or yas <= 0 or ya >= 1:
                continue
            cyc[r["ts"]][r["tk"]] = (yb + ya) / 2
    return dict(cyc)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    orr = 0.65
    for a in sys.argv[1:]:
        if a.startswith("--or="):
            orr = float(a.split("=")[1])
    path = Path(args[0]) if args else sorted(DATA.glob("snapshots_*.jsonl"))[-1]
    cycles = load_mids(path)
    ts_sorted = sorted(cycles)
    print(f"{len(ts_sorted)} cycles from {path.name}")

    # per game: total anchor (strike 9) and KS prop mids
    series_of = lambda tk: TKRE.match(tk).group(1) if TKRE.match(tk) else None
    game_of = lambda tk: TKRE.match(tk).group(2) if TKRE.match(tk) else None

    rows = []
    for i, ts in enumerate(ts_sorted):
        for tk, mid in cycles[ts].items():
            s = series_of(tk)
            if s in ("KXMLBTOTAL", "KXMLBKS"):
                rows.append({"i": i, "ts": ts, "tk": tk, "series": s,
                             "game": game_of(tk), "mid": mid})
    df = pd.DataFrame(rows)
    if df.empty:
        print("no data")
        return

    tot = df[(df["series"] == "KXMLBTOTAL") & df["tk"].str.endswith("-9")]
    ks = df[df["series"] == "KXMLBKS"]
    tot_piv = tot.pivot_table(index="i", columns="game", values="mid")
    obs = []
    for tk, g in ks.groupby("tk"):
        game = g["game"].iloc[0]
        if game not in tot_piv.columns:
            continue
        m = g.set_index("i")["mid"].reindex(tot_piv.index)
        x = tot_piv[game]
        both = pd.DataFrame({"x": x, "y": m}).dropna()
        if len(both) < 6:
            continue
        dx = both["x"].diff()
        dy = both["y"].diff()
        # predicted dy: OR-preserving mixture anchored at previous mids
        pa0, pb0 = both["y"].shift(), both["x"].shift()
        pred = pd.Series(predicted_shift(pa0, pb0, both["x"], orr),
                         index=both.index)
        obs.append(pd.DataFrame({
            "tk": tk, "dx": dx, "dy": dy, "pred": pred}).dropna())
    if not obs:
        print("no paired series with enough cycles yet")
        return
    allobs = pd.concat(obs)
    allobs = allobs[allobs["dx"].abs() >= 0.02]  # meaningful total moves only
    print(f"{len(allobs)} prop-cycle observations with |d total| >= 2c")
    for lag in range(4):
        d = pd.concat([o.assign(pl=o["pred"].shift(lag)) for o in obs]).dropna()
        d = d[d["pl"].abs() > 1e-4]
        if len(d) < 10:
            print(f"lag {lag}: insufficient data ({len(d)})")
            continue
        beta = np.polyfit(d["pl"], d["dy"], 1)[0]
        r = np.corrcoef(d["pl"], d["dy"])[0, 1]
        print(f"lag {lag}: n={len(d)} beta={beta:+.2f} r={r:+.3f}")


if __name__ == "__main__":
    main()
