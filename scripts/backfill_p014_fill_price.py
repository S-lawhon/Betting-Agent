#!/usr/bin/env python3
"""
scripts/backfill_p014_fill_price.py
───────────────────────────────────
Re-derive `fill_price` for P-014 PLACED rows that were written with
`fill_price: null`.

    python3 -m scripts.backfill_p014_fill_price \
        --log data/trade_logs/trade_log.jsonl \
        --log 'data/trade_logs/trade_log.archive_*.jsonl.gz' \
        --log 'data/trade_logs/archive/2026-*.jsonl.gz'

WHY THE PRICE IS RECOVERABLE WITHOUT GUESSING.  P-014's ScanResult never
set `fill_price`, so every PLACED row it wrote between 2026-03-28 and
2026-07-28 (356 of 356, verified on the droplet) carries a null — no fee
(0.07*P*(1-P)) and no contract count (size / price) can be computed from
it.  But every one of those rows is `mode: paper`, and in paper mode the
pod's `_place_order()` does not model slippage at all: it logs the limit
and returns a synthetic id.  The limit it is handed is `venue_prob`, and
`venue_prob` is recorded on the row.  So for a paper row

    fill_price == venue_prob

is true BY CONSTRUCTION, not by estimation — it is exactly the rule
`MultiExecutor` applies to every other pod's paper fill
(`fill_price=result.venue_prob`).  There is no other source: `clv_log.jsonl`
holds 658 records and all 658 are P-001, and the pod's own logs record the
same limit price this row already carries.

`venue_prob` is the price OF THE SIDE BOUGHT — for a NO row it is already
1 − mid — so `contracts = position_size_usd / fill_price` holds on both
sides.  (P-014 bought NO on 170 of the 356.)

A LIVE row would NOT be recoverable this way: a live limit order can fill
better than its limit and Kalshi's response carries no fill price.  This
script therefore REFUSES any row that is not `mode: paper` and reports it
rather than filling it in.

**This script never mutates trade history.**  It writes a corrected series
to a NEW file (default
`data/corrections/p014_fill_price_backfill_<date>.jsonl`).  Merging it into
the live log is a separate, deliberate decision.
"""

import argparse
import glob
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_fees import fee_per_contract  # noqa: E402

POD_ID = "P-014"
BACKFILL_BASIS = "paper_limit_backfill"

DEFAULT_LOGS = [
    "data/trade_logs/trade_log.jsonl",
    "data/trade_logs/trade_log.archive_*.jsonl.gz",
    "data/trade_logs/archive/2026-*.jsonl.gz",
]


# ── log reading ──────────────────────────────────────────────────────

def iter_rows(patterns: List[str]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield (path, record) over plain and gzipped JSONL logs."""
    files: List[str] = []
    for pat in patterns:
        files.extend(sorted(glob.glob(pat)))
    for path in files:
        try:
            opener = (gzip.open(path, "rt", errors="replace")
                      if path.endswith(".gz")
                      else open(path, "r", encoding="utf-8", errors="replace"))
            with opener as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        yield path, json.loads(raw)
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            print(f"  ! unreadable {path}: {exc}", file=sys.stderr)


def find_nulls(patterns: List[str], pod_id: str) -> List[Dict[str, Any]]:
    """P-014 PLACED rows with no usable fill_price, deduped on fingerprint.

    A rotated log holds the same row more than once; counting it twice
    would double the fee estimate.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for _path, rec in iter_rows(patterns):
        if rec.get("pod_id") != pod_id or rec.get("action") != "PLACED":
            continue
        if rec.get("fill_price") is not None:
            continue
        key = rec.get("fingerprint") or f"{rec.get('market_id')}|{rec.get('timestamp_utc')}"
        out.setdefault(key, rec)
    return list(out.values())


# ── derivation ───────────────────────────────────────────────────────

def derive_fill_price(rec: Dict[str, Any]) -> Tuple[float, str]:
    """Return (fill_price, reason). fill_price is 0.0 when unrecoverable."""
    if (rec.get("mode") or "").lower() != "paper":
        return 0.0, "not paper mode — a live fill is not the limit price"

    for field in ("venue_prob", "kalshi_prob"):
        price = rec.get(field)
        if isinstance(price, (int, float)) and 0.0 < float(price) < 1.0:
            return float(price), field

    return 0.0, "no usable venue_prob/kalshi_prob on the row"


def corrected_row(rec: Dict[str, Any], price: float, source: str) -> Dict[str, Any]:
    out = dict(rec)
    out["fill_price"] = price
    extra = dict(out.get("extra") or {})
    extra["fill_price_basis"] = BACKFILL_BASIS
    extra["fill_price_backfill_source"] = source
    out["extra"] = extra
    return out


# ── main ─────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", action="append", dest="logs",
                    help="log path or glob (repeatable; defaults to the "
                         "standard trade_logs set)")
    ap.add_argument("--pod", default=POD_ID)
    ap.add_argument("--out", default=None,
                    help="output JSONL (default data/corrections/"
                         "<pod>_fill_price_backfill_<date>.jsonl)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing")
    args = ap.parse_args()

    patterns = args.logs or DEFAULT_LOGS
    rows = find_nulls(patterns, args.pod)
    print(f"{args.pod}: {len(rows)} PLACED rows with null fill_price")
    if not rows:
        return 0

    fixed: List[Dict[str, Any]] = []
    unrecoverable: List[Tuple[Dict[str, Any], str]] = []
    by_source: Dict[str, int] = {}

    for rec in rows:
        price, reason = derive_fill_price(rec)
        if price <= 0.0:
            unrecoverable.append((rec, reason))
            continue
        fixed.append(corrected_row(rec, price, reason))
        by_source[reason] = by_source.get(reason, 0) + 1

    print(f"  recoverable:   {len(fixed)}")
    for src, n in sorted(by_source.items()):
        print(f"    from {src}: {n}")
    print(f"  unrecoverable: {len(unrecoverable)}")
    for rec, reason in unrecoverable[:10]:
        print(f"    {rec.get('timestamp_utc','')[:19]} {rec.get('market_id')}: {reason}")
    if len(unrecoverable) > 10:
        print(f"    ... and {len(unrecoverable) - 10} more")

    # What the null was costing: the fee bill this unblocks.  P-014 crosses
    # the spread with a marketable limit, so it pays the taker rate —
    # 0.07*P*(1-P) per contract, series-independent.
    notional = sum(float(r.get("position_size_usd") or 0) for r in fixed)
    contracts = sum(float(r.get("position_size_usd") or 0) / r["fill_price"]
                    for r in fixed)
    fees = sum((float(r.get("position_size_usd") or 0) / r["fill_price"])
               * fee_per_contract(r["fill_price"], maker=False)
               for r in fixed)
    print(f"\n  notional:  ${notional:,.2f}")
    print(f"  contracts: {contracts:,.1f}")
    print(f"  taker fee: ${fees:,.2f}  ({100 * fees / notional:.2f}% of notional)"
          if notional else "  taker fee: n/a")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else Path(
        f"data/corrections/{args.pod}_fill_price_backfill_{stamp}.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for rec in fixed:
            fh.write(json.dumps(rec, default=str) + "\n")
    print(f"\n  wrote {len(fixed)} corrected rows → {out_path}")
    print("  trade history was NOT modified; merging is a separate decision.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
