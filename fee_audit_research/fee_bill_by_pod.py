"""
fee_audit_research/fee_bill_by_pod.py
─────────────────────────────────────
The LIVE fee bill by pod, computed from the production trade log.

Companion to the P-017A same-direction maker study
(`golf_research/REPORT_P017_Maker_2026-07-30.md`), which asked what the taker
fee actually costs and whether making could recover it. That study answered it
for one pod on backtest data; this answers it for the whole live book.

**Runs on the droplet**, against the DEPLOYED `src/kalshi_fees.py`, so the fee
model is exactly production's rather than a local copy that might have drifted:

    scp fee_audit_research/fee_bill_by_pod.py root@129.212.176.202:/tmp/
    ssh root@129.212.176.202 "cd /opt/betting-pod-shop && python3 /tmp/fee_bill_by_pod.py" > out.json

It is strictly READ-ONLY. It can also run locally with
`--repo-root . --data-dir data/trade_logs`, but the local trade log is stale
(2026-03-13), which is why this exists as a remote pull.

Method — and the four things that will bite anyone redoing this
───────────────────────────────────────────────────────────────
1. **Trade records carry NO fee field.** The bill is computed:
       contracts = position_size_usd / fill_price
       fee       = fee_per_contract(fill_price, maker, series) * contracts

2. **Most files in `data/trade_logs/` are SNAPSHOTS, not history.** Only
   `trade_log.jsonl`, `trade_log.archive_*.jsonl.gz` and `archive/2026-*.jsonl.gz`
   are distinct. The `.bak*`, `*pre-compact*` and `*pre_cleanup*` files are
   copies of those same records — including them multiplies the bill several
   times over. Deduped by `fingerprint` as a second line of defence.

3. **The live `trade_log.jsonl` holds almost nothing.** Rotation moves history
   out, so it carried 73 of the 3766 Kalshi PLACED trades at time of writing.
   Reading it alone understates the bill by ~98%.

4. **41% of PLACED trades are POLYMARKET, and Kalshi's fee formula must not
   touch them.** The `venue` field is authoritative — it disagrees with a
   "starts with 0x" ticker heuristic on 233 rows. Rows with no `venue` field
   all carry KX* tickers and belong to P-001 / pre-pod_id records, so they
   default to kalshi. The first version of this script skipped this gate and
   charged Kalshi fees to P-006, which trades Polymarket exclusively.

Maker fees come from `maker_fills.jsonl` (the standalone P-016 / P-017M
engines, which do not write to `trade_log`), with `shadow` fills excluded.

Run:  python3 fee_bill_by_pod.py [--data-dir ...] [--repo-root ...] [--json]
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List

DEFAULT_ROOT = "/opt/betting-pod-shop"


def opener(p: str):
    return gzip.open(p, "rt") if p.endswith(".gz") else open(p, "r")


def series_of(t: Any) -> str:
    return (t or "").split("-")[0]


def canonical_sources(d: str) -> List[str]:
    """The distinct trade-log files. See note 2 in the module docstring."""
    return ([os.path.join(d, "trade_log.jsonl")]
            + sorted(glob.glob(os.path.join(d, "trade_log.archive_*.jsonl.gz")))
            + sorted(glob.glob(os.path.join(d, "archive", "2026-*.jsonl.gz"))))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=DEFAULT_ROOT)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args(argv)

    sys.path.insert(0, args.repo_root)
    from src.kalshi_fees import (fee_per_contract,          # noqa: E402
                                 series_maker_charges_fee)

    D = args.data_dir or os.path.join(args.repo_root, "data", "trade_logs")

    seen: set = set()
    pods: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    series_roll: Dict[str, Dict[str, float]] = defaultdict(
        lambda: defaultdict(float))
    nonkalshi: Dict[Any, Dict[str, float]] = defaultdict(
        lambda: defaultdict(float))
    dates: Dict[str, List[Any]] = defaultdict(lambda: [None, None])
    modes: Dict[str, int] = defaultdict(int)
    per_file: Dict[str, int] = {}
    bad: Dict[str, int] = defaultdict(int)
    bad_pods: Dict[str, int] = defaultdict(int)

    for path in canonical_sources(D):
        if not os.path.exists(path):
            continue
        n_here = 0
        with opener(path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    bad["unparseable"] += 1
                    continue
                if r.get("action") != "PLACED":
                    continue
                fp = r.get("fingerprint")
                if fp:
                    if fp in seen:
                        continue
                    seen.add(fp)
                pod = r.get("pod_id") or "?"
                px, usd = r.get("fill_price"), r.get("position_size_usd")
                if (not isinstance(px, (int, float))
                        or not isinstance(usd, (int, float))
                        or not (0.0 < px < 1.0) or usd <= 0):
                    bad["no_usable_price_or_size"] += 1
                    bad_pods[pod] += 1
                    continue
                venue = (r.get("venue") or "kalshi").lower()
                ser = series_of(r.get("market_ticker"))
                cts = usd / px
                if venue != "kalshi":
                    nk = nonkalshi[(pod, venue)]
                    nk["n_trades"] += 1
                    nk["contracts"] += cts
                    nk["notional_usd"] += usd
                    nk["px_x_cts"] += px * cts
                    continue
                modes[r.get("mode") or "?"] += 1
                tk = fee_per_contract(px, maker=False, series_ticker=ser) * cts
                mk = fee_per_contract(px, maker=True, series_ticker=ser) * cts
                v = pods[pod]
                v["n_trades"] += 1
                v["contracts"] += cts
                v["notional_usd"] += usd
                v["taker_fee_usd"] += tk
                v["maker_fee_if_usd"] += mk
                v["px_x_cts"] += px * cts
                if series_maker_charges_fee(ser):
                    v["taker_fee_on_charging_usd"] += tk
                else:
                    v["taker_fee_on_free_usd"] += tk
                s = series_roll[ser]
                s["n_trades"] += 1
                s["contracts"] += cts
                s["taker_fee_usd"] += tk
                s["px_x_cts"] += px * cts
                ts = r.get("timestamp_utc") or ""
                dd = dates[pod]
                if ts:
                    if dd[0] is None or ts < dd[0]:
                        dd[0] = ts
                    if dd[1] is None or ts > dd[1]:
                        dd[1] = ts
                n_here += 1
        per_file[os.path.basename(path)] = n_here

    # ── maker side: the standalone engines ───────────────────────────────
    mkpods: Dict[str, Dict[str, float]] = defaultdict(
        lambda: defaultdict(float))
    shadow = 0
    mkpath = os.path.join(D, "maker_fills.jsonl")
    seen_fill: set = set()
    if os.path.exists(mkpath):
        with open(mkpath) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("type") != "FILL":
                    continue
                fid = r.get("fill_id")
                if fid:
                    if fid in seen_fill:
                        continue
                    seen_fill.add(fid)
                if r.get("shadow"):
                    shadow += 1
                    continue
                px, qty = r.get("price"), r.get("qty")
                if (not isinstance(px, (int, float))
                        or not isinstance(qty, (int, float))
                        or not (0.0 < px < 1.0) or qty <= 0):
                    continue
                ser = series_of(r.get("ticker"))
                m = mkpods[r.get("pod_id") or "?"]
                m["n_fills"] += 1
                m["contracts"] += qty
                m["notional_usd"] += px * qty
                m["maker_fee_usd"] += fee_per_contract(
                    px, maker=True, series_ticker=ser) * qty
                m["px_x_cts"] += px * qty
                m["_charges"] = 1.0 if series_maker_charges_fee(ser) else 0.0

    # ── assemble ─────────────────────────────────────────────────────────
    def rd(x, n=2):
        return round(x, n)

    out: Dict[str, Any] = {
        "_generated_by": "fee_audit_research/fee_bill_by_pod.py",
        "_venue_note": "Kalshi fee model applied ONLY to venue=kalshi rows.",
        "n_unique_placed_all_venues": len(seen),
        "modes": dict(modes),
        "taker_by_pod_kalshi": {}, "non_kalshi_excluded": {},
        "maker_by_pod": {}, "by_series_kalshi": {},
        "files_scanned": per_file,
        "excluded_rows": dict(bad), "excluded_rows_by_pod": dict(bad_pods),
        "maker_shadow_fills_excluded": shadow,
    }
    tot_t = tot_m = 0.0
    for pod, v in sorted(pods.items()):
        tot_t += v["taker_fee_usd"]
        tot_m += v["maker_fee_if_usd"]
        out["taker_by_pod_kalshi"][pod] = {
            "n_trades": int(v["n_trades"]),
            "contracts": rd(v["contracts"], 1),
            "notional_usd": rd(v["notional_usd"]),
            "taker_fee_usd": rd(v["taker_fee_usd"]),
            "fee_cents_per_contract": rd(
                100 * v["taker_fee_usd"] / v["contracts"], 4),
            "vwap": rd(v["px_x_cts"] / v["contracts"], 4),
            "fee_pct_of_notional": rd(
                100 * v["taker_fee_usd"] / v["notional_usd"], 3),
            "maker_fee_if_made_usd": rd(v["maker_fee_if_usd"]),
            "recoverable_usd": rd(v["taker_fee_usd"] - v["maker_fee_if_usd"]),
            "recoverable_pct": rd(
                100 * (v["taker_fee_usd"] - v["maker_fee_if_usd"])
                / v["taker_fee_usd"], 1) if v["taker_fee_usd"] else None,
            "taker_fee_on_maker_free_series_usd": rd(
                v["taker_fee_on_free_usd"]),
            "taker_fee_on_maker_charging_series_usd": rd(
                v["taker_fee_on_charging_usd"]),
            "first_utc": dates[pod][0], "last_utc": dates[pod][1],
        }
    out["totals"] = {
        "taker_fee_usd": rd(tot_t), "maker_fee_if_made_usd": rd(tot_m),
        "recoverable_usd": rd(tot_t - tot_m),
        "recoverable_pct": rd(100 * (tot_t - tot_m) / tot_t, 1) if tot_t else None,
    }
    for (pod, ven), v in sorted(nonkalshi.items()):
        out["non_kalshi_excluded"][f"{pod}@{ven}"] = {
            "n_trades": int(v["n_trades"]),
            "contracts": rd(v["contracts"], 1),
            "notional_usd": rd(v["notional_usd"]),
            "vwap": rd(v["px_x_cts"] / v["contracts"], 4),
            "note": "Kalshi fee model NOT applied — other venue, other schedule",
        }
    for pod, v in sorted(mkpods.items()):
        out["maker_by_pod"][pod] = {
            "n_fills": int(v["n_fills"]),
            "contracts": rd(v["contracts"], 1),
            "notional_usd": rd(v["notional_usd"]),
            "maker_fee_usd": rd(v["maker_fee_usd"]),
            "fee_cents_per_contract": rd(
                100 * v["maker_fee_usd"] / v["contracts"], 4),
            "vwap": rd(v["px_x_cts"] / v["contracts"], 4),
            "series_charges_maker_fee": bool(v["_charges"]),
        }
    for ser, v in sorted(series_roll.items(),
                         key=lambda kv: -kv[1]["taker_fee_usd"])[:15]:
        out["by_series_kalshi"][ser] = {
            "n_trades": int(v["n_trades"]),
            "contracts": rd(v["contracts"], 1),
            "taker_fee_usd": rd(v["taker_fee_usd"]),
            "fee_cents_per_contract": rd(
                100 * v["taker_fee_usd"] / v["contracts"], 4),
            "vwap": rd(v["px_x_cts"] / v["contracts"], 4),
            "maker_would_charge": series_maker_charges_fee(ser),
        }

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    print(f"unique PLACED (all venues): {out['n_unique_placed_all_venues']}   "
          f"modes: {dict(modes)}")
    if set(modes) == {"paper"}:
        print("  *** ALL PAPER — this is a NOTIONAL bill. No money was paid. ***")
    print(f"\n{'pod':8} {'trades':>7} {'contracts':>10} {'notional':>10} "
          f"{'FEE $':>9} {'c/ct':>7} {'vwap':>7} {'recover $':>10} {'%':>6}  window")
    for pod, v in out["taker_by_pod_kalshi"].items():
        print(f"{pod:8} {v['n_trades']:>7} {v['contracts']:>10.0f} "
              f"{v['notional_usd']:>10.0f} {v['taker_fee_usd']:>9.2f} "
              f"{v['fee_cents_per_contract']:>7.3f} {v['vwap']:>7.3f} "
              f"{v['recoverable_usd']:>10.2f} {v['recoverable_pct']:>5.1f}%  "
              f"{(v['first_utc'] or '')[:10]}..{(v['last_utc'] or '')[:10]}")
    t = out["totals"]
    print(f"{'TOTAL':8} {'':>7} {'':>10} {'':>10} {t['taker_fee_usd']:>9.2f} "
          f"{'':>7} {'':>7} {t['recoverable_usd']:>10.2f} "
          f"{t['recoverable_pct']:>5.1f}%")
    print("\nnon-Kalshi (excluded from the fee model):")
    for k, v in out["non_kalshi_excluded"].items():
        print(f"  {k:20} {v['n_trades']:>5} trades  ${v['notional_usd']:>9.0f}"
              f"  vwap {v['vwap']:.3f}")
    print("\nmaker side (standalone engines):")
    for k, v in out["maker_by_pod"].items():
        print(f"  {k:8} {v['n_fills']:>5} fills {v['contracts']:>8.1f} cts  "
              f"FEE ${v['maker_fee_usd']:>7.2f}  "
              f"{v['fee_cents_per_contract']:.4f}c/ct  vwap {v['vwap']:.4f}  "
              f"charges={v['series_charges_maker_fee']}")
    print(f"  ({shadow} shadow fills excluded)")
    print(f"\ntop Kalshi series by fee paid:")
    print(f"  {'series':24} {'trades':>7} {'contracts':>10} {'FEE $':>9} "
          f"{'c/ct':>7} {'vwap':>7}  maker_charges")
    for k, v in out["by_series_kalshi"].items():
        print(f"  {k:24} {v['n_trades']:>7} {v['contracts']:>10.0f} "
              f"{v['taker_fee_usd']:>9.2f} {v['fee_cents_per_contract']:>7.3f} "
              f"{v['vwap']:>7.3f}  {v['maker_would_charge']}")
    if bad:
        print(f"\nEXCLUDED rows: {dict(bad)}  by pod: {dict(bad_pods)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
