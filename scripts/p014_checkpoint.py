#!/usr/bin/env python3
"""
scripts/p014_checkpoint.py
──────────────────────────
Sanctioned reader for the P-014 (Live Game Agent) gate.

    python3 -m scripts.p014_checkpoint
    python3 -m scripts.p014_checkpoint --json
    python3 -m scripts.p014_checkpoint --unblind      # see the economics

WHY THIS EXISTS
───────────────
P-014's gate declared `metric: settled_trades`, `source: trade_log`,
`threshold: 500` and **no reader at all**.  `manager.checks._gate_progress`
returns None for a `source` it does not recognise, so the gate was
**unreadable by construction** — 345 settled rows sat in the trade log against
a 500 threshold and nobody could state progress from a sanctioned number.
Found 2026-07-28 by the gate throughput audit
(`research/REPORT_Gate_Throughput_2026-07.md`).

WHY IT REFUSES TO GIVE A VERDICT
────────────────────────────────
**P-014 has no pre-registered decision rule.**  There is no
`P014_DECISION_RULE.md`.  The registry carries a threshold (500) and a question
("does the calibration edge become statistically significant with more n?") but
**no decision schedule**: no kill line, no promotion condition, no hard-kill z.

P-015 and P-022 each have a locked document written *before* results existed,
and both documents say why: P-013 lost $2,094 while its criteria were still
being decided after the fact.  A reader that invents an edge statistic and
prints it next to a threshold is how criteria get decided after the fact — the
number gets anchored on, and the rule is then written to fit it.

So this reader **counts, and declines to judge**:

* it reports progress toward 500, which is unambiguous and is what the gate
  actually declares;
* it returns `NO DECISION — no pre-registered rule exists` at every n;
* it puts the economics behind `--unblind`, so they cannot leak into the daily
  brief before a rule is written.

`--unblind` is a speed bump, not a lock.  The rows are in the trade log and
anyone may compute anything from them.  The point is that **the sanctioned
reader** — the thing the manager quotes — stays silent on a question no rule
has been written for.

**The action this reader is asking for is a written rule, before n hits 500.**
At the realised rate that is months away, so there is time to write it blind.

VOIDs
─────
The rule does not say whether VOIDs count toward the 500.  Every other pod in
this repo excludes them ("no risk taken"), so `progress` follows that
convention — and the with-VOID figure is reported alongside it, so the
ambiguity is visible rather than silently resolved.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

POD_ID = "P-014"
THRESHOLD = 500

# Same globs as the P-015 reader, for the same reasons: rotation TRUNCATES the
# live file, so a reader that opens only `trade_log.jsonl` under-reports by
# whatever has rotated.
DEFAULT_LOGS = [
    "data/trade_logs/trade_log*.jsonl",
    "data/trade_logs/trade_log*.jsonl.gz",
    "data/trade_logs/archive/*.jsonl",
    "data/trade_logs/archive/*.jsonl.gz",
    "data/pods/P-014.jsonl",
]

SETTLED = ("WIN", "LOSS")          # VOIDs excluded — no risk taken
ALL_TERMINAL = ("WIN", "LOSS", "VOID")


def taker_fee(price: float, rate: float = 0.07) -> float:
    return math.ceil(rate * price * (1.0 - price) * 100.0) / 100.0


def _open_any(path: str):
    return (gzip.open(path, "rt", encoding="utf-8", errors="replace")
            if path.endswith(".gz")
            else open(path, "r", encoding="utf-8", errors="replace"))


def load_terminal(patterns, pod_id: str = POD_ID) -> List[Dict[str, Any]]:
    """Every terminal P-014 row (WIN/LOSS/VOID), deduplicated on fingerprint.

    Dedup matters: a rotated log can carry the same settlement twice, and n is
    measured against a pre-registered threshold, so double-counting moves the
    gate rather than merely looking untidy.
    """
    if isinstance(patterns, (str, Path)):
        patterns = [str(patterns)]
    files: List[str] = []
    for pat in patterns:
        files.extend(sorted(glob.glob(str(pat))))

    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(set(files)):
        try:
            with _open_any(path) as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("pod_id") != pod_id:
                        continue
                    if (rec.get("outcome") or "").upper() not in ALL_TERMINAL:
                        continue
                    key = (rec.get("fingerprint")
                           or f"{rec.get('market_id')}|{rec.get('settled_at_utc')}")
                    out[key] = rec
        except OSError:
            continue
    return list(out.values())


def entry_price(rec: Dict[str, Any]) -> Optional[float]:
    """Entry price, or None — never defaulted.

    P-014 writes `fill_price: null` on every row (345 of 345 observed) and
    carries the entry in `venue_prob` / `kalshi_prob`.  A reader that assumed
    `fill_price` would price nothing; one that substituted a constant would
    fabricate the input to a statistic.
    """
    for field in ("fill_price", "venue_prob", "kalshi_prob"):
        v = rec.get(field)
        if v is None:
            continue
        try:
            p = float(v)
        except (TypeError, ValueError):
            continue
        if 0.0 < p < 1.0:
            return p
    return None


def evaluate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Progress and composition only. No verdict — see the module docstring."""
    settled = [r for r in rows if (r.get("outcome") or "").upper() in SETTLED]
    voids = [r for r in rows if (r.get("outcome") or "").upper() == "VOID"]
    n = len(settled)
    wins = sum(1 for r in settled if (r.get("outcome") or "").upper() == "WIN")

    days = sorted({(r.get("settled_at_utc") or "")[:10]
                   for r in rows if r.get("settled_at_utc")})
    return {
        "pod": POD_ID,
        "metric": "settled_trades",
        "threshold": THRESHOLD,
        # The gate's declared unit, house convention (VOIDs excluded).
        "progress": n,
        "n_settled_excl_voids": n,
        "n_voids": len(voids),
        "n_terminal_incl_voids": len(rows),
        "wins": wins,
        "losses": n - wins,
        "first_settlement_utc": days[0] if days else None,
        "last_settlement_utc": days[-1] if days else None,
        "active_days": len(days),
        "n_unpriced": sum(1 for r in settled if entry_price(r) is None),
        # Deliberately constant. See the module docstring.
        "verdict": "NO DECISION",
        "reason": ("no pre-registered decision rule exists for P-014 — "
                   "there is no P014_DECISION_RULE.md, so there is no kill "
                   "line, no promotion condition and no hard-kill z. Progress "
                   "is countable; a verdict is not."),
        "rule_document": None,
        "action_required": (
            "Write a pre-registered decision rule BEFORE n reaches "
            f"{THRESHOLD} — and write it without looking at the economics "
            "(`--unblind`), or it is a rule fitted to results. P-013 is why."),
    }


def economics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Descriptive statistics, shown only under --unblind.

    This is NOT a gate statistic and must not be treated as one: no rule
    defines which of these numbers decides anything.
    """
    settled = [r for r in rows if (r.get("outcome") or "").upper() in SETTLED]
    priced = [r for r in settled if entry_price(r) is not None]
    n = len(priced)
    if not n:
        return {"n": 0}
    wins = sum(1 for r in priced if (r.get("outcome") or "").upper() == "WIN")
    hit = wins / n
    be = sum(entry_price(r) + taker_fee(entry_price(r)) for r in priced) / n
    edge = hit - be
    se = math.sqrt(max(hit * (1 - hit), 1e-9) / n)
    return {
        "n": n, "wins": wins, "hit": hit, "breakeven": be, "edge": edge,
        "z": edge / se if se > 0 else 0.0,
        "pnl_usd": sum(float(r.get("pnl_usd") or 0) for r in settled),
        "mean_fair_prob": (sum(float(r.get("fair_prob") or 0) for r in priced)
                           / n),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="P-014 gate reader — counts progress; gives no verdict, "
                    "because no pre-registered rule exists")
    ap.add_argument("--log", action="append", default=[],
                    help="log path or glob (repeatable); overrides the defaults")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--unblind", action="store_true",
                    help="also print the economics — see the warning it prints")
    args = ap.parse_args(argv)

    rows = load_terminal(args.log or DEFAULT_LOGS)
    r = evaluate(rows)
    if args.unblind:
        r["economics"] = economics(rows)

    if args.json:
        print(json.dumps(r, indent=1))
        return 0

    print("P-014 Live Game Agent — gate reader")
    print("=" * 62)
    print(f"  settled trades : {r['progress']} of {THRESHOLD}   "
          f"({r['wins']}W / {r['losses']}L, VOIDs excluded)")
    print(f"  incl. VOIDs    : {r['n_terminal_incl_voids']} "
          f"({r['n_voids']} void) — the rule does not say which counts")
    print(f"  span           : {r['first_settlement_utc']} → "
          f"{r['last_settlement_utc']}  ({r['active_days']} active days)")
    if r["n_unpriced"]:
        print(f"  UNPRICED       : {r['n_unpriced']} settled row(s) carry no "
              "usable entry price")
    print()
    print(f"  VERDICT: {r['verdict']} — {r['reason']}")
    print()
    print("  *** ACTION REQUIRED ***")
    print(f"  {r['action_required']}")

    if args.unblind:
        e = r["economics"]
        print()
        print("  " + "!" * 58)
        print("  UNBLINDED. These are DESCRIPTIVE statistics, not a gate")
        print("  statistic — no rule says which of them decides anything.")
        print("  Writing P-014's decision rule AFTER reading this block is")
        print("  the P-013 failure mode, and it cost $2,094.")
        print("  " + "!" * 58)
        if e.get("n"):
            print(f"  n (priced)     : {e['n']}")
            print(f"  realized hit   : {e['hit']*100:.1f}%")
            print(f"  breakeven hit  : {e['breakeven']*100:.1f}%")
            print(f"  realized edge  : {e['edge']*100:+.2f}pp   z = {e['z']:+.2f}")
            print(f"  paper P&L      : ${e['pnl_usd']:+,.2f}")
    print()
    print("  Gate: 500 settled trades. Registry: manager/registry.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
