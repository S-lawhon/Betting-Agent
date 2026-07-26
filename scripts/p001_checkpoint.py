#!/usr/bin/env python3
"""
scripts/p001_checkpoint.py
──────────────────────────
The single sanctioned reader of P-001's re-scoped CLV forward gate (scenario D,
approved 2026-07-26).

Why the gate was re-scoped
──────────────────────────
The old gate counted **rows in clv_log**. On 2026-07-26 it stood at 650 rows —
3.25x its own 200-row threshold — reading +1.39c/ct net-maker, almost exactly the
+1.4pp target it was written against. Read literally, P-001 had passed.

It should not have. ``Legacy/Kalshi Arb Project/src/matcher.py`` broke fuzzy-score
ties by list order, and every game of an MLB series carries the same two team
names and therefore the same score. So the pod computed its edge from one day's
game and placed the bet on another day's Kalshi market: 628 of 734 settled MLB
PLACED rows (85.6%) sat 17-43h from their own ticker's game. Splitting the
captured rows:

    ticker == priced game    n=105   +7.65c/ct   day-clustered CI [+3.46, +12.05]
    ticker != priced game    n=545   +0.19c/ct   CI [-0.26, +0.66]

The metric was fine and correctly computed. The population was not the one the
hypothesis is about.

**The bug is invisible in clv_log alone.** Its ``commence`` field agrees with the
ticker in 650 of 650 rows, because settlement re-derives it from the ticker's own
time. Auditing that log by itself gives a clean bill of health. The mismatch only
appears when joined to the **trade log's ``game_time``** — the Odds API event that
actually produced the edge at placement time. That is why this script reads both.

Scenario D, as approved
───────────────────────
* **Clean forward sample only.** A row counts only if its bet was PLACED at or
  after ``EPOCH`` — the moment the matcher fix went live on the droplet. The 105
  legacy admissible rows are NOT carried in: they are non-randomly selected (they
  are the cases where the odds list happened to contain one game for that pair;
  83 of 105 are April), and mixing two matcher regimes muddies the replication.
* **Admissibility, not a new metric.** A row counts only if
  ``|ticker-encoded start - priced game_time| <= 3h``. This filters *which trades
  are eligible*; it does not change "de-vigged Pinnacle close, net of maker fee".
* **MLB only.** The +1.4pp was measured on MLB. Generalising mid-test changes the
  population.
* **n = 200**, unchanged.

Usage
─────
    python3 scripts/p001_checkpoint.py
    python3 scripts/p001_checkpoint.py --json
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import random
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_TRADE_LOG = Path("data/trade_logs/trade_log.jsonl")
DEFAULT_CLV_LOG = Path("data/trade_logs/clv_log.jsonl")

POD = "P-001"
THRESHOLD = 200
ADMISSIBLE_HOURS = 3.0

# Matcher fix deployed to the droplet 2026-07-26 21:31 UTC (scripts/deploy.sh,
# health check passed 21:31:32Z). Bets placed before this ran under the broken
# tie-break and are not part of the clean forward sample.
EPOCH = datetime(2026, 7, 26, 21, 31, 0, tzinfo=timezone.utc)

_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
_TICKER_RE = re.compile(r"KXMLBGAME-(\d{2})([A-Z]{3})(\d{2})(\d{4})")
_ET = timezone(timedelta(hours=-4))   # Kalshi MLB tickers encode ET


def ticker_start(ticker: str) -> Optional[datetime]:
    m = _TICKER_RE.search(ticker or "")
    if not m:
        return None
    yy, mon, dd, hhmm = m.groups()
    try:
        return datetime(2000 + int(yy), _MON[mon], int(dd),
                        int(hhmm[:2]), int(hhmm[2:]), tzinfo=_ET)
    except (KeyError, ValueError):
        return None


def parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _open_any(path: str):
    return gzip.open(path, "rt", errors="ignore") if path.endswith(".gz") \
        else open(path, "r", errors="ignore")


def _sources(log_path: Path) -> List[str]:
    stem = str(log_path)[: -len(".jsonl")] if str(log_path).endswith(".jsonl") else str(log_path)
    out = sorted(glob.glob(f"{stem}.archive_*.jsonl.gz"))
    if log_path.exists():
        out.append(str(log_path))
    return out


def load_placements(trade_log: Path) -> Dict[str, Dict[str, Any]]:
    """fingerprint -> {'game_time', 'placed_at', 'market_id'} for P-001 MLB."""
    out: Dict[str, Dict[str, Any]] = {}
    for src in _sources(trade_log):
        with _open_any(src) as fh:
            for raw in fh:
                if '"P-001"' not in raw or "KXMLBGAME" not in raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("pod_id") != POD or (rec.get("action") or "").upper() != "PLACED":
                    continue
                fp = rec.get("fingerprint")
                if not fp:
                    continue
                out[fp] = {
                    "game_time": parse_ts(rec.get("game_time")),
                    "placed_at": parse_ts(rec.get("timestamp_utc") or rec.get("timestamp")),
                    "market_id": rec.get("market_id") or rec.get("market_ticker") or "",
                }
    return out


def _boot_ci(by_day: Dict[str, List[float]], n: int = 5000,
             seed: int = 11) -> Tuple[Optional[float], Optional[float]]:
    days = list(by_day)
    if len(days) < 2:
        return None, None
    rnd = random.Random(seed)
    means: List[float] = []
    for _ in range(n):
        vals = [v for d in (rnd.choice(days) for _ in days) for v in by_day[d]]
        if vals:
            means.append(statistics.fmean(vals))
    if not means:
        return None, None
    means.sort()
    return means[int(0.025 * len(means))], means[int(0.975 * len(means))]


def evaluate(trade_log: Path = DEFAULT_TRADE_LOG,
             clv_log: Path = DEFAULT_CLV_LOG) -> Dict[str, Any]:
    placements = load_placements(trade_log)

    counted: List[float] = []
    by_day: Dict[str, List[float]] = {}
    tally = {"clv_rows": 0, "joined": 0, "post_epoch": 0,
             "admissible_post_epoch": 0, "pre_epoch": 0,
             "inadmissible_post_epoch": 0, "unjoinable": 0, "not_mlb": 0}

    if clv_log.exists():
        with open(clv_log, errors="ignore") as fh:
            for raw in fh:
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                tally["clv_rows"] += 1
                ticker = row.get("market_ticker") or row.get("market_id") or ""
                if "KXMLBGAME" not in ticker:
                    tally["not_mlb"] += 1
                    continue
                fp = row.get("fingerprint")
                p = placements.get(fp) if fp else None
                if not p or not p.get("placed_at") or not p.get("game_time"):
                    tally["unjoinable"] += 1
                    continue
                tally["joined"] += 1

                if p["placed_at"] < EPOCH:
                    tally["pre_epoch"] += 1
                    continue
                tally["post_epoch"] += 1

                start = ticker_start(ticker)
                if start is None:
                    tally["inadmissible_post_epoch"] += 1
                    continue
                delta_h = abs((start - p["game_time"]).total_seconds()) / 3600.0
                if delta_h > ADMISSIBLE_HOURS:
                    tally["inadmissible_post_epoch"] += 1
                    continue

                clv = row.get("clv_net_maker")
                if clv is None:
                    tally["inadmissible_post_epoch"] += 1
                    continue
                tally["admissible_post_epoch"] += 1
                counted.append(float(clv))
                by_day.setdefault(start.date().isoformat(), []).append(float(clv))

    n = len(counted)
    edge = statistics.fmean(counted) * 100 if counted else None   # cents
    lo, hi = _boot_ci(by_day)
    lo = lo * 100 if lo is not None else None
    hi = hi * 100 if hi is not None else None

    if n == 0:
        verdict = ("NO DECISION — clean forward sample is empty; the epoch is "
                   f"{EPOCH:%Y-%m-%d %H:%M}Z and no admissible row has settled yet")
    elif n < THRESHOLD:
        verdict = f"NO DECISION — {n} of {THRESHOLD} admissible forward rows"
    elif edge is not None and lo is not None and lo > 0:
        verdict = f"PASS — {n} rows, {edge:+.2f}c/ct, day-clustered CI excludes zero"
    elif edge is not None and edge <= 0:
        verdict = f"KILL — {n} rows, edge {edge:+.2f}c/ct is not positive"
    else:
        verdict = f"MARGINAL — {n} rows, {edge:+.2f}c/ct but CI includes zero"

    return {
        "pod": POD,
        "metric": "admissible_clv_forward_rows",
        "scenario": "D (clean forward sample, post-fix only)",
        "epoch_utc": EPOCH.isoformat(),
        "admissible_rule": f"|ticker start - priced game_time| <= {ADMISSIBLE_HOURS}h",
        "threshold": THRESHOLD,
        "progress": n,
        "edge_cents_net_maker": edge,
        "ci_low": lo, "ci_high": hi,
        "day_clusters": len(by_day),
        "tally": tally,
        "verdict": verdict,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trade-log", type=Path, default=DEFAULT_TRADE_LOG)
    ap.add_argument("--clv-log", type=Path, default=DEFAULT_CLV_LOG)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.clv_log.exists():
        print(f"ERROR: no clv log at {args.clv_log}", file=sys.stderr)
        return 2

    res = evaluate(args.trade_log, args.clv_log)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        return 0

    t = res["tally"]
    print(f"P-001 gate (scenario D) — admissible forward rows: "
          f"{res['progress']} of {THRESHOLD}")
    print(f"  verdict : {res['verdict']}")
    print(f"  epoch   : {res['epoch_utc']}  (matcher fix live)")
    print(f"  rule    : {res['admissible_rule']}, MLB only")
    if res["edge_cents_net_maker"] is not None:
        ci = (f"  CI [{res['ci_low']:+.2f}, {res['ci_high']:+.2f}]"
              if res["ci_low"] is not None else "  (CI needs >=2 day clusters)")
        print(f"  edge    : {res['edge_cents_net_maker']:+.2f}c/ct net-maker"
              f"   {res['day_clusters']} day clusters{ci}")
    print("\n  attrition:")
    print(f"    clv rows scanned          {t['clv_rows']}")
    print(f"    not MLB                   {t['not_mlb']}")
    print(f"    unjoinable to a placement {t['unjoinable']}")
    print(f"    joined                    {t['joined']}")
    print(f"      pre-epoch (old matcher) {t['pre_epoch']}")
    print(f"      post-epoch              {t['post_epoch']}")
    print(f"        inadmissible (>3h)    {t['inadmissible_post_epoch']}")
    print(f"        ADMISSIBLE            {t['admissible_post_epoch']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
