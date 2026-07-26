#!/usr/bin/env python3
"""
golf_quirks_research/estimate_scalar_contamination.py
─────────────────────────────────────────────────────
Quantify how much the `result="scalar"` → VOID bug in
`src/kalshi_golf_settler.py` distorts P-017's forward numbers, using
only cached data (no live API, no VPS access needed).

The bug: every scalar settlement was booked VOID with pnl_gross = 0
("stake refunded").  Kalshi actually pays `settlement_value_dollars` per
contract.  For a YES buyer the true gross P&L is

    contracts x (settlement_value - fill_price)

so the *booking error* per contract is exactly `settlement_value -
fill_price`, and the *sample* error is that the row was dropped from the
WIN/LOSS population the gate counts.

We do not have P-017's live fills locally, so fill_price is proxied by
the market's own yes_ask inside P-017's configured entry window
(`min_days_to_close` 4.0 → `max_days_to_close` 10.0) filtered to its
tradeable band (`ask_floor` 0.08 → `ask_cap` 0.45).  That is the price
P-017 would have paid on that market.

Run:  python3 golf_quirks_research/estimate_scalar_contamination.py
"""

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CENSUS = HERE / "data" / "settled_meta.jsonl"
CANDLES = HERE / "data" / "candles"

# P-017 config (config_multi_pod.yaml → pods.P-017.strategy)
P017_SERIES = ("KXPGATOP10", "KXPGATOP20")
ASK_FLOOR, ASK_CAP = 0.08, 0.45
MIN_DAYS, MAX_DAYS = 4.0, 10.0
# P-022's universe, for contrast (Phase-2 report §1)
P022_SERIES_SUFFIX = "LEAD"
P022_BAND = (0.03, 0.12)


def _ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(
        tzinfo=timezone.utc
    )


def load_census():
    rows = []
    with CENSUS.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def entry_ask(ticker, close_time):
    """Proxy fill price: mean yes_ask across P-017's 4–10 day window.

    Candle coverage does not always reach back 10 days (Kalshi's history
    is ~1 month and some markets open late), so when the window is empty
    we fall back to the EARLIEST available ask — the closest thing to a
    pre-tournament entry price on record.  Returns (ask, source).
    """
    path = CANDLES / f"{ticker}.json"
    if not path.exists():
        return None, "no_candles"
    try:
        blob = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None, "unreadable"
    close_ts = _ts(close_time).timestamp()
    pts = []
    for c in blob.get("candles") or []:
        try:
            ts = float(c["end_period_ts"])
            ask = float((c.get("yes_ask") or {}).get("close_dollars"))
        except (KeyError, TypeError, ValueError):
            continue
        if 0.0 < ask < 1.0:
            pts.append(((close_ts - ts) / 86400.0, ask))
    if not pts:
        return None, "no_ask_quotes"
    window = [a for d, a in pts if MIN_DAYS <= d <= MAX_DAYS]
    if window:
        return statistics.mean(window), "in_window"
    return max(pts, key=lambda p: p[0])[1], "earliest_available"


def main():
    rows = load_census()
    total = len(rows)
    scalars = [r for r in rows if r.get("result") == "scalar"]

    print("=" * 72)
    print("Scalar settlement census — cached golf universe")
    print("=" * 72)
    print(f"  settled markets      : {total:,}")
    print(f"  result=scalar        : {len(scalars):,} "
          f"({100.0*len(scalars)/total:.2f}%)")
    nonzero = [r for r in scalars
               if float(r.get("settlement_value_dollars") or 0) > 0]
    print(f"  ...with a NON-ZERO settlement_value: {len(nonzero):,} "
          f"({100.0*len(nonzero)/max(len(scalars),1):.1f}% of scalars)")
    print("  Every one of these was booked pnl_gross = 0 by the old settler.")

    # ── P-017's own universe ─────────────────────────────────────────
    p017_all = [r for r in rows if r.get("series") in P017_SERIES]
    p017_scal = [r for r in p017_all if r.get("result") == "scalar"]
    print()
    print("-" * 72)
    print(f"P-017 universe ({'/'.join(P017_SERIES)})")
    print("-" * 72)
    print(f"  settled markets      : {len(p017_all):,}")
    print(f"  result=scalar        : {len(p017_scal):,} "
          f"({100.0*len(p017_scal)/max(len(p017_all),1):.2f}%)")

    priced, sources = [], {}
    for r in p017_scal:
        ask, src = entry_ask(r["ticker"], r["close_time"])
        sources[src] = sources.get(src, 0) + 1
        if ask is None:
            continue
        sv = float(r.get("settlement_value_dollars") or 0)
        priced.append((r["ticker"], ask, sv, sv - ask, src))

    print(f"  price coverage       : {sources}")
    in_band = [d for d in priced if ASK_FLOOR <= d[1] <= ASK_CAP]

    def _summary(label, data):
        if not data:
            print(f"  {label}: no rows")
            return
        errs = [d[3] for d in data]
        print(f"  {label} (n={len(errs)})")
        print(f"    mean booking error : {statistics.mean(errs)*100:+.2f} "
              f"c/contract  (true P&L minus the $0 that was booked)")
        print(f"    median             : {statistics.median(errs)*100:+.2f} c")
        print(f"    range              : {min(errs)*100:+.1f} to "
              f"{max(errs)*100:+.1f} c")
        print(f"    sign               : "
              f"{sum(1 for e in errs if e < -0.005)} negative / "
              f"{sum(1 for e in errs if abs(e) <= 0.005)} flat / "
              f"{sum(1 for e in errs if e > 0.005)} positive")

    print()
    _summary("all priced scalars", priced)
    print()
    _summary(f"P-017 tradeable band [{ASK_FLOOR},{ASK_CAP}]", in_band)
    if in_band:
        print()
        print("  worst 10 by |error| (in band):")
        for t, ask, sv, e, _src in sorted(in_band, key=lambda d: -abs(d[3]))[:10]:
            print(f"    {t:34s} entry {ask:.3f} → settled {sv:.3f}  "
                  f"{e*100:+.1f} c")
        print()
        print("  DIRECTION: the error is one-sided NEGATIVE.  A withdrawal is")
        print("  marked to fair value at cancellation, which for a golfer who")
        print("  WDs is usually well BELOW the pre-tournament entry price.")
        print("  Booking these as $0 therefore FLATTERS P-017, it does not")
        print("  penalise it — the pod's realised edge is overstated.")

    # ── P-022's universe: the reason this bug is a gating item ───────
    lead = [r for r in rows if P022_SERIES_SUFFIX in (r.get("series") or "")]
    lead_scal = [r for r in lead if r.get("result") == "scalar"]
    print()
    print("-" * 72)
    print("P-022 universe (round-leader *LEAD series)")
    print("-" * 72)
    print(f"  settled markets      : {len(lead):,}")
    print(f"  result=scalar        : {len(lead_scal):,} "
          f"({100.0*len(lead_scal)/max(len(lead),1):.2f}%)")
    if lead_scal:
        svs = [float(r.get("settlement_value_dollars") or 0) for r in lead_scal]
        print(f"  mean split payout    : ${statistics.mean(svs):.3f}/contract")
        print(f"  the old settler booked every one of these as $0.000 —")
        print(f"  i.e. a P-022 fade would have shown zero cost on 100% of the")
        print(f"  dead heats it exists to price.")

    # tie-count verification: LEAD scalars must be exactly $1/n
    ev = {}
    for r in lead_scal:
        ev.setdefault(r["event_ticker"], []).append(r)
    ok = sum(1 for ds in ev.values()
             if all(abs(float(d["settlement_value_dollars"]) - 1.0/len(ds))
                    <= 0.011 for d in ds))
    print(f"  $1/n verification    : {ok}/{len(ev)} scalar LEAD events match "
          f"1/n exactly")


if __name__ == "__main__":
    main()
