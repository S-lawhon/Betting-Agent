"""
golf_quirks_research/classify_anchor_books.py
─────────────────────────────────────────────
P-022 — was the validated edge measured on TWO-SIDED books?

The 2026-07-29 pre-flight found that every in-band live reference at
window-open is a ONE-SIDED ask (`yes_bid: null`). The fund's standing rule
since R3 is "two-sided quotes only". So: is one-sided the norm for this
family — in which case the pod is consistent with its own evidence — or is
the live population a population the backtest never measured?

Answered from the settled data, using the HARNESS'S OWN anchor logic
(`quirks_common.anchor_at` / `candle_price`), never a reimplementation. The
P-001 lesson is that a second reader of a quantity meant to be unambiguous
produces a second, subtly different number.

Two questions, kept apart on purpose:

  A. Which ROUTE produced each anchor the backtest actually used —
     `candle_price` prefers an executed trade price, and falls back to the
     mid of a TIGHT two-sided quote. Those are the only two routes; there is
     no third.
  B. What did the book LOOK LIKE at the anchor instant T = close - H, whether
     or not the harness could use it. This is the live-comparable quantity,
     because the pod prices off whatever is on the screen at T.

    python3 classify_anchor_books.py --hours 12 --offset 0.02
    python3 classify_anchor_books.py --json anchor_book_classes.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quirks_common as qc  # noqa: E402
import backtest_fade_fills as bf  # noqa: E402


def classify_candle(c: Dict[str, Any],
                    max_spread: float = qc.ANCHOR_MAX_SPREAD) -> str:
    """What this candle's book is, in the same terms the live pod would see.

    Mirrors the STRUCTURE of `quirks_common.candle_price` — deliberately, so
    the classes partition exactly the accept/reject decision that function
    makes — but returns the reason rather than a price.
    """
    traded = (c.get("price") or {}).get("close_dollars")
    vol = qc.fnum(c.get("volume_fp")) or 0.0
    has_trade = traded is not None and vol > 0 and qc.fnum(traded) is not None

    bid = qc.fnum((c.get("yes_bid") or {}).get("close_dollars"))
    ask = qc.fnum((c.get("yes_ask") or {}).get("close_dollars"))
    bid_ok = bid is not None and bid > 0
    ask_ok = ask is not None and ask < 1

    if has_trade:
        # The harness takes the trade price and never looks at the book, so
        # record the book alongside it for the live comparison.
        if bid_ok and ask_ok:
            return "traded_two_sided"
        if ask_ok and not bid_ok:
            return "traded_one_sided_ask"
        if bid_ok and not ask_ok:
            return "traded_one_sided_bid"
        return "traded_no_book"

    if bid is None or ask is None:
        return "no_book"
    if not bid_ok and ask_ok:
        return "one_sided_ask"          # yes_bid absent or 0 — the live case
    if bid_ok and not ask_ok:
        return "one_sided_bid"
    if not bid_ok and not ask_ok:
        return "no_book"
    if ask < bid:
        return "crossed"
    if (ask - bid) > max_spread:
        return "two_sided_wide"         # two-sided but rejected by max_spread
    return "two_sided_tight"            # the ONLY quote-derived anchor route


ACCEPTED = {"traded_two_sided", "traded_one_sided_ask", "traded_one_sided_bid",
            "traded_no_book", "two_sided_tight"}


def last_candle_at_or_before(rec: Dict[str, Any],
                             target: float) -> Optional[Dict[str, Any]]:
    """The book as it stood at T, usable or not — the live-comparable view."""
    best = None
    for c in rec.get("candles") or []:
        end = c.get("end_period_ts")
        if end is None or end > target:
            continue
        if best is None or end > best.get("end_period_ts", -1):
            best = c
    return best


def analyse(recs: Sequence[Dict[str, Any]], hours: float, offset: float,
            quote_size: float = 25.0) -> Dict[str, Any]:
    # ── the outcomes, from the harness itself ──
    res = qc.replay(recs, "sell_yes", hours, offset, quote_size=quote_size,
                    collect_per_market=True)
    per_market = {m["ticker"]: m for m in res["per_market"]}

    anchor_route: Dict[str, str] = {}
    book_at_t: Dict[str, str] = {}
    anchor_lag_h: Dict[str, float] = {}
    skipped: List[Tuple[str, str]] = []

    for rec in recs:
        tk = rec["ticker"]
        cand = rec["_candles"]
        close = qc.iso_epoch(cand.get("close_time"))
        if close is None:
            continue
        target = close - hours * 3600.0

        # A. the route the harness used — found by asking the harness where
        # its anchor is, then classifying THAT candle.
        a = qc.anchor_at(cand, hours)
        if a is None:
            last = last_candle_at_or_before(cand, target)
            skipped.append((tk, classify_candle(last) if last else "no_candle"))
        else:
            anchor_end = a[0]
            hit = None
            for c in cand.get("candles") or []:
                if c.get("end_period_ts") == anchor_end:
                    hit = c
                    break
            anchor_route[tk] = classify_candle(hit) if hit else "unknown"
            anchor_lag_h[tk] = (target - anchor_end) / 3600.0

        # B. what was on the screen at T, usable or not
        last = last_candle_at_or_before(cand, target)
        book_at_t[tk] = classify_candle(last) if last else "no_candle"

    def tally(d: Dict[str, str]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for v in d.values():
            out[v] = out.get(v, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    # ── B: split the measured edge by class, tournament-clustered ──
    def edge_for(tickers: Sequence[str]) -> Dict[str, Any]:
        keep = set(tickers)
        sub = [r for r in recs if r["ticker"] in keep]
        if not sub:
            return {"posted": 0, "filled": 0, "contracts": 0.0,
                    "net_c": None, "ci": None, "tournaments": 0}
        r = qc.replay(sub, "sell_yes", hours, offset, quote_size=quote_size)
        return {
            "posted": r["posted_markets"],
            "filled": r["filled_markets"],
            "contracts": r["contracts_filled"],
            "net_c": (None if r["net_per_contract"] is None
                      else round(r["net_per_contract"] * 100, 2)),
            "ci": (None if r["ci95_lo"] is None else
                   [round(r["ci95_lo"] * 100, 2), round(r["ci95_hi"] * 100, 2)]),
            "tournaments": r["n_fill_tournaments"],
        }

    by_route: Dict[str, Any] = {}
    for cls in sorted(set(anchor_route.values())):
        tks = [t for t, c in anchor_route.items() if c == cls]
        by_route[cls] = edge_for(tks) | {"n_markets": len(tks)}

    by_book_at_t: Dict[str, Any] = {}
    for cls in sorted(set(book_at_t.values())):
        tks = [t for t, c in book_at_t.items()
               if c == cls and t in anchor_route]
        if not tks:
            continue
        by_book_at_t[cls] = edge_for(tks) | {"n_markets": len(tks)}

    lags = sorted(anchor_lag_h.values())
    return {
        "hours": hours, "offset": offset,
        "n_recs": len(recs),
        "headline": {
            "posted": res["posted_markets"], "filled": res["filled_markets"],
            "contracts": res["contracts_filled"],
            "net_c": round(res["net_per_contract"] * 100, 2),
            "ci": [round(res["ci95_lo"] * 100, 2),
                   round(res["ci95_hi"] * 100, 2)],
            "tournaments": res["n_fill_tournaments"],
            "skipped_no_anchor": res["skipped_no_anchor"],
        },
        "anchor_route_counts": tally(anchor_route),
        "book_at_T_counts": tally(book_at_t),
        "skipped_reason_counts": tally(dict(skipped)),
        "edge_by_anchor_route": by_route,
        "edge_by_book_at_T": by_book_at_t,
        "anchor_lag_h": {
            "n": len(lags),
            "median": round(lags[len(lags) // 2], 2) if lags else None,
            "p90": round(lags[int(len(lags) * 0.9)], 2) if lags else None,
            "max": round(lags[-1], 2) if lags else None,
        },
        "n_posted_by_market": len(per_market),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--offset", type=float, default=0.02)
    ap.add_argument("--universe", choices=("pinned", "all"), default="pinned")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    recs = bf.load()
    if args.universe == "pinned":
        recs, n_ex, missing = bf.restrict_to_pinned(recs)
        if missing:
            print(f"WARNING: {len(missing)} pinned markets missing",
                  file=sys.stderr)
        print(f"universe: pinned, {len(recs)} markets ({n_ex} excluded)")
    else:
        print(f"universe: all, {len(recs)} markets")

    out = analyse(recs, args.hours, args.offset)
    h = out["headline"]
    print(f"\nheadline H={args.hours:g} off={args.offset:+.2f}: "
          f"{h['posted']} posted / {h['filled']} filled / "
          f"{h['contracts']:.0f} ct  {h['net_c']:+.2f}c/ct "
          f"CI [{h['ci'][0]:+.2f}, {h['ci'][1]:+.2f}]  "
          f"({h['skipped_no_anchor']} skipped: no clean anchor)")

    print("\nA. anchor ROUTE the harness actually used")
    for k, v in out["anchor_route_counts"].items():
        print(f"    {k:24s} {v:5d}")
    print("\n   why the skipped markets had no anchor")
    for k, v in out["skipped_reason_counts"].items():
        print(f"    {k:24s} {v:5d}")

    print("\nB. what the BOOK looked like at T = close - H (live-comparable)")
    for k, v in out["book_at_T_counts"].items():
        print(f"    {k:24s} {v:5d}")

    print("\nC. net edge by anchor route (tournament-clustered CI)")
    hdr = f"    {'class':24s} {'posted':>7} {'filled':>7} {'ct':>8} {'net c':>8}  CI"
    print(hdr)
    for k, v in out["edge_by_anchor_route"].items():
        ci = "—" if v["ci"] is None else f"[{v['ci'][0]:+.2f}, {v['ci'][1]:+.2f}]"
        net = "—" if v["net_c"] is None else f"{v['net_c']:+.2f}"
        print(f"    {k:24s} {v['posted']:>7} {v['filled']:>7} "
              f"{v['contracts']:>8.0f} {net:>8}  {ci}")

    print("\nD. net edge by book-at-T class")
    print(hdr)
    for k, v in out["edge_by_book_at_T"].items():
        ci = "—" if v["ci"] is None else f"[{v['ci'][0]:+.2f}, {v['ci'][1]:+.2f}]"
        net = "—" if v["net_c"] is None else f"{v['net_c']:+.2f}"
        print(f"    {k:24s} {v['posted']:>7} {v['filled']:>7} "
              f"{v['contracts']:>8.0f} {net:>8}  {ci}")

    lag = out["anchor_lag_h"]
    print(f"\nanchor staleness (T - anchor): median {lag['median']}h  "
          f"p90 {lag['p90']}h  max {lag['max']}h")

    if args.json:
        path = (args.json if os.path.isabs(args.json)
                else os.path.join(qc.HERE, args.json))
        with open(path, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
