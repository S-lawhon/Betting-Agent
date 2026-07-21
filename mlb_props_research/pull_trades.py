"""Q3 — pull trade prints for settled MLB markets (maker/taker economics).

Universe: settled markets from data/settled/ with volume >= MIN_VOL, closed in
the last DAYS days: all prop series + moneyline/totals baselines.
Output: data/trades.parquet (one row per trade, joined with settlement).

Run AFTER pull_settled_props.py completes (shares the Kalshi rate budget with
the live collector; throttled to ~2.2 req/s).
"""
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kalshi-ev-map" / "src"))
import kalshi_client as kc

kc.MIN_INTERVAL = 0.32  # collector averages ~1.9 req/s over its cycle; backoff absorbs overlap

import pandas as pd

DATA = Path(__file__).resolve().parent / "data"
MIN_VOL = 20
# window: markets closed between MAX_DAYS and MIN_DAYS ago
MIN_DAYS = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--min-days=")), 0))
MAX_DAYS = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--max-days=")), 45))
TAG = f"_{MIN_DAYS}_{MAX_DAYS}" if MIN_DAYS else ""
# per-series market caps (by volume desc) to keep the pull ~4h; None = all
SERIES = [("KXMLBKS", None), ("KXMLBHR", 8000), ("KXMLBRFI", None),
          ("KXMLBF5", None), ("KXMLBTEAMTOTAL", None), ("KXMLBHIT", 6000),
          ("KXMLBTB", 6000), ("KXMLBHRR", 6000), ("KXMLBSB", None),
          ("KXMLBGAME", 1200), ("KXMLBTOTAL", 1500)]  # last two = efficient baselines


def target_markets():
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_DAYS)
    newest = datetime.now(timezone.utc) - timedelta(days=MIN_DAYS)
    targets = []
    for s, cap in SERIES:
        path = DATA / "settled" / f"{s}.json"
        if not path.exists():
            print(f"[skip] {s}: no settled cache", flush=True)
            continue
        with open(path) as f:
            ms = json.load(f)
        rows = []
        for m in ms:
            if m.get("result") not in ("yes", "no"):
                continue
            vol = kc.fnum(m.get("volume_fp")) or 0
            ct = m.get("close_time")
            if vol < MIN_VOL or not ct:
                continue
            closed = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            if closed < cutoff or closed > newest:
                continue
            rows.append((m["ticker"], m["result"], m["close_time"], vol))
        rows.sort(key=lambda r: -r[3])
        if cap:
            rows = rows[:cap]
        targets.extend((s,) + r for r in rows)
        print(f"[{s}] {len(rows)} target markets", flush=True)
    return targets


def main():
    targets = target_markets()
    print(f"total targets: {len(targets)}", flush=True)
    out_path = DATA / f"trades{TAG}.parquet"
    done_path = DATA / f"trades_done{TAG}.json"
    done = set()
    if done_path.exists():
        done = set(json.load(open(done_path)))
    rows = []
    if out_path.exists():
        rows = pd.read_parquet(out_path).to_dict("records")
    n_done = 0
    for s, tk, result, close_time, vol in targets:
        if tk in done:
            continue
        try:
            for t in kc.paginate("/markets/trades", {"ticker": tk, "limit": 1000},
                                 list_key="trades"):
                rows.append({
                    "series": s, "ticker": tk, "result": result,
                    "close_time": close_time,
                    "created_time": t.get("created_time"),
                    "taker_side": t.get("taker_side"),
                    "yes_price": kc.fnum(t.get("yes_price_dollars")),
                    "count": kc.fnum(t.get("count_fp")),
                })
        except Exception as e:
            print(f"[{tk}] failed: {str(e)[:120]}", flush=True)
            continue
        done.add(tk)
        n_done += 1
        if n_done % 200 == 0:
            pd.DataFrame(rows).to_parquet(out_path, index=False)
            json.dump(sorted(done), open(done_path, "w"))
            print(f"checkpoint: {n_done} markets, {len(rows)} trades", flush=True)
    pd.DataFrame(rows).to_parquet(out_path, index=False)
    json.dump(sorted(done), open(done_path, "w"))
    print(f"[done] {n_done} markets, {len(rows)} trades -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
