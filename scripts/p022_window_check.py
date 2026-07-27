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
# get a book pull; there are ~350 open leader markets and pulling all of them
# every run would be gratuitous against a public endpoint.
MAX_BOOK_PULLS = 60

OK_STATES = ("NO_MARKETS", "WAITING", "WINDOW_OPEN_QUOTING")
ALARM_STATES = ("CLOSE_REF_PLACEHOLDER", "WINDOW_OPEN_NO_QUOTES",
                "SCHEDULE_UNRESOLVED")


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


def assess(engine: RoundLeaderFadeMakerEngine,
           placeholder_days: float,
           quote_lookback_s: float,
           max_book_pulls: int = MAX_BOOK_PULLS) -> Dict[str, Any]:
    now = engine._now()
    lo_band, hi_band = engine.mid_band

    markets: List[Dict[str, Any]] = []
    for s in engine.series:
        try:
            markets.extend(engine.kalshi.open_markets(s))
        except Exception as exc:                       # noqa: BLE001
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
        e["placeholder"] = bool(span is not None and span >= placeholder_days)
        e["in_pod_window"] = (
            engine.no_new_quote_h <= e["hours_to_close_ref"] <= engine.fade_start_h
        )

    # Only price the events the pod thinks it is quoting right now.
    in_window = [e for e in events.values() if e["in_pod_window"]]
    n_in_band, pulls = 0, 0
    for e in in_window:
        e["n_in_band"] = 0
        for tk in e["tickers"]:
            if pulls >= max_book_pulls:
                break
            pulls += 1
            try:
                mid = engine._mid(tk)
            except Exception:                          # noqa: BLE001
                continue
            if mid is not None and lo_band <= mid <= hi_band:
                e["n_in_band"] += 1
                n_in_band += 1

    quotes = recent_quotes(QUOTES_LOG, now - quote_lookback_s)

    n_placeholder = sum(1 for e in events.values() if e["placeholder"])
    n_listed_events = len(events) + len(unresolved)
    if not markets:
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
    elif in_window and n_in_band > 0 and quotes["n_recent"] == 0:
        state = "WINDOW_OPEN_NO_QUOTES"
        detail = (
            f"{len(in_window)} event(s) in the placement window with "
            f"{n_in_band} name(s) priced inside the "
            f"[{lo_band:.2f}, {hi_band:.2f}] band, and ZERO quotes written in "
            f"the last {quote_lookback_s / 60:.0f} min. This is the condition "
            "that went unnoticed for three days.")
    elif in_window and n_in_band > 0:
        state = "WINDOW_OPEN_QUOTING"
        detail = (f"{n_in_band} name(s) in band across {len(in_window)} event(s); "
                  f"{quotes['n_recent']} quote(s) in the last "
                  f"{quote_lookback_s / 60:.0f} min")
    else:
        state = "WAITING"
        detail = (f"{len(events)} event(s) listed with real close references, "
                  "none inside the placement window — silence is correct")

    return {
        "ts": now,
        "iso": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "alarm": state in ALARM_STATES,
        "detail": detail,
        "n_events": len(events),
        "n_markets": len(markets),
        "n_resolved_events": len(events),
        "n_unresolved_events": len(unresolved),
        "unresolved": unresolved,
        "n_placeholder_events": n_placeholder,
        "n_in_window_events": len(in_window),
        "n_in_band": n_in_band,
        "book_pulls": pulls,
        "quotes": quotes,
        "params": {
            "series": list(engine.series),
            "mid_band": [lo_band, hi_band],
            "fade_start_h": engine.fade_start_h,
            "no_new_quote_h": engine.no_new_quote_h,
            "placeholder_days": placeholder_days,
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
    ap.add_argument("--no-write", action="store_true",
                    help="skip appending to the status log")
    args = ap.parse_args()

    try:
        config = load_config()
    except Exception:                                  # noqa: BLE001
        config = {}
    engine = RoundLeaderFadeMakerPod.from_config(config)

    result = assess(engine, args.placeholder_days,
                    args.quote_lookback_min * 60.0, args.max_book_pulls)

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
    print(f"  events resolved: {result['n_resolved_events']} "
          f"({result['n_markets']} markets listed)")
    print(f"  UNRESOLVED     : {result['n_unresolved_events']} event(s) "
          "— the pod fails closed on these")
    print(f"  placeholder    : {result['n_placeholder_events']} of "
          f"{result['n_events']} events")
    print(f"  in pod window  : {result['n_in_window_events']} events, "
          f"{result['n_in_band']} names in band")
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
