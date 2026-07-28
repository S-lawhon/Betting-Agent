#!/usr/bin/env python3
"""
scripts/p022_dry_run.py
───────────────────────
Drive P-022's OWN ``from_config() → discover() → cycle()`` path at an injected
wall-clock and report the placement funnel. Paper-only by construction: the
public Kalshi client this pod uses cannot submit orders, and this script writes
its logs to a scratch directory so a dry run never contaminates the live
``data/trade_logs/round_leader_fade_*.jsonl`` that ``p022_checkpoint.py``
reads.

This exists because the 2026-07-29 pre-flight established the funnel by hand
and the numbers then had to be re-established by hand to check a fix. A funnel
that cannot be re-run is not a baseline.

    python3 -m scripts.p022_dry_run --at 2026-07-29T15:31:00Z
    python3 -m scripts.p022_dry_run --at 2026-07-29T15:31:00Z --json out.json

``--at`` may be repeated; each clock is run against a FRESH engine so the
tournaments do not contaminate each other.

Prices are live-as-of-now in every clock — only the window arithmetic time
travels. That is a stated limitation of the method, not a defect: an event
whose books are not yet quoted reads as junk at any injected time.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config_loader import load_config  # type: ignore
from src.round_leader_fade_maker import RoundLeaderFadeMakerPod


def _epoch(iso: str) -> float:
    s = iso.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(s).timestamp()


def _iso(ep: float) -> str:
    return datetime.fromtimestamp(ep, timezone.utc).isoformat()


def dry_run(at_iso: str, log_dir: Path,
            max_books: Optional[int] = None) -> Dict[str, Any]:
    """One injected clock, one fresh engine, one cycle. Returns the funnel."""
    now = _epoch(at_iso)
    try:
        config = load_config()
    except Exception:                                  # noqa: BLE001
        config = {}
    engine = RoundLeaderFadeMakerPod.from_config(
        config, log_dir=log_dir, _now_fn=lambda: now,
        kill_file=log_dir / "KILL_NEVER")

    n_listed = engine.discover()

    # Only the in-window books are cycled, purely to bound orderbook calls.
    # Pruned books hold zero collateral and no quote, so no cap, no reservation
    # and no decision changes — but the pruning is reported so the bound is
    # visible rather than assumed.
    in_window: List[str] = []
    for tk, b in engine.books.items():
        h = (b.close_epoch - now) / 3600.0
        if engine.no_new_quote_h <= h <= engine.fade_start_h:
            in_window.append(tk)
    in_window.sort()
    if max_books is not None:
        in_window = in_window[:max_books]
    keep = set(in_window)
    pruned = [tk for tk in engine.books if tk not in keep]
    for tk in pruned:
        del engine.books[tk]

    # Price every surviving book once, so the band funnel is reported from the
    # same reads the cycle will use. (`_mid` is called again inside the cycle;
    # KalshiPublic caches orderbooks for a few seconds.)
    priced = 0
    in_band = 0
    lo, hi = engine.mid_band
    book_state: Dict[str, Dict[str, Any]] = {}
    for tk in in_window:
        try:
            ob = engine.kalshi.orderbook(tk) or {}
        except Exception:                              # noqa: BLE001
            ob = {}
        mid = engine._mid(tk)
        sided = ("none" if not ob else
                 "two_sided" if (ob.get("yes_bid") or 0) > 0
                 and ob.get("yes_ask") is not None else
                 "one_sided_ask" if ob.get("yes_ask") is not None else
                 "one_sided_bid")
        book_state[tk] = {"mid": mid, "sidedness": sided,
                          "yes_bid": ob.get("yes_bid"),
                          "yes_ask": ob.get("yes_ask"),
                          "ask_qty": ob.get("ask_qty") or ob.get("yes_ask_qty")}
        if mid is None:
            continue
        priced += 1
        if lo <= mid <= hi:
            in_band += 1

    engine.cycle()

    quotes: List[Dict[str, Any]] = []
    for tk, b in sorted(engine.books.items()):
        q = b.ask_quote
        if q is None:
            continue
        quotes.append({
            "ticker": tk, "event": b.event_code, "price": q.price,
            "size": q.qty, "mid_at_quote": q.mid_at_quote,
            "collateral": round((1.0 - q.price) * q.qty, 4),
            "close_source": b.close_source,
            "sidedness": book_state.get(tk, {}).get("sidedness"),
            "ask_qty": book_state.get(tk, {}).get("ask_qty"),
        })

    per_event: Dict[str, Dict[str, Any]] = {}
    for q in quotes:
        e = per_event.setdefault(q["event"], {"quotes": 0, "contracts": 0.0,
                                              "collateral": 0.0})
        e["quotes"] += 1
        e["contracts"] += q["size"]
        e["collateral"] = round(e["collateral"] + q["collateral"], 4)

    return {
        "at": at_iso,
        "at_epoch": now,
        "funnel": {
            "markets_discovered": n_listed,
            "in_window": len(in_window),
            "priced": priced,
            "in_band": in_band,
            "quotes_placed": len(quotes),
        },
        "pruned_out_of_window": len(pruned),
        "worst_case_collateral": round(sum(q["collateral"] for q in quotes), 4),
        "caps": {
            "per_name": engine.max_collateral_per_name,
            "per_tournament": engine.max_collateral_per_tournament,
            "total": engine.max_total_collateral,
        },
        "per_event": per_event,
        "quotes": quotes,
        "breaches": sorted(f"{ev}:{k}" for ev, k in engine._breaches),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", action="append", required=True,
                    help="injected UTC wall clock, e.g. 2026-07-29T15:31:00Z")
    ap.add_argument("--max-books", type=int, default=None,
                    help="cap orderbook pulls (books are sorted by ticker)")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    out = []
    for at in args.at:
        with tempfile.TemporaryDirectory(prefix="p022-dry-") as td:
            r = dry_run(at, Path(td), args.max_books)
        out.append(r)
        f = r["funnel"]
        print(f"\n=== {at} ===")
        print(f"  discovered {f['markets_discovered']} → in window "
              f"{f['in_window']} → priced {f['priced']} → in band "
              f"{f['in_band']} → PLACED {f['quotes_placed']}")
        print(f"  worst-case collateral ${r['worst_case_collateral']:.2f}  "
              f"(per-tournament cap ${r['caps']['per_tournament']:.2f}, "
              f"total ${r['caps']['total']:.2f})")
        for ev, e in sorted(r["per_event"].items()):
            flag = "  ** OVER CAP **" if (
                e["collateral"] > r["caps"]["per_tournament"] + 1e-9) else ""
            print(f"    {ev}: {e['quotes']} quotes, {e['contracts']:.0f} ct, "
                  f"${e['collateral']:.2f}{flag}")
        if r["breaches"]:
            print(f"  CAP BREACHES: {r['breaches']}")
        sizes = sorted({q["size"] for q in r["quotes"]})
        if sizes:
            print(f"  quote sizes: {sizes}")

    if args.json:
        args.json.write_text(json.dumps(out, indent=2, default=str))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
