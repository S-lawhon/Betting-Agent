"""Phase 3 validation — does the hits model beat the market, and beat the
naive "buy every cheap YES" rule that Phase 2 found worth +8c/contract?

Joins model predictions to settled KXMLBHIT markets:
  ticker KXMLBHIT-26JUL182205WSHATH-ATHTSODERSTROM21-2
    event  26JUL182205WSHATH -> ET date + team pair
    player ATHTSODERSTROM21  -> TEAM + first initial + LASTNAME + jersey
                               (Kalshi DROPS non-ASCII: Nunez->NUEZ)
    strike 2                 -> "2+ hits"

Market price = last pregame trade (both trade windows), the same basis as the
Phase 2 baseline. Reports calibration, Brier (model vs market), and P&L of
model-selected bets vs the naive buy-all-cheap-YES rule.
"""
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / "data"
ET = ZoneInfo("America/New_York")
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

TK_RE = re.compile(r"^KXMLBHIT-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})([A-Z]+)-(.+)-(\d+)$")
FEE = lambda p: 0.07 * p * (1 - p)


def ascii_alpha(s):
    """Kalshi-style: drop non-ASCII entirely, keep letters."""
    return re.sub(r"[^A-Za-z]", "", (s or "").encode("ascii", "ignore").decode())


def player_code(name, team, jersey):
    parts = (name or "").split()
    if len(parts) < 2:
        return None
    first, last = parts[0], " ".join(parts[1:])
    return f"{team}{ascii_alpha(first)[:1]}{ascii_alpha(last)}{jersey}".upper()


def parse_ticker(tk):
    m = TK_RE.match(tk)
    if not m:
        return None
    yy, mon, dd, hh, mi, teams, player, strike = m.groups()
    try:
        start = pd.Timestamp(2000 + int(yy), MONTHS[mon], int(dd),
                             int(hh), int(mi), tz=ET)
    except (ValueError, KeyError):
        return None
    return {"start_et": start, "game_date": start.date(),
            "teams": teams, "code": player.upper(), "n": int(strike)}


def load_market_prices():
    """Last pregame trade price per HIT ticker across both windows."""
    frames = []
    for f in ("trades.parquet", "trades_45_90.parquet"):
        p = DATA / f
        if not p.exists():
            continue
        d = pd.read_parquet(p)
        d = d[d["series"] == "KXMLBHIT"]
        frames.append(d)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["created_time"] = pd.to_datetime(df["created_time"], utc=True,
                                        format="ISO8601")
    info = {t: parse_ticker(t) for t in df["ticker"].unique()}
    df["start_utc"] = df["ticker"].map(
        lambda t: info[t]["start_et"].tz_convert("UTC") if info.get(t) else pd.NaT)
    df = df.dropna(subset=["start_utc"])
    pre = df[df["created_time"] < df["start_utc"] - pd.Timedelta("5min")]
    last = pre.sort_values("created_time").groupby("ticker").tail(1)
    return last[["ticker", "yes_price", "result"]].rename(
        columns={"yes_price": "mkt_price"})


def main():
    preds = pd.read_parquet(DATA / "hits_model_preds.parquet")
    preds["game_date"] = pd.to_datetime(preds["date"]).dt.date
    preds["code"] = [player_code(n, t, j) for n, t, j
                     in zip(preds["name"], preds["team"], preds["jersey"])]
    preds = preds.dropna(subset=["code"])

    mkt = load_market_prices()
    if mkt.empty:
        print("no trade data found")
        return
    meta = pd.DataFrame([{**parse_ticker(t), "ticker": t}
                         for t in mkt["ticker"] if parse_ticker(t)])
    mkt = mkt.merge(meta, on="ticker")
    print(f"{len(mkt)} settled HIT markets with a pregame price")

    # long-form model predictions: one row per (batter-game, strike)
    rows = []
    for n in (1, 2, 3):
        sub = preds[["game_date", "code", f"p_{n}plus", "h", "name",
                     "team", "slot", "b_rate", "q_rate"]].copy()
        sub = sub.rename(columns={f"p_{n}plus": "p_model"})
        sub["n"] = n
        rows.append(sub)
    long = pd.concat(rows, ignore_index=True)

    j = mkt.merge(long, on=["game_date", "code", "n"], how="inner")
    match_rate = len(j) / max(len(mkt), 1)
    print(f"joined {len(j)} ({match_rate:.1%} of markets matched to model)\n")
    if len(j) < 200:
        print("insufficient matches — inspect code construction")
        print("unmatched sample:", mkt["code"].head(5).tolist())
        print("model codes sample:", preds["code"].head(5).tolist())
        return

    j["y"] = (j["result"] == "yes").astype(float)
    j = j.dropna(subset=["p_model", "mkt_price"])

    # ---- accuracy: model vs market ----
    print("=== Brier score (lower is better) ===")
    for n in (1, 2, 3):
        s = j[j["n"] == n]
        if len(s) < 50:
            continue
        bm = np.mean((s["p_model"] - s["y"]) ** 2)
        bk = np.mean((s["mkt_price"] - s["y"]) ** 2)
        print(f"  {n}+ hits (n={len(s):5d}): model={bm:.4f}  market={bk:.4f}  "
              f"{'MODEL' if bm < bk else 'MARKET'} better by {abs(bm-bk):.4f}")
    s = j
    print(f"  pooled  (n={len(s):5d}): model={np.mean((s['p_model']-s['y'])**2):.4f}"
          f"  market={np.mean((s['mkt_price']-s['y'])**2):.4f}")

    # ---- calibration of each ----
    print("\n=== calibration by market-price bucket ===")
    j["bucket"] = pd.cut(j["mkt_price"], [0, .15, .3, .45, .6, .8, 1.0])
    cal = j.groupby("bucket", observed=True).agg(
        n=("y", "count"), mkt=("mkt_price", "mean"),
        model=("p_model", "mean"), realized=("y", "mean"))
    cal["mkt_bias_pp"] = 100 * (cal["realized"] - cal["mkt"])
    cal["model_bias_pp"] = 100 * (cal["realized"] - cal["model"])
    print(cal.to_string(float_format=lambda x: f"{x:.3f}"))

    # ---- P&L: naive baseline vs model-selected ----
    print("\n=== P&L per contract, buy YES, hold to settle (net of fee + 1.5c) ===")
    cheap = j[(j["mkt_price"] >= 0.15) & (j["mkt_price"] <= 0.45)].copy()
    cost = 0.015
    cheap["net"] = cheap["y"] - cheap["mkt_price"] - FEE(cheap["mkt_price"]) - cost
    base = 100 * cheap["net"].mean()
    print(f"  naive buy-all-cheap-YES : n={len(cheap):5d}  {base:+.2f}c")
    cheap["edge"] = cheap["p_model"] - cheap["mkt_price"]
    for thr in (0.0, 0.03, 0.05, 0.08):
        sel = cheap[cheap["edge"] > thr]
        if len(sel) < 30:
            continue
        print(f"  model edge > {thr:.2f}        : n={len(sel):5d}  "
              f"{100*sel['net'].mean():+.2f}c  "
              f"(hit {sel['y'].mean():.3f} vs priced {sel['mkt_price'].mean():.3f})")
    # negative control: model says market is too HIGH
    for thr in (0.0, 0.05):
        sel = cheap[cheap["edge"] < -thr]
        if len(sel) < 30:
            continue
        print(f"  model edge < -{thr:.2f} (avoid): n={len(sel):5d}  "
              f"{100*sel['net'].mean():+.2f}c")

    j.to_parquet(DATA / "hits_model_validation.parquet", index=False)
    print(f"\nsaved -> {DATA/'hits_model_validation.parquet'}")


if __name__ == "__main__":
    main()
