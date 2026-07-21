"""Live MLB order book snapshot collector (Phase 1, Q4 liquidity ramp + Q1 data).

Every CYCLE_SECS:
  1. Pulls market objects for all 12 MLB series, logs compact records for
     markets expiring within HORIZON_H hours (top-of-book, volume, OI).
  2. Fetches full /orderbook depth for markets expiring within FOCUS_H hours
     (today's slate), capped per cycle, prioritized by series.

Output: data/live/snapshots_YYYYMMDD.jsonl — one record per market per cycle.
Run:    python collector.py [end_hour_local_24h]   (default 23:45)
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kalshi-ev-map" / "src"))
import kalshi_client as kc

SERIES = [
    "KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBF5", "KXMLBRFI",
    "KXMLBKS", "KXMLBHR", "KXMLBHIT", "KXMLBTB", "KXMLBTEAMTOTAL",
    "KXMLBHRR", "KXMLBSB",
]
CYCLE_SECS = 300
HORIZON_H = 48       # log market objects out to here
FOCUS_H = 14         # full orderbooks for today's slate
MAX_BOOKS_PER_CYCLE = 550

OUT_DIR = Path(__file__).resolve().parent / "data" / "live"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def compact(m, now_iso):
    return {
        "t": "mkt", "ts": now_iso,
        "tk": m["ticker"], "ev": m.get("event_ticker"),
        "exp": m.get("expected_expiration_time"),
        "yb": kc.fnum(m.get("yes_bid_dollars")), "ya": kc.fnum(m.get("yes_ask_dollars")),
        "ybs": kc.fnum(m.get("yes_bid_size_fp")), "yas": kc.fnum(m.get("yes_ask_size_fp")),
        "last": kc.fnum(m.get("last_price_dollars")),
        "vol": kc.fnum(m.get("volume_fp")), "oi": kc.fnum(m.get("open_interest_fp")),
        "status": m.get("status"),
    }


def cycle(out_path):
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    horizon = now + timedelta(hours=HORIZON_H)
    focus_cut = now + timedelta(hours=FOCUS_H)
    focus = []
    n_mkt = 0
    with open(out_path, "a") as f:
        for series in SERIES:
            try:
                for m in kc.paginate("/markets", {
                        "series_ticker": series, "status": "open", "limit": 1000}):
                    exp = m.get("expected_expiration_time")
                    if not exp:
                        continue
                    exp_dt = parse_ts(exp)
                    if exp_dt > horizon:
                        continue
                    f.write(json.dumps(compact(m, now_iso)) + "\n")
                    n_mkt += 1
                    if exp_dt <= focus_cut:
                        focus.append((SERIES.index(series), m["ticker"]))
            except Exception as e:  # keep the loop alive across transient API errors
                f.write(json.dumps({"t": "err", "ts": now_iso,
                                    "series": series, "err": str(e)[:200]}) + "\n")
        focus.sort()
        n_books = 0
        for _, tk in focus[:MAX_BOOKS_PER_CYCLE]:
            try:
                ob = kc.get(f"/markets/{tk}/orderbook", {"depth": 10})
                book = ob.get("orderbook_fp") or ob.get("orderbook") or {}
                f.write(json.dumps({
                    "t": "ob", "ts": now_iso, "tk": tk,
                    "yes": book.get("yes_dollars"),   # yes bids [price, size], ascending
                    "no": book.get("no_dollars"),     # no bids  [price, size], ascending
                }) + "\n")
                n_books += 1
            except Exception as e:
                f.write(json.dumps({"t": "err", "ts": now_iso,
                                    "tk": tk, "err": str(e)[:200]}) + "\n")
    return n_mkt, len(focus), n_books


def main():
    end_hour = float(sys.argv[1]) if len(sys.argv) > 1 else 23.75
    while True:
        loc = datetime.now()
        if loc.hour + loc.minute / 60 >= end_hour:
            print(f"[{loc:%H:%M}] end hour reached, exiting", flush=True)
            return
        start = time.time()
        out_path = OUT_DIR / f"snapshots_{loc:%Y%m%d}.jsonl"
        try:
            n_mkt, n_focus, n_books = cycle(out_path)
            print(f"[{loc:%H:%M:%S}] {n_mkt} mkts, {n_focus} focus, "
                  f"{n_books} books in {time.time()-start:.0f}s", flush=True)
        except Exception as e:
            print(f"[{loc:%H:%M:%S}] cycle failed: {e}", flush=True)
        time.sleep(max(0, CYCLE_SECS - (time.time() - start)))


if __name__ == "__main__":
    main()
