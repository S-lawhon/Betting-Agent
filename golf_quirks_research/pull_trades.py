"""
golf_quirks_research/pull_trades.py
───────────────────────────────────
Caches public executed trade prints for the golf settlement-quirk studies.

REBUILT 2026-07-26 (original lost 2026-07-25, never committed). Output schema
is byte-compatible with the surviving `data/leader_trades/` cache:

    {"ticker", "event", "close_time", "result",
     "settlement_value_dollars",
     "trades": [{"epoch", "yes_price", "count", "taker_side"}, ...]}

Modes
  leader   P-022 round-leader fade universe   — 12h anchor in [0.03, 0.12]
  makecut  P-023 make-cut universe (PGA+DPW)  — 48h anchor in [0.40, 0.60]
  livtopn  P-023 LIV top-N (MARGINAL cohort)  — 48h anchor in [0.08, 0.45]

The universe is selected from the CANDLE cache, so no API call is spent on
markets that were never in the tradeable band. Already-cached tickers are
skipped, making the script safely resumable.

RATE LIMIT: the Kalshi public API budget (2 req/s per IP) is SHARED with the
live VPS and other agent sessions. Default `--min-interval 0.6` deliberately
uses well under half of it. Do not lower it.

Usage
  python3 pull_trades.py --mode makecut            # pull
  python3 pull_trades.py --mode makecut --dry-run  # size the job first
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quirks_common as qc  # noqa: E402  (same dir)
from src.kalshi_public import KalshiPublic, fnum  # noqa: E402

MODES: Dict[str, Dict[str, Any]] = {
    "leader": {
        "series": qc.LEADER_SERIES,
        "hours": 12.0,
        "band": (0.03, 0.12),
        "out": "leader_trades",
    },
    "makecut": {
        "series": qc.MAKECUT_SERIES_PGA + qc.MAKECUT_SERIES_DPW,
        "hours": 48.0,
        "band": (0.40, 0.60),
        "out": "makecut_trades",
    },
    "livtopn": {
        "series": qc.LIV_TOPN_SERIES,
        "hours": 48.0,
        "band": (0.08, 0.45),
        "out": "livtopn_trades",
    },
}


def fetch_all_trades(kp: KalshiPublic, ticker: str,
                     page_limit: int = 1000,
                     max_pages: int = 60) -> List[Dict[str, Any]]:
    """Every public print for `ticker`, oldest-first.

    `/markets/trades` returns newest-first and paginates by cursor. We walk
    the whole history: unlike the live engine's `trades_since`, a backtest
    needs the full tape, and the pre-event window we care about is the
    OLDEST part of it.
    """
    out: List[Dict[str, Any]] = []
    params: Dict[str, Any] = {"ticker": ticker, "limit": page_limit}
    seen: set = set()
    for _ in range(max_pages):
        data = kp.get("/markets/trades", params)
        if not data:
            break
        trades = data.get("trades") or []
        if not trades:
            break
        for t in trades:
            tid = t.get("trade_id")
            if tid and tid in seen:
                continue
            if tid:
                seen.add(tid)
            ts = qc.iso_epoch(t.get("created_time"))
            px = fnum(t.get("yes_price_dollars"))
            if px is None:
                raw = fnum(t.get("yes_price"))
                px = raw / 100.0 if raw is not None else None
            cnt = fnum(t.get("count_fp")) or fnum(t.get("count")) or 0.0
            if ts is None or px is None or cnt <= 0:
                continue
            out.append({
                "epoch": ts,
                "yes_price": px,
                "count": cnt,
                "taker_side": t.get("taker_side", ""),
            })
        cursor = data.get("cursor")
        if not cursor:
            break
        params["cursor"] = cursor
    out.sort(key=lambda t: t["epoch"])
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", required=True, choices=sorted(MODES))
    ap.add_argument("--out-dir", default=None,
                    help="override the per-mode cache dir (under data/)")
    ap.add_argument("--min-interval", type=float, default=0.6,
                    help="seconds between API calls (shared 2 req/s budget)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the universe and exit without any API call")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N markets (smoke test)")
    args = ap.parse_args(argv)

    cfg = MODES[args.mode]
    out_dir = os.path.join(qc.DATA, args.out_dir or cfg["out"])
    universe = qc.build_universe(cfg["series"], cfg["hours"], cfg["band"])

    tourns = sorted({r["tournament"] for r in universe})
    print(f"[trades] mode={args.mode} anchor=H-{cfg['hours']:.0f}h "
          f"band={cfg['band']} -> {len(universe)} postable markets "
          f"across {len(tourns)} tournaments")
    print(f"[trades] tournaments: {', '.join(tourns)}")

    os.makedirs(out_dir, exist_ok=True)
    todo = [r for r in universe
            if not os.path.exists(os.path.join(out_dir, f"{r['ticker']}.json"))]
    print(f"[trades] {len(universe) - len(todo)} already cached, "
          f"{len(todo)} to pull -> {out_dir}")
    if args.dry_run:
        return 0
    if args.limit:
        todo = todo[:args.limit]

    kp = KalshiPublic(min_interval=args.min_interval)
    n_tr = 0
    for i, r in enumerate(todo, 1):
        trades = fetch_all_trades(kp, r["ticker"])
        rec = {
            "ticker": r["ticker"],
            "event": r["event"],
            "close_time": r["close_time"],
            "result": r["result"],
            "settlement_value_dollars": r["settlement_value_dollars"],
            "trades": trades,
        }
        with open(os.path.join(out_dir, f"{r['ticker']}.json"), "w") as fh:
            json.dump(rec, fh)
        n_tr += len(trades)
        if i % 25 == 0 or i == len(todo):
            print(f"[trades] {i}/{len(todo)} ({n_tr} prints)")
    print("[trades] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
