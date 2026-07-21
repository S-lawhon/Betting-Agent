"""Phase 2 — rule-based maker backtest on historical trade prints.

Strategy per market:
  fair f = EWMA(alpha) of past trade prices (>=3 prints to arm)
  rest buy YES at floor(f - k), sell YES at ceil(f + k)  (cent grid, 1..99)
  FILL RULE (conservative): a print must trade THROUGH the quote —
    taker sell (taker_side=no) at p < bid  -> we buy at our bid
    taker buy  (taker_side=yes) at p > ask -> we sell at our ask
  inventory capped at +/-CAP contracts; fills sized to min(print, capacity)
  optional jump-pull: after |print - prev_print| >= JUMP, stop quoting for
    PULL_MIN minutes (reacts after the jump print: no lookahead)
  settle inventory at result; prop maker fees = $0.

Windows (hours before scheduled start, from ticker key): quoting allowed only
inside the window; positions settle at result regardless.

Output: P&L per contract filled, fill counts, per-market P&L by
series x window x half-spread x pull-variant.
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

SERIES = ["KXMLBHIT", "KXMLBTB", "KXMLBHRR", "KXMLBTEAMTOTAL", "KXMLBKS"]
ALPHA = 0.3
CAP = 100.0
JUMP = 0.05
PULL_MIN = 30
WINDOWS = [("24-5h", 24, 5), ("5-2h", 5, 2), ("2-0h", 2, 0)]
KS = [0.01, 0.02, 0.03]


def parse_start(tk):
    m = KEY_RE.match(tk)
    if not m:
        return None
    yy, mon, dd, hh, mi = m.groups()
    try:
        return pd.Timestamp(2000 + int(yy), MONTHS[mon], int(dd),
                            int(hh), int(mi), tz=ET).tz_convert("UTC")
    except (ValueError, KeyError):
        return None


def sim_market_fast(times_h, prices, sides, counts, result, w_hi, w_lo, k, pull):
    y = 1.0 if result == "yes" else 0.0
    inv = 0.0
    cash = 0.0
    filled = 0.0
    fair = prices[0]
    n_seen = 1
    pull_until = -1e9
    for i in range(1, len(prices)):
        t = times_h[i]
        now = -t
        p, s, c = prices[i], sides[i], counts[i]
        # quotes derive from state BEFORE print i
        if (n_seen >= 3 and w_lo <= t <= w_hi
                and not (pull and now < pull_until)):
            bid = np.floor((fair - k) * 100) / 100
            ask = np.ceil((fair + k) * 100) / 100
            if 0.01 <= bid and s == "no" and p < bid and inv < CAP:
                q = min(c, CAP - inv)
                inv += q
                cash -= q * bid
                filled += q
            elif ask <= 0.99 and s == "yes" and p > ask and inv > -CAP:
                q = min(c, CAP + inv)
                inv -= q
                cash += q * ask
                filled += q
        if abs(p - prices[i - 1]) >= JUMP:
            pull_until = now + PULL_MIN / 60.0
        fair = ALPHA * p + (1 - ALPHA) * fair
        n_seen += 1
    pnl = cash + inv * y
    return pnl, filled


def main():
    src = DATA / (sys.argv[1] if len(sys.argv) > 1 else "trades.parquet")
    df = pd.read_parquet(src)
    df = df[df["series"].isin(SERIES)].copy()
    df["created_time"] = pd.to_datetime(df["created_time"], utc=True,
                                        format="ISO8601")
    starts = {t: parse_start(t) for t in df["ticker"].unique()}
    df["start"] = df["ticker"].map(starts)
    df = df.dropna(subset=["start", "yes_price", "count"])
    df["tth"] = (df["start"] - df["created_time"]).dt.total_seconds() / 3600
    df = df.sort_values(["ticker", "created_time"])
    print(f"{len(df)} prop trades, {df['ticker'].nunique()} markets\n")

    groups = {}
    for tk, g in df.groupby("ticker"):
        groups[tk] = (g["series"].iloc[0], g["tth"].values,
                      g["yes_price"].values, g["taker_side"].values,
                      g["count"].values, g["result"].iloc[0])

    rows = []
    for wname, w_hi, w_lo in WINDOWS:
        for k in KS:
            for pull in (False, True):
                agg = {}
                for tk, (s, th, pr, sd, ct, res) in groups.items():
                    pnl, fl = sim_market_fast(th, pr, sd, ct, res,
                                              w_hi, w_lo, k, pull)
                    if fl > 0:
                        a = agg.setdefault(s, [0.0, 0.0, 0])
                        a[0] += pnl
                        a[1] += fl
                        a[2] += 1
                for s, (pnl, fl, nm) in agg.items():
                    rows.append({
                        "series": s, "window": wname, "k_c": int(k * 100),
                        "pull": pull, "markets": nm,
                        "contracts": round(fl),
                        "pnl_usd": round(pnl, 2),
                        "pnl_per_contract_c": round(100 * pnl / fl, 2),
                    })
    out = pd.DataFrame(rows)
    out.to_csv(DATA / "maker_backtest_results.csv", index=False)
    for s in SERIES:
        sub = out[out["series"] == s]
        if sub.empty:
            continue
        print(f"=== {s} ===")
        print(sub.drop(columns="series")
                 .sort_values(["window", "k_c", "pull"])
                 .to_string(index=False), "\n")


if __name__ == "__main__":
    main()
