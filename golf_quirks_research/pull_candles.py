"""
golf_quirks_research/pull_candles.py
────────────────────────────────────
Pull 1-minute candlesticks for settled golf markets into `data/candles/`.

**Why this file exists at all:** 11,349 candle records sit in `data/candles/`
and **nothing in the repo generated them** — the original puller was never
committed and is gone, the same 2026-07-25 loss `CLAUDE.md` warns about. Every
backtest here builds its universe from that cache
(`quirks_common.iter_candle_recs`), so without a puller the sample can never be
extended: `pull_trades.py --mode leader` reported "0 to pull" for eight
genuinely missing tournaments purely because they had no candles.

This is a replacement for the missing generator, **not** a third backtest
harness — the replay itself (`backtest_fade_fills.py`) is untouched and must
still pass `--validate`.

Output is byte-compatible with the surviving cache: one JSON per ticker with
`ticker, series, event, result, settlement_value_dollars, close_time, candles`.
Already-cached tickers are skipped, so it is safely resumable.

    python3 -m golf_quirks_research.pull_candles --events SOO26,DOWC26 --dry-run
    python3 -m golf_quirks_research.pull_candles --events SOO26,DOWC26
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi_public import KalshiPublic          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CANDLE_DIR = os.path.join(HERE, "data", "candles")

LEADER_SERIES = (
    "KXPGAR1LEAD", "KXPGAR2LEAD", "KXPGAR3LEAD",
    "KXLIVR1LEAD", "KXLIVR2LEAD", "KXLIVR3LEAD",
    "KXLPGAR1LEAD", "KXLPGAR2LEAD", "KXLPGAR3LEAD",
    "KXDPWORLDTOURR1LEAD", "KXDPWORLDTOURR2LEAD", "KXDPWORLDTOURR3LEAD",
    "KXCHAMPTOURR1LEAD",
)


def settled_markets(k: KalshiPublic, series: str) -> List[Dict[str, Any]]:
    out, params = [], {"series_ticker": series, "status": "settled", "limit": 200}
    for _ in range(12):
        d = k.get("/markets", params)
        if not d:
            break
        out.extend(d.get("markets", []))
        cur = d.get("cursor")
        if not cur:
            break
        params["cursor"] = cur
    return out


def fetch_candles(k: KalshiPublic, series: str, ticker: str,
                  close_ts: int):
    """1-minute candles over the 48h before close, or None if the FETCH FAILED.

    The None-vs-[] distinction is load-bearing and was missing in the first
    version of this file. `k.get` returns None on a transport failure, and
    `(d or {}).get("candlesticks") or []` collapsed that into the same empty
    list a genuinely candle-less market returns. During a local DNS outage the
    puller therefore wrote 28 records with `"candles": []` that looked
    successfully fetched — and because the puller skips anything already on
    disk, a resume would never re-fetch them. A transient network blip would
    have silently truncated the sample, which is precisely the class of failure
    this backtest cannot afford.

    Returns None on failure so the caller can skip writing anything.
    """
    start = close_ts - 48 * 3600
    d = k.get(f"/series/{series}/markets/{ticker}/candlesticks",
              {"start_ts": start, "end_ts": close_ts, "period_interval": 1})
    if d is None:
        return None
    return d.get("candlesticks") or []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True,
                    help="comma-separated tournament codes, e.g. SOO26,DOWC26")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-interval", type=float, default=0.6)
    args = ap.parse_args()

    want = {e.strip().upper() for e in args.events.split(",") if e.strip()}
    k = KalshiPublic(min_interval=args.min_interval)
    os.makedirs(CANDLE_DIR, exist_ok=True)

    targets: List[Dict[str, Any]] = []
    for s in LEADER_SERIES:
        for m in settled_markets(k, s):
            et = m.get("event_ticker") or ""
            code = et.split("-", 1)[1] if "-" in et else ""
            if code.upper() not in want:
                continue
            targets.append({"m": m, "series": s})

    todo = [t for t in targets
            if not os.path.exists(os.path.join(CANDLE_DIR,
                                               f"{t['m']['ticker']}.json"))]
    print(f"[candles] {len(targets)} markets across {sorted(want)}; "
          f"{len(targets) - len(todo)} already cached, {len(todo)} to pull")
    if args.dry_run or not todo:
        return 0

    written = empty = failed = 0
    for i, t in enumerate(todo, 1):
        m, s = t["m"], t["series"]
        ticker = m["ticker"]
        ct = m.get("close_time") or ""
        try:
            close_ts = int(time.mktime(time.strptime(ct[:19], "%Y-%m-%dT%H:%M:%S"))
                           - time.timezone)
        except (ValueError, TypeError):
            continue
        candles = fetch_candles(k, s, ticker, close_ts)
        if candles is None:
            failed += 1
            continue          # write NOTHING, so a resume re-fetches it
        rec = {
            "ticker": ticker, "series": s, "event": m.get("event_ticker"),
            "result": m.get("result"),
            "settlement_value_dollars": m.get("settlement_value_dollars"),
            "close_time": ct, "candles": candles,
        }
        with open(os.path.join(CANDLE_DIR, f"{ticker}.json"), "w") as fh:
            json.dump(rec, fh)
        written += 1
        empty += int(not candles)
        if i % 100 == 0:
            print(f"[candles] {i}/{len(todo)}")
    print(f"[candles] wrote {written} records ({empty} with no candles); "
          f"{failed} FETCH FAILURES skipped and left un-cached for a resume")
    if failed:
        print("[candles] re-run to pick them up — nothing was written for them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
