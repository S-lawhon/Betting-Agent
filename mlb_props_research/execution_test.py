"""Phase 3b — the execution test.

Every edge estimate so far priced entries at TRADE PRINTS. This prices them at
the ASK actually displayed in the order book near first pitch, with the size
actually available, then settles them.

Method:
  1. From live snapshots, take each KXMLBHIT market's last observation within
     WINDOW_MIN minutes before its scheduled first pitch.
  2. Require a real two-sided quote (ask size > 0). The ask is what you pay.
  3. Keep asks in the cheap-YES range [0.15, 0.45].
  4. Pull settlement from Kalshi for those tickers.
  5. P&L = settle - ask - taker_fee(ask). No synthetic cost assumption: this is
     an executable price and a displayed size.

Also reports: quoted-rate (how often a tradeable ask even exists), displayed
size at the ask, and the gap between the ask and the last trade price — i.e.
exactly how optimistic the earlier trade-print basis was.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kalshi-ev-map" / "src"))
import kalshi_client as kc

DATA = Path(__file__).resolve().parent / "data"
LIVE = DATA / "live"
ET = ZoneInfo("America/New_York")
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
TKRE = re.compile(r"^(KXMLB[A-Z]+)-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})")

WINDOW_MIN = 30
LO, HI = 0.15, 0.45
FEE = lambda p: 0.07 * p * (1 - p)


def start_of(tk):
    m = TKRE.match(tk)
    if not m:
        return None
    _, yy, mon, dd, hh, mi = m.groups()
    try:
        return pd.Timestamp(2000 + int(yy), MONTHS[mon], int(dd),
                            int(hh), int(mi), tz=ET).tz_convert("UTC")
    except (ValueError, KeyError):
        return None


def load_snaps(series_prefix):
    """Last pre-pitch observation per ticker inside the window."""
    starts = {}
    best = {}
    for path in sorted(LIVE.glob("snapshots_*.jsonl")):
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("t") != "mkt":
                    continue
                tk = r["tk"]
                if not tk.startswith(series_prefix):
                    continue
                if tk not in starts:
                    starts[tk] = start_of(tk)
                st = starts[tk]
                if st is None:
                    continue
                ts = pd.Timestamp(r["ts"])
                mins = (st - ts).total_seconds() / 60
                if mins < 0 or mins > WINDOW_MIN:
                    continue
                # keep the observation closest to first pitch
                prev = best.get(tk)
                if prev is None or mins < prev["mins"]:
                    best[tk] = {"tk": tk, "mins": mins, "ts": r["ts"],
                                "yb": r.get("yb"), "ya": r.get("ya"),
                                "ybs": r.get("ybs") or 0,
                                "yas": r.get("yas") or 0,
                                "last": r.get("last"), "vol": r.get("vol")}
    return pd.DataFrame(best.values())


def fetch_results(tickers):
    out = {}
    for i, tk in enumerate(tickers):
        try:
            m = kc.get(f"/markets/{tk}")["market"]
            out[tk] = m.get("result")
        except Exception:
            out[tk] = None
        if (i + 1) % 200 == 0:
            print(f"  fetched {i+1}/{len(tickers)} results", flush=True)
    return out


def main():
    series = sys.argv[1] if len(sys.argv) > 1 else "KXMLBHIT"
    df = load_snaps(series)
    if df.empty:
        print("no snapshots in window")
        return
    print(f"{len(df)} {series} markets observed within {WINDOW_MIN}m of first pitch")

    df["has_ask"] = (df["ya"].notna() & (df["ya"] > 0) & (df["ya"] < 1)
                     & (df["yas"] > 0))
    print(f"  tradeable ask displayed: {df['has_ask'].mean():.1%}")
    q = df[df["has_ask"]].copy()
    q["spread_c"] = 100 * (q["ya"] - q["yb"].fillna(0))
    cheap = q[(q["ya"] >= LO) & (q["ya"] <= HI)].copy()
    print(f"  asks in [{LO},{HI}]: {len(cheap)}")
    if cheap.empty:
        return
    print(f"  median displayed size at ask: {cheap['yas'].median():.0f} contracts")
    print(f"  median spread: {cheap['spread_c'].median():.1f}c")

    # how optimistic was the trade-print basis?
    both = cheap[cheap["last"].notna() & (cheap["last"] > 0)]
    if len(both):
        gap = 100 * (both["ya"] - both["last"])
        print(f"  ask minus last-trade: mean {gap.mean():+.2f}c, "
              f"median {gap.median():+.2f}c (n={len(both)})")

    print("\nfetching settlements...")
    res = fetch_results(cheap["tk"].tolist())
    cheap["result"] = cheap["tk"].map(res)
    s = cheap[cheap["result"].isin(["yes", "no"])].copy()
    print(f"  {len(s)} settled\n")
    if len(s) < 20:
        print("too few settled markets to conclude")
        return

    s["y"] = (s["result"] == "yes").astype(float)
    s["net_ask"] = s["y"] - s["ya"] - FEE(s["ya"])
    print("=== EXECUTION TEST: buy YES at the displayed ask ===")
    print(f"  n={len(s)}  mean ask={s['ya'].mean():.3f}  "
          f"realized hit={s['y'].mean():.3f}")
    print(f"  gross edge (hit - ask): {100*(s['y'].mean()-s['ya'].mean()):+.2f}pp")
    print(f"  NET P&L per contract  : {100*s['net_ask'].mean():+.2f}c")
    se = 100 * s["net_ask"].std() / np.sqrt(len(s))
    print(f"  std error             : {se:.2f}c  "
          f"(95% CI {100*s['net_ask'].mean()-1.96*se:+.2f} to "
          f"{100*s['net_ask'].mean()+1.96*se:+.2f}c)")

    # size-weighted: what you could actually get filled
    fillable = np.minimum(s["yas"], 100)
    print(f"\n  size-weighted net (cap 100/mkt): "
          f"{100*(s['net_ask']*fillable).sum()/fillable.sum():+.2f}c")
    print(f"  total displayed size available: {s['yas'].sum():.0f} contracts")
    print(f"  theoretical $ at that net     : "
          f"${(s['net_ask']*fillable).sum():.2f}")

    # counterfactual: same markets priced at last trade (the old basis)
    lt = s[s["last"].notna() & (s["last"] > 0)].copy()
    if len(lt) > 20:
        lt["net_last"] = lt["y"] - lt["last"] - FEE(lt["last"]) - 0.015
        print(f"\n  same markets on OLD trade-print basis: "
              f"{100*lt['net_last'].mean():+.2f}c (n={len(lt)})")
        print(f"  execution gap: "
              f"{100*(lt['net_last'].mean()-lt['net_ask'].mean()):+.2f}c overstated")

    s.to_csv(DATA / f"execution_test_{series}.csv", index=False)
    print(f"\nsaved -> data/execution_test_{series}.csv")


if __name__ == "__main__":
    main()
