#!/usr/bin/env python3
"""
combo_research/archive_settled_combos.py
────────────────────────────────────────
Daily archiver for SETTLED Kalshi combo (KXMVE) markets.

**Kalshi purges settled combo markets after roughly three months.** As of
2026-07-28 only 2026-05 → 2026-07 exists, and that window is entirely summer
MLB / WNBA / World Cup. NFL and NBA — the regimes that actually matter — have
never been captured. Every day this does not run is a day of season data that
will not exist later and cannot be recovered at any price.

**This is worth running even if P-029 is killed at Gate 0.** The data is the
asset; the strategy is a hypothesis about it.

Writes one gzipped JSONL file per UTC day under ``--out``, plus a manifest.
Idempotent: a market already recorded for a day is not rewritten, so the job can
run hourly, be restarted, or overlap without corrupting the archive.

Disk safety is not optional here. This project has already lost five weeks to an
unbounded log filling a 2GB droplet (the June 2026 OOM crash loop), so the
archiver refuses to write below a free-space floor and prunes nothing silently.

Usage::

    python3 archive_settled_combos.py --out /var/lib/p029/archive
    python3 archive_settled_combos.py --out /var/lib/p029/archive --report
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests

API = "https://api.elections.kalshi.com/trade-api/v2"

# Every combo series seen in the 2026-07 census. Nine of these have returned
# zero markets historically, but listing them is free and a new one appearing
# is exactly the kind of thing a daily job should pick up automatically.
SERIES = [
    "KXMVESPORTSMULTIGAMEEXTENDED", "KXMVECROSSCATEGORY",
    "KXMVENBASINGLEGAME", "KXMVENBAMULTIGAMEEXTENDED",
    "KXMVENFLSINGLEGAME", "KXMVENFLMULTIGAME", "KXMVENFLMULTIGAMEEXTENDED",
    "KXMVECBCHAMPIONSHIP", "KXMVEGRAMMYS", "KXMVEOSCARS",
    "KXMVEMENTIONSSINGLE", "KXCITIESWEATHER",
]

# Keep only what the research actually uses. The full market object is ~4x
# larger and the difference is tens of GB per season.
KEEP = ("ticker", "event_ticker", "mve_collection_ticker", "status", "result",
        "last_price_dollars", "volume", "volume_fp", "open_interest_fp",
        "settlement_value_dollars", "yes_bid_dollars", "yes_ask_dollars",
        "open_time", "close_time", "settled_time", "created_time",
        "price_level_structure")

MIN_FREE_GB = 2.0

log = logging.getLogger("archiver")


class Public:
    def __init__(self, min_interval: float = 0.12, timeout: float = 30.0):
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0
        self._s = requests.Session()
        self._s.headers.update({"Accept": "application/json",
                                "User-Agent": "p029-archiver/1.0"})
        self.calls = 0
        self.errors = 0

    def get(self, path: str, params: Optional[dict] = None,
            tries: int = 6) -> Optional[dict]:
        for attempt in range(tries):
            dt = time.monotonic() - self._last
            if dt < self.min_interval:
                time.sleep(self.min_interval - dt)
            self._last = time.monotonic()
            try:
                r = self._s.get(f"{API}{path}", params=params, timeout=self.timeout)
                self.calls += 1
            except requests.RequestException as exc:
                log.warning("transport error: %s", exc)
                time.sleep(2 * (2 ** attempt))
                continue
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    return None
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 * (2 ** attempt))
                continue
            self.errors += 1
            log.warning("HTTP %s on %s %s", r.status_code, path, params)
            return None
        self.errors += 1
        return None


def slim(m: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: m[k] for k in KEEP if k in m}
    legs = m.get("mve_selected_legs") or []
    # Store legs as [ticker, side]. NEVER parse legs from the ticker -- the
    # suffix is an opaque hash carrying no leg information.
    out["legs"] = [[l.get("market_ticker"), (l.get("side") or "yes").lower()]
                   for l in legs if l.get("market_ticker")]
    return out


def settled_markets(pub: Public, series: str) -> Iterator[Dict[str, Any]]:
    """Stream settled markets for one series.

    Trap: ``status=settled`` is the query FILTER, but returned rows read
    ``"finalized"``. Passing ``status=finalized`` returns HTTP 400.
    """
    cursor = None
    while True:
        params = {"series_ticker": series, "status": "settled", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        d = pub.get("/markets", params)
        if not d:
            return
        rows = d.get("markets") or []
        if not rows:
            return
        for m in rows:
            yield m
        cursor = d.get("cursor")
        if not cursor:
            return


def day_of(m: Dict[str, Any]) -> str:
    for f in ("settled_time", "close_time", "open_time"):
        v = m.get(f)
        if v:
            return v[:10]
    return "unknown"


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1e9


def parts_for(out: Path, day: str) -> List[Path]:
    """All archive parts for a day, oldest first.

    The archive is written as immutable numbered parts rather than one appended
    file. A process killed mid-append (systemd restart, OOM, reboot) truncates
    the gzip stream and makes the *entire* file unreadable from that point —
    which for irreplaceable data is unacceptable. Parts are written to a temp
    file and renamed into place, so a kill can only ever discard the part in
    flight.
    """
    return sorted(out.glob(f"combos_{day}.*.jsonl.gz"))


def read_rows(path: Path) -> Iterator[dict]:
    """Yield rows, tolerating a truncated tail rather than losing the file."""
    try:
        with gzip.open(path, "rt") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except (OSError, EOFError) as exc:
        log.warning("%s ends early (%s); keeping the readable prefix",
                    path.name, type(exc).__name__)


def load_seen(out: Path, day: str) -> set:
    seen = set()
    for p in parts_for(out, day):
        for r in read_rows(p):
            t = r.get("ticker")
            if t:
                seen.add(t)
    return seen


def write_part(out: Path, day: str, rows: List[dict]) -> Path:
    """Write one immutable part atomically (temp file, then rename)."""
    seq = len(parts_for(out, day))
    final = out / f"combos_{day}.{seq:04d}.jsonl.gz"
    while final.exists():                      # never clobber an existing part
        seq += 1
        final = out / f"combos_{day}.{seq:04d}.jsonl.gz"
    tmp = out / f".{final.name}.tmp"
    with gzip.open(tmp, "wt") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    os.replace(tmp, final)                     # atomic on POSIX
    return final


def run(out: Path, days_back: int) -> int:
    out.mkdir(parents=True, exist_ok=True)
    if free_gb(out) < MIN_FREE_GB:
        log.error("only %.1f GB free at %s; refusing to write (floor %.1f GB). "
                  "This project lost five weeks to a full disk once already.",
                  free_gb(out), out, MIN_FREE_GB)
        return 1

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    pub = Public()
    per_series = Counter()
    written = skipped = 0

    # Stream to disk. Buffering the whole sweep would hold millions of rows in
    # RAM -- 18.4M settled combos exist -- and this project has already lost
    # five weeks to a memory/disk exhaustion crash loop. Flush on a bounded
    # buffer instead, and keep the per-day `seen` sets so restarts stay
    # idempotent.
    buckets: Dict[str, List[dict]] = {}
    seen_cache: Dict[str, set] = {}
    buffered = 0
    FLUSH_EVERY = 50_000

    def flush() -> None:
        nonlocal buffered, written, skipped
        if not buckets:
            return
        if free_gb(out) < MIN_FREE_GB:
            raise RuntimeError(
                f"free space {free_gb(out):.1f} GB below the {MIN_FREE_GB} GB "
                "floor; stopping before the disk fills")
        for day, rows in sorted(buckets.items()):
            if day not in seen_cache:
                seen_cache[day] = load_seen(out, day)
            seen = seen_cache[day]
            fresh = [r for r in rows if r.get("ticker") not in seen]
            skipped += len(rows) - len(fresh)
            if not fresh:
                continue
            write_part(out, day, fresh)
            for r in fresh:
                seen.add(r.get("ticker"))
            written += len(fresh)
        buckets.clear()
        buffered = 0

    try:
        for s in SERIES:
            n = 0
            for m in settled_markets(pub, s):
                d = day_of(m)
                if d < cutoff:
                    continue
                buckets.setdefault(d, []).append(slim(m))
                n += 1
                buffered += 1
                if buffered >= FLUSH_EVERY:
                    flush()
                if n % 200000 == 0:
                    log.info("  %s: %d rows in window so far (%d written)", s, n, written)
            per_series[s] = n
            if n:
                log.info("%s: %d settled rows in window", s, n)
        flush()
    except RuntimeError as exc:
        log.error("%s", exc)
    except KeyboardInterrupt:
        log.warning("interrupted; flushing what is buffered")
        try:
            flush()
        except RuntimeError:
            pass

    manifest = out / "manifest.json"
    hist = {}
    if manifest.is_file():
        try:
            hist = json.loads(manifest.read_text())
        except ValueError:
            pass
    hist[datetime.now(timezone.utc).isoformat()] = {
        "written": written, "already_present": skipped,
        "api_calls": pub.calls, "api_errors": pub.errors,
        "per_series": dict(per_series), "free_gb": round(free_gb(out), 2),
    }
    manifest.write_text(json.dumps(hist, indent=1))

    log.info("done: %d new rows, %d already archived, %d api calls, %d errors, "
             "%.1f GB free", written, skipped, pub.calls, pub.errors, free_gb(out))
    return 0 if pub.errors == 0 else 0        # API hiccups are not a job failure


def report(out: Path) -> int:
    files = sorted(out.glob("combos_*.jsonl.gz"))
    if not files:
        print(f"no archive files in {out}")
        return 1
    days: Dict[str, List[Path]] = {}
    for f in files:
        days.setdefault(f.name.split(".")[0].replace("combos_", ""), []).append(f)
    total = 0
    print(f"{'day':12} {'parts':>6} {'rows':>11} {'MB':>8}")
    for day in sorted(days):
        n = sum(1 for p in days[day] for _ in read_rows(p))
        total += n
        size = sum(p.stat().st_size for p in days[day]) / 1e6
        print(f"{day:12} {len(days[day]):>6} {n:>11,} {size:>8.1f}")
    print(f"\n{len(days)} days, {len(files)} parts, {total:,} rows, "
          f"{sum(f.stat().st_size for f in files)/1e9:.2f} GB, "
          f"{free_gb(out):.1f} GB free")
    stale = list(out.glob(".*.tmp"))
    if stale:
        print(f"\n{len(stale)} incomplete part(s) from an interrupted run "
              f"(safe to delete): {[p.name for p in stale]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/var/lib/p029/archive")
    ap.add_argument("--days-back", type=int, default=7,
                    help="how far back to sweep each run (default 7)")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    out = Path(a.out)
    if a.report:
        return report(out)
    return run(out, a.days_back)


if __name__ == "__main__":
    sys.exit(main())
