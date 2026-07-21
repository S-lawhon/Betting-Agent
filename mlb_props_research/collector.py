"""Live MLB order book snapshot collector (Phase 1, Q4 liquidity ramp + Q1 data).

Every CYCLE_SECS:
  1. Pulls market objects for all 12 MLB series, logs compact records for
     markets expiring within HORIZON_H hours (top-of-book, volume, OI).
  2. Fetches full /orderbook depth for markets expiring within FOCUS_H hours
     (today's slate), capped per cycle, prioritized by series.

Output: data/live/snapshots_YYYYMMDD.jsonl — one record per market per cycle.
Run:    python collector.py [end_hour_local_24h] [--rate REQ_PER_S]

RATE LIMITING — READ BEFORE RAISING --rate
    Kalshi throttles on CONCURRENT CONNECTIONS and a SHARED PER-IP rate, not
    per-process.  On the droplet this collector shares one IP with
    betting-pod-shop (2 req/s), betting-live-maker and betting-book-capture
    (2.5 req/s).  The documented pain threshold for the host is ~7 req/s.

    A cycle issues ~1 page per series (12) plus MAX_BOOKS_PER_CYCLE orderbooks
    — about 570 requests.  At the client default of 4.5 req/s that is a 123s
    BURST inside a 300s cycle: average 1.9 req/s but peak 4.5, and the peak is
    what trips the shared limiter.  Measured 2026-07-21: betting-book-capture
    logged zero 429s all day until this collector started at 14:00 UTC, then
    took 7 and self-throttled from 2.5 to ~1.25 req/s.  The collector's own
    429s were invisible because kalshi_client retries them internally.

    The default below therefore SPREADS the same ~570 requests across the full
    cycle instead of bursting: same total throughput, peak cut from 4.5 to
    2.0 req/s.  No data is lost — only the burst is.  Raise this only after
    confirming the host's 429 count stays at zero.
"""
import argparse
import json
import sys
import threading
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

# Share of the host's ~7 req/s budget this collector may spend. See the module
# docstring: sized to fill the cycle rather than burst through it.
DEFAULT_RATE = 2.0
RATE_FLOOR = 0.5     # never back off below this, or a cycle never completes

OUT_DIR = Path(__file__).resolve().parent / "data" / "live"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Touch this file to make the collector idle without unloading the systemd
# unit — the kill switch a systemctl stop cannot give us mid-slate.
KILL_FILE = OUT_DIR.parent / "KILL_COLLECTOR"


class Throttle:
    """Adaptive wrapper over kalshi_client's global request spacing.

    kalshi_client.get() retries 429s internally and returns success, so
    rate-limit pressure is invisible to callers.  This registers a status hook
    to make it visible and, like src/book_capture.py, YIELDS on 429: the
    collector is the lowest-priority consumer on the box and must never be the
    reason a trading pod misses a quote.
    """

    def __init__(self, rate, floor=RATE_FLOOR):
        self.configured = float(rate)
        self.rate = float(rate)
        self.floor = float(floor)
        self.n_429 = 0
        self.n_429_cycle = 0
        self._lock = threading.Lock()
        self._apply()
        kc.set_on_status(self.on_status)

    def _apply(self):
        kc.set_min_interval(1.0 / self.rate)

    def on_status(self, code):
        if code != 429:
            return
        with self._lock:
            self.n_429 += 1
            self.n_429_cycle += 1
            self.rate = max(self.floor, self.rate * 0.5)
            self._apply()
        print(f"    HTTP 429 — throttling down to {self.rate:.2f} req/s "
              f"({self.n_429} total); collector yields to trading services.",
              flush=True)

    def end_cycle(self):
        """Drift back toward the configured rate after a clean cycle."""
        with self._lock:
            n = self.n_429_cycle
            self.n_429_cycle = 0
            if n == 0 and self.rate < self.configured:
                self.rate = min(self.configured, self.rate * 1.25)
                self._apply()
        return n


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
        # NO SILENT CAPS. focus routinely exceeds MAX_BOOKS_PER_CYCLE (1362 vs
        # 550 observed 2026-07-21), and an unrecorded truncation reads
        # downstream as full slate coverage. Record the drop in-band so any
        # analysis can see exactly which cycles were partial.
        dropped = max(0, len(focus) - MAX_BOOKS_PER_CYCLE)
        if dropped:
            f.write(json.dumps({
                "t": "cap", "ts": now_iso,
                "focus": len(focus), "captured": MAX_BOOKS_PER_CYCLE,
                "dropped": dropped,
            }) + "\n")
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
    return n_mkt, len(focus), n_books, dropped


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("end_hour", nargs="?", type=float, default=23.75,
                    help="local hour (24h decimal) to stop at; default 23.75")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE,
                    help=f"max requests/sec (default {DEFAULT_RATE}); shared "
                         "per-IP budget — see module docstring before raising")
    args = ap.parse_args()

    throttle = Throttle(args.rate)
    print(f"collector starting: end_hour={args.end_hour} rate={args.rate} req/s "
          f"cycle={CYCLE_SECS}s max_books={MAX_BOOKS_PER_CYCLE} out={OUT_DIR}",
          flush=True)

    while True:
        loc = datetime.now()
        if loc.hour + loc.minute / 60 >= args.end_hour:
            print(f"[{loc:%H:%M}] end hour reached, exiting "
                  f"({throttle.n_429} HTTP 429s this run)", flush=True)
            return
        if KILL_FILE.exists():
            print(f"[{loc:%H:%M:%S}] kill file present ({KILL_FILE}) — idling",
                  flush=True)
            time.sleep(CYCLE_SECS)
            continue
        start = time.time()
        out_path = OUT_DIR / f"snapshots_{loc:%Y%m%d}.jsonl"
        try:
            n_mkt, n_focus, n_books, dropped = cycle(out_path)
            elapsed = time.time() - start
            msg = (f"[{loc:%H:%M:%S}] {n_mkt} mkts, {n_focus} focus, "
                   f"{n_books} books in {elapsed:.0f}s")
            if dropped:
                msg += f" (CAPPED: dropped {dropped} focus markets)"
            print(msg, flush=True)
            if elapsed > CYCLE_SECS:
                # Overrun means sampling is slower than the nominal 5 min.
                # Surface it rather than silently drifting to a longer cadence.
                print(f"    WARNING: cycle took {elapsed:.0f}s > {CYCLE_SECS}s "
                      f"— effective sampling is slower than configured",
                      flush=True)
        except Exception as e:
            print(f"[{loc:%H:%M:%S}] cycle failed: {e}", flush=True)
        throttle.end_cycle()
        time.sleep(max(0, CYCLE_SECS - (time.time() - start)))


if __name__ == "__main__":
    main()
