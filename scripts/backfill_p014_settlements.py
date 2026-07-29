#!/usr/bin/env python3
"""
scripts/backfill_p014_settlements.py
────────────────────────────────────
Book the terminal rows for P-014 positions that the generic Kalshi settler
stopped resolving on 2026-07-26.

Why these are open
──────────────────
P-014 has never had a settler of its own.  It was resolved *incidentally* by
the generic legacy `Settler`, which walked every PLACED row in the trade log
regardless of pod.  Commit 3b0daae (2026-07-26T21:30Z) scoped
`_open_placed_entries()` to `pod_ids=("P-001", "")` — the correct fix for the
P-017 bug where `result or "void"` was voiding golf top-N positions at $0.00 —
and in doing so removed the only path that was settling P-014.

  * last P-014 settlement   2026-07-26T01:35Z
  * fix deployed            2026-07-26T21:30Z
  * first orphan            2026-07-26T21:21Z

Everything P-014 placed after that has never been checked.

Why this runs BEFORE re-enabling P-014 in the settler
─────────────────────────────────────────────────────
P-014 rows carry `game_time`, so the settler's stale threshold is
`STALE_HOURS_AFTER_GAME = 8`.  Every orphan is far past that.  If P-014 were
added to `_SETTLER_DEP_PODS` first, the very next cycle would run each orphan
through `_check_and_settle`, and any that the Odds API and the *demo* Kalshi
client both failed to resolve would be **auto-voided at $0.00** — precisely the
P-017 failure mode this whole mess started with.  Backfill first, then flip the
config.

Resolution source
─────────────────
`KalshiPublic` (production `api.elections.kalshi.com`), NOT the engine's demo
client.  The settler's own docstring notes the demo API never settles sports
markets, which is why it leans on the Odds API — but the Odds API `/scores`
window only reaches back ~3 days and the oldest orphans are outside it.  The
public per-ticker GET carries the real `status` / `result` on a settled market;
the LIST endpoints null those out.

Discipline, matching `void_unsettleable_positions.py`:

  * narrow allow-list — `P-014` only, no other pod can be touched;
  * `--expect N` is REQUIRED and it refuses unless exactly N positions are
    open, so it can never silently settle more than was reviewed;
  * **never voids.**  A market with no populated `result` is left OPEN and
    reported.  Voiding on "no result yet" is the exact bug that caused this;
  * P&L comes from the settler's own `_calc_outcome_pnl`, imported rather than
    reimplemented, so these rows are arithmetically identical to the 10 P-014
    settlements already booked before the regression;
  * outcome is written to **both** `action` and `outcome` — consumers disagree
    about which they read, and setting one and not the other is how the JDAY
    correction silently failed its first pass;
  * APPENDS only; never rewrites or deletes a line.  Backup first.  Idempotent
    — a second run finds 0 open.

**Stop the service before running**, so nothing interleaves with the append.

    systemctl stop betting-pod-shop
    ./venv/bin/python -m scripts.backfill_p014_settlements --expect 15
    ./venv/bin/python -m scripts.backfill_p014_settlements --expect 15 --apply
    systemctl start betting-pod-shop
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "Legacy" / "Kalshi Arb Project" / "src"))

from settler import _calc_outcome_pnl          # noqa: E402  the settler's own math
from src.kalshi_public import KalshiPublic     # noqa: E402

POD = "P-014"                       # the allow-list, all of it
TERMINAL = ("WIN", "LOSS", "VOID")
DEFAULT_LOG = REPO / "data" / "trade_logs" / "trade_log.jsonl"
SOURCE_TAG = "backfill_p014_settler_regression_3b0daae"


def open_positions(log_path: Path) -> List[Dict[str, Any]]:
    """Unresolved PLACED rows for POD, keyed exactly as the settler keys them.

    Mirrors `Settler._open_placed_entries`: fingerprint when present, ticker
    otherwise; a BANKROLL_RESET wipes the slate.
    """
    placed: Dict[str, Dict[str, Any]] = {}
    resolved: set = set()

    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = (rec.get("action") or "").upper()
            outcome = (rec.get("outcome") or "").upper()
            ticker = rec.get("market_ticker") or rec.get("market_id") or ""
            fp = rec.get("fingerprint", "")

            if action == "BANKROLL_RESET":
                placed.clear()
                resolved.clear()
                continue
            if not ticker:
                continue

            key = fp or ticker
            if action == "PLACED" and float(rec.get("position_size_usd") or 0) > 0:
                placed[key] = rec
            if outcome in TERMINAL:
                resolved.add(key)

    return [
        rec for key, rec in placed.items()
        if key not in resolved and (rec.get("pod_id") or "") == POD
    ]


def resolve(client: KalshiPublic, ticker: str) -> Tuple[Optional[str], str]:
    """Return (result, status) for *ticker*; result is None when unresolved.

    Only a populated `result` of "yes"/"no" counts.  Anything else — an empty
    result, a market still active, a scalar, an API miss — returns None and the
    caller leaves the position OPEN.
    """
    try:
        market = client.get_market(ticker)
    except Exception as exc:                                # noqa: BLE001
        return None, f"api_error: {exc}"
    if not market:
        return None, "not_found"

    status = (market.get("status") or "").lower()
    result = (market.get("result") or "").strip().lower()
    if result in ("yes", "no"):
        return result, status
    return None, f"{status or 'unknown'}/result={result or 'empty'}"


def build_settlement(placed: Dict[str, Any], result: str, now: datetime) -> Dict[str, Any]:
    """Replicate `Settler._settle_position`'s record, plus a provenance tag."""
    side = placed.get("side", "YES")
    size_usd = float(placed.get("position_size_usd") or 0)
    kalshi_prob = float(
        placed.get("kalshi_prob") or placed.get("venue_prob") or 0.5
    )
    outcome, pnl_usd = _calc_outcome_pnl(side, result, size_usd, kalshi_prob)
    return {
        **placed,
        "action":            outcome,
        "outcome":           outcome,
        "result":            result,
        "pnl_usd":           pnl_usd,
        "settled_at_utc":    now.isoformat(),
        "timestamp_utc":     now.isoformat(),
        "resolution_source": SOURCE_TAG,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expect", type=int, required=True,
                    help="refuse to run unless exactly this many P-014 positions are open")
    ap.add_argument("--apply", action="store_true",
                    help="write the settlement rows (default is a dry run)")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = ap.parse_args()

    log_path = args.log
    if not log_path.exists():
        print(f"FATAL: no trade log at {log_path}", file=sys.stderr)
        return 2

    rows = open_positions(log_path)
    print(f"open {POD} positions: {len(rows)} (expected {args.expect})")
    if len(rows) != args.expect:
        print(f"FATAL: expected {args.expect}, found {len(rows)} — refusing to run.",
              file=sys.stderr)
        return 2

    client = KalshiPublic()
    now = datetime.now(timezone.utc)
    settlements: List[Dict[str, Any]] = []
    unresolved: List[Tuple[str, str]] = []
    cache: Dict[str, Tuple[Optional[str], str]] = {}

    rows.sort(key=lambda r: r.get("timestamp_utc", ""))
    for placed in rows:
        ticker = placed.get("market_ticker") or placed.get("market_id") or ""
        if ticker not in cache:
            cache[ticker] = resolve(client, ticker)
        result, status = cache[ticker]

        if result is None:
            unresolved.append((ticker, status))
            print(f"  OPEN   {ticker:<36} {placed.get('side'):<3}  {status}")
            continue

        rec = build_settlement(placed, result, now)
        settlements.append(rec)
        print(f"  {rec['outcome']:<6} {ticker:<36} {placed.get('side'):<3} "
              f"result={result} @{placed.get('kalshi_prob')}  "
              f"pnl=${rec['pnl_usd']:+.2f}")

    total = sum(float(r["pnl_usd"]) for r in settlements)
    wins = sum(1 for r in settlements if r["outcome"] == "WIN")
    losses = sum(1 for r in settlements if r["outcome"] == "LOSS")
    print(f"\nresolved {len(settlements)}  ({wins}W / {losses}L)   net P&L ${total:+.2f}")
    if unresolved:
        print(f"left OPEN {len(unresolved)} (no populated result — NOT voided)")

    if not settlements:
        print("nothing to write.")
        return 0
    if not args.apply:
        print("\nDRY RUN — re-run with --apply to write.")
        return 0

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    backup = log_path.with_suffix(f".jsonl.bak_p014_backfill_{stamp}")
    shutil.copy2(log_path, backup)
    print(f"\nbackup: {backup}")

    with log_path.open("a", encoding="utf-8") as fh:
        for rec in settlements:
            fh.write(json.dumps(rec) + "\n")
    print(f"appended {len(settlements)} settlement rows to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
