"""
golf_research/backtest_p017_maker.py
────────────────────────────────────
P-017A **same-direction maker** — does resting a YES bid at the pre-tournament
anchor beat lifting the ask, once non-fill is paid for?

Pre-registered in `golf_research/P017_MAKER_PREDECLARATION.md`, committed
BEFORE any effect number was computed. Read that file first; this script only
executes what it declares.

This is NOT Leg B. Leg B rested a YES *offer* (short YES, a fade) and returned
+3.34c with a CI straddling zero. This rests a YES *bid* — the same side Leg A
takes — so the only variable that changes versus Leg A is execution.

Design, in one paragraph
  The universe, the anchor candle, and the settlement are Leg A's, taken from
  Leg A's own `golf_research/candles.jsonl` (the rule reproduces the published
  n=1149 at the shipped 0.08-0.45 band exactly). On each market BOTH arms are
  computed from that one anchor: the taker buys Q=25 at the anchor ask and
  pays the taker fee; the maker rests a bid for Q and fills only on public
  prints that come to it. The primary statistic is the PAIRED per-market
  difference, because Leg A's own CI spans 5.6c and cannot resolve a ~1c
  effect by comparing two independent means.

Fill model
  Reused, not rebuilt. `quirks_common.replay(side="buy_yes")` already rests a
  YES bid and fills on `taker_side="no"` prints strictly through the quote at
  zero maker fee. Two additive parameters were added to it for this study
  (`quote_px_by_ticker`, `collect_per_market`); both default to the previous
  behaviour and were verified to leave `backtest_fade_fills.py --validate`
  byte-identical.

Run:  python3 golf_research/backtest_p017_maker.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "golf_quirks_research"))

import quirks_common as qc                       # noqa: E402
from src.kalshi_fees import fee_per_contract     # noqa: E402

SERIES = ("KXPGATOP10", "KXPGATOP20")
BAND = (0.08, 0.45)          # the SHIPPED band (p017_params.json)
MAX_SPREAD = 0.06            # Leg A's screen
DAYS = (4.0, 10.0)           # Leg A's anchor window
QUOTE_SIZE = 25.0            # intended size, identical in both arms
SECONDS_PER_DAY = 86400.0

CANDLES = os.path.join(_HERE, "candles.jsonl")
TAPE = os.path.join(_ROOT, "golf_quirks_research", "data", "topn_full_trades")

# Quote placements (pre-registered, all reported).
PLACEMENTS = ("bid", "mid-0.00", "mid-0.02", "mid-0.04")
# Cancel times, hours before close. 96h = the far edge of Leg A's own window,
# so the primary quote never rests inside the tournament.
WINDOWS = (96.0, 48.0, 0.0)
PRIMARY = ("bid", 96.0)


# ── Leg A universe (Leg A's own rule, on Leg A's own data) ───────────────

def _f(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _iso_epoch(s: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError, TypeError):
        return None


def two_sided(c: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Leg A's `two_sided`: (yes_bid, yes_ask) close, or None."""
    a = _f((c.get("yes_ask") or {}).get("close_dollars"))
    b = _f((c.get("yes_bid") or {}).get("close_dollars"))
    if a is None or b is None or b <= 0 or a >= 1 or a < b:
        return None
    return b, a


def build_leg_a_universe(path: str) -> List[Dict[str, Any]]:
    """Every market Leg A would bet, with its anchor candle attached."""
    out: List[Dict[str, Any]] = []
    with open(path) as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in rec or not rec.get("candles"):
                continue
            ticker = rec["ticker"]
            series = ticker.split("-")[0]
            if series not in SERIES:
                continue
            close = _iso_epoch(rec.get("close_time"))
            if close is None:
                continue
            best = None
            for c in rec["candles"]:
                end = c.get("end_period_ts")
                if end is None:
                    continue
                dtc = (close - end) / SECONDS_PER_DAY
                if not (DAYS[0] <= dtc <= DAYS[1]):
                    continue
                if two_sided(c) is None:
                    continue
                if best is None or end > best["end_period_ts"]:
                    best = c
            if best is None:
                continue
            bid, ask = two_sided(best)
            if (ask - bid) > MAX_SPREAD:
                continue
            if not (BAND[0] <= ask <= BAND[1]):
                continue
            result = rec.get("result")
            out.append({
                "ticker": ticker,
                "series": series,
                "event": rec.get("event", ""),
                "tournament": qc.tournament_of(rec.get("event", "")),
                "close_time": rec["close_time"],
                "close_epoch": close,
                "anchor_epoch": float(best["end_period_ts"]),
                "anchor_bid": bid,
                "anchor_ask": ask,
                "anchor_mid": (bid + ask) / 2.0,
                "anchor_spread": ask - bid,
                "result": result,
                # Verified in the pre-declaration: this universe contains ZERO
                # scalar rows, so binary settlement is exact, not a convention.
                "settlement_value": 1.0 if result == "yes" else 0.0,
                "anchor_days_to_close": (close - float(best["end_period_ts"]))
                / SECONDS_PER_DAY,
                "_anchor_candle": best,
            })
    out.sort(key=lambda r: r["ticker"])
    return out


def taker_net_per_contract(m: Dict[str, Any]) -> float:
    """Leg A: buy YES at the anchor ask, pay the taker fee."""
    ask = m["anchor_ask"]
    fee = fee_per_contract(ask, maker=False, series_ticker=m["series"])
    return m["settlement_value"] - ask - fee


# ── join to the tick tape ────────────────────────────────────────────────

def attach_tape(universe: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Records shaped for `qc.replay`, for the markets that have a tape.

    `_candles` is Leg A's anchor candle ALONE, so `qc.anchor_at` inside the
    replay resolves to exactly the candle Leg A priced off and
    `post_at_anchor=True` posts at the moment Leg A transacted.
    """
    out: List[Dict[str, Any]] = []
    for m in universe:
        p = os.path.join(TAPE, m["ticker"] + ".json")
        if not os.path.exists(p):
            continue
        try:
            with open(p) as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        trades = rec.get("trades") or []
        out.append({
            "ticker": m["ticker"],
            "event": m["event"],
            "tournament": m["tournament"],
            "close_time": m["close_time"],
            "result": m["result"],
            "settlement_value_dollars": m["settlement_value"],
            "trades": trades,
            "_candles": {"ticker": m["ticker"],
                         "close_time": m["close_time"],
                         "candles": [m["_anchor_candle"]]},
        })
    return out


def quote_prices(universe: Sequence[Dict[str, Any]],
                 placement: str) -> Dict[str, float]:
    """Per-market resting bid price, rounded to the cent (Kalshi's tick)."""
    out: Dict[str, float] = {}
    for m in universe:
        if placement == "bid":
            px = m["anchor_bid"]
        else:
            off = float(placement.split("-")[1])
            px = m["anchor_mid"] - off
        px = round(px, 2)
        if 0.0 < px < 1.0:
            out[m["ticker"]] = px
    return out


# ── the paired comparison ────────────────────────────────────────────────

def paired(res: Dict[str, Any], by_ticker: Dict[str, Dict[str, Any]],
           quote_size: float) -> Dict[str, Any]:
    """Δ = maker − taker, per posted market, per intended contract.

    Both arms are evaluated on the SAME market from the SAME anchor. The
    taker's fill of all `quote_size` contracts is an ASSUMPTION (Leg A models
    no fill risk); it is generous to the taker and is reported as such.
    """
    diffs: List[Tuple[str, float]] = []       # (tournament, Δ in dollars/ct)
    maker_ct: List[Tuple[str, float]] = []
    taker_ct: List[Tuple[str, float]] = []
    filled_contracts = 0.0
    posted_contracts = 0.0
    sv_posted: List[float] = []
    sv_filled_num = 0.0
    rows = []
    for pm in res.get("per_market", []):
        m = by_ticker[pm["ticker"]]
        t_ct = taker_net_per_contract(m)
        taker_usd = quote_size * t_ct
        maker_usd = pm["pnl_usd"]
        d_ct = (maker_usd - taker_usd) / quote_size
        diffs.append((pm["tournament"], d_ct))
        taker_ct.append((pm["tournament"], t_ct))
        posted_contracts += quote_size
        filled_contracts += pm["filled"]
        sv_posted.append(pm["settlement_value"])
        if pm["filled"] > 0:
            sv_filled_num += pm["settlement_value"] * pm["filled"]
            maker_ct.append((pm["tournament"],
                             maker_usd / pm["filled"]))
        rows.append({"ticker": pm["ticker"], "tournament": pm["tournament"],
                     "quote_px": pm["quote_px"], "ask": m["anchor_ask"],
                     "sv": pm["settlement_value"], "filled": pm["filled"],
                     "maker_usd": round(maker_usd, 4),
                     "taker_usd": round(taker_usd, 4),
                     "delta_ct": round(d_ct, 6)})

    d_mean, d_lo, d_hi = qc.bootstrap_unweighted(diffs)
    t_mean, t_lo, t_hi = qc.bootstrap_unweighted(taker_ct)
    phi = filled_contracts / posted_contracts if posted_contracts else 0.0
    maker_fill_mean = (sum(v for _, v in maker_ct) / len(maker_ct)
                       if maker_ct else None)
    return {
        "posted_markets": len(rows),
        "filled_markets": res["filled_markets"],
        "fill_rate_markets": (round(res["filled_markets"] / len(rows), 4)
                              if rows else None),
        "contracts_intended": round(posted_contracts, 1),
        "contracts_filled": round(filled_contracts, 1),
        "phi_contract_fill_fraction": round(phi, 4),
        # secondary: the seductive number
        "maker_net_ct_on_fills": qc.r4(res["net_per_contract"]),
        "maker_net_ct_on_fills_ci": [qc.r4(res["ci95_lo"]),
                                     qc.r4(res["ci95_hi"])],
        "maker_net_ct_on_fills_unweighted": qc.r4(maker_fill_mean),
        # taker baseline on the SAME markets
        "taker_net_ct_paired": qc.r4(t_mean),
        "taker_net_ct_paired_ci": [qc.r4(t_lo), qc.r4(t_hi)],
        # PRIMARY
        "delta_ct_per_posted_per_intended": qc.r4(d_mean),
        "delta_ci95": [qc.r4(d_lo), qc.r4(d_hi)],
        "delta_usd_per_posted_market": qc.r4(d_mean * quote_size),
        "e_settle_posted": qc.r4(sum(sv_posted) / len(sv_posted))
        if sv_posted else None,
        "e_settle_filled": qc.r4(sv_filled_num / filled_contracts)
        if filled_contracts else None,
        # What the resting bid would earn if EVERY posted market filled at the
        # quote. Isolates the price advantage (bid vs ask, zero fee) from the
        # fill problem; the gap to maker_net_ct_on_fills IS adverse selection.
        "maker_naive_ct_if_all_filled": qc.r4(res["naive_edge_if_all_filled"]),
        "delta_leave_one_out": qc.leave_one_out(
            [(ev, v, 1.0) for ev, v in diffs]),
        "delta_per_tournament": qc.per_tournament(
            [(ev, v, 1.0) for ev, v in diffs]),
        "_rows": rows,
    }


def verdict(p: Dict[str, Any]) -> str:
    d = p["delta_ct_per_posted_per_intended"]
    lo, hi = p["delta_ci95"]
    if p["phi_contract_fill_fraction"] < 0.25:
        return "KILL (contract fill fraction < 25%)"
    if (p["fill_rate_markets"] or 0) < 0.25:
        return "KILL (market fill rate < 25%)"
    if d is None or lo is None or hi is None:
        return "NO DECISION (insufficient data)"
    if d >= 0.01 and lo > 0:
        return "ADVANCE"
    if d <= -0.01 and hi < 0:
        return "KILL"
    return "NO DECISION (CI includes zero or |Δ| < 1c)"


# ── main ─────────────────────────────────────────────────────────────────

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.path.join(_HERE,
                                                  "p017_maker_results.json"))
    ap.add_argument("--quote-size", type=float, default=QUOTE_SIZE)
    args = ap.parse_args(argv)
    Q = args.quote_size

    uni = build_leg_a_universe(CANDLES)
    by_ticker = {m["ticker"]: m for m in uni}
    tourns = sorted({m["tournament"] for m in uni})
    print(f"Leg A universe: {len(uni)} markets, {len(tourns)} tournaments "
          f"(published n=1149 at the 0.08-0.45 band)")
    res_counts = defaultdict(int)
    for m in uni:
        res_counts[m["result"]] += 1
    print(f"  result distribution: {dict(res_counts)}  "
          f"(scalar rows: {res_counts.get('scalar', 0)})")

    # Leg A baseline on the FULL universe — the published comparison.
    full = [(m["tournament"], taker_net_per_contract(m)) for m in uni]
    fm, flo, fhi = qc.bootstrap_unweighted(full)
    print(f"  taker baseline, all {len(uni)}: {fm * 100:+.2f}c "
          f"[{flo * 100:+.2f}, {fhi * 100:+.2f}]  "
          f"(params: +7.14c [+4.30,+9.90])")

    recs = attach_tape(uni)
    have = {r["ticker"] for r in recs}
    missing = [m for m in uni if m["ticker"] not in have]
    print(f"  with tick tape: {len(recs)}  |  without: {len(missing)}")

    # Declared selection diagnostic (pre-declaration §8).
    def _rate(ms):
        return sum(m["settlement_value"] for m in ms) / len(ms) if ms else None
    kept = [m for m in uni if m["ticker"] in have]
    sel = {
        "n_with_tape": len(kept), "n_without_tape": len(missing),
        "settle_rate_with_tape": qc.r4(_rate(kept)),
        "settle_rate_without_tape": qc.r4(_rate(missing)),
        "mean_ask_with_tape": qc.r4(sum(m["anchor_ask"] for m in kept)
                                    / len(kept)) if kept else None,
        "mean_ask_without_tape": qc.r4(sum(m["anchor_ask"] for m in missing)
                                       / len(missing)) if missing else None,
    }
    tm, tlo, thi = qc.bootstrap_unweighted(
        [(m["tournament"], taker_net_per_contract(m)) for m in kept])
    sel["taker_net_ct_with_tape"] = qc.r4(tm)
    sel["taker_net_ct_with_tape_ci"] = [qc.r4(tlo), qc.r4(thi)]
    mm, mlo, mhi = qc.bootstrap_unweighted(
        [(m["tournament"], taker_net_per_contract(m)) for m in missing])
    sel["taker_net_ct_without_tape"] = qc.r4(mm)
    sel["taker_net_ct_without_tape_ci"] = [qc.r4(mlo), qc.r4(mhi)]
    print(f"  SELECTION: settle rate {sel['settle_rate_with_tape']} (tape) "
          f"vs {sel['settle_rate_without_tape']} (no tape); taker "
          f"{(tm or 0) * 100:+.2f}c vs {(mm or 0) * 100:+.2f}c")

    # Anchor contemporaneity (house rule).
    dtc = sorted(m["anchor_days_to_close"] for m in uni)
    spr = sorted(m["anchor_spread"] for m in uni)

    def q(v, p):
        return round(v[int(p * (len(v) - 1))], 4)
    contemporaneity = {
        "anchor_days_to_close": {"min": q(dtc, 0), "p10": q(dtc, .1),
                                 "median": q(dtc, .5), "p90": q(dtc, .9),
                                 "max": q(dtc, 1)},
        "anchor_spread": {"median": q(spr, .5), "p90": q(spr, .9),
                          "max": q(spr, 1)},
        "note": "Both arms transact at the anchor instant, so staleness is "
                "shared. The quote is posted at the anchor candle end "
                "(post_at_anchor=True), never at a later nominal T.",
    }
    print(f"  anchor days-to-close: median {q(dtc, .5)}d "
          f"[{q(dtc, 0)}, {q(dtc, 1)}]; spread median {q(spr, .5)}")

    # ── the grid ─────────────────────────────────────────────────────────
    grid: Dict[str, Any] = {}
    print(f"\n{'placement':>10} {'cancel':>7} {'posted':>7} {'fill mkt':>9} "
          f"{'fill%':>6} {'phi':>6} {'maker c/ct':>11} {'taker c/ct':>11} "
          f"{'DELTA c':>9} {'95% CI':>18} {'E[sv|p]':>8} {'E[sv|f]':>8}")
    print("-" * 128)
    for placement in PLACEMENTS:
        qpx = quote_prices(uni, placement)
        for w in WINDOWS:
            res = qc.replay(recs, "buy_yes", hours=96.0, offset=0.0,
                            quote_size=Q, window_end_h=w,
                            strict=True, post_at_anchor=True,
                            quote_px_by_ticker=qpx, collect_per_market=True)
            p = paired(res, by_ticker, Q)
            key = f"{placement}_cancel{w:g}h"
            rows = p.pop("_rows")
            grid[key] = p
            if placement == PRIMARY[0] and w == PRIMARY[1]:
                grid[key]["_is_primary"] = True
                primary_rows = rows
            ci = p["delta_ci95"]
            cis = ("[     n/a      ]" if ci[0] is None
                   else f"[{ci[0] * 100:+.2f},{ci[1] * 100:+.2f}]")
            print(f"{placement:>10} {w:>6.0f}h {p['posted_markets']:>7} "
                  f"{p['filled_markets']:>9} "
                  f"{(p['fill_rate_markets'] or 0) * 100:>5.1f}% "
                  f"{p['phi_contract_fill_fraction'] * 100:>5.1f}% "
                  f"{(p['maker_net_ct_on_fills'] or 0) * 100:>+11.2f} "
                  f"{(p['taker_net_ct_paired'] or 0) * 100:>+11.2f} "
                  f"{(p['delta_ct_per_posted_per_intended'] or 0) * 100:>+9.2f} "
                  f"{cis:>18} {(p['e_settle_posted'] or 0):>8.3f} "
                  f"{(p['e_settle_filled'] or 0):>8.3f}")

    # Pre-registered robustness: the friendlier at-or-through fill convention
    # (a print AT our price fills us, i.e. we assume queue priority). If the
    # KILL is an artifact of the pessimistic rule, it shows up here.
    print("\nat-or-through (non-strict) robustness — same cells:")
    for placement in ("bid", "mid-0.00"):
        qpx = quote_prices(uni, placement)
        for w in WINDOWS:
            res = qc.replay(recs, "buy_yes", hours=96.0, offset=0.0,
                            quote_size=Q, window_end_h=w,
                            strict=False, post_at_anchor=True,
                            quote_px_by_ticker=qpx, collect_per_market=True)
            p = paired(res, by_ticker, Q)
            p.pop("_rows")
            key = f"{placement}_cancel{w:g}h_atOrThrough"
            grid[key] = p
            ci = p["delta_ci95"]
            cis = ("[     n/a      ]" if ci[0] is None
                   else f"[{ci[0] * 100:+.2f},{ci[1] * 100:+.2f}]")
            print(f"{placement:>10} {w:>6.0f}h "
                  f"fill%={(p['fill_rate_markets'] or 0) * 100:>5.1f} "
                  f"phi={p['phi_contract_fill_fraction'] * 100:>5.1f}% "
                  f"maker={(p['maker_net_ct_on_fills'] or 0) * 100:>+6.2f}c "
                  f"DELTA={(p['delta_ct_per_posted_per_intended'] or 0) * 100:>+6.2f}c "
                  f"{cis}")

    pk = f"{PRIMARY[0]}_cancel{PRIMARY[1]:g}h"
    prim = grid[pk]
    v = verdict(prim)
    print(f"\nPRIMARY cell [{pk}]: Δ = "
          f"{(prim['delta_ct_per_posted_per_intended'] or 0) * 100:+.2f}c "
          f"per posted market per intended contract, CI "
          f"[{(prim['delta_ci95'][0] or 0) * 100:+.2f}, "
          f"{(prim['delta_ci95'][1] or 0) * 100:+.2f}]")
    print(f"VERDICT: {v}")
    loo = prim["delta_leave_one_out"]
    if loo:
        print(f"  leave-one-out Δ: [{min(loo.values()) * 100:+.2f}, "
              f"{max(loo.values()) * 100:+.2f}]c "
              f"({sum(1 for x in loo.values() if x > 0)}/{len(loo)} positive)")

    payload = {
        "generated": "golf_research/backtest_p017_maker.py",
        "predeclaration": "golf_research/P017_MAKER_PREDECLARATION.md",
        "universe": {
            "series": list(SERIES), "band": list(BAND),
            "max_spread": MAX_SPREAD, "anchor_days_to_close": list(DAYS),
            "n_markets": len(uni), "n_tournaments": len(tourns),
            "tournaments": tourns, "result_distribution": dict(res_counts),
            "n_with_tape": len(recs),
        },
        "quote_size": Q,
        "maker_fee": 0.0,
        "maker_fee_source": "src/kalshi_fees.fee_per_contract(series_ticker=) "
                            "-> 0.0 for KXPGATOP10/KXPGATOP20 (quadratic, "
                            "from the generated fixture)",
        "taker_fee_source": "src/kalshi_fees.fee_per_contract(maker=False) "
                            "= 0.07*P*(1-P)",
        "taker_baseline_full_universe": {
            "net_ct": qc.r4(fm), "ci95": [qc.r4(flo), qc.r4(fhi)],
            "n": len(uni),
        },
        "selection_diagnostic": sel,
        "anchor_contemporaneity": contemporaneity,
        "primary_cell": pk,
        "verdict": v,
        "grid": grid,
    }
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
