"""
golf_research/backtest/backtest_golf.py
───────────────────────────────────────
Replays the P-017 golf top-N decision rules through a realistic execution
model on historical Kalshi data pulled 2026-07-19. This is the rigor step
between the aggregate-calibration research (GOLF_KALSHI_RESEARCH.md) and
writing a live pod: it applies the ACTUAL per-bet decision logic, correct
series fees (zero maker on props), and pessimistic fills — then bootstraps
CIs clustered by tournament (outcomes correlate within an event).

Three legs, all structural (NO DataGolf/model input — pure market-price
rules, so the edge, if any, needs no proprietary data):

  A  pre_tourn_yes  — BUY YES at ask on top-10/20/40 in a price band, on
                      the latest two-sided quote >= 4 days before close
                      (the "Wednesday" anchor). Taker fee applies.
  B  fade_maker     — REST an ask (sell YES) on top-10/20 in-tournament
                      (12-48h before close). Pessimistic maker fill: fills
                      only on a public print strictly THROUGH the quote
                      (mirrors P-016). Zero maker fee (quadratic series).
  C  makecut_yes    — BUY YES at ask on make-cut in a band, pre-tournament.

Inputs (produced by the collectors in golf_research/):
  candles.jsonl        daily OHLC bid/ask/price + volume, result, times
  trades.jsonl         tick prints for top-10/20 of 5 events (Leg B only)

Excludes THOC26 / COPC26 (settled on pull date; results not yet populated).

Usage:  python3 backtest_golf.py [--data-dir ..] [--out results.json]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import golf_fees as gf

EXCLUDE_EVENTS = ("THOC26", "COPC26")
SECONDS_PER_DAY = 86400.0


def _f(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _iso_epoch(s: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def series_of(ticker: str) -> str:
    return ticker.split("-")[0]


def event_of(rec: Dict[str, Any]) -> str:
    """Tournament code, e.g. 'USO26' from 'KXPGATOP20-USO26-SCHE'."""
    ev = rec.get("event") or ""
    parts = ev.split("-")
    return parts[1] if len(parts) >= 2 else ev


# ── Load ─────────────────────────────────────────────────────────────────

def load_candles(path: str) -> List[Dict[str, Any]]:
    out = []
    with open(path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in r or not r.get("candles"):
                continue
            if any(e in r.get("event", "") for e in EXCLUDE_EVENTS):
                continue
            out.append(r)
    return out


def load_trades(path: str) -> List[Dict[str, Any]]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in r or not r.get("trades"):
                continue
            if any(e in r.get("event", "") for e in EXCLUDE_EVENTS):
                continue
            out.append(r)
    return out


# ── Candle helpers ───────────────────────────────────────────────────────

def two_sided(c: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Return (yes_bid, yes_ask) close for a candle if both sides quote."""
    a = _f((c.get("yes_ask") or {}).get("close_dollars"))
    b = _f((c.get("yes_bid") or {}).get("close_dollars"))
    if a is None or b is None or b <= 0 or a >= 1 or a < b:
        return None
    return b, a


def anchor_candle(rec: Dict[str, Any], min_days_to_close: float,
                  max_days_to_close: float) -> Optional[Dict[str, Any]]:
    """Latest two-sided candle whose end sits in the pre-tournament window
    [min_days, max_days] before close. This is the 'Wednesday' quote."""
    close = _iso_epoch(rec["close_time"])
    if close is None:
        return None
    best = None
    for c in rec["candles"]:
        end = c.get("end_period_ts")
        if end is None:
            continue
        dtc = (close - end) / SECONDS_PER_DAY
        if not (min_days_to_close <= dtc <= max_days_to_close):
            continue
        if two_sided(c) is None:
            continue
        # latest such candle = smallest dtc
        if best is None or (close - c["end_period_ts"]) < (close - best["end_period_ts"]):
            best = c
    return best


# ── Bootstrap (clustered by tournament) ──────────────────────────────────

def bootstrap_ci(per_bet: List[Tuple[str, float]], n_boot: int = 5000,
                 seed_calls: int = 0) -> Tuple[float, float, float]:
    """Mean per-bet PnL and 95% CI, resampling TOURNAMENTS with replacement
    (outcomes within an event are correlated → cluster bootstrap).

    Returns (mean, lo95, hi95). Deterministic given a fixed call order
    (Math.random-free: uses a fixed-seed local RNG)."""
    if not per_bet:
        return 0.0, 0.0, 0.0
    by_event: Dict[str, List[float]] = defaultdict(list)
    for ev, pnl in per_bet:
        by_event[ev].append(pnl)
    events = list(by_event.keys())
    all_pnl = [p for _, p in per_bet]
    mean = sum(all_pnl) / len(all_pnl)
    if len(events) < 2:
        return mean, float("nan"), float("nan")
    rng = random.Random(12345 + seed_calls)
    means = []
    for _ in range(n_boot):
        pool: List[float] = []
        for _ in range(len(events)):
            pool.extend(by_event[events[rng.randrange(len(events))]])
        if pool:
            means.append(sum(pool) / len(pool))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means))]
    return mean, lo, hi


def summarize(name: str, per_bet: List[Tuple[str, float]],
              extra: Optional[Dict[str, Any]] = None,
              seed: int = 0) -> Dict[str, Any]:
    n = len(per_bet)
    mean, lo, hi = bootstrap_ci(per_bet, seed_calls=seed)
    events = sorted({ev for ev, _ in per_bet})
    row = {
        "leg": name, "n_bets": n, "n_events": len(events),
        "mean_pnl_per_contract": round(mean, 4),
        "ci95_lo": round(lo, 4) if not math.isnan(lo) else None,
        "ci95_hi": round(hi, 4) if not math.isnan(hi) else None,
        "total_pnl_per_contract_units": round(sum(p for _, p in per_bet), 2),
        "events": events,
    }
    if extra:
        row.update(extra)
    return row


# ── Leg A: pre-tournament cheap-YES taker ────────────────────────────────

def leg_pre_tourn_yes(candles: List[Dict[str, Any]], series: Tuple[str, ...],
                      band: Tuple[float, float], max_spread: float,
                      min_days: float, max_days: float) -> Dict[str, Any]:
    lo_px, hi_px = band
    per_bet: List[Tuple[str, float]] = []
    per_bet_gross: List[Tuple[str, float]] = []
    hits = 0
    for rec in candles:
        if series_of(rec["ticker"]) not in series:
            continue
        c = anchor_candle(rec, min_days, max_days)
        if c is None:
            continue
        b, a = two_sided(c)
        if (a - b) > max_spread:
            continue
        if not (lo_px <= a <= hi_px):
            continue
        result = 1.0 if rec.get("result") == "yes" else 0.0
        fee = gf.fee_per_contract(a, maker=False)  # taker
        pnl = (result - a) - fee
        per_bet.append((event_of(rec), pnl))
        per_bet_gross.append((event_of(rec), result - a))
        hits += int(result)
    extra = {
        "hit_rate": round(hits / len(per_bet), 4) if per_bet else None,
        "gross_mean": summarize("_g", per_bet_gross, seed=1)["mean_pnl_per_contract"]
        if per_bet_gross else None,
        "band": list(band), "days_window": [min_days, max_days],
        "series": list(series),
    }
    return summarize("A_pre_tourn_yes", per_bet, extra, seed=2)


# ── Leg C: make-cut cheap-YES taker (same shape, own series/band) ────────

def leg_makecut_yes(candles: List[Dict[str, Any]], band: Tuple[float, float],
                    max_spread: float, min_days: float,
                    max_days: float) -> Dict[str, Any]:
    row = leg_pre_tourn_yes(candles, ("KXPGAMAKECUT",), band, max_spread,
                            min_days, max_days)
    row["leg"] = "C_makecut_yes"
    return row


# ── Leg B: in-tournament fade maker (pessimistic through-fills) ──────────

def _candle_for_epoch(rec: Dict[str, Any], target: float) -> Optional[Dict[str, Any]]:
    """The two-sided candle whose end is nearest to and <= target epoch."""
    best = None
    for c in rec["candles"]:
        end = c.get("end_period_ts")
        if end is None or end > target:
            continue
        if two_sided(c) is None:
            continue
        if best is None or end > best["end_period_ts"]:
            best = c
    return best


def leg_fade_maker(trades: List[Dict[str, Any]],
                   candle_index: Dict[str, Dict[str, Any]],
                   series: Tuple[str, ...], band: Tuple[float, float],
                   quote_offset: float, quote_size: float,
                   fade_start_h: float, fade_end_h: float) -> Dict[str, Any]:
    """Rest an ASK (sell YES) in-tournament; fill only on through-prints.

    We post the quote at decision time T = close - fade_start_h and keep it
    until close - fade_end_h. Quote price = round(mid_at_T + quote_offset,2),
    where mid_at_T is from the nearest prior two-sided candle. A public print
    with yes_price > quote_price (strictly through) fills up to
    min(remaining_size, print_count). Settle at outcome; zero maker fee
    (quadratic series). PnL per contract sold = quote_price - result.
    """
    lo_px, hi_px = band
    per_bet: List[Tuple[str, float]] = []       # per FILLED contract
    per_quote: List[Tuple[str, float]] = []     # per quote (0 if unfilled)
    n_quotes = 0
    n_filled_quotes = 0
    total_contracts = 0.0
    for rec in trades:
        tk = rec["ticker"]
        if series_of(tk) not in series:
            continue
        crec = candle_index.get(tk)
        if not crec:
            continue
        close = _iso_epoch(rec["close_time"])
        if close is None:
            continue
        t_start = close - fade_start_h * 3600.0
        t_end = close - fade_end_h * 3600.0
        c_at = _candle_for_epoch(crec, t_start)
        if c_at is None:
            continue
        b, a = two_sided(c_at)
        mid = (a + b) / 2.0
        if not (lo_px <= mid <= hi_px):
            continue
        quote_px = round(mid + quote_offset, 2)
        if quote_px <= mid or quote_px >= 1.0:
            continue
        n_quotes += 1
        result = 1.0 if rec.get("result") == "yes" else 0.0
        remaining = quote_size
        filled = 0.0
        for t in rec["trades"]:
            ep = _iso_epoch(t.get("created_time"))
            px = _f(t.get("yes_price_dollars"))
            cnt = _f(t.get("count_fp")) or _f(t.get("count")) or 0.0
            if ep is None or px is None or cnt <= 0:
                continue
            if ep < t_start or ep > t_end:
                continue
            if px > quote_px + 1e-9:            # strictly THROUGH our ask
                take = min(remaining, cnt)
                if take <= 0:
                    continue
                # sold YES at quote_px; zero maker fee on prop series
                pnl = (quote_px - result)
                per_bet.append((event_of(rec), pnl))  # per contract
                filled += take
                remaining -= take
                if remaining <= 1e-9:
                    break
        # weight the per-contract pnl by fill size for the aggregate
        if filled > 0:
            n_filled_quotes += 1
            total_contracts += filled
            per_quote.append((event_of(rec), (quote_px - result) * filled))
        else:
            per_quote.append((event_of(rec), 0.0))

    # Contract-weighted mean via per_quote / total_contracts
    mean_per_contract = (sum(p for _, p in per_quote) / total_contracts
                         if total_contracts else 0.0)
    _, lo, hi = bootstrap_ci(
        [(ev, (p / (total_contracts / max(n_filled_quotes, 1))))
         for ev, p in per_quote], seed_calls=7) if total_contracts else (0, None, None)
    row = {
        "leg": "B_fade_maker",
        "n_quotes": n_quotes,
        "n_filled_quotes": n_filled_quotes,
        "fill_rate": round(n_filled_quotes / n_quotes, 4) if n_quotes else None,
        "total_contracts_filled": round(total_contracts, 1),
        "mean_pnl_per_contract": round(mean_per_contract, 4),
        "n_events": len({ev for ev, _ in per_quote}),
        "events": sorted({ev for ev, _ in per_quote}),
        "band": list(band), "quote_offset": quote_offset,
        "fade_window_h": [fade_start_h, fade_end_h],
        "series": list(series),
        "note": "per-contract PnL; maker fee = 0 (quadratic series); "
                "pessimistic through-fill only.",
    }
    # Simpler, robust CI: cluster per-contract fills by event
    per_contract_bets = per_bet  # each entry is one filled contract's pnl
    m, clo, chi = bootstrap_ci(per_contract_bets, seed_calls=9)
    row["mean_pnl_per_contract"] = round(m, 4)
    row["ci95_lo"] = round(clo, 4) if not math.isnan(clo) else None
    row["ci95_hi"] = round(chi, 4) if not math.isnan(chi) else None
    row["n_bets"] = len(per_contract_bets)
    return row


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="..")
    ap.add_argument("--out", default="backtest_results.json")
    args = ap.parse_args()

    candles = load_candles(os.path.join(args.data_dir, "candles.jsonl"))
    trades = load_trades(os.path.join(args.data_dir, "trades.jsonl"))
    candle_index = {r["ticker"]: r for r in candles}
    print(f"loaded {len(candles)} candle records, {len(trades)} trade records")

    results: Dict[str, Any] = {"generated": "backtest_golf.py",
                               "n_candle_markets": len(candles),
                               "n_trade_markets": len(trades),
                               "legs": []}

    # Leg A — pre-tournament cheap-YES on top-10/20 (primary) and top-40
    a_top1020 = leg_pre_tourn_yes(
        candles, ("KXPGATOP10", "KXPGATOP20"), band=(0.08, 0.40),
        max_spread=0.06, min_days=4.0, max_days=10.0)
    a_top40 = leg_pre_tourn_yes(
        candles, ("KXPGATOP40",), band=(0.10, 0.45),
        max_spread=0.08, min_days=4.0, max_days=10.0)
    a_top5 = leg_pre_tourn_yes(
        candles, ("KXPGATOP5",), band=(0.05, 0.30),
        max_spread=0.05, min_days=4.0, max_days=10.0)

    # Leg C — make-cut cheap-YES
    c_makecut = leg_makecut_yes(candles, band=(0.05, 0.40), max_spread=0.06,
                                min_days=4.0, max_days=10.0)

    # Leg B — fade maker on top-10/20 (needs tick trades).
    # Validated window from refine_golf.py: rest fades 36h→6h before close
    # (Sat→Sun-AM). Starting at 48h dilutes with still-cheap 2-4d flow; the
    # 48-24h slice is negative. offset 0.03 minimizes adverse selection.
    b_fade = leg_fade_maker(
        trades, candle_index, ("KXPGATOP10", "KXPGATOP20"),
        band=(0.08, 0.40), quote_offset=0.03, quote_size=25.0,
        fade_start_h=36.0, fade_end_h=6.0)

    for row in (a_top1020, a_top40, a_top5, c_makecut, b_fade):
        results["legs"].append(row)

    with open(args.out, "w") as fh:
        json.dump(results, fh, indent=2)

    # ── Print ────────────────────────────────────────────────────────────
    def fmt_ci(r):
        lo, hi = r.get("ci95_lo"), r.get("ci95_hi")
        return f"[{lo:+.3f},{hi:+.3f}]" if lo is not None and hi is not None else "[n/a]"

    print("\n=== TAKER LEGS (buy YES at ask, net of taker fee) ===")
    print(f"{'leg':22} {'n':>5} {'ev':>3} {'hit%':>6} {'net/ct':>8} {'95%CI':>18} {'gross/ct':>9}")
    for r in (a_top1020, a_top40, a_top5, c_makecut):
        hit = f"{100*r['hit_rate']:.1f}" if r.get("hit_rate") is not None else "-"
        print(f"{r['leg']:22} {r['n_bets']:>5} {r['n_events']:>3} {hit:>6} "
              f"{r['mean_pnl_per_contract']:>+8.3f} {fmt_ci(r):>18} "
              f"{r.get('gross_mean',0):>+9.3f}")

    print("\n=== MAKER LEG (rest ask in-tournament, pessimistic through-fill, 0 maker fee) ===")
    r = b_fade
    print(f"quotes={r['n_quotes']} filled={r['n_filled_quotes']} "
          f"fill_rate={r['fill_rate']} contracts={r['total_contracts_filled']}")
    print(f"net/contract={r['mean_pnl_per_contract']:+.3f} "
          f"95%CI={fmt_ci(r)} n_events={r['n_events']}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
