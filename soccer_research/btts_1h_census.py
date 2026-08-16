#!/usr/bin/env python3
"""First-half soccer BTTS — Phase 0 feasibility census (READ-ONLY).

Governing rule: soccer_research/BTTS_1H_FEASIBILITY_RULE.md, committed before
this file was run. This script measures whether the INSTRUMENT exists. It
reads live books only; it never reads a settlement, never places an order, and
returns no edge estimate.

Two snapshots are required by rule section 4 (persistence). Run:

    python3 soccer_research/btts_1h_census.py --snapshot 1
    # wait >= 1 hour
    python3 soccer_research/btts_1h_census.py --snapshot 2
    python3 soccer_research/btts_1h_census.py --report
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.kalshi_public import KalshiPublic  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FIXTURE = ROOT / "src" / "fixtures" / "kalshi_series_fees.json"

# Rule section 3 thresholds, quoted from the pre-registration.
MIN_MARKETS = 30
MIN_TWO_SIDED_FRAC = 0.25
MAX_MEDIAN_SPREAD_C = 4.0
STOP_SPREAD_C = 6.0


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def btts_series() -> tuple[list[str], list[str]]:
    """(first_half_series, full_match_series) from the generated fee fixture."""
    fx = json.loads(FIXTURE.read_text())
    alls = [s for s in fx["all_series"] if "BTTS" in str(s).upper()]
    first = sorted(s for s in alls if "1HBTTS" in s.upper())
    # Full-match = a BTTS series that is neither a 1H nor a 2H variant.
    full = sorted(s for s in alls
                  if "1HBTTS" not in s.upper() and "2HBTTS" not in s.upper())
    return first, full


def live_fee_types(kp: KalshiPublic, series: list[str]) -> dict[str, str]:
    """Live fee_type per series, straight from /series. The fixture is NOT
    trusted here: 18 soccer series were missing from it as of 2026-07-30."""
    out: dict[str, str] = {}
    for s in series:
        resp = kp.get(f"/series/{s}")
        if not resp:
            out[s] = "UNREACHABLE"
            continue
        blob = resp.get("series") if isinstance(resp, dict) else None
        out[s] = (blob or resp).get("fee_type", "MISSING")
    return out


def measure_market(kp: KalshiPublic, m: dict) -> dict:
    ob = kp.orderbook(m["ticker"]) or {}
    bid, ask = ob.get("yes_bid"), ob.get("yes_ask")
    bid_qty, ask_qty = ob.get("bid_qty") or 0.0, ob.get("ask_qty") or 0.0
    mid = (bid + ask) / 2.0 if (bid is not None and ask is not None) else None
    spread_c = (ask - bid) * 100.0 if (bid is not None and ask is not None) else None
    # "Genuinely two-sided" per rule section 2: a real quote on both sides with
    # size, not a one-lot artifact resting on an otherwise empty book.
    two_sided = bool(
        bid is not None and ask is not None
        and bid > 0.0 and ask < 1.0
        and bid_qty > 0 and ask_qty > 0
    )
    return {
        "ticker": m.get("ticker"),
        "event_ticker": m.get("event_ticker"),
        "series": m.get("ticker", "").split("-")[0],
        "sub_title": m.get("yes_sub_title"),
        "close_time": m.get("close_time"),
        "yes_bid": bid, "yes_ask": ask,
        "bid_qty": bid_qty, "ask_qty": ask_qty,
        "mid": mid, "spread_c": spread_c,
        "two_sided": two_sided,
    }


def snapshot(idx: int) -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    kp = KalshiPublic()
    first, full = btts_series()

    rows: dict[str, list[dict]] = {"first_half": [], "full_match": []}
    listed_series: dict[str, list[str]] = {"first_half": [], "full_match": []}

    for label, series_list in (("first_half", first), ("full_match", full)):
        for s in series_list:
            markets = kp.open_markets(s)
            if not markets:
                continue
            listed_series[label].append(s)
            for m in markets:
                rows[label].append(measure_market(kp, m))
            print(f"  {label:11} {s:28} {len(markets):3d} markets", flush=True)

    touched = sorted(set(listed_series["first_half"] + listed_series["full_match"]))
    fees = live_fee_types(kp, touched)

    payload = {
        "snapshot": idx,
        "captured_at_utc": _utcnow(),
        "rule_document": "soccer_research/BTTS_1H_FEASIBILITY_RULE.md",
        "series_scanned": {"first_half": len(first), "full_match": len(full)},
        "series_with_open_markets": listed_series,
        "live_fee_types": fees,
        "markets": rows,
    }
    out = DATA / f"btts_1h_snapshot_{idx}.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"\nwrote {out}")
    return payload


def _summarise(rows: list[dict]) -> dict:
    n = len(rows)
    two = [r for r in rows if r["two_sided"]]
    spreads = [r["spread_c"] for r in two if r["spread_c"] is not None]
    mids = [r["mid"] for r in two if r["mid"] is not None]
    return {
        "n_markets": n,
        "n_two_sided": len(two),
        "two_sided_frac": (len(two) / n) if n else None,
        "median_spread_c": statistics.median(spreads) if spreads else None,
        "median_mid": statistics.median(mids) if mids else None,
        "mean_mid": statistics.fmean(mids) if mids else None,
        "median_bid_qty": statistics.median([r["bid_qty"] for r in two]) if two else None,
        "median_ask_qty": statistics.median([r["ask_qty"] for r in two]) if two else None,
    }


def verdict(fh: dict) -> tuple[str, str]:
    """Rule section 3, applied mechanically."""
    n, frac, spread = fh["n_markets"], fh["two_sided_frac"], fh["median_spread_c"]
    if n < MIN_MARKETS:
        return ("NO DECISION — SEASONAL",
                f"only {n} first-half markets listed (< {MIN_MARKETS}); "
                "re-run once the top-5 European leagues are fully in season")
    if frac is None or spread is None:
        return ("NO DECISION", "no two-sided markets to measure a spread on")
    if frac >= MIN_TWO_SIDED_FRAC and spread <= MAX_MEDIAN_SPREAD_C:
        return ("PROCEED",
                f"{n} markets, {frac:.0%} two-sided, median spread {spread:.1f}c")
    if frac < MIN_TWO_SIDED_FRAC or spread > STOP_SPREAD_C:
        return ("STOP — INSTRUMENT TOO THIN",
                f"{n} markets, {frac:.0%} two-sided (need >= {MIN_TWO_SIDED_FRAC:.0%}), "
                f"median spread {spread:.1f}c")
    return ("NO DECISION",
            f"{n} markets, {frac:.0%} two-sided, median spread {spread:.1f}c "
            "falls between the PROCEED and STOP bands")


def report() -> None:
    snaps = sorted(DATA.glob("btts_1h_snapshot_*.json"))
    if not snaps:
        print("no snapshots; run --snapshot 1 first")
        return
    loaded = [json.loads(p.read_text()) for p in snaps]

    for snap in loaded:
        print(f"\n===== snapshot {snap['snapshot']} @ {snap['captured_at_utc']} =====")
        for label in ("first_half", "full_match"):
            s = _summarise(snap["markets"][label])
            print(f"  {label}: n={s['n_markets']} two_sided={s['n_two_sided']}"
                  f" ({(s['two_sided_frac'] or 0):.0%})"
                  f" median_spread={s['median_spread_c']}c"
                  f" median_mid={s['median_mid']} mean_mid={s['mean_mid']}"
                  f" depth_bid/ask={s['median_bid_qty']}/{s['median_ask_qty']}")
        bad = {k: v for k, v in snap["live_fee_types"].items()
               if v not in ("quadratic",)}
        print(f"  live fee types: {len(snap['live_fee_types'])} series checked; "
              f"non-quadratic: {bad or 'NONE — all maker-free'}")

    latest = loaded[-1]
    fh = _summarise(latest["markets"]["first_half"])
    v, why = verdict(fh)
    print(f"\n===== VERDICT (rule section 3, snapshot {latest['snapshot']}) =====")
    print(f"  {v}\n  {why}")

    if len(loaded) >= 2:
        a = {r["ticker"] for r in loaded[-2]["markets"]["first_half"] if r["two_sided"]}
        b = {r["ticker"] for r in loaded[-1]["markets"]["first_half"] if r["two_sided"]}
        both = a & b
        print(f"\n  persistence (rule section 4): {len(both)} of {len(a)} two-sided "
              f"markets in snapshot {loaded[-2]['snapshot']} still two-sided in "
              f"{loaded[-1]['snapshot']}")
    else:
        print("\n  persistence: NOT YET MEASURED — a single snapshot is not a "
              "liquidity measurement (rule section 4). Second snapshot required.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=int, default=None)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.snapshot:
        snapshot(args.snapshot)
    if args.report or not args.snapshot:
        report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
