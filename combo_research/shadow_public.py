#!/usr/bin/env python3
"""P-029 Phase 0 — PUBLIC SHADOW LOGGER.

Measures the one number the research report could not: the COMPETITION MARGIN.

Every Kalshi combo market is instantiated on demand when someone builds a ticket,
so a newly-created KXMVE market IS an RFQ, observable without credentials. This
logger watches the two live rolling collections for new target-zone combos,
snapshots the underlying leg books at that instant, and later records whether the
combo traded and at what price.

    winning_price - our_model_price  =  how much room a quoter had

That distribution is the direct input to Gate 1 (fill rate). It requires no API
key, no capital and no order placement, and it can start collecting today.

DESIGN RULE: store RAW leg marks, never just a derived price. Phase 0's data must
survive re-pricing under the Gate 3 copula without being re-collected — Kalshi
purges settled combo markets after ~3 months, so anything not captured now is
gone.

Usage:
    python3 shadow_public.py --db shadow.sqlite            # run both loops
    python3 shadow_public.py --db shadow.sqlite --report   # print current stats

Runs indefinitely; safe to restart (idempotent on market_ticker).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import signal
import sqlite3
import sys
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple

import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
UA = "p029-shadow/1.0"

# --- target zone, from REPORT_Combo_MM_2026-07-28.md §4.6 -------------------
MIN_LEGS, MAX_LEGS = 2, 4
MIN_PX, MAX_PX = 0.10, 0.35          # on our MODEL price, not on any quote
HARD_MAX_PX = 0.60                    # >60c measured -2.46 c/ct: never quote
# Log a wider band than we would trade, so the zone boundaries stay testable.
LOG_MIN_PX, LOG_MAX_PX = 0.02, 0.75

MAX_NEW_PER_SWEEP = 400               # bound a sweep; leg lookups dominate cost
DISCOVERY_INTERVAL = 45.0             # s between new-market sweeps
RESOLVE_INTERVAL = 300.0              # s between trade-resolution sweeps
RESOLVE_BATCH = 600                   # rows per resolve sweep; exactly 1 call each
RESOLVE_MIN_AGE_H = 40.0              # check a combo ONCE, after its trading life
LEG_CACHE_TTL = 20.0                  # s; leg books move, but not every call
LEG_CACHE_MAX = 10_000                # hard bound; stale entries are not useful
ERROR_HISTORY_MAX = 1_000             # diagnostics, never an unbounded ledger
SQLITE_MEMBERSHIP_BATCH = 500         # stay below conservative variable limits
# Warn on the IN-ZONE due backlog only. Out-of-zone rows are deliberately
# starved — they get whatever capacity is left after the gate's own sample is
# served — so a warning on the total backlog would be permanently on, and a
# permanently-on warning is an ignored one. In-zone inflow is ~55k/day against
# ~108k/day of resolve capacity, so a sustained in-zone backlog this size means
# resolution is genuinely losing to discovery.
BACKLOG_WARN = 25_000

# Resolution priority.
#
# Measured 2026-07-31: discovery outran resolution ~3:1, the unresolved backlog
# reached 546,370, and every traded row in the Gate 0 sample came from a single
# 6-hour window — the first six hours of the tape. Nothing errored; the log
# printed "resolved 400 combos" every cycle while the resolved frontier stayed
# pinned near the start. Two causes, both fixed here:
#
#   1. Rows were re-polled every sweep while young and untraded, so a scarce API
#      budget went on repeat checks. /markets/trades returns the FULL history —
#      including for markets that have since settled, confirmed by >48h-old rows
#      still resolving traded=1 — so one check after the combo's life is over
#      captures the first trade exactly as well, at one call per row instead of
#      many.
#   2. Strict oldest-first spent ~3/4 of those calls on rows the gate never
#      reads. In-zone rows are prioritised now.
#
# The band below is a deliberate SUPERSET of the gate zone on BOTH pricing
# bases. The stored in_zone column mixes an independence-based rule (rows logged
# before the copula landed) with a copula-based one, and the gate read re-derives
# membership uniformly offline (gate0_zone_recompute.py) — so prioritising on the
# narrow stored flag would truncate the very sample the gate recomputes. Copula
# runs ~+3c above independence, hence the widened edges.
PRIORITY_SQL = ("n_legs BETWEEN 2 AND 4"
                " AND COALESCE(copula_price, indep_price, -1) BETWEEN 0.07 AND 0.40")

log = logging.getLogger("shadow")

# The copula lives in src/, which is not on the path when this runs from
# combo_research/ on the droplet. Import it defensively: if it is unavailable
# the logger must keep collecting on the independence basis rather than stop —
# a gap in the tape cannot be backfilled, but a missing copula column can be
# filled offline from the stored raw leg marks.
_COPULA = None
_corr_matrix = None
_strongest_relation = None
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.combo_copula import correlation_matrix as _corr_matrix  # noqa: E402
    from src.combo_copula import ComboCopula, load_rho, relation as _relation  # noqa: E402

    # Fitted ρ when combo_research/fitted_rho.json exists (written by
    # fit_correlation.py); load_rho falls back to the priors per block — an
    # unfitted block keeps its prior rather than silently becoming zero.
    _RHO_PATH = str(Path(__file__).resolve().parent / "fitted_rho.json")
    _COPULA = ComboCopula(rho=load_rho(_RHO_PATH))

    def _strongest_relation(tickers: List[str]) -> str:
        """The most-correlated pair in the combo — the adjustment is driven by
        it, and same-game vs cross-game differ by an order of magnitude."""
        order = {"same_player": 3, "same_game": 2, "same_day": 1, "cross": 0}
        best = "cross"
        for i in range(len(tickers)):
            for j in range(i + 1, len(tickers)):
                r = _relation(tickers[i], tickers[j])
                if order[r] > order[best]:
                    best = r
        return best
except Exception as _exc:                        # pragma: no cover
    log.warning("combo_copula unavailable (%s) — independence basis only", _exc)

    def _strongest_relation(tickers: List[str]) -> str:  # type: ignore[misc]
        return "unknown"


# --------------------------------------------------------------------------
# throttled public client
# --------------------------------------------------------------------------
class Public:
    """Minimal throttled client for Kalshi public endpoints.

    Mirrors src/kalshi_public.py conventions. Retries 429/5xx with backoff;
    Kalshi returns no retry headers, so backoff is exponential and blind.
    """

    def __init__(self, min_interval: float = 0.12, timeout: float = 20.0):
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0
        self._s = requests.Session()
        self._s.headers.update({"Accept": "application/json", "User-Agent": UA})
        self.calls = 0
        self.errors: Deque[Tuple[str, int]] = deque(maxlen=ERROR_HISTORY_MAX)

    def _throttle(self) -> None:
        dt = time.monotonic() - self._last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last = time.monotonic()

    def get(self, path: str, params: Optional[dict] = None,
            tries: int = 5) -> Optional[dict]:
        for attempt in range(tries):
            self._throttle()
            try:
                r = self._s.get(f"{API}{path}", params=params, timeout=self.timeout)
                self.calls += 1
            except requests.RequestException as exc:
                log.warning("transport error %s %s: %s", path, params, exc)
                time.sleep(1.5 * (2 ** attempt))
                continue
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    log.warning("non-JSON 200 from %s", path)
                    return None
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (2 ** attempt))
                continue
            self.errors.append((path, r.status_code))
            log.warning("HTTP %s %s %s -> %s", r.status_code, path, params,
                        r.text[:200])
            return None
        return None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def fnum(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_at(ts: float) -> str:
    """A wall-clock instant in the SAME format seen_ts is stored in.

    Resolution selects on ``seen_ts <= ?``, which is a string comparison in
    SQLite — it is only correct because every timestamp is written by now_iso()
    in one UTC ISO-8601 format. Do not mix in a differently-formatted or
    non-UTC timestamp here.
    """
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def parse_iso(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def legs_of(m: dict) -> List[Tuple[str, str]]:
    """(market_ticker, side) per leg, from mve_selected_legs.

    NEVER parse legs out of the combo ticker -- the suffix is an opaque hash.
    """
    out = []
    for leg in (m.get("mve_selected_legs") or []):
        t = leg.get("market_ticker")
        s = (leg.get("side") or "yes").lower()
        if t:
            out.append((t, s))
    return out


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS combo (
    market_ticker   TEXT PRIMARY KEY,
    collection      TEXT,
    event_ticker    TEXT,
    created_ts      TEXT,      -- Kalshi's creation stamp for the combo market
    seen_ts         TEXT,      -- when WE first saw it
    n_legs          INTEGER,
    legs_json       TEXT,      -- [[ticker, side], ...]
    leg_marks_json  TEXT,      -- RAW leg books at discovery -- reprice offline
    indep_price     REAL,      -- product of leg marks (biased LOW baseline)
    copula_price    REAL,      -- correlation-adjusted joint -- THE GATE 0 BASIS
    model_price     REAL,      -- = copula when available, else indep
    leg_relation    TEXT,      -- strongest pair: same_player|same_game|same_day|cross
    combo_yes_bid   REAL,
    combo_yes_ask   REAL,
    in_zone         INTEGER,
    -- resolution
    resolved        INTEGER DEFAULT 0,
    traded          INTEGER DEFAULT 0,
    first_trade_ts  TEXT,
    first_trade_px  REAL,
    first_trade_sz  REAL,
    taker_side      TEXT,
    n_trades        INTEGER DEFAULT 0,
    vwap            REAL,
    last_check_ts   TEXT,
    settle_result   TEXT,
    settle_value    REAL
);
CREATE INDEX IF NOT EXISTS ix_unresolved ON combo(resolved, seen_ts);
CREATE INDEX IF NOT EXISTS ix_zone       ON combo(in_zone, traded);

CREATE TABLE IF NOT EXISTS runlog (
    ts TEXT, kind TEXT, detail TEXT
);
"""


def open_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=60.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    con.executescript(SCHEMA)
    con.commit()
    return con


def note(con: sqlite3.Connection, kind: str, detail: str) -> None:
    """Never let bookkeeping take the process down."""
    try:
        con.execute("INSERT INTO runlog VALUES (?,?,?)", (now_iso(), kind, detail))
        con.commit()
    except sqlite3.Error as exc:
        log.warning("could not write runlog (%s): %s", kind, exc)


class SingleInstance:
    """Refuse to start if another logger already owns this DB.

    Two concurrent instances lock each other out of SQLite and silently stop
    collecting -- and a gap in the tape cannot be backfilled, because Kalshi
    purges settled combo markets.
    """

    def __init__(self, db_path: str):
        self.path = db_path + ".lock"
        self.fh = None

    def __enter__(self):
        import os
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                with open(self.path) as f:
                    pid = int((f.read() or "0").strip() or 0)
                os.kill(pid, 0)          # signal 0 = liveness probe
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                log.warning("removing stale lock %s", self.path)
                try:
                    os.unlink(self.path)
                except OSError:
                    pass
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                raise SystemExit(
                    f"another shadow logger (pid {pid}) already owns "
                    f"{self.path}; refusing to start")
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        import os
        try:
            os.unlink(self.path)
        except OSError:
            pass
        return False


# --------------------------------------------------------------------------
# pricing
# --------------------------------------------------------------------------
class LegPricer:
    """Marks the underlying legs off their own Kalshi order books.

    Leg books ARE genuinely two-sided (396/400 sampled, median spread 1.00c),
    unlike the combo books -- so a mid is meaningful here even though it is
    meaningless on the combo itself.
    """

    def __init__(self, pub: Public):
        self.pub = pub
        self._cache: OrderedDict[str, Tuple[float, dict]] = OrderedDict()

    def prune(self, now: Optional[float] = None) -> int:
        """Drop expired marks and enforce the hard cache bound."""
        current = time.monotonic() if now is None else now
        stale = [ticker for ticker, (stamp, _) in self._cache.items()
                 if current - stamp >= LEG_CACHE_TTL]
        for ticker in stale:
            self._cache.pop(ticker, None)
        while len(self._cache) > LEG_CACHE_MAX:
            self._cache.popitem(last=False)
        return len(stale)

    def mark(self, ticker: str) -> Optional[dict]:
        hit = self._cache.get(ticker)
        if hit and (time.monotonic() - hit[0]) < LEG_CACHE_TTL:
            self._cache.move_to_end(ticker)
            return hit[1]
        self._cache.pop(ticker, None)
        d = self.pub.get(f"/markets/{ticker}")
        if not d or "market" not in d:
            return None
        m = d["market"]
        yb, ya = fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars"))
        rec = {
            "ticker": ticker,
            "yes_bid": yb,
            "yes_ask": ya,
            "yes_mid": (yb + ya) / 2.0 if (yb is not None and ya is not None
                                           and yb > 0 and ya < 1.0) else None,
            "one_sided": not (yb is not None and ya is not None
                              and yb > 0 and ya < 1.0),
            "status": m.get("status"),
            "vol": fnum(m.get("volume")),
            "oi": fnum(m.get("open_interest")),
            "ts": now_iso(),
        }
        self._cache[ticker] = (time.monotonic(), rec)
        self._cache.move_to_end(ticker)
        while len(self._cache) > LEG_CACHE_MAX:
            self._cache.popitem(last=False)
        return rec

    def price_combo(self, legs: List[Tuple[str, str]]
                    ) -> Tuple[Optional[float], Optional[float], List[dict], str]:
        """Price a combo two ways from live leg marks.

        Returns ``(independence, copula, marks, relation)``.

        The independence product is kept because it is what the first day of
        Gate 0 data was measured against and the two must stay comparable. The
        **copula price is the one the gate is specified against** — independence
        understates a correlated joint, so a margin measured against it is
        biased upward and part of it is correlation rather than profit.

        ``relation`` is the strongest leg-pair relationship in the combo
        (``same_player`` / ``same_game`` / ``same_day`` / ``cross``). It is
        reported separately because the adjustment is an order of magnitude
        larger on same-game combos than cross-game ones, so pooling them would
        hide the effect.
        """
        marks, prod, ok = [], 1.0, True
        eff: List[float] = []
        for tkr, side in legs:
            rec = self.mark(tkr)
            if rec is None:
                marks.append({"ticker": tkr, "side": side, "error": "no_mark"})
                ok = False
                continue
            rec = dict(rec, side=side)
            marks.append(rec)
            mid = rec.get("yes_mid")
            if mid is None:
                ok = False
                continue
            p = mid if side == "yes" else (1.0 - mid)
            prod *= p
            eff.append(p)

        if not ok:
            return None, None, marks, "unknown"

        tickers = [t for t, _ in legs]
        rel = _strongest_relation(tickers)
        copula = None
        if _COPULA is not None:
            try:
                # Pass the copula's own ρ — the bare call would silently use
                # DEFAULT_RHO and discard the fitted values.
                corr = _corr_matrix(tickers, _COPULA.rho)
                copula = _COPULA.joint_yes(eff, corr)
            except Exception as exc:            # never break collection
                log.warning("copula pricing failed (%s); independence only", exc)
        return prod, copula, marks, rel


# --------------------------------------------------------------------------
# loops
# --------------------------------------------------------------------------
def live_collections(pub: Public) -> List[str]:
    """Only the rolling -R containers are ever live, and they are re-opened
    daily -- so this must be re-resolved every cycle, never cached to disk."""
    out, cursor = [], None
    now = time.time()
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        d = pub.get("/multivariate_event_collections", params)
        if not d:
            break
        for c in d.get("multivariate_contracts", []):
            close = parse_iso(c.get("close_date"))
            if close and close > now:
                out.append(c["collection_ticker"])
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def _existing_tickers(
    con: sqlite3.Connection, tickers: Iterable[str],
) -> Set[str]:
    """Return existing tickers using bounded primary-key probes.

    The database holds millions of historical rows. Materialising every ticker
    into a Python set consumed hundreds of MiB and forced the service against
    its cgroup cap. Discovery only needs membership for the current API page.
    """
    unique = list(dict.fromkeys(str(ticker) for ticker in tickers if ticker))
    existing: Set[str] = set()
    for start in range(0, len(unique), SQLITE_MEMBERSHIP_BATCH):
        batch = unique[start:start + SQLITE_MEMBERSHIP_BATCH]
        placeholders = ",".join("?" for _ in batch)
        existing.update(row[0] for row in con.execute(
            f"SELECT market_ticker FROM combo WHERE market_ticker IN "
            f"({placeholders})", batch))
    return existing


def discover(con: sqlite3.Connection, pub: Public, pricer: LegPricer) -> int:
    """Sweep live collections for combo markets we have not seen before."""
    series = ["KXMVESPORTSMULTIGAMEEXTENDED", "KXMVECROSSCATEGORY",
              "KXMVENBASINGLEGAME", "KXMVENFLSINGLEGAME"]
    pricer.prune()
    seen_this_sweep: Set[str] = set()
    added = 0
    for s in series:
        cursor, pages = None, 0
        while pages < 40:                       # bound the sweep; it re-runs
            if added >= MAX_NEW_PER_SWEEP:
                log.info("sweep cap %d reached; remainder picked up next cycle",
                         MAX_NEW_PER_SWEEP)
                return added
            params = {"series_ticker": s, "status": "open", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            d = pub.get("/markets", params)
            if not d:
                break
            rows = d.get("markets", [])
            if not rows:
                break
            existing = _existing_tickers(
                con, (m.get("ticker") for m in rows))
            fresh = [m for m in rows
                     if m.get("ticker") not in existing
                     and m.get("ticker") not in seen_this_sweep]
            for m in fresh:
                tkr = m["ticker"]
                legs = legs_of(m)
                if not (MIN_LEGS <= len(legs) <= MAX_LEGS + 4):
                    continue                    # log a little wider than we trade
                indep, copula, marks, rel = pricer.price_combo(legs)
                if indep is None:
                    continue
                model = copula if copula is not None else indep
                if not (LOG_MIN_PX <= model <= LOG_MAX_PX):
                    continue
                in_zone = int(MIN_LEGS <= len(legs) <= MAX_LEGS
                              and MIN_PX <= model <= MAX_PX)
                con.execute(
                    "INSERT OR IGNORE INTO combo (market_ticker, collection,"
                    " event_ticker, created_ts, seen_ts, n_legs, legs_json,"
                    " leg_marks_json, indep_price, copula_price, model_price,"
                    " leg_relation, combo_yes_bid, combo_yes_ask, in_zone)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tkr, m.get("mve_collection_ticker"), m.get("event_ticker"),
                     m.get("created_time") or m.get("open_time"), now_iso(),
                     len(legs), json.dumps(legs), json.dumps(marks),
                     indep, copula, model, rel,
                     fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars")),
                     in_zone))
                seen_this_sweep.add(tkr)
                added += 1
            con.commit()
            cursor = d.get("cursor")
            pages += 1
            if not cursor:
                break
    return added


def _due(con: sqlite3.Connection, before_iso: str, limit: int,
         priority: bool) -> List[Tuple[str, str]]:
    """Oldest unresolved rows past the min age, inside/outside the priority band."""
    if limit <= 0:
        return []
    neg = "" if priority else "NOT "
    return con.execute(
        "SELECT market_ticker, seen_ts FROM combo WHERE resolved=0"
        " AND seen_ts <= ?"
        f" AND {neg}({PRIORITY_SQL})"
        " ORDER BY seen_ts LIMIT ?", (before_iso, limit)).fetchall()


def resolve(con: sqlite3.Connection, pub: Public) -> int:
    """For combos we logged, find out whether they traded and at what price.

    One call per combo, taken once the combo is past ``RESOLVE_MIN_AGE_H`` so
    its trading life is over, in-zone rows first. A row is marked resolved on
    that single check whether or not it traded — it is never re-polled.
    """
    before = iso_at(time.time() - RESOLVE_MIN_AGE_H * 3600)
    rows = _due(con, before, RESOLVE_BATCH, True)
    n_priority = len(rows)
    rows = rows + _due(con, before, RESOLVE_BATCH - n_priority, False)

    done = traded_n = 0
    for tkr, seen_ts in rows:
        d = pub.get("/markets/trades", {"ticker": tkr, "limit": 500})
        trades = (d or {}).get("trades", []) or []
        if trades:
            trades.sort(key=lambda t: t.get("created_time") or "")
            first = trades[0]
            px = fnum(first.get("yes_price_dollars"))
            if px is None:
                p = first.get("yes_price")
                px = p / 100.0 if p is not None else None
            szs = [fnum(t.get("count")) or 0.0 for t in trades]
            pxs = []
            for t in trades:
                q = fnum(t.get("yes_price_dollars"))
                if q is None and t.get("yes_price") is not None:
                    q = t["yes_price"] / 100.0
                pxs.append(q)
            tot = sum(szs) or 1.0
            vwap = sum((p or 0) * s for p, s in zip(pxs, szs)) / tot
            con.execute(
                "UPDATE combo SET resolved=1, traded=1, first_trade_ts=?,"
                " first_trade_px=?, first_trade_sz=?, taker_side=?, n_trades=?,"
                " vwap=?, last_check_ts=? WHERE market_ticker=?",
                (first.get("created_time"), px, fnum(first.get("count")),
                 first.get("taker_side"), len(trades), vwap, now_iso(), tkr))
            done += 1
            traded_n += 1
        else:
            # Past its trading life with no prints: settled untraded. Never
            # re-polled — /markets/trades would return the same empty history.
            con.execute(
                "UPDATE combo SET resolved=1, traded=0, last_check_ts=?"
                " WHERE market_ticker=?", (now_iso(), tkr))
            done += 1
    con.commit()

    # Report the backlog, not just the batch. The starvation this fix addresses
    # hid for two days behind a log line that only ever said "resolved 400".
    due_zone = con.execute(
        f"SELECT COUNT(*) FROM combo WHERE resolved=0 AND seen_ts <= ?"
        f" AND ({PRIORITY_SQL})", (before,)).fetchone()[0]
    due_all = con.execute(
        "SELECT COUNT(*) FROM combo WHERE resolved=0 AND seen_ts <= ?",
        (before,)).fetchone()[0]
    pending = con.execute(
        "SELECT COUNT(*) FROM combo WHERE resolved=0").fetchone()[0]
    log.info("resolve: checked %d (%d in-zone), traded %d; due %d in-zone / %d "
             "total; unresolved %d", len(rows), n_priority, traded_n,
             due_zone, due_all, pending)
    if due_zone > BACKLOG_WARN:
        log.warning("resolve is LOSING GROUND on the gate sample: %d IN-ZONE rows "
                    "are past %.0fh and still unresolved. Discovery is outrunning "
                    "resolution; the Gate 0 sample stops growing when this climbs.",
                    due_zone, RESOLVE_MIN_AGE_H)
    return done


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def report(con: sqlite3.Connection) -> None:
    tot = con.execute("SELECT COUNT(*) FROM combo").fetchone()[0]
    zone = con.execute("SELECT COUNT(*) FROM combo WHERE in_zone=1").fetchone()[0]
    res = con.execute("SELECT COUNT(*) FROM combo WHERE resolved=1").fetchone()[0]
    trd = con.execute("SELECT COUNT(*) FROM combo WHERE traded=1").fetchone()[0]
    print(f"logged {tot:,}  in-zone {zone:,}  resolved {res:,}  traded {trd:,}")
    if res:
        print(f"trade rate among resolved: {100.0*trd/res:.1f}%")
        print(f"  ^ BIASED UPWARD until the tape matures: a combo that trades "
              f"resolves immediately,\n    while an untraded one only resolves "
              f"after {RESOLVE_MIN_AGE_H:.0f}h. Ignore this figure until the "
              f"run is older than that.")

    rows = con.execute(
        "SELECT indep_price, copula_price, first_trade_px, n_legs, in_zone,"
        " leg_relation FROM combo"
        " WHERE traded=1 AND first_trade_px IS NOT NULL").fetchall()
    if len(rows) < 5:
        print("not enough resolved trades yet for a margin estimate")
        return

    def _stats(label, sub):
        if len(sub) < 5:
            return
        sub = sorted(sub)
        n = len(sub)
        print(f"  {label:<14} n={n:>6}  median {sub[n // 2]:+7.2f}c  "
              f"p25 {sub[n // 4]:+7.2f}c  p75 {sub[3 * n // 4]:+7.2f}c  "
              f"mean {sum(sub) / n:+7.2f}c  "
              f"frac>0 {100 * sum(1 for x in sub if x > 0) / n:.0f}%")

    slices = (("all", lambda r: True),
              ("in-zone", lambda r: r[4] == 1),
              ("2 legs", lambda r: r[3] == 2),
              ("3 legs", lambda r: r[3] == 3),
              ("4 legs", lambda r: r[3] == 4),
              ("same_player", lambda r: r[5] == "same_player"),
              ("same_game", lambda r: r[5] == "same_game"),
              ("same_day", lambda r: r[5] == "same_day"),
              ("cross", lambda r: r[5] == "cross"))

    have = sum(1 for r in rows if r[1] is not None)
    print("\n=== GATE 0: COMPETITION MARGIN ===")
    print(f"copula priced on {have}/{len(rows)} traded rows")

    print("\n--- vs COPULA (correlation-adjusted) -- THIS IS THE GATE ---")
    if have < 5:
        print("  too few copula-priced rows; rows logged before the copula")
        print("  landed carry NULL. Re-price them offline from leg_marks_json.")
    else:
        for label, sel in slices:
            _stats(label, [(r[2] - r[1]) * 100.0
                           for r in rows if sel(r) and r[1] is not None])

    print("\n--- vs INDEPENDENCE (biased UPWARD -- kept for comparability) ---")
    print("  independence understates a correlated joint, so this OVERSTATES")
    print("  the room a quoter had. Do NOT judge the gate on it.")
    for label, sel in slices:
        _stats(label, [(r[2] - r[0]) * 100.0 for r in rows if sel(r)])

    by_rel = {}
    for r in rows:
        if r[1] is not None:
            by_rel.setdefault(r[5] or "unknown", []).append((r[1] - r[0]) * 100.0)
    if by_rel:
        print("\n--- how much of the independence margin is CORRELATION, not profit ---")
        for rel, vals in sorted(by_rel.items(), key=lambda kv: -len(kv[1])):
            vals.sort()
            print(f"    {rel:<12} n={len(vals):>6}  "
                  f"median premium {vals[len(vals) // 2]:+.2f}c")

    print("\n=== GATE 0 DECISION (pre-registered 2026-07-28) ===")
    zone = [r for r in rows if r[4] == 1 and r[1] is not None]
    if len(zone) < 5:
        print("  in-zone copula-priced sample too small to decide")
        return
    m = sorted((r[2] - r[1]) * 100.0 for r in zone)
    med, n = m[len(m) // 2], len(m)
    verdict = ("CONTINUE" if (med >= 2.0 and n >= 500)
               else "STOP" if med < 1.0 else "EXTEND one week")
    print(f"  in-zone median vs copula: {med:+.2f}c   n={n}")
    print("  thresholds: CONTINUE >= +2.0c with n>=500 | STOP < +1.0c")
    print(f"  -> {verdict}")
    if n < 500:
        print("  (n below 500 -- not yet decidable either way)")


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="shadow.sqlite")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")
    con = open_db(a.db)

    if a.report:
        report(con)
        return 0

    with SingleInstance(a.db):
        return _run(con, a)


def _run(con: sqlite3.Connection, a) -> int:
    pub = Public()
    pricer = LegPricer(pub)
    stop = {"v": False}

    def _sig(_s, _f):
        stop["v"] = True
        log.info("shutdown requested; finishing current sweep")
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    cols = live_collections(pub)
    log.info("live collections: %s", cols or "(none)")
    note(con, "start", json.dumps({"live_collections": cols}))

    last_resolve = 0.0
    while not stop["v"]:
        t0 = time.monotonic()
        try:
            n = discover(con, pub, pricer)
            if n:
                log.info("discovered %d new combos", n)
        except sqlite3.Error as exc:
            log.error("db error in discover: %s", exc)
            note(con, "error", f"discover: {exc}")
        except requests.RequestException as exc:
            log.error("http error in discover: %s", exc)

        if time.monotonic() - last_resolve > RESOLVE_INTERVAL:
            try:
                resolve(con, pub)      # logs its own batch + backlog detail
                last_resolve = time.monotonic()
            except sqlite3.Error as exc:
                log.error("db error in resolve: %s", exc)

        if a.once:
            break
        time.sleep(max(0.0, DISCOVERY_INTERVAL - (time.monotonic() - t0)))

    report(con)
    log.info("api calls %d, non-retryable errors %d", pub.calls, len(pub.errors))
    return 0


if __name__ == "__main__":
    sys.exit(main())
