#!/usr/bin/env python3
"""
scripts/p017_checkpoint.py
──────────────────────────
The single sanctioned reader of P-017's forward gate.

Why this exists
───────────────
Until 2026-07-26 the gate progress lived in ``manager/registry.yaml`` as a
hand-maintained ``progress: 1  # 3M Open, in flight``. Nothing computed it, and
``manager/checks.py`` read that number straight out of the YAML. As written the
gate could be satisfied by *placing into* eight tournaments with most positions
never resolving — a gate satisfiable without a single observation of the thing it
measures. The 3M Open was counted on entry day, while 16 of its 38 positions were
still open.

**A tournament counts when it is SETTLED, not when it is entered.**

Definitions (locked; mirrors the P-022 decision rule §2/§3)
───────────────────────────────────────────────────────────
* **Unit = TOURNAMENT.** One golf event = one observation, pooled across series.
  ``KXPGATOP10-3MO26-*`` and ``KXPGATOP20-3MO26-*`` are the same tournament
  (``event_code = 3MO26``), not two. Names within an event are correlated through
  one leaderboard; treating each bet as an observation is how P-017M produced a
  phantom +9.1¢/ct.
* **SETTLED** = the tournament has at least one resolved position and **zero**
  still-open positions. A partially-resolved event is IN FLIGHT and does not count.
  This is the specific failure the 3M Open exposed: its missed-cut names resolve at
  the Friday cut, days before the players who made it.
* Within a tournament, the statistic is the **contract-weighted** mean net ¢/ct.
  Across tournaments, each enters with **equal weight**.
* **VOIDs are excluded** — no risk was taken. Scalar settlements are NOT voids and
  are always counted at their realised value (``settlement_kind='scalar_partial'``).

Reads the active log **and the gzipped archives**. That is not optional: the
truncating rotation moved every 3M Open placement into an archive, and a reader
that only scans the active log sees a tournament's settlements without their
placements.

Usage
─────
    python3 scripts/p017_checkpoint.py
    python3 scripts/p017_checkpoint.py --json
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_LOG = Path("data/trade_logs/trade_log.jsonl")
POD = "P-017"
THRESHOLD = 8
SETTLED_ACTIONS = ("WIN", "LOSS", "VOID")
_EVENT_RE = re.compile(r"^KX[A-Z0-9]*?TOP\d+-([A-Z0-9]+)-")


def _open_any(path: str):
    return gzip.open(path, "rt", errors="ignore") if path.endswith(".gz") \
        else open(path, "r", errors="ignore")


def _sources(log_path: Path) -> List[str]:
    stem = str(log_path)[: -len(".jsonl")] if str(log_path).endswith(".jsonl") else str(log_path)
    out = sorted(glob.glob(f"{stem}.archive_*.jsonl.gz"))
    if log_path.exists():
        out.append(str(log_path))
    return out


def event_code(rec: Dict[str, Any]) -> Optional[str]:
    code = ((rec.get("extra") or {}).get("event_code") or "").strip()
    if code:
        return code
    m = _EVENT_RE.match(rec.get("market_id") or rec.get("market_ticker") or "")
    return m.group(1) if m else None


def load(log_path: Path = DEFAULT_LOG) -> Dict[str, Dict[str, Any]]:
    """Return {event_code: {"open": [...], "settled": [...]}} for P-017."""
    placed: Dict[str, Dict[str, Any]] = {}
    settled: Dict[str, Dict[str, Any]] = {}
    for src in _sources(log_path):
        with _open_any(src) as fh:
            for raw in fh:
                if '"P-017"' not in raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("pod_id") != POD:
                    continue
                fp = rec.get("fingerprint")
                if not fp:
                    continue
                act = (rec.get("action") or "").upper()
                if act == "PLACED":
                    placed[fp] = rec
                elif act in SETTLED_ACTIONS:
                    settled[fp] = rec

    events: Dict[str, Dict[str, Any]] = {}
    for fp, rec in placed.items():
        code = event_code(rec)
        if not code:
            continue
        ev = events.setdefault(code, {"open": [], "settled": []})
        if fp in settled:
            ev["settled"].append(settled[fp])
        else:
            ev["open"].append(rec)
    # Settlements whose PLACED row has aged out of every archive still count —
    # dropping them would silently shrink a tournament's sample.
    for fp, rec in settled.items():
        if fp in placed:
            continue
        code = event_code(rec)
        if not code:
            continue
        events.setdefault(code, {"open": [], "settled": []})["settled"].append(rec)
    return events


def _contracts(rec: Dict[str, Any]) -> float:
    c = (rec.get("extra") or {}).get("contracts")
    try:
        c = float(c or 0)
    except (TypeError, ValueError):
        c = 0.0
    if c > 0:
        return c
    # Fall back to collateral / fill price.
    try:
        px = float(rec.get("fill_price") or 0)
        size = float(rec.get("position_size_usd") or 0)
        return size / px if px > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def tournament_stat(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Contract-weighted mean net ¢/ct for one tournament. VOIDs excluded."""
    num = den = 0.0
    counted = voids = 0
    for r in rows:
        if (r.get("action") or "").upper() == "VOID":
            voids += 1
            continue
        n = _contracts(r)
        if n <= 0:
            continue
        try:
            pnl = float(r.get("pnl_usd") or 0)
        except (TypeError, ValueError):
            continue
        num += pnl * 100.0     # dollars -> cents
        den += n
        counted += 1
    if den <= 0:
        return None
    return {"cents_per_contract": num / den, "contracts": den,
            "positions": counted, "voids": voids}


def evaluate(log_path: Path = DEFAULT_LOG) -> Dict[str, Any]:
    events = load(log_path)
    settled_stats: Dict[str, Dict[str, Any]] = {}
    in_flight: Dict[str, Dict[str, int]] = {}

    for code, ev in sorted(events.items()):
        if ev["open"] or not ev["settled"]:
            in_flight[code] = {"open": len(ev["open"]), "settled": len(ev["settled"])}
            continue
        st = tournament_stat(ev["settled"])
        if st is not None:
            settled_stats[code] = st
        else:
            in_flight[code] = {"open": 0, "settled": len(ev["settled"]),
                               "note": "all VOID — no risk taken, not an observation"}

    xs = [s["cents_per_contract"] for s in settled_stats.values()]
    T = len(xs)
    edge = statistics.fmean(xs) if xs else None
    se = (statistics.stdev(xs) / math.sqrt(T)) if T >= 2 else None
    z = (edge / se) if (edge is not None and se) else None

    if T == 0:
        verdict = "NO DECISION — no settled tournaments yet"
    elif T < THRESHOLD:
        verdict = f"NO DECISION — {T} of {THRESHOLD} settled tournaments"
    elif edge is not None and edge <= 0:
        verdict = "KILL — gate reached with a non-positive edge"
    else:
        verdict = f"GATE REACHED — {T} settled tournaments, edge {edge:+.2f}c/ct"

    return {
        "pod": POD, "metric": "settled_tournaments",
        "threshold": THRESHOLD, "progress": T,
        "edge_cents_per_contract": edge, "se": se, "z": z,
        "settled_tournaments": settled_stats,
        "in_flight": in_flight,
        "verdict": verdict,
        "sources": _sources(log_path),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log-path", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.log_path.exists() and not _sources(args.log_path):
        print(f"ERROR: no trade log at {args.log_path}", file=sys.stderr)
        return 2

    res = evaluate(args.log_path)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        return 0

    print(f"P-017 gate — settled tournaments: {res['progress']} of {THRESHOLD}")
    print(f"  verdict: {res['verdict']}")
    if res["edge_cents_per_contract"] is not None:
        e, se, z = res["edge_cents_per_contract"], res["se"], res["z"]
        print(f"  edge   : {e:+.2f}c/ct" +
              (f"   se {se:.2f}   z {z:+.2f}" if se else "   (se needs T>=2)"))
    if res["settled_tournaments"]:
        print("\n  SETTLED (counts toward the gate):")
        for code, s in res["settled_tournaments"].items():
            print(f"    {code:12} {s['cents_per_contract']:+7.2f}c/ct  "
                  f"{s['positions']:3} positions  {s['contracts']:.0f} contracts"
                  + (f"  ({s['voids']} void excluded)" if s["voids"] else ""))
    if res["in_flight"]:
        print("\n  IN FLIGHT (does NOT count — a partially-resolved event is not an observation):")
        for code, s in res["in_flight"].items():
            note = f"  {s['note']}" if s.get("note") else ""
            print(f"    {code:12} {s['open']:3} open, {s['settled']:3} settled{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
