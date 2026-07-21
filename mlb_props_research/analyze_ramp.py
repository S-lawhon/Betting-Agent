"""Q4 — liquidity ramp: spread/depth/volume vs time-to-first-pitch.

Uses live snapshot mkt records; game start parsed from the event key in each
ticker. Buckets time-to-start and reports per series: share of books quoted
two-sided, median spread, median top-of-book depth ($), cumulative volume.
"""
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / "data" / "live"
ET = ZoneInfo("America/New_York")
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
TKRE = re.compile(r"^(KXMLB[A-Z]+)-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})")


def start_of(tk, cache={}):
    if tk in cache:
        return cache[tk]
    m = TKRE.match(tk)
    out = None
    if m:
        series, yy, mon, dd, hh, mi = m.groups()
        try:
            out = pd.Timestamp(2000 + int(yy), MONTHS[mon], int(dd),
                               int(hh), int(mi), tz=ET).tz_convert("UTC")
        except (ValueError, KeyError):
            pass
    cache[tk] = out
    return out


def load(paths):
    rows = []
    for path in paths:
        with open(path) as f:
            for line in f:
                r = json.loads(line)
                if r["t"] != "mkt":
                    continue
                st = start_of(r["tk"])
                if st is None:
                    continue
                rows.append({
                    "series": r["tk"].split("-")[0], "tk": r["tk"],
                    "ts": r["ts"], "start": st,
                    "yb": r.get("yb"), "ya": r.get("ya"),
                    "ybs": r.get("ybs") or 0, "yas": r.get("yas") or 0,
                    "vol": r.get("vol") or 0,
                })
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df["tts_h"] = (df["start"] - df["ts"]).dt.total_seconds() / 3600
    df["quoted"] = ((df["ybs"] > 0) & (df["yas"] > 0)
                    & df["yb"].notna() & df["ya"].notna()
                    & (df["ya"] < 1) & (df["yb"] > 0))
    df["spread_c"] = np.where(df["quoted"], 100 * (df["ya"] - df["yb"]), np.nan)
    df["depth"] = np.where(df["quoted"], df["ybs"] + df["yas"], np.nan)
    return df


BINS = [-2, 0, 1, 2, 4, 8, 12, 24, 48]
LABELS = ["in-game", "0-1h", "1-2h", "2-4h", "4-8h", "8-12h", "12-24h", "24-48h"]


def main():
    paths = [Path(a) for a in sys.argv[1:]] or sorted(DATA.glob("snapshots_*.jsonl"))
    df = load(paths)
    print(f"{len(df)} market-snapshots, {df['tk'].nunique()} markets, "
          f"{df['ts'].nunique()} cycles\n")
    df["bucket"] = pd.cut(df["tts_h"], BINS, labels=LABELS)
    for s in sorted(df["series"].unique()):
        g = df[df["series"] == s]
        agg = g.groupby("bucket", observed=True).agg(
            snaps=("tk", "count"),
            quoted=("quoted", "mean"),
            med_spread_c=("spread_c", "median"),
            med_depth=("depth", "median"),
            med_vol=("vol", "median"),
        )
        print(f"=== {s} ===")
        print(agg.to_string(float_format=lambda x: f"{x:.2f}"), "\n")


if __name__ == "__main__":
    main()
