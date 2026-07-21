#!/usr/bin/env python3
"""
scripts/maker_report.py
───────────────────────
P-016 Live Maker pilot report — the success-gate metrics.

Pre-registered gate (live_agent_research/REPORT_Live_InPlay_Agent_2026-07-19.md §7):
  BUILD-ON only if markout-adjusted P&L is positive over ≥500 fills,
  robust to excluding the single best day.  Kill otherwise.

Reads data/trade_logs/maker_fills.jsonl (FILL / MARKOUT / SETTLE records).

Usage:
    python3 -m scripts.maker_report [--fills data/trade_logs/maker_fills.jsonl]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_fees import fee_per_contract  # noqa: E402

HORIZON_LABELS = {60.0: "+1m", 300.0: "+5m", 900.0: "+15m"}


def load(path: Path):
    fills, markouts, settles = {}, defaultdict(dict), {}
    if not path.exists():
        return fills, markouts, settles
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = rec.get("type")
        fid = rec.get("fill_id")
        if t == "FILL":
            fills[fid] = rec
        elif t == "MARKOUT":
            markouts[fid][rec.get("horizon_s")] = rec
        elif t == "SETTLE":
            settles[fid] = rec
    return fills, markouts, settles


def fmt_cents(x) -> str:
    return f"{100.0 * x:+.2f}c" if x is not None else "   n/a"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fills", default="data/trade_logs/maker_fills.jsonl")
    args = ap.parse_args()

    fills, markouts, settles = load(Path(args.fills))
    if not fills:
        print("No fills logged yet.")
        return

    n_fills = len(fills)
    total_qty = sum(f.get("qty", 0) for f in fills.values())

    # ── Spread capture at fill: fair minus price, signed by side ─────
    #   buy at price below fair → positive capture
    def capture(f):
        fair = f.get("fair_at_fill")
        if fair is None:
            return None
        sign = 1.0 if f.get("side") == "buy" else -1.0
        return sign * (fair - f.get("price", 0.0))

    captures = [c for c in (capture(f) for f in fills.values()) if c is not None]

    # ── Markout table (per contract, cents) ──────────────────────────
    print("═" * 64)
    print(f"P-016 LIVE MAKER PILOT — {n_fills} fills, "
          f"{total_qty:.0f} contracts")
    print("═" * 64)
    if captures:
        print(f"\nSpread capture at fill (fair − px, signed): "
              f"avg {fmt_cents(sum(captures)/len(captures))}")

    print("\nMarkouts (avg per contract vs fill price, qty-weighted):")
    for h in (60.0, 300.0, 900.0):
        vals, wts = [], []
        for fid, f in fills.items():
            m = markouts.get(fid, {}).get(h)
            if m and m.get("markout_per_contract") is not None:
                vals.append(m["markout_per_contract"])
                wts.append(f.get("qty", 1.0))
        if vals:
            w_avg = sum(v * w for v, w in zip(vals, wts)) / sum(wts)
            print(f"  {HORIZON_LABELS[h]:>4}: {fmt_cents(w_avg)}  (n={len(vals)})")
        else:
            print(f"  {HORIZON_LABELS[h]:>4}: no data")

    # ── Settled P&L (fees included at settle time) ───────────────────
    settled_pnl = sum(s.get("pnl_usd", 0.0) for s in settles.values())
    print(f"\nSettled fills: {len(settles)}/{n_fills}  "
          f"realized P&L: ${settled_pnl:+.2f}")

    # Per-day P&L and best-day robustness
    day_pnl = defaultdict(float)
    for fid, s in settles.items():
        day = str(fills.get(fid, {}).get("iso", s.get("iso", "")))[:10]
        day_pnl[day] += s.get("pnl_usd", 0.0)
    if day_pnl:
        print("\nPer-day realized P&L:")
        for day in sorted(day_pnl):
            print(f"  {day}: ${day_pnl[day]:+.2f}")
        if len(day_pnl) > 1:
            best = max(day_pnl.values())
            print(f"  Excluding best day: ${settled_pnl - best:+.2f}")

    # ── By side / price bucket ───────────────────────────────────────
    print("\nBy side:")
    for side in ("buy", "sell"):
        fs = [f for f in fills.values() if f.get("side") == side]
        if not fs:
            continue
        pnls = [settles[f["fill_id"]]["pnl_usd"] for f in fs
                if f.get("fill_id") in settles]
        print(f"  {side:4s}: {len(fs)} fills"
              + (f", settled P&L ${sum(pnls):+.2f}" if pnls else ""))

    # ── Gate verdict ─────────────────────────────────────────────────
    print("\n" + "─" * 64)
    m5 = []
    for fid, f in fills.items():
        m = markouts.get(fid, {}).get(300.0)
        if m and m.get("markout_per_contract") is not None:
            fee = fee_per_contract(f.get("price", 0.5), maker=True)
            m5.append((m["markout_per_contract"] - fee) * f.get("qty", 1.0))
    m5_total = sum(m5) if m5 else None
    print("GATE (≥500 fills, positive fee-adjusted +5m markout, "
          "robust ex-best-day):")
    print(f"  fills: {n_fills}/500"
          + (f"   +5m markout net of maker fee: ${m5_total:+.2f}"
             if m5_total is not None else "   +5m markout: no data yet"))
    print("─" * 64)


if __name__ == "__main__":
    main()
