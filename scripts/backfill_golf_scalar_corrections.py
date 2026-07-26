#!/usr/bin/env python3
"""
scripts/backfill_golf_scalar_corrections.py
───────────────────────────────────────────
Re-derive golf settlements that `KalshiGolfSettler` mis-booked as VOID
when the market actually settled `result="scalar"` — a PARTIAL PAYOUT at
`settlement_value_dollars`, not a refund.

    python3 -m scripts.backfill_golf_scalar_corrections \
        --log data/trade_logs/trade_log.jsonl \
        --log 'data/trade_logs/archive/*.jsonl'

**This script never mutates trade history.** It reads the logs, queries
Kalshi for the true settlement, and writes a corrected series to a NEW
file (default `data/corrections/<pod>_scalar_corrections_<date>.jsonl`)
plus a summary. Applying the correction to live books is a separate,
deliberate decision — see the printed P&L delta first.

Signature of an affected row (all pre-2026-07-26 settlements):
    outcome == "VOID"  and  resolution_source == "kalshi_withdrawn"
Rows already carrying `settlement_kind` were written by the fixed
settler and are skipped.

Kalshi's public API is a shared 2 req/s per-IP budget across every
session and the live VPS. Every market fetched is cached to
`--cache` so a re-run costs nothing.
"""

import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_golf_settler import (  # noqa: E402
    SCALAR_RESULT,
    _calc_outcome_pnl,
    scalar_settlement_value,
)
from src.kalshi_public import KalshiPublic  # noqa: E402

# Rows written by the buggy settler carry this source.
BUGGY_SOURCE = "kalshi_withdrawn"
SETTLED_OUTCOMES = ("WIN", "LOSS", "VOID")


# ── log reading ──────────────────────────────────────────────────────

def iter_rows(patterns: List[str]):
    seen_files = []
    for pat in patterns:
        seen_files.extend(sorted(glob.glob(pat)))
    for path in seen_files:
        try:
            with open(path, "r", encoding="utf-8") as fh:
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


def find_suspects(patterns: List[str], pod_id: str) -> List[Dict[str, Any]]:
    """Settlement rows that the scalar bug could have mis-booked.

    Deduplicated on (market_id, fingerprint): a rotated log can hold the
    same settlement twice and double-counting would inflate the delta.
    """
    out: Dict[tuple, Dict[str, Any]] = {}
    for path, rec in iter_rows(patterns):
        if rec.get("pod_id") != pod_id:
            continue
        if (rec.get("outcome") or "").upper() not in SETTLED_OUTCOMES:
            continue
        if rec.get("settlement_kind"):
            continue                       # written by the fixed settler
        src = (rec.get("resolution_source") or "").strip().lower()
        if src != BUGGY_SOURCE:
            continue
        key = (rec.get("market_id") or rec.get("market_ticker") or "",
               rec.get("fingerprint") or "")
        rec["_source_file"] = path
        out[key] = rec
    return list(out.values())


# ── market lookup with an on-disk cache ──────────────────────────────

class MarketCache:
    def __init__(self, cache_dir: Path, client: Optional[KalshiPublic] = None):
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self.fetched = 0
        self.cached = 0

    def get(self, ticker: str) -> Optional[Dict[str, Any]]:
        path = self.dir / f"{ticker}.json"
        if path.exists():
            try:
                self.cached += 1
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        if self._client is None:
            self._client = KalshiPublic()
        market = self._client.get_market(ticker)   # KalshiPublic throttles
        self.fetched += 1
        if market is not None:
            try:
                path.write_text(json.dumps(market), encoding="utf-8")
            except OSError:
                pass
        return market


# ── correction ───────────────────────────────────────────────────────

def correct_row(row: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute a mis-booked row. Returns a correction record."""
    ticker = row.get("market_id") or row.get("market_ticker") or ""
    result = str(market.get("result") or "").strip().lower()
    value = scalar_settlement_value(market) if result == SCALAR_RESULT else None

    side = row.get("side", "YES")
    size_usd = float(row.get("position_size_usd") or 0)
    fill_price = float(row.get("fill_price") or row.get("venue_prob") or 0.5)
    extra = row.get("extra") or {}
    fees_usd = round(float(extra.get("contracts") or 0)
                     * float(extra.get("taker_fee") or 0), 4)

    booked_net = float(row.get("pnl_usd") or 0)

    if result != SCALAR_RESULT:
        status = "not_scalar"
        outcome, gross = row.get("outcome"), float(row.get("pnl_gross_usd") or 0)
        net = booked_net
    elif value is None:
        status = "scalar_no_value"
        outcome, gross, net = row.get("outcome"), None, None
    else:
        status = "corrected"
        outcome, gross = _calc_outcome_pnl(
            side, SCALAR_RESULT, size_usd, fill_price, settlement_value=value,
        )
        net = round(gross - fees_usd, 2)

    return {
        "correction_of": row.get("fingerprint"),
        "market_id": ticker,
        "pod_id": row.get("pod_id"),
        "side": side,
        "position_size_usd": size_usd,
        "fill_price": fill_price,
        "kalshi_result": result,
        "settlement_value": value,
        "settlement_kind": ("scalar_partial" if status == "corrected"
                            else "unchanged"),
        "status": status,
        "booked_outcome": row.get("outcome"),
        "booked_pnl_usd": booked_net,
        "corrected_outcome": outcome,
        "corrected_pnl_gross_usd": gross,
        "corrected_pnl_usd": net,
        "pnl_delta_usd": (None if net is None else round(net - booked_net, 2)),
        "fees_usd": fees_usd,
        "settled_at_utc": row.get("settled_at_utc"),
        "source_file": row.get("_source_file"),
        "corrected_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "note": ("scalar is a PARTIAL PAYOUT at settlement_value_dollars, "
                 "not a refunded void"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Re-derive mis-voided golf scalar settlements "
                    "(writes a NEW file; never edits trade history)")
    ap.add_argument("--log", action="append", default=[],
                    help="trade log path or glob (repeatable)")
    ap.add_argument("--pod", default="P-017")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cache", default="data/corrections/_market_cache")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    patterns = args.log or [
        "data/trade_logs/trade_log.jsonl",
        "data/trade_logs/archive/*.jsonl",
        f"data/pods/{args.pod}.jsonl",
    ]

    suspects = find_suspects(patterns, args.pod)
    print(f"{args.pod}: {len(suspects)} settlement row(s) booked "
          f"resolution_source={BUGGY_SOURCE!r}")
    if not suspects:
        print("  nothing to correct — either the pod has not settled a "
              "scalar yet, or the logs searched do not contain its history.")
        print(f"  searched: {patterns}")
        return 0

    cache = MarketCache(Path(args.cache))
    corrections = []
    for row in suspects:
        ticker = row.get("market_id") or row.get("market_ticker") or ""
        market = cache.get(ticker)
        if market is None:
            print(f"  ! {ticker}: market not returned by Kalshi — skipped")
            continue
        corrections.append(correct_row(row, market))

    out_path = Path(args.out or (
        f"data/corrections/{args.pod}_scalar_corrections_"
        f"{datetime.now(tz=timezone.utc):%Y%m%d}.jsonl"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.resolve() in {Path(p).resolve()
                              for p in glob.glob(patterns[0])}:
        print("REFUSING to write over a trade log", file=sys.stderr)
        return 2
    with out_path.open("w", encoding="utf-8") as fh:
        for c in corrections:
            fh.write(json.dumps(c) + "\n")

    fixed = [c for c in corrections if c["status"] == "corrected"]
    delta = sum(c["pnl_delta_usd"] or 0 for c in fixed)
    booked = sum(c["booked_pnl_usd"] for c in fixed)
    print(f"  API fetches {cache.fetched}, cache hits {cache.cached}")
    print(f"  corrected   {len(fixed)} / {len(corrections)}")
    print(f"  booked P&L on those rows  : ${booked:+,.2f}")
    print(f"  corrected P&L on those rows: "
          f"${booked + delta:+,.2f}")
    print(f"  ** P&L DELTA               : ${delta:+,.2f} **")
    wins = sum(1 for c in fixed if c["corrected_outcome"] == "WIN")
    print(f"  outcome reclassification  : {len(fixed)} VOID -> "
          f"{wins} WIN / {len(fixed)-wins} LOSS "
          f"(these rows now COUNT in the gate sample)")
    for c in corrections:
        if c["status"] == "scalar_no_value":
            print(f"  ! {c['market_id']}: scalar with no settlement value — "
                  "needs manual review, NOT auto-corrected")
    print(f"  written -> {out_path}  (trade history untouched)")

    if args.json:
        print(json.dumps({"n": len(corrections), "corrected": len(fixed),
                          "pnl_delta_usd": round(delta, 2),
                          "out": str(out_path)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
