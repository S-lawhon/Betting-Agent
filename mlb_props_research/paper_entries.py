"""Paper-trade logger for the live execution validation (Phase 3b follow-up).

Records what the strategy WOULD have bought, at the ask actually displayed near
first pitch, with the activity filter that Phase 3b showed is load-bearing.

Entry rule (from PHASE3_REPORT.md addendum):
  - series KXMLBHIT, strike ladder any N
  - observed within WINDOW_MIN of scheduled first pitch
  - displayed ask in [0.15, 0.45] with size > 0
  - ACTIVITY FILTER: market must have traded (volume > MIN_VOL) — Phase 3b
    found lifting asks in untraded books loses money
  - spread <= MAX_SPREAD_C (avoid wide uncontested books)

  python paper_entries.py            # log today's entries from snapshots
  python paper_entries.py --settle   # fetch results for open entries
  python paper_entries.py --report   # cumulative P&L
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kalshi-ev-map" / "src"))
import kalshi_client as kc

from execution_test import load_snaps, LO, HI, FEE

DATA = Path(__file__).resolve().parent / "data"
LEDGER = DATA / "paper_ledger.csv"

MIN_VOL = 50        # activity filter: market must have real trading
MAX_SPREAD_C = 6.0  # skip wide, uncontested books
MAX_SIZE = 100      # per-market paper size cap, contracts


def log_entries():
    df = load_snaps("KXMLBHIT")
    if df.empty:
        print("no snapshots in window")
        return
    q = df[df["ya"].notna() & (df["ya"] > 0) & (df["ya"] < 1) & (df["yas"] > 0)].copy()
    q["spread_c"] = 100 * (q["ya"] - q["yb"].fillna(0))
    sel = q[(q["ya"] >= LO) & (q["ya"] <= HI)
            & (q["vol"].fillna(0) >= MIN_VOL)
            & (q["spread_c"] <= MAX_SPREAD_C)].copy()
    sel["size"] = np.minimum(sel["yas"], MAX_SIZE)
    sel["entry_price"] = sel["ya"]
    sel["fee"] = FEE(sel["ya"])
    sel["result"] = ""
    sel["pnl"] = np.nan
    cols = ["tk", "ts", "mins", "entry_price", "size", "spread_c", "vol",
            "fee", "result", "pnl"]
    out = sel[cols]
    if LEDGER.exists():
        old = pd.read_csv(LEDGER)
        out = pd.concat([old, out[~out["tk"].isin(old["tk"])]], ignore_index=True)
    out.to_csv(LEDGER, index=False)
    print(f"logged {len(sel)} entries (ledger now {len(out)} rows)")
    if len(sel):
        print(f"  mean ask {sel['ya'].mean():.3f}, median size {sel['size'].median():.0f}, "
              f"median spread {sel['spread_c'].median():.1f}c")


def settle():
    if not LEDGER.exists():
        print("no ledger")
        return
    led = pd.read_csv(LEDGER)
    open_rows = led[led["result"].isna() | (led["result"] == "")]
    print(f"{len(open_rows)} open entries")
    for i, tk in open_rows["tk"].items():
        try:
            m = kc.get(f"/markets/{tk}")["market"]
            r = m.get("result")
        except Exception:
            continue
        if r in ("yes", "no"):
            led.at[i, "result"] = r
            y = 1.0 if r == "yes" else 0.0
            led.at[i, "pnl"] = (y - led.at[i, "entry_price"]
                                - led.at[i, "fee"]) * led.at[i, "size"]
    led.to_csv(LEDGER, index=False)
    done = led[led["result"].isin(["yes", "no"])]
    print(f"settled {len(done)} total")


def report():
    if not LEDGER.exists():
        print("no ledger")
        return
    led = pd.read_csv(LEDGER)
    d = led[led["result"].isin(["yes", "no"])].copy()
    if d.empty:
        print(f"{len(led)} entries logged, none settled yet")
        return
    d["y"] = (d["result"] == "yes").astype(float)
    contracts = d["size"].sum()
    per_ct = 100 * d["pnl"].sum() / contracts
    net = (d["y"] - d["entry_price"] - d["fee"])
    se = 100 * net.std() / np.sqrt(len(d))
    days = pd.to_datetime(d["ts"]).dt.date.nunique()
    print(f"=== PAPER LEDGER ===")
    print(f"  entries settled : {len(d)} across {days} days")
    print(f"  contracts       : {contracts:.0f}")
    print(f"  mean entry ask  : {d['entry_price'].mean():.3f}")
    print(f"  realized hit    : {d['y'].mean():.3f}")
    print(f"  P&L             : ${d['pnl'].sum():+.2f}  ({per_ct:+.2f}c/contract)")
    print(f"  95% CI per ct   : {100*net.mean()-1.96*se:+.2f} to {100*net.mean()+1.96*se:+.2f}c")
    print(f"  per day         : ${d['pnl'].sum()/max(days,1):+.2f}")


if __name__ == "__main__":
    if "--settle" in sys.argv:
        settle()
    elif "--report" in sys.argv:
        report()
    else:
        log_entries()
