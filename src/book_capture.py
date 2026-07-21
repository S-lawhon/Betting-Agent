"""
src/book_capture.py
───────────────────
High-cadence Kalshi order-book capture daemon.

WHY THIS EXISTS
    Three separate workstreams are gated on sub-5-minute book data and
    nothing was scheduled to collect it:

      • R-EV-MAP Build 3   — 2 weeks of quote capture measuring fill rates at
                             ±1¢ around a reference mid, post-fill adverse
                             selection, and effective spread capture
                             (`05_build_roadmap.md`, gate `build3-gate`).
      • MLB props Phase 4  — the correlation overlay; measured lags are real
                             but underpowered at 5-min sampling
                             (`mlb_props_research/PHASE2_REPORT.md`).
      • P-016 repricing lag— sizing the repricing-lag edge
                             (`registry.yaml`, open question `websocket-capture`).

    Every night this does not run is calibration data lost forever — the same
    standing debt the settled-market archiver has (90-day API history horizon).

WHY REST POLLING AND NOT WEBSOCKET
    The registry asks for "1-minute or websocket" capture.  Kalshi's websocket
    (wss://.../trade-api/ws/v2) requires an AUTHENTICATED production API key.
    This project holds demo credentials only (`.env`, KALSHI_ENVIRONMENT=demo),
    and the demo environment does not carry real production book data.  So the
    1-minute REST leg is the available option, not a shortcut.  If production
    API credentials are ever provisioned, the websocket leg is the upgrade
    path — the on-disk schema below is deliberately websocket-shaped
    (per-snapshot rows keyed by ticker+epoch) so a future ws feed can write the
    same records without a migration.

CAPACITY / RATE LIMITS
    The documented pain threshold is ~7 req/s (the existing client 429s above
    it).  This daemon runs a token bucket well under that and, crucially,
    DERIVES the market cap from the rate budget rather than trusting a
    hand-set constant:

        max_markets = floor(rate * cadence * safety / requests_per_market)

    If discovery returns more markets than the budget allows, the daemon
    TRUNCATES DELIBERATELY (highest 24h volume first) and logs the drop at
    WARNING with the exact count.  It never silently samples — a silent cap
    reads downstream as "we covered everything" when we did not.

STORAGE
    Append-only JSONL, one file per UTC day, gzipped on rotation.  Parquet was
    rejected: the established `weather_depth.py` pattern rewrites the whole
    file each run, which is O(n²) and fine at 2 snapshots/day but fatal at
    1440.  Budget ~40-60 MB/day raw, ~5-8 MB/day gzipped.

Read-only against Kalshi public endpoints.  This module CANNOT place orders.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.kalshi_public import KalshiPublic, fnum

logger = logging.getLogger("book_capture")

SCHEMA_VERSION = 1

# Requests issued per market per cycle: 1 orderbook + 1 trades page.
REQUESTS_PER_MARKET = 2

# Fraction of the theoretical rate budget we actually spend.  Leaves headroom
# for discovery calls, retries, and the other two services sharing the host's
# egress.
DEFAULT_SAFETY = 0.6


# ══════════════════════════════════════════════════════════════════════
# Rate budget
# ══════════════════════════════════════════════════════════════════════

class RateBudget:
    """Token bucket shared across capture threads.

    Separate from KalshiPublic's own min_interval throttle: that one paces
    a single client, this one bounds the daemon's TOTAL request rate so the
    cap arithmetic below is honest.
    """

    def __init__(self, rate_per_s: float, burst: Optional[float] = None):
        if rate_per_s <= 0:
            raise ValueError("rate_per_s must be positive")
        self.rate = float(rate_per_s)
        self.burst = float(burst if burst is not None else max(1.0, rate_per_s))
        self._tokens = self.burst
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: float = 1.0) -> float:
        """Block until `n` tokens are available.  Returns seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.burst, self._tokens + (now - self._last) * self.rate
                )
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return waited
                deficit = n - self._tokens
                sleep_for = deficit / self.rate
            time.sleep(sleep_for)
            waited += sleep_for

    # ── adaptive control ─────────────────────────────────────────────
    #
    # The host's API budget is SHARED with two trading services, and the main
    # engine already 429s heavily at peak (829/24h, ~134/hr at 03:00 UTC,
    # measured 2026-07-20).  Capture is the lowest-priority consumer on the
    # box: when the API pushes back, this must yield FIRST and recover slowly,
    # so it can never be the reason a trading pod misses a quote.

    def penalize(self, floor: float = 0.5, factor: float = 0.5) -> float:
        """Halve the rate on a 429.  Returns the new rate."""
        with self._lock:
            self.rate = max(floor, self.rate * factor)
            self.burst = max(1.0, self.rate)
            self._tokens = 0.0          # spend the bucket, back off now
            return self.rate

    def recover(self, ceiling: float, step: float = 1.05) -> float:
        """Drift back toward the configured rate after a clean period."""
        with self._lock:
            self.rate = min(ceiling, self.rate * step)
            self.burst = max(1.0, self.rate)
            return self.rate


def max_markets_for_budget(
    rate_per_s: float,
    cadence_s: float,
    requests_per_market: int = REQUESTS_PER_MARKET,
    safety: float = DEFAULT_SAFETY,
) -> int:
    """How many markets fit in one cadence window at this request rate.

    Exposed (and tested) separately because the whole no-silent-caps property
    depends on this number being right.
    """
    usable = rate_per_s * cadence_s * safety
    return max(1, int(math.floor(usable / max(1, requests_per_market))))


# ══════════════════════════════════════════════════════════════════════
# Output writer
# ══════════════════════════════════════════════════════════════════════

class RotatingJsonlWriter:
    """Append-only JSONL, one file per UTC day, gzip the day when it rolls."""

    def __init__(self, out_dir: Path, prefix: str = "book_capture",
                 compress_on_rotate: bool = True):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.compress_on_rotate = compress_on_rotate
        self._day: Optional[str] = None
        self._fh = None
        self._lock = threading.Lock()

    def _path_for(self, day: str) -> Path:
        return self.out_dir / f"{self.prefix}_{day}.jsonl"

    def _roll(self, day: str) -> None:
        if self._fh is not None:
            self._fh.close()
            old = self._path_for(self._day) if self._day else None
            if (self.compress_on_rotate and old and old.exists()
                    and not old.with_suffix(".jsonl.gz").exists()):
                try:
                    with open(old, "rb") as src, gzip.open(
                        old.with_suffix(".jsonl.gz"), "wb"
                    ) as dst:
                        shutil.copyfileobj(src, dst)
                    old.unlink()
                    logger.info("compressed %s", old.name)
                except OSError as exc:
                    # Never let housekeeping kill the capture.
                    logger.warning("compress failed for %s: %s", old, exc)
        self._day = day
        self._fh = open(self._path_for(day), "a", buffering=1)

    def write(self, rec: Dict[str, Any]) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            if day != self._day:
                self._roll(day)
            self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


# ══════════════════════════════════════════════════════════════════════
# Capture
# ══════════════════════════════════════════════════════════════════════

@dataclass
class CaptureConfig:
    series: List[str] = field(default_factory=lambda: ["KXMLBGAME"])
    cadence_s: float = 60.0
    # 5.0 req/s => 90-market cap at 60s cadence, which covers a full MLB slate
    # (74 markets observed 2026-07-20) with headroom. Still ~30% below the
    # ~7 req/s level where the API starts 429ing, and the two trading services
    # sharing this host are both low-rate (P-016 polls on a 12s cycle).
    rate_per_s: float = 5.0
    safety: float = DEFAULT_SAFETY
    depth: int = 10
    discover_every_s: float = 900.0
    capture_trades: bool = True
    out_dir: Path = Path("data/book_capture")
    kill_file: Path = Path("data/KILL_CAPTURE")

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "CaptureConfig":
        block = (cfg or {}).get("book_capture", {}) or {}
        base = cls()
        return cls(
            series=block.get("series", base.series),
            cadence_s=float(block.get("cadence_s", base.cadence_s)),
            rate_per_s=float(block.get("rate_per_s", base.rate_per_s)),
            safety=float(block.get("safety", base.safety)),
            depth=int(block.get("depth", base.depth)),
            discover_every_s=float(
                block.get("discover_every_s", base.discover_every_s)
            ),
            capture_trades=bool(block.get("capture_trades", base.capture_trades)),
            out_dir=Path(block.get("out_dir", base.out_dir)),
            kill_file=Path(block.get("kill_file", base.kill_file)),
        )


def _depth_summary(levels: Iterable, side: str) -> Dict[str, Any]:
    """Aggregate one side of the book.  Levels are [price, qty] in DOLLARS
    (orderbook_fp), sub-penny ticks possible — see CLAUDE.md."""
    n, notional, contracts, best = 0, 0.0, 0.0, None
    top: List[List[float]] = []
    for lvl in levels or []:
        try:
            px, qty = fnum(lvl[0]), fnum(lvl[1]) or 0.0
        except (TypeError, IndexError):
            continue
        if px is None:
            continue
        if px > 1.5:                     # cents-denominated fallback
            px = px / 100.0
        n += 1
        contracts += qty
        notional += px * qty
        if best is None or px > best:
            best = px
        top.append([px, qty])
    top.sort(key=lambda r: -r[0])
    return {
        f"{side}_levels": n,
        f"{side}_best": best,
        f"{side}_contracts": contracts,
        f"{side}_notional": round(notional, 4),
        f"{side}_top": top[:5],
    }


class BookCapture:
    """Polls a target market set on a fixed cadence and appends snapshots."""

    def __init__(self, cfg: CaptureConfig,
                 client: Optional[KalshiPublic] = None,
                 writer: Optional[RotatingJsonlWriter] = None,
                 clock: Callable[[], float] = time.time):
        self.cfg = cfg
        # min_interval 0 — pacing is the RateBudget's job, not the client's.
        self.kalshi = client or KalshiPublic(min_interval=0.0)
        self.writer = writer or RotatingJsonlWriter(cfg.out_dir)
        self.budget = RateBudget(cfg.rate_per_s)
        self.clock = clock
        self.max_markets = max_markets_for_budget(
            cfg.rate_per_s, cfg.cadence_s,
            REQUESTS_PER_MARKET if cfg.capture_trades else 1,
            cfg.safety,
        )
        self._targets: List[Dict[str, Any]] = []
        self._last_discover = 0.0
        self._last_trade_epoch: Dict[str, float] = {}
        self._cycles = 0
        self._snapshots = 0
        self._dropped_last_discovery = 0
        self._configured_rate = cfg.rate_per_s
        self._429s = 0
        self._429s_this_cycle = 0

    def _on_status(self, code: int) -> None:
        """Yield immediately when the API pushes back."""
        if code == 429:
            self._429s += 1
            self._429s_this_cycle += 1
            new_rate = self.budget.penalize()
            logger.warning(
                "book_capture: HTTP 429 — throttling down to %.2f req/s "
                "(%d total). Capture yields to the trading services.",
                new_rate, self._429s,
            )

    # ── discovery ────────────────────────────────────────────────────

    def discover(self) -> List[Dict[str, Any]]:
        """Refresh the target market set, deliberately truncated to budget."""
        found: List[Dict[str, Any]] = []
        for series in self.cfg.series:
            self.budget.acquire()
            try:
                markets = self.kalshi.open_markets(series)
            except Exception as exc:               # never die on discovery
                logger.warning("discovery failed for %s: %s", series, exc)
                continue
            for m in markets or []:
                tk = m.get("ticker")
                if not tk:
                    continue
                found.append({
                    "ticker": tk,
                    "series": series,
                    "event_ticker": m.get("event_ticker"),
                    "volume_24h": fnum(m.get("volume_24h_fp"))
                    or fnum(m.get("volume_24h")) or 0.0,
                })

        # Rank by 24h volume: if we must drop markets, drop the ones with the
        # least to say about fill rates and adverse selection.
        found.sort(key=lambda r: -(r["volume_24h"] or 0.0))
        dropped = max(0, len(found) - self.max_markets)
        if dropped:
            # NO SILENT CAPS — an unlogged truncation reads downstream as
            # full coverage.  Say exactly what was dropped.
            logger.warning(
                "book_capture: %d markets discovered, budget fits %d at "
                "%.1f req/s / %.0fs cadence — DROPPING %d lowest-volume "
                "markets. Raise rate_per_s or cadence_s to widen coverage.",
                len(found), self.max_markets, self.cfg.rate_per_s,
                self.cfg.cadence_s, dropped,
            )
        self._dropped_last_discovery = dropped
        self._targets = found[: self.max_markets]
        self._last_discover = self.clock()

        self.writer.write({
            "type": "DISCOVERY",
            "schema_version": SCHEMA_VERSION,
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "series": self.cfg.series,
            "n_discovered": len(found),
            "n_captured": len(self._targets),
            "n_dropped": dropped,
            "max_markets": self.max_markets,
            "rate_per_s": self.cfg.rate_per_s,
            "cadence_s": self.cfg.cadence_s,
        })
        logger.info("book_capture: tracking %d markets (%d discovered)",
                    len(self._targets), len(found))
        return self._targets

    # ── one market ───────────────────────────────────────────────────

    def snapshot_market(self, target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ticker = target["ticker"]
        self.budget.acquire()
        raw = self.kalshi.get(f"/markets/{ticker}/orderbook",
                              {"depth": self.cfg.depth},
                              on_status=self._on_status)
        if not raw:
            return None
        book = raw.get("orderbook_fp") or raw.get("orderbook") or {}
        yes_levels = book.get("yes_dollars") or book.get("yes") or []
        no_levels = book.get("no_dollars") or book.get("no") or []

        rec: Dict[str, Any] = {
            "type": "BOOK",
            "schema_version": SCHEMA_VERSION,
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "epoch": self.clock(),
            "ticker": ticker,
            "series": target.get("series"),
            "event_ticker": target.get("event_ticker"),
        }
        rec.update(_depth_summary(yes_levels, "yes"))
        rec.update(_depth_summary(no_levels, "no"))

        # Derived top-of-book. Best YES ask = 1 - best NO bid (CLAUDE.md).
        yes_bid = rec.get("yes_best")
        no_bid = rec.get("no_best")
        yes_ask = (1.0 - no_bid) if no_bid is not None else None
        rec["yes_bid"] = yes_bid
        rec["yes_ask"] = yes_ask
        rec["mid"] = ((yes_bid + yes_ask) / 2.0
                      if yes_bid is not None and yes_ask is not None else None)
        rec["spread"] = (yes_ask - yes_bid
                         if yes_bid is not None and yes_ask is not None else None)

        if self.cfg.capture_trades:
            since = self._last_trade_epoch.get(ticker)
            if since is None:
                # First sight of this ticker: look back one cadence only, so
                # we don't pull a huge backlog and blow the rate budget.
                since = self.clock() - self.cfg.cadence_s
            self.budget.acquire()
            try:
                trades = self.kalshi.trades_since(ticker, since, max_pages=2)
            except Exception as exc:
                logger.debug("trades failed for %s: %s", ticker, exc)
                trades = []
            if trades:
                self._last_trade_epoch[ticker] = trades[-1]["epoch"]
                rec["trades"] = trades
                rec["n_trades"] = len(trades)
            else:
                self._last_trade_epoch[ticker] = self.clock()
                rec["n_trades"] = 0

        return rec

    # ── cycle ────────────────────────────────────────────────────────

    def cycle(self) -> int:
        if self.cfg.kill_file.exists():
            logger.warning("book_capture: kill file present (%s) — idling",
                           self.cfg.kill_file)
            return 0
        if (not self._targets
                or self.clock() - self._last_discover >= self.cfg.discover_every_s):
            self.discover()

        self._429s_this_cycle = 0
        written = 0
        for target in list(self._targets):
            try:
                rec = self.snapshot_market(target)
            except Exception:
                logger.exception("book_capture: snapshot failed for %s",
                                 target.get("ticker"))
                continue
            if rec is not None:
                self.writer.write(rec)
                written += 1
        self._cycles += 1
        self._snapshots += written

        # Clean cycle: drift back toward the configured rate. Slow recovery
        # (5%/cycle) is deliberate — fast recovery would oscillate against
        # the main engine's own retry storms.
        if self._429s_this_cycle == 0 and self.budget.rate < self._configured_rate:
            self.budget.recover(self._configured_rate)
        return written

    def run(self, stop: Optional[Callable[[], bool]] = None,
            max_cycles: Optional[int] = None) -> None:
        stop = stop or (lambda: False)
        logger.info(
            "book_capture starting: series=%s cadence=%.0fs rate=%.1f/s "
            "cap=%d markets out=%s",
            self.cfg.series, self.cfg.cadence_s, self.cfg.rate_per_s,
            self.max_markets, self.cfg.out_dir,
        )
        n = 0
        while not stop():
            started = time.monotonic()
            written = self.cycle()
            n += 1
            if n % 10 == 0 or written == 0:
                logger.info("book_capture: cycle %d wrote %d snapshots "
                            "(%d total)", n, written, self._snapshots)
            if max_cycles is not None and n >= max_cycles:
                break
            elapsed = time.monotonic() - started
            if elapsed > self.cfg.cadence_s:
                # Overrun means the cap arithmetic is wrong for live
                # conditions — surface it rather than silently drifting to a
                # slower effective cadence.
                logger.warning(
                    "book_capture: cycle took %.1fs > %.0fs cadence — "
                    "effective sampling is slower than configured",
                    elapsed, self.cfg.cadence_s,
                )
            time.sleep(max(0.0, self.cfg.cadence_s - elapsed))
        self.writer.close()
        logger.info("book_capture stopped after %d cycles, %d snapshots",
                    self._cycles, self._snapshots)
