"""Internal-consistency scan over the open universe.

Checks, per event (group of markets sharing event_ticker):
  1. Bracket-sum arbitrage (mutually exclusive & exhaustive candidates):
     buy-all-YES cost = sum(yes_ask) + taker fees < $1  -> long arb
     buy-all-NO  cost = sum(1 - yes_bid) + fees < n-1   -> short arb
     (Only meaningful if the event is genuinely one-winner-exhaustive; we
     flag candidates and require manual rules check before trading.)
  2. Threshold monotonicity: within an event whose markets share strike_type
     'greater'/'less' with a numeric strike (floor_strike/cap_strike parsed
     from ticker suffix where possible), P(>=x) must be non-increasing in x.
     Violation size = executable crossing (bid of the dominated leg above ask
     of the dominating leg).

Output: data/consistency_findings.parquet + console summary.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fees

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def bracket_scan(df):
    out = []
    for ev, g in df.groupby("event_ticker"):
        g = g[(g["yes_ask"].notna()) & (g["yes_bid"].notna())]
        g = g[(g["yes_ask"] > 0) & (g["yes_ask"] < 1)]
        n = len(g)
        if n < 3:  # 2-outcome events are enforced yes/no complements
            continue
        st = g["series_ticker"].iloc[0]
        ask_sum = g["yes_ask"].sum()
        ask_fees = sum(fees.fee_per_contract(a, st) for a in g["yes_ask"])
        long_cost = ask_sum + ask_fees
        bid_sum = g["yes_bid"].sum()
        no_cost = (1 - g["yes_bid"]).sum() + sum(
            fees.fee_per_contract(1 - b, st) for b in g["yes_bid"])
        out.append({
            "event_ticker": ev, "series_ticker": st, "n_legs": n,
            "family": g["family"].iloc[0],
            "ask_sum": round(ask_sum, 4), "long_cost": round(long_cost, 4),
            "long_arb": round(1 - long_cost, 4),
            "bid_sum": round(bid_sum, 4),
            "short_arb": round((n - 1) - no_cost, 4),
            "min_vol24": g["volume_24h"].min(), "sum_vol24": g["volume_24h"].sum(),
        })
    return pd.DataFrame(out)


STRIKE_RX = re.compile(r"-(T|B)?(\d+(?:\.\d+)?)$")


def parse_strike(ticker):
    m = STRIKE_RX.search(ticker)
    return float(m.group(2)) if m else None


def monotonicity_scan(df):
    out = []
    thr = df[df["strike_type"].isin(["greater", "greater_or_equal",
                                     "less", "less_or_equal"])].copy()
    thr["strike"] = thr["ticker"].map(parse_strike)
    thr = thr[thr["strike"].notna()]
    for (ev, stype), g in thr.groupby(["event_ticker", "strike_type"]):
        if len(g) < 2:
            continue
        asc = stype.startswith("less")  # P(<=x) increasing in x
        g = g.sort_values("strike")
        # For 'greater': as strike rises, yes price must not rise.
        # Executable violation: bid(higher strike) > ask(lower strike).
        rows = list(g.itertuples())
        for lo, hi in zip(rows, rows[1:]):
            if asc:
                lo, hi = hi, lo
            if (hi.yes_bid is not None and lo.yes_ask is not None
                    and not np.isnan(hi.yes_bid) and not np.isnan(lo.yes_ask)
                    and hi.yes_bid > lo.yes_ask and 0 < lo.yes_ask < 1):
                st = lo.series_ticker
                gross = hi.yes_bid - lo.yes_ask
                cost = (fees.fee_per_contract(lo.yes_ask, st)
                        + fees.fee_per_contract(1 - hi.yes_bid, st))
                out.append({
                    "event_ticker": ev, "series_ticker": st,
                    "family": lo.family, "strike_type": stype,
                    "buy": lo.ticker, "buy_ask": lo.yes_ask,
                    "sell": hi.ticker, "sell_bid": hi.yes_bid,
                    "gross": round(gross, 4),
                    "net": round(gross - cost, 4),
                    "min_vol24": min(lo.volume_24h or 0, hi.volume_24h or 0),
                })
    return pd.DataFrame(out)


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    df = pd.read_parquet(DATA_DIR / "kalshi_universe.parquet")
    br = bracket_scan(df)
    mono = monotonicity_scan(df)
    br.to_parquet(DATA_DIR / "consistency_brackets.parquet", index=False)
    if len(mono):
        mono.to_parquet(DATA_DIR / "consistency_mono.parquet", index=False)
    print(f"events scanned: {len(br)}")
    cand = br[(br["long_arb"] > 0.005) & (br["min_vol24"] > 0)]
    print(f"\nlong bracket-arb candidates (sum asks + fees < 99.5c, all legs "
          f"traded today): {len(cand)}")
    print(cand.sort_values("long_arb", ascending=False).head(20).to_string(index=False))
    scand = br[(br["short_arb"] > 0.005) & (br["min_vol24"] > 0)]
    print(f"\nshort bracket-arb candidates: {len(scand)}")
    print(scand.sort_values("short_arb", ascending=False).head(20).to_string(index=False))
    print(f"\nmonotonicity violations (executable, net>0): "
          f"{len(mono[mono['net'] > 0]) if len(mono) else 0}")
    if len(mono):
        print(mono[mono["net"] > 0].sort_values("net", ascending=False)
              .head(20).to_string(index=False))
