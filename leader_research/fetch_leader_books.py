"""
leader_research/fetch_leader_books.py
─────────────────────────────────────
P-026 Step 1 — the $0 pre-trade test.  READ-ONLY.  Cannot place orders.

Pulls, for each target KXLEADER* series:
  * the open markets (strike list, per event)   -> data/markets_<series>.json
  * top-of-book for every market                -> data/ob_<ticker>.json
  * recent executed prints (14d) for legs that show a live quote
                                                -> data/tr_<ticker>.json

Everything is cached per-ticker, so a re-run costs zero API calls and an
interrupted run resumes.  Kalshi is a shared ~2 req/s per-IP budget across
several concurrent agent sessions and the live VPS services, so this throttles
to ~1.2 req/s and backs off on 429 (handled inside KalshiPublic.get).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi_public import KalshiPublic  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

# Target series.  Integer-counting, tie-capable stats only.
# NEVER-FADE (deliberately excluded): sacks (half-sacks), steals, all yardage,
# all rate stats (ERA / AVG / OPS / WAR), NBA & WNBA per-game averages.
# tie rate `s` = share of the last 30 seasons whose leader board ended tied.
TARGETS = [
    ("KXLEADERMLBWINS",    "MLB pitcher wins",   0.30),
    ("KXLEADERMLBHR",      "MLB home runs",      0.07),
    ("KXLEADERMLBRBI",     "MLB RBI",            0.07),
    ("KXLEADERNFLINT",     "NFL interceptions",  0.47),
    ("KXLEADERNFLPINT",    "NFL pass INT",       0.47),
    ("KXLEADERNFLRUSHTDS", "NFL rushing TDs",    0.20),
    ("KXLEADERNFLRTDS",    "NFL receiving TDs",  0.20),
    ("KXLEADERNFLPTDS",    "NFL passing TDs",    0.20),
    # controls: same mechanism, lower tie rate
    ("KXLEADERMLBRUNS",    "MLB runs",           0.10),
    ("KXLEADERMLBHITS",    "MLB hits",           0.05),
]


def cached(name, fn):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        try:
            with open(p) as fh:
                return json.load(fh)
        except ValueError:
            pass
    val = fn()
    with open(p, "w") as fh:
        json.dump(val, fh, indent=1)
    return val


def main() -> None:
    k = KalshiPublic(min_interval=0.8)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
    snapshot = {"pulled_utc": datetime.now(timezone.utc).isoformat(),
                "series": {}}

    for series, label, s_tie in TARGETS:
        mk = cached(f"markets_{series}.json",
                    lambda s=series: k.open_markets(s, max_pages=5))
        rows = []
        for m in mk:
            t = m["ticker"]
            ob = cached(f"ob_{t}.json", lambda t=t: k.orderbook(t, depth=10))
            ob = ob or {}
            two_sided = (ob.get("yes_bid") is not None
                         and ob.get("yes_ask") is not None)
            tr = []
            if two_sided:
                tr = cached(
                    f"tr_{t}.json",
                    lambda t=t: k.trades_since(t, time.time() - 30 * 86400,
                                               max_pages=3)) or []
            rows.append({
                "ticker": t,
                "event_ticker": m.get("event_ticker"),
                "name": m.get("yes_sub_title") or m.get("subtitle") or m.get("title"),
                "status": m.get("status"),
                "close_time": m.get("close_time"),
                "volume": m.get("volume"),
                "open_interest": m.get("open_interest"),
                "liquidity": m.get("liquidity"),
                "orderbook": ob,
                "n_trades_30d": len(tr),
                "vol_30d": sum(x["count"] for x in tr),
                "last_print": (tr[-1]["yes_price"] if tr else None),
                "last_print_epoch": (tr[-1]["epoch"] if tr else None),
            })
        print(f"{series:22s} {label:20s} markets={len(mk):3d}", flush=True)
        snapshot["series"][series] = {"label": label, "tie_rate": s_tie,
                                      "markets": rows}

    out = os.path.join(DATA, f"snapshot_{stamp}.json")
    with open(out, "w") as fh:
        json.dump(snapshot, fh, indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main()
