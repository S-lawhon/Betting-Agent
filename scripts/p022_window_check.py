#!/usr/bin/env python3
"""
scripts/p022_window_check.py
────────────────────────────
Make P-022's SILENCE observable.  Read-only.

P-022's failure mode is that it writes nothing, and writing nothing is also
its correct behaviour between golf tournaments.  It ran live from 2026-07-23
to 2026-07-26 producing zero quotes and nothing noticed, because there is no
signal that distinguishes "healthy and waiting" from "structurally incapable
of quoting".  This script is that signal.

    python3 -m scripts.p022_window_check
    python3 -m scripts.p022_window_check --json

Exit 0 = nothing wrong.  Exit 1 = ALARM.

WHY THIS DOES NOT JUST CALL THE POD
───────────────────────────────────
A detector that decides "is a window open?" the same way the pod does agrees
with the pod by construction, including when the pod is wrong — it would have
sat silent through all three dead days.  So this script takes the pod's own
close reference (`resolve_event_close`, imported, not reimplemented) and then
asks INDEPENDENT questions about the answer:

  1. Did the reference resolve at all?  Since 2026-07-28 the close comes from
     an external schedule (`src/golf_schedule.py`) and the pod fails CLOSED
     when it does not resolve.  Failing closed is correct and it is also
     indistinguishable from health, so `SCHEDULE_UNRESOLVED` alarms on it.
  2. Is the reference a real timestamp, or a fallback placeholder?  (The
     listing-span discriminator below.  It now also catches a regression to
     reading Kalshi's own fields.)
  3. Are names actually priced in band with no quotes being written?

THE DISTINCTION THIS SCRIPT EXISTS TO MAKE (added 2026-07-29)
─────────────────────────────────────────────────────────────
Until now the detector collapsed two very different situations into one
silent `WAITING`: *no window is open* and *a window is open but nothing
passes the screens*.  Only the first is unambiguously healthy.  The states
are now:

    NO_MARKETS                       no open round-leader market anywhere
    NO_WINDOW                        listed, none within [12h, 24h] of close
    WINDOW_OPEN_NO_CANDIDATE         window open, nothing clears band/caps
    WINDOW_OPEN_CANDIDATE_NO_QUOTE   window open, a name clears EVERY screen,
                                     and the pod has written no quote
                                     in TWO consecutive runs           -> ALARM
    WINDOW_OPEN_CANDIDATE_NO_QUOTE_ONCE
                                     same condition seen in ONE run only —
                                     recorded, not paged; escalates if the
                                     same name is still unquoted next run
    QUOTED                           >=1 quote written since the window opened
    CLOSE_REF_PLACEHOLDER            close reference is a fallback     -> ALARM
    SCHEDULE_UNRESOLVED              pod is failing closed             -> ALARM
    CHECK_FAILED                     this checker could not measure    -> ALARM

Three rules that keep the alarm honest rather than noisy:

* **"Has it quoted?" is measured PER TICKER SINCE THAT EVENT'S WINDOW
  OPENED**, not "in the last hour".  P-022 writes a QUOTE row when it places
  or re-prices, then rests the quote through the round writing nothing — so
  an hour-lookback would flip to ALARM a few minutes after a perfectly
  healthy placement.
* **A grace period.** `run_round_leader_fade.py` re-discovers every 900 s, so
  a market whose window has just opened may legitimately not be in the pod's
  book yet.  A candidate only counts as missing a quote once its window has
  been open longer than `--grace-min` (default 20).
* **Two consecutive runs to page.** The grace above is anchored to the
  WINDOW opening, so a name whose price drifts into the band mid-window is
  alert-eligible the instant it crosses — with zero effective grace.  On
  2026-08-12 the */15 cron sampled the ~2-minute gap between CAME's mid
  ticking onto the band's lower edge and the pod's next cycle quoting it
  (80 s after the sample), and a CRITICAL email went out about a quote that
  already existed.  One run cannot tell that race from the five-day failure;
  the next run can, because the pod cycles and rediscovers well inside the
  15-minute cron cadence.  The first observation is recorded as
  `WINDOW_OPEN_CANDIDATE_NO_QUOTE_ONCE` (no page) and escalates only if the
  SAME name is still unquoted when the next run looks.

A CHECKER FAILURE IS A FAILURE, NEVER A SKIP.  If listing raises for any
series the state is `CHECK_FAILED` and the run alarms; the previous version
printed a warning to stderr and then reported `NO_MARKETS`, which is the
same "looks healthy" outcome as everything else this file exists to prevent.

THE PLACEHOLDER PROBLEM (measured 2026-07-27, see the report)
─────────────────────────────────────────────────────────────
On an OPEN Kalshi round-leader market, every time field collapses to one
conservative fallback:

    KXPGAR1LEAD-ROC26      open 2026-07-27T00:10Z   close/occurrence/
    KXLPGAR1LEAD-AIGWO26   expiration/latest_expiration ALL 2026-08-16T00:00Z
    KXCHAMPTOURR1LEAD-POI26

Three tournaments on three tours cannot end Round 1 at the same instant.
`close_time` is the scheduled fallback; the market carries
`can_close_early=true` / "will close and expire after a winner is declared"
and `close_time` is only REWRITTEN to the true value at the moment it closes.
Confirmed on settled markets, where `close_time` has moved to a to-the-second
early-close stamp while `expiration_time` still holds the fallback:

    KXPGAR1LEAD-3MO26    open 07-22T16:10  close 07-24T00:11:10  exp 08-09T00:00
    KXPGAR1LEAD-COPC26   open 07-16T16:10  close 07-16T23:40:01  exp 08-02T00:00
    KXPGAR1LEAD-THOC26   open 07-13T19:11  close 07-16T21:02:30  exp 08-02T04:00

and confirmed as an exchange-wide pattern on a control series where Kalshi
demonstrably knows the schedule: KXMLBGAME-26JUL261920NYYPHI was still open
with close_time 2026-07-29T23:20Z — three days after its own first pitch.

Consequence: a listed round-leader market's close reference runs ~16-20 days
out, so P-022's [12h, 24h] placement window opens roughly a fortnight AFTER
the round has been played and settled.  That is an ALARM on its own, with no
window and no quote needed to detect it — which is the whole point.

DISCRIMINATOR
─────────────
Observed listing-to-real-close spans are 0.31 / 1.33 / 3.08 days; observed
fallback spans are 16.3 / 17.3 / 20.0 days.  `--placeholder-days` (default
7.0) sits in the empty gap between them.  Field-collapse alone is NOT used as
the test: Kalshi collapses close_time onto expiration_time even for markets
whose real schedule it knows (the MLB control above), so collapse means only
"not yet closed".  The span is the discriminator.

OUTPUT
──────
Appends one status record per run to data/p022_window_check/status.jsonl.
That file is the manager's staleness target, and its history is also the
cheapest way to answer the open question "does Kalshi ever correct close_time
BEFORE the round?" — each run records the close reference it saw per event,
so a correction shows up as a change.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config              # noqa: E402
from src.round_leader_fade_maker import (              # noqa: E402
    RoundLeaderFadeMakerEngine, RoundLeaderFadeMakerPod,
)

STATUS_DIR = Path("data/p022_window_check")
STATUS_FILE = STATUS_DIR / "status.jsonl"
QUOTES_LOG = Path("data/trade_logs/round_leader_fade_quotes.jsonl")

# Bound the orderbook cost. Only markets the pod could plausibly be quoting
# get a book pull; there are ~950 open leader markets and pulling all of them
# every run would be gratuitous against a public endpoint.
#
# Raised 60 -> 200 on 2026-07-29: one in-window event is a full tournament
# field (144 markets for AIGWO26, 149 for ROC26), and at 60 the detector
# priced less than half of it. The names that clear the band are NOT at the
# front of the list — of AIGWO26's 144, the 13 in band are scattered — so a
# 60-pull budget could report "no candidate" for an event the pod is quoting,
# which is the same blindness in a new place.
MAX_BOOK_PULLS = 200

# How long after a window opens the pod is still allowed to be silent.
#
# For a market already in the pod's book — which is the normal case, since
# round-leader markets list 2.7-4.9 days ahead of their round — the pod needs
# only one 20 s cycle, because `book.close_epoch` is already resolved. The
# grace exists solely for a market listed after the last `--rediscover 900`
# pass, which for these series would require Kalshi to list a round-leader
# market less than 24 h before the round; that has never been observed. 10
# minutes covers the rediscover interval with margin while keeping end-to-end
# detection inside the ~30 min the alert path can deliver.
DEFAULT_GRACE_S = 600.0

# How old the PREVIOUS status row must be for this run to count as its
# consecutive confirmation. The floor stops a manual run seconds after a
# cron row from "confirming" a price still flapping on the band edge — the
# two samples must be far enough apart that the pod's 20 s cycles saw the
# name in band and still declined. The ceiling is three cron slots
# (8,23,38,53 * * * *), so one missed run still confirms; older than that
# and "the condition persisted" is really "the condition recurred", which
# starts the count over.
CONFIRM_MIN_PREV_AGE_S = 600.0
CONFIRM_MAX_PREV_AGE_S = 45 * 60.0

OK_STATES = ("NO_MARKETS", "NO_WINDOW", "WINDOW_OPEN_NO_CANDIDATE",
             "WINDOW_OPEN_GRACE", "WINDOW_OPEN_CANDIDATE_NO_QUOTE_ONCE",
             "QUOTED")
ALARM_STATES = ("CLOSE_REF_PLACEHOLDER", "WINDOW_OPEN_CANDIDATE_NO_QUOTE",
                "SCHEDULE_UNRESOLVED", "CHECK_FAILED")

# Pre-2026-07-29 names, recorded alongside the new state so the existing
# status.jsonl history stays comparable.
LEGACY_STATE = {
    "NO_WINDOW": "WAITING",
    "WINDOW_OPEN_NO_CANDIDATE": "WAITING",
    "WINDOW_OPEN_GRACE": "WAITING",
    "WINDOW_OPEN_CANDIDATE_NO_QUOTE": "WINDOW_OPEN_NO_QUOTES",
    "QUOTED": "WINDOW_OPEN_QUOTING",
}


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _epoch(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def recent_quotes(path: Path, since_epoch: float) -> Dict[str, Any]:
    """QUOTE rows written since `since_epoch`, plus the last one ever seen."""
    n_recent, last_ts, total = 0, None, 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "QUOTE":
                    continue
                total += 1
                ts = rec.get("ts")
                try:
                    ts = float(ts)
                except (TypeError, ValueError):
                    continue
                if last_ts is None or ts > last_ts:
                    last_ts = ts
                if ts >= since_epoch:
                    n_recent += 1
    except OSError:
        # No file at all is the expected state before the first quote ever.
        return {"exists": False, "n_recent": 0, "total": 0, "last_ts": None}
    return {"exists": True, "n_recent": n_recent, "total": total,
            "last_ts": last_ts}


def last_quote_per_ticker(path: Path) -> Dict[str, float]:
    """`ticker -> epoch of its most recent QUOTE row`.

    The unit of "has the pod quoted?" has to be the ticker and the reference
    point has to be that event's window opening.  P-022 writes a QUOTE row
    only when it PLACES or RE-PRICES; a healthy quote then rests through the
    round writing nothing at all, so any fixed lookback would read a correct
    placement as silence within the hour.
    """
    out: Dict[str, float] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "QUOTE":
                    continue
                tk = rec.get("ticker")
                try:
                    ts = float(rec.get("ts"))
                except (TypeError, ValueError):
                    continue
                if tk and (tk not in out or ts > out[tk]):
                    out[tk] = ts
    except OSError:
        return {}
    return out


def resting_quotes_from_log(quotes_path: Path,
                            fills_path: Path) -> Dict[str, Dict[str, Any]]:
    """Reconstruct the pod's LIVE resting quotes: `ticker -> {event, coll, ts}`.

    `rebuild_from_log()` restores FILLS only, so on 2026-07-30 the detector's
    engine saw $9.25 of ROC26 exposure while the pod held ~$49.50 — the caps
    looked wide open, late-band names read as "candidates with cap room", and
    a healthy cap-bound pod paged CRITICAL. This replay closes that gap:

      * QUOTE places/re-prices a resting quote; PULL removes it.
      * RESTART resets everything — a restarted process holds no quotes in
        memory (only fills survive its rebuild). Markers are written by the
        runner at startup; logs from before the marker existed replay from
        the top, which matches a process that has not restarted since.
      * FILL rows at/after the quote's own timestamp consume its qty (the
        filled part is already counted by `rebuild_from_log`); SETTLE rows
        drop the ticker entirely (`_maybe_settle` sets `done`, releasing it).
        An UNFILLED book writes no SETTLE rows, so `_maybe_settle` also pulls
        its resting quote through the log (PULL, reason "settled") — logs from
        before that existed overstate exposure until the next RESTART marker.

    Collateral is worst-case, `qty × (1 − ask)`, exactly what the pod's
    exposure accessors charge for a resting sell-YES quote.
    """
    resting: Dict[str, Dict[str, Any]] = {}
    try:
        with open(quotes_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = rec.get("type")
                if kind == "RESTART":
                    resting.clear()
                    continue
                tk = rec.get("ticker")
                if not tk:
                    continue
                if kind == "QUOTE":
                    try:
                        resting[tk] = {
                            "event": rec.get("event") or "",
                            "ask": float(rec.get("ask")),
                            "qty": float(rec.get("size")),
                            "ts": float(rec.get("ts")),
                        }
                    except (TypeError, ValueError):
                        continue
                elif kind == "PULL":
                    resting.pop(tk, None)
    except OSError:
        return {}

    # Fills consume resting qty; settlement releases the book outright.
    try:
        with open(fills_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                tk = rec.get("ticker")
                if not tk or tk not in resting:
                    continue
                kind = rec.get("type")
                if kind == "SETTLE":
                    resting.pop(tk, None)
                elif kind == "FILL":
                    try:
                        ts, qty = float(rec.get("ts")), float(rec.get("qty"))
                    except (TypeError, ValueError):
                        continue
                    if ts >= resting[tk]["ts"]:
                        resting[tk]["qty"] -= qty
    except OSError:
        pass

    out: Dict[str, Dict[str, Any]] = {}
    for tk, q in resting.items():
        if q["qty"] <= 0:
            continue                      # fully consumed -> nothing rests
        out[tk] = {"event": q["event"], "ts": q["ts"],
                   "coll": q["qty"] * max(1.0 - q["ask"], 0.0)}
    return out


def screen_after_band(engine: RoundLeaderFadeMakerEngine, ticker: str,
                      event_code: str, mid: float,
                      pending_event: float = 0.0, pending_total: float = 0.0):
    """Every screen the pod applies AFTER the band, as (ok, reason, size, px).

    This is the one place the detector deliberately duplicates pod logic
    instead of importing it, because the pod's copy lives inline in
    ``_cycle_book`` and reaching into it would make the detector agree with
    the pod by construction — including when the pod is wrong.  The
    duplication is pinned by ``tests/test_p022_window_check.py::
    test_screen_agrees_with_the_engines_own_decision``, which drives a real
    engine over the same states and asserts the two answers match.

    Cap room is read from the engine's public exposure accessors, so it is
    only meaningful after ``rebuild_from_log()`` has restored live exposure.

    ``pending_event`` / ``pending_total`` carry the collateral the CALLER has
    already allocated to earlier candidates in this same pass. The detector
    holds no quotes of its own — it never calls ``cycle()`` — so without them
    every candidate would see an empty book and the detector would expect the
    pod to quote all of them. Once the per-tournament cap started dropping
    names, that would have paged ``WINDOW_OPEN_CANDIDATE_NO_QUOTE`` on names
    the pod correctly declined. Callers must walk candidates in the pod's own
    allocation order (ticker, ascending) for these to mean anything.
    """
    # ── size screen (R5), duplicated from _cycle_book in the same position:
    # after the band, before the caps. Reads the book snapshot `engine._mid`
    # recorded — callers must have priced the ticker through `engine._mid`
    # first (``assess`` does), or an empty snapshot reads as thin, which is
    # correct: a book the detector never saw cannot vouch for its own size.
    if engine.min_top_size > 0:
        snap = getattr(engine, "_last_book", {}).get(ticker) or {}
        bid_q = snap.get("bid_qty") or 0.0
        ask_q = snap.get("ask_qty") or 0.0
        thin = (ask_q < engine.min_top_size
                if snap.get("book_side") == "one_sided_ask"
                else min(bid_q, ask_q) < engine.min_top_size)
        if thin:
            return False, "thin_book", 0.0, None

    book = engine.books.get(ticker)
    book_coll = book.collateral if book is not None else 0.0
    book_sold = book.sold if book is not None else 0.0
    own_quote_coll = book.quoted_collateral if book is not None else 0.0

    room_name_coll = engine.max_collateral_per_name - book_coll
    room_name_ct = engine.max_contracts_per_name - book_sold
    if room_name_coll <= 0 or room_name_ct <= 0:
        return False, "cap_per_name", 0.0, None
    # Exposure, not filled collateral — §7's caps bind on resting quotes too.
    # This book's own resting quote is excluded because the quote priced below
    # replaces it, exactly as the pod does.
    room_event = (engine.max_collateral_per_tournament
                  - (engine.tournament_exposure(event_code) - own_quote_coll)
                  - pending_event)
    room_total = (engine.max_total_collateral
                  - (engine.total_exposure() - own_quote_coll)
                  - pending_total)
    if room_event <= 0 or room_total <= 0:
        return False, "cap_collateral", 0.0, None
    quote_px = round(mid + engine.quote_offset, 2)
    if quote_px <= mid or quote_px >= 0.99:
        return False, "quote_px_unusable", 0.0, quote_px
    per_ct_coll = max(1.0 - quote_px, 1e-6)
    size = float(int(min(room_name_ct,
                         room_name_coll / per_ct_coll,
                         room_event / per_ct_coll,
                         room_total / per_ct_coll)))
    if size <= 0:
        return False, "cap_sized_to_zero", 0.0, quote_px
    return True, "", size, quote_px


def assess(engine: RoundLeaderFadeMakerEngine,
           placeholder_days: float,
           quote_lookback_s: float,
           max_book_pulls: int = MAX_BOOK_PULLS,
           grace_s: float = DEFAULT_GRACE_S) -> Dict[str, Any]:
    now = engine._now()
    lo_band, hi_band = engine.mid_band

    # The cap screen below reads the engine's collateral accessors, which are
    # zero on a freshly constructed engine. Restoring the live book from the
    # fills log is what makes "would the pod have quoted?" a real question
    # rather than one whose answer is always "yes, there is room".
    rebuild_error: Optional[str] = None
    try:
        engine.rebuild_from_log()
    except Exception as exc:                           # noqa: BLE001
        rebuild_error = f"{type(exc).__name__}: {exc}"

    # The rebuild restores fills only. The pod's RESTING quotes also consume
    # its caps (§7 binds on exposure), and missing them is how a cap-bound pod
    # paged CRITICAL on 2026-07-30: the detector computed ~$40 of ROC26 room
    # that did not exist. Replayed from the pod's own logs, seeded into the
    # same pending_* mechanism the greedy walk below already uses.
    resting = resting_quotes_from_log(engine.quotes_log, engine.fills_log)
    resting_by_event: Dict[str, float] = {}
    for q in resting.values():
        resting_by_event[q["event"]] = (
            resting_by_event.get(q["event"], 0.0) + q["coll"])

    markets: List[Dict[str, Any]] = []
    discovery_errors: Dict[str, str] = {}
    for s in engine.series:
        try:
            markets.extend(engine.kalshi.open_markets(s))
        except Exception as exc:                       # noqa: BLE001
            # A checker failure is a FAILURE, never a skip. Swallowing this
            # made a total listing outage read as NO_MARKETS — "healthy and
            # between tournaments" — which is the exact confusion this file
            # exists to remove.
            discovery_errors[s] = f"{type(exc).__name__}: {exc}"
            print(f"  WARN: discovery failed for {s}: {exc}", file=sys.stderr)

    # The pod's close reference now comes from the EXTERNAL schedule
    # (src/golf_schedule.py), not from any Kalshi field. Resolve it exactly
    # the way the pod does — imported, never reimplemented — and then keep
    # asking the independent questions below about the answer.
    resolved: Dict[str, Any] = {}
    unresolved: Dict[str, str] = {}
    for ev in sorted({m.get("event_ticker") or "" for m in markets} - {""}):
        try:
            rc = engine.resolve_event_close(ev)
        except Exception as exc:                       # noqa: BLE001
            rc, exc_s = None, str(exc)
            unresolved[ev] = f"resolver raised: {exc_s}"
        if rc is None:
            unresolved.setdefault(
                ev, engine.unresolved_events.get(ev, "unresolved"))
        else:
            resolved[ev] = rc

    events: Dict[str, Dict[str, Any]] = {}
    for m in markets:
        ev = m.get("event_ticker") or ""
        rc = resolved.get(ev)
        if rc is None:
            continue
        open_ep = _epoch(m.get("open_time"))
        span_d = ((rc.close_epoch - open_ep) / 86400.0) if open_ep else None
        e = events.setdefault(ev, {
            "event": ev, "n_markets": 0, "close_ref": rc.close_epoch,
            "close_source": rc.source, "competition": rc.competition,
            "open_time": m.get("open_time"),
            # Kept alongside the resolved close on purpose: this is Kalshi's
            # own placeholder, and logging both every run is what will answer
            # "does Kalshi ever correct close_time BEFORE the round?"
            "kalshi_close_time": m.get("close_time"),
            "expiration_time": m.get("expiration_time"),
            "listing_span_days": span_d, "tickers": [],
        })
        e["n_markets"] += 1
        if len(e["tickers"]) < max_book_pulls:
            e["tickers"].append(m.get("ticker"))

    for e in events.values():
        span = e["listing_span_days"]
        e["hours_to_close_ref"] = (e["close_ref"] - now) / 3600.0
        # Recorded for every event, not only the ones already in window: this
        # is the single number a human watching a pre-flight actually wants,
        # and it MOVES — every event below resolves through the coarse
        # per-tour day offset today and will be re-resolved to the precise
        # tee-time close as soon as ESPN publishes pairings.
        e["window_open_epoch"] = e["close_ref"] - engine.fade_start_h * 3600.0
        e["window_open_iso"] = _iso(e["window_open_epoch"])
        e["placeholder"] = bool(span is not None and span >= placeholder_days)
        e["in_pod_window"] = (
            engine.no_new_quote_h <= e["hours_to_close_ref"] <= engine.fade_start_h
        )

    # Only price the events the pod thinks it is quoting right now.
    in_window = [e for e in events.values() if e["in_pod_window"]]
    quote_ts = last_quote_per_ticker(QUOTES_LOG)

    n_priced = n_in_band = n_candidates = n_quoted = 0
    pulls = 0
    # Aggregate cap is across tournaments, so this accumulates across events —
    # and it STARTS at the pod's live resting exposure (every event, in window
    # or not: an out-of-window round's resting quotes still hold the cap).
    pending_total = sum(q["coll"] for q in resting.values())
    missing: List[str] = []
    refusals: Dict[str, int] = {}
    for e in in_window:
        e["window_open_for_s"] = now - e["window_open_epoch"]
        e["event_code"] = engine._event_code({"event_ticker": e["event"]})
        e.update({"n_priced": 0, "n_in_band": 0, "n_candidates": 0,
                  "n_quoted": 0, "candidates_without_quote": [],
                  "screen_refusals": {}})
        # The pod allocates the per-tournament cap greedily in ticker order,
        # so the detector must ask its question in the same order or it will
        # expect quotes on the names the cap dropped. Sorting also makes a
        # `max_book_pulls` truncation a correct PREFIX of the pod's allocation
        # rather than an arbitrary subset of it.
        #
        # Seeded with the event's live resting quotes for the same reason the
        # aggregate is: the per-tournament cap spans every round of the
        # tournament, so R1 quotes resting through settlement consume the room
        # R2's late-band names are asking about.
        pending_event = resting_by_event.get(e["event_code"], 0.0)
        for tk in sorted(e["tickers"]):
            if pulls >= max_book_pulls:
                break
            pulls += 1
            try:
                mid = engine._mid(tk)
            except Exception:                          # noqa: BLE001
                continue
            if mid is None:
                continue
            e["n_priced"] += 1
            n_priced += 1
            if not (lo_band <= mid <= hi_band):
                continue
            e["n_in_band"] += 1
            n_in_band += 1
            ts = quote_ts.get(tk)
            if ts is not None and ts >= e["window_open_epoch"]:
                # The pod already answered this name — screening it again
                # would double it: its live resting collateral is in the
                # pending_* seed, and the seed cannot grant the own-quote
                # exclusion the pod's re-price path gets (the detector's
                # engine holds no quote object to exclude).
                e["n_candidates"] += 1
                n_candidates += 1
                e["n_quoted"] += 1
                n_quoted += 1
                continue
            ok, why, _size, _px = screen_after_band(
                engine, tk, e["event_code"], mid,
                pending_event=pending_event, pending_total=pending_total)
            if ok:
                _alloc = _size * max(1.0 - (_px or 0.0), 1e-6)
                pending_event += _alloc
                pending_total += _alloc
            if not ok:
                e["screen_refusals"][why] = e["screen_refusals"].get(why, 0) + 1
                refusals[why] = refusals.get(why, 0) + 1
                continue
            e["n_candidates"] += 1
            n_candidates += 1
            if e["window_open_for_s"] >= grace_s:
                # Only counts as missing once the pod has had a rediscover
                # pass to see it. Before that, silence is scheduling.
                e["candidates_without_quote"].append(tk)
                missing.append(tk)

    quotes = recent_quotes(QUOTES_LOG, now - quote_lookback_s)

    funnel = {
        "markets_listed": len(markets),
        "close_resolved": sum(e["n_markets"] for e in events.values()),
        "inside_window": sum(e["n_markets"] for e in in_window),
        "priced": n_priced,
        "in_band": n_in_band,
        "passes_every_screen": n_candidates,
        "quoted_since_window_open": n_quoted,
        "candidates_without_quote": len(missing),
    }

    n_placeholder = sum(1 for e in events.values() if e["placeholder"])
    if discovery_errors or rebuild_error:
        state = "CHECK_FAILED"
        bits = [f"{k}: {v}" for k, v in list(discovery_errors.items())[:4]]
        if rebuild_error:
            bits.append(f"rebuild_from_log: {rebuild_error}")
        detail = (
            f"the CHECKER could not measure P-022 ({len(discovery_errors)} of "
            f"{len(engine.series)} series failed to list"
            + (", and the live book could not be restored" if rebuild_error else "")
            + "). Everything below is incomplete, so treat P-022's state as "
              "UNKNOWN, not healthy. " + "; ".join(bits))
    elif not markets:
        state = "NO_MARKETS"
        detail = ("no open round-leader markets in any of the "
                  f"{len(engine.series)} configured series — silence is "
                  "correct between tournaments")
    elif not events:
        # Fail-closed is working as designed, and the pod is still mute. That
        # is a legitimate outcome (a wrong round time is worse than no quote)
        # but it must never be silent, because it looks identical to health.
        state = "SCHEDULE_UNRESOLVED"
        detail = (
            f"{len(unresolved)} event(s) LISTED but the external round "
            "schedule resolved NONE of them, so the pod is failing closed and "
            "will not quote. Reasons: "
            + "; ".join(f"{k}: {v}" for k, v in list(unresolved.items())[:4]))
    elif n_placeholder == len(events):
        state = "CLOSE_REF_PLACEHOLDER"
        detail = (
            f"{len(events)} event(s), {sum(e['n_markets'] for e in events.values())} "
            f"markets are LISTED, but every close reference is a fallback "
            f"placeholder (listing span >= {placeholder_days:g}d). The pod's "
            f"[{engine.no_new_quote_h:g}h, {engine.fade_start_h:g}h] window is "
            "computed off a timestamp ~2 weeks past the real round end, so it "
            "cannot open while these markets are tradeable. P-022 CANNOT QUOTE.")
    elif missing:
        state = "WINDOW_OPEN_CANDIDATE_NO_QUOTE"
        detail = (
            f"{len(missing)} name(s) across {len(in_window)} event(s) clear "
            f"EVERY screen the pod applies — inside the "
            f"[{engine.no_new_quote_h:g}h, {engine.fade_start_h:g}h] window, "
            f"priced inside [{lo_band:.2f}, {hi_band:.2f}], with cap room and a "
            f"usable quote price — and P-022 has written NO quote for them "
            f"since their window opened (grace {grace_s / 60:.0f} min elapsed). "
            f"This is the failure that has cost five days. First: "
            + ", ".join(missing[:5]))
    elif n_quoted > 0:
        state = "QUOTED"
        detail = (f"{n_quoted} of {n_candidates} candidate name(s) across "
                  f"{len(in_window)} event(s) have a QUOTE row written since "
                  f"their window opened; {quotes['n_recent']} quote row(s) in "
                  f"the last {quote_lookback_s / 60:.0f} min")
    elif n_candidates > 0:
        # Candidates exist, none quoted, but no window has been open long
        # enough for the pod's 900 s rediscover pass. Distinct from
        # NO_CANDIDATE so a run at window_open + 1 min is not mistaken for
        # "there was nothing to quote".
        state = "WINDOW_OPEN_GRACE"
        detail = (
            f"{n_candidates} candidate name(s) clear every screen but the "
            f"earliest window has only been open "
            f"{min(e['window_open_for_s'] for e in in_window) / 60:.0f} min "
            f"(grace {grace_s / 60:.0f} min). Silence is still allowed; this "
            "run does NOT clear P-022 — the next one does.")
    elif in_window:
        state = "WINDOW_OPEN_NO_CANDIDATE"
        detail = (
            f"{len(in_window)} event(s) in the placement window, "
            f"{funnel['inside_window']} markets, {n_priced} priced, "
            f"{n_in_band} in band, {n_candidates} clearing every screen"
            + (f" (refused: {refusals})" if refusals else "")
            + ". Not quoting is CORRECT here, but it is a different state from "
              "'no window is open' and the two used to be the same silence.")
    else:
        state = "NO_WINDOW"
        detail = (f"{len(events)} event(s) listed with real close references, "
                  "none inside the placement window — silence is correct")

    return {
        "ts": now,
        "iso": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "legacy_state": LEGACY_STATE.get(state, state),
        "alarm": state in ALARM_STATES,
        "detail": detail,
        "funnel": funnel,
        "screen_refusals": refusals,
        "candidates_without_quote": missing,
        # The pod's live resting quotes, replayed from its own logs. This is
        # the exposure the caps actually bind against; without it the 2026-07-30
        # false CRITICAL was undiagnosable from this file alone.
        "resting_exposure": {
            "total": round(sum(q["coll"] for q in resting.values()), 2),
            "by_event": {k: round(v, 2)
                         for k, v in sorted(resting_by_event.items())},
            "n_tickers": len(resting),
        },
        "discovery_errors": discovery_errors,
        "rebuild_error": rebuild_error,
        "n_events": len(events),
        "n_markets": len(markets),
        "n_resolved_events": len(events),
        "n_unresolved_events": len(unresolved),
        "unresolved": unresolved,
        "n_placeholder_events": n_placeholder,
        "n_in_window_events": len(in_window),
        "n_in_band": n_in_band,
        "n_candidates": n_candidates,
        "book_pulls": pulls,
        "quotes": quotes,
        "params": {
            "series": list(engine.series),
            "mid_band": [lo_band, hi_band],
            "quote_offset": engine.quote_offset,
            "fade_start_h": engine.fade_start_h,
            "no_new_quote_h": engine.no_new_quote_h,
            "placeholder_days": placeholder_days,
            "grace_min": grace_s / 60.0,
        },
        # Per-event close references are recorded every run on purpose: their
        # history answers "does Kalshi ever correct close_time before the
        # round?" without a second script.
        "events": [
            {k: v for k, v in e.items() if k != "tickers"}
            | {"close_ref_iso": _iso(e["close_ref"])}
            for e in sorted(events.values(), key=lambda x: x["close_ref"])
        ],
    }


def _previous_status_row(path: Path,
                         tail_bytes: int = 1 << 18) -> Optional[Dict[str, Any]]:
    """Last parseable row of the status log, reading only the file's tail."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - tail_bytes))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def apply_two_run_confirmation(result: Dict[str, Any],
                               status_path: Path = STATUS_FILE,
                               now: Optional[float] = None) -> Dict[str, Any]:
    """Downgrade a first-run WINDOW_OPEN_CANDIDATE_NO_QUOTE to ONCE.

    Called on the assess() result BEFORE it is appended to the status log, so
    the log's last row is the previous run. The condition confirms — and
    pages — only when the previous row's age sits inside
    [``CONFIRM_MIN_PREV_AGE_S``, ``CONFIRM_MAX_PREV_AGE_S``] — one genuine
    cron interval, give or take — and shares at least one missing name with
    this run: a DIFFERENT name missing in each of two runs is two transients,
    not one persistent failure. A confirmed row keeps escalating on every
    subsequent run it persists (``confirmed_runs`` counts them), so a real
    outage pages on run 2 and keeps paging.

    The previous row's own state does not matter, only its missing names:
    a ONCE row confirms its successor exactly like a full alarm row does,
    and rows written before this mechanism existed (no ``confirmed_runs``
    key) count as one prior observation.
    """
    if result.get("state") != "WINDOW_OPEN_CANDIDATE_NO_QUOTE":
        return result
    now = time.time() if now is None else now
    prev = _previous_status_row(status_path) or {}
    prev_ts = prev.get("ts")
    consecutive = (isinstance(prev_ts, (int, float))
                   and CONFIRM_MIN_PREV_AGE_S
                   <= now - prev_ts <= CONFIRM_MAX_PREV_AGE_S)
    overlap = (set(result.get("candidates_without_quote") or [])
               & set(prev.get("candidates_without_quote") or []))
    if consecutive and overlap:
        result["confirmed_runs"] = int(prev.get("confirmed_runs") or 1) + 1
        return result
    result["state"] = "WINDOW_OPEN_CANDIDATE_NO_QUOTE_ONCE"
    result["legacy_state"] = LEGACY_STATE.get(result["state"], result["state"])
    result["alarm"] = False
    result["confirmed_runs"] = 1
    result["detail"] = (
        "FIRST run to see this — recorded, not paged. " + result["detail"]
        + " The window-open grace gives ZERO cover to a name whose price "
          "drifts into the band mid-window, and one run cannot tell that "
          "race from a mute pod (2026-08-12: the pod quoted 80 s after the "
          "detector sampled). Escalates to WINDOW_OPEN_CANDIDATE_NO_QUOTE "
          "if any of these names is still unquoted next run.")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(
        description="P-022 quotable-window detector — makes silence loud")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--placeholder-days", type=float, default=7.0,
                    help="listing-to-close span at or above which the close "
                         "reference is treated as a fallback placeholder "
                         "(observed real spans 0.3-3.1d, fallbacks 16-20d)")
    ap.add_argument("--quote-lookback-min", type=float, default=60.0,
                    help="how far back to look for QUOTE rows")
    ap.add_argument("--max-book-pulls", type=int, default=MAX_BOOK_PULLS)
    ap.add_argument("--grace-min", type=float, default=DEFAULT_GRACE_S / 60.0,
                    help="how long after a window opens the pod may still be "
                         "silent before a candidate counts as un-quoted "
                         "(default covers run_round_leader_fade's 900s "
                         "rediscover interval)")
    ap.add_argument("--no-write", action="store_true",
                    help="skip appending to the status log")
    args = ap.parse_args()

    try:
        config = load_config()
        engine = RoundLeaderFadeMakerPod.from_config(config)
        result = assess(engine, args.placeholder_days,
                        args.quote_lookback_min * 60.0, args.max_book_pulls,
                        args.grace_min * 60.0)
        result = apply_two_run_confirmation(result)
    except Exception as exc:                           # noqa: BLE001
        # The checker dying must LOOK like a failure. Exiting on a traceback
        # with no status row would leave the last healthy row as the most
        # recent thing anyone reads.
        import traceback
        traceback.print_exc()
        result = {
            "ts": time.time(),
            "iso": datetime.now(timezone.utc).isoformat(),
            "state": "CHECK_FAILED",
            "legacy_state": "CHECK_FAILED",
            "alarm": True,
            "detail": ("the P-022 window checker RAISED and measured nothing: "
                       f"{type(exc).__name__}: {exc}. P-022's state is UNKNOWN."),
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not args.no_write:
        try:
            STATUS_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATUS_FILE, "a") as fh:
                fh.write(json.dumps(result, default=str) + "\n")
        except OSError as exc:
            print(f"  WARN: could not write {STATUS_FILE}: {exc}",
                  file=sys.stderr)

    if args.json:
        print(json.dumps(result, indent=1, default=str))
        return 1 if result["alarm"] else 0

    print("P-022 quotable-window check")
    print("=" * 62)
    if "funnel" not in result:
        print(f"  *** ALARM: {result['state']} ***")
        print(f"  {result['detail']}")
        return 1
    print(f"  events resolved: {result['n_resolved_events']} "
          f"({result['n_markets']} markets listed)")
    print(f"  UNRESOLVED     : {result['n_unresolved_events']} event(s) "
          "— the pod fails closed on these")
    print(f"  placeholder    : {result['n_placeholder_events']} of "
          f"{result['n_events']} events")
    f = result["funnel"]
    print("  funnel         : "
          f"listed {f['markets_listed']} -> resolved {f['close_resolved']} "
          f"-> in window {f['inside_window']} -> priced {f['priced']} "
          f"-> in band {f['in_band']} -> passes every screen "
          f"{f['passes_every_screen']} -> QUOTED {f['quoted_since_window_open']}")
    if result["screen_refusals"]:
        print(f"  screen refusals: {result['screen_refusals']}")
    q = result["quotes"]
    print(f"  quotes         : {q['n_recent']} recent / {q['total']} ever"
          + ("" if q["exists"] else "   (no quote log has ever been written)"))
    for e in result["events"][:8]:
        flag = "PLACEHOLDER" if e["placeholder"] else e.get("close_source", "real")
        print(f"    {e['event']:30s} close_ref={e['close_ref_iso']} "
              f"h={e['hours_to_close_ref']:8.1f} span="
              f"{e['listing_span_days'] if e['listing_span_days'] is None else round(e['listing_span_days'], 2)}d "
              f"[{flag}]")
    for ev, why in list(result["unresolved"].items())[:6]:
        print(f"    {ev:30s} UNRESOLVED — {why}")
    print()
    if result["alarm"]:
        print(f"  *** ALARM: {result['state']} ***")
    else:
        print(f"  OK: {result['state']}")
    print(f"  {result['detail']}")
    print()
    print("  Gate reader (the only sanctioned verdict): "
          "python3 -m scripts.p022_checkpoint")
    return 1 if result["alarm"] else 0


if __name__ == "__main__":
    sys.exit(main())
