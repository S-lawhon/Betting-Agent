"""Q2 + Q3 — trades-based calibration and maker/taker economics.

Uses data/trades.parquet (from pull_trades.py). Game start time is parsed from
the event key embedded in every ticker (e.g. 26JUL182205WSHATH = 2026-07-18
22:05 EDT). Trades strictly before start - 5min are 'pregame'.

Q2: last pregame trade price per market vs settlement -> calibration by series.
    (last_price on settled markets is settlement-contaminated; pregame trade
    prices are not.)
Q3: maker P&L per trade: taker_side=yes => maker short YES at p, pnl = p - 1{yes};
    taker_side=no => maker long YES at p, pnl = 1{yes} - p. Maker fees are zero
    on all prop series. Reported per series x phase x price bucket, plus
    fill-frequency capacity stats.
"""
import re
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(__file__).resolve().parent / "data"
ET = ZoneInfo("America/New_York")

MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

KEY_RE = re.compile(r"^KXMLB[A-Z]+-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})")


def parse_start(ticker):
    m = KEY_RE.match(ticker)
    if not m:
        return None
    yy, mon, dd, hh, mm = m.groups()
    try:
        return pd.Timestamp(2000 + int(yy), MONTHS[mon], int(dd),
                            int(hh), int(mm), tz=ET).tz_convert("UTC")
    except (ValueError, KeyError):
        return None


def load():
    df = pd.read_parquet(DATA / "trades.parquet")
    df["created_time"] = pd.to_datetime(df["created_time"], utc=True, format="ISO8601")
    starts = {t: parse_start(t) for t in df["ticker"].unique()}
    df["game_start"] = df["ticker"].map(starts)
    df = df.dropna(subset=["game_start", "yes_price", "count"])
    df["pregame"] = df["created_time"] < (df["game_start"] - pd.Timedelta("5min"))
    df["y"] = (df["result"] == "yes").astype(float)
    # maker P&L per contract (maker fee = 0 on prop series)
    short = df["taker_side"] == "yes"
    df["maker_pnl"] = np.where(short, df["yes_price"] - df["y"],
                               df["y"] - df["yes_price"])
    df["maker_pnl_usd"] = df["maker_pnl"] * df["count"]
    return df


def calibration(df):
    pre = df[df["pregame"]].sort_values("created_time")
    last = pre.groupby("ticker").tail(1)
    rows = []
    for s, g in last.groupby("series"):
        p, y = g["yes_price"].values, g["y"].values
        rows.append({"series": s, "markets": len(g),
                     "mean_price": p.mean(), "hit_rate": y.mean(),
                     "bias_pp": 100 * (y.mean() - p.mean()),
                     "se_pp": 100 * np.sqrt(max(y.mean() * (1 - y.mean()), 1e-9) / len(g)),
                     "brier": np.mean((p - y) ** 2)})
    return pd.DataFrame(rows).sort_values("markets", ascending=False), last


def flb(last, series_list, buckets=(0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0001)):
    sub = last[last["series"].isin(series_list)].copy()
    sub["bucket"] = pd.cut(sub["yes_price"], list(buckets))
    out = sub.groupby("bucket", observed=True).agg(
        n=("y", "count"), price=("yes_price", "mean"), hit=("y", "mean"))
    out["bias_pp"] = 100 * (out["hit"] - out["price"])
    out["se_pp"] = 100 * np.sqrt((out["hit"] * (1 - out["hit"])).clip(lower=1e-9)
                                 / out["n"].clip(lower=1))
    return out


def maker_econ(df):
    df = df.copy()
    df["phase"] = np.where(df["pregame"], "pregame", "live")
    rows = []
    for (s, ph), g in df.groupby(["series", "phase"]):
        contracts = g["count"].sum()
        rows.append({
            "series": s, "phase": ph, "trades": len(g),
            "contracts": contracts,
            "maker_pnl_per_contract_c": 100 * g["maker_pnl_usd"].sum() / contracts,
            "maker_wr": (g["maker_pnl"] > 0).mean(),
        })
    return pd.DataFrame(rows).sort_values(["series", "phase"])


def capacity(df):
    pre = df[df["pregame"]]
    per_mkt = pre.groupby(["series", "ticker"])["count"].sum()
    return per_mkt.groupby("series").agg(
        markets="count", median_pregame_contracts="median",
        p90=lambda s: s.quantile(0.9), total="sum")


def main():
    df = load()
    print(f"{len(df)} trades on {df['ticker'].nunique()} markets; "
          f"{df['pregame'].mean():.0%} pregame by trade count\n")
    cal, last = calibration(df)
    print("=== Q2: pregame-price calibration by series ===")
    print(cal.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("\n=== Q2: FLB profile, all PROP series pooled (pregame last price) ===")
    props = ["KXMLBKS", "KXMLBHR", "KXMLBHIT", "KXMLBTB", "KXMLBHRR", "KXMLBSB"]
    print(flb(last, props).to_string(float_format=lambda x: f"{x:.3f}"))
    print("\n=== Q2: FLB profile, team derivatives (TOTAL/SPREAD/F5/RFI/TEAMTOTAL) ===")
    print(flb(last, ["KXMLBTOTAL", "KXMLBSPREAD", "KXMLBF5", "KXMLBRFI",
                     "KXMLBTEAMTOTAL"]).to_string(float_format=lambda x: f"{x:.3f}"))
    print("\n=== Q3: maker economics by series x phase ===")
    print(maker_econ(df).to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("\n=== Q3: pregame capacity (contracts traded per market) ===")
    print(capacity(df).to_string(float_format=lambda x: f"{x:.1f}"))


if __name__ == "__main__":
    main()
