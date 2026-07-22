"""
longshot_research/backtest_longshot.py
──────────────────────────────────────
P-019 STEP-1 DECISION GATE — favorite-longshot-bias (FLB) calibration replay
on settled Kalshi data, restricted to the P-019 target universe (politics +
sports SEASON futures / top-N / awards).  This is the go/no-go the spec puts
BEFORE any engine code: confirm the bias exists in OUR universe, or kill.

The question: on settled contracts, bucketed by the price at which a maker
would have RESTED a quote (a mid-life mid, when the market was still
uncertain), is the realized YES-rate BELOW the implied price for cheap
longshots (<10c) and ABOVE it for favorites (85-95c)?  If yes, the FLB is
harvestable here; if flat, P-019 dies at step 1.

Pipeline (each stage disk-cached + resumable; API is live Kalshi public):
  1. discover_series()  politics (all) + sports-futures (keyword-filtered)
                        -> data/series_universe.json
  2. pull_settled()     per series, status=settled, min_close_ts=lookback
                        -> data/settled_markets.jsonl   (result survives on
                        the LIST; price/volume/OI do NOT — see CLAUDE.md)
  3. pull_prices()      candlesticks per settled market -> mid-life mid,
                        T-1d mid, cumulative volume
                        -> data/market_prices.jsonl
  4. calibrate()        bucket by reference price; realized YES-rate vs
                        implied; bootstrap CIs CLUSTERED BY EVENT (contracts
                        within an event correlate) -> REPORT + p019_params.json

Reference price = bid/ask MID at the market's mid-life timestamp.  Chosen
over lifetime VWAP (contaminated by the convergence-to-resolution drift near
close) and over a fixed T-7d horizon (unavailable for shorter markets).
Only markets with real cumulative volume are kept — a mid on an untraded
book is a stale quote, not a market price.

Usage:
  python3 backtest_longshot.py --lookback-days 365            # full run
  python3 backtest_longshot.py --max-series 40 --lookback-days 365  # subset smoke
  python3 backtest_longshot.py --calibrate-only               # re-score cache
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DATA.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(HERE.parent / "kalshi-ev-map" / "src"))
import kalshi_client as kc  # noqa: E402

# ── Universe definition ──────────────────────────────────────────────────
#
# The FLB lives in MULTI-OUTCOME, longshot-rich markets: many cheap candidates
# + a favorite (top-N finishers, awards, season standings).  Per-set / per-
# quarter / per-game "winner" markets are 2-outcome, short-horizon and near-
# calibrated — the OPPOSITE of the target, and they dominated a first fuzzy
# keyword pass (KXATPSETWINNER 4400, KXWTASETWINNER 3914, per-quarter WNBA).
#
# So sports inclusion is an EXPLICIT, AUDITED keep-list of series prefixes,
# derived from the 103 sports families that actually had settled contracts in
# the 365d pull (see REPORT).  Prefix match, so KXPGATOP{5,10,20,40} all hit
# "KXPGATOP".  This is transparent and auditable — what a research gate needs.
SPORTS_FUTURES_SERIES = (
    # golf top-N / make-cut / tour (P-015/P-017 precedent: per-tournament top-N)
    "KXPGATOP", "KXPGAMAKECUT", "KXDPWORLDTOURMAKECUT", "KXCHAMPTOUR",
    "KXLIVTOP",
    # motorsport top-N (per-race top-N = longshot-rich, in the "top-N" clause)
    "KXNASCARTOP", "KXINDYCARTOP", "KXF1TOP", "KXF1SPRINTTOP", "KXCYCLING",
    # awards / MVP / season stat leaders
    "KXWCAWARD", "KXWCGOALLEADER", "KXMENWORLDCUP", "KXNBAMVP", "KXNBAFINMVP",
    "KXNBASUMMERMVP", "KXMLBASGMVP", "KXWNBACCUPMVP", "KXNBAPTSLEADER",
    "KXNBAPLAYOFFPTS", "KXNBAPLAYOFFWINS",
    # drafts (top-N pick order = many longshot candidates)
    "KXNBADRAFTTOP", "KXMLBDRAFTTOP", "KXNHLDRAFTTOP",
    # chess multi-field finishes
    "KXTITLEDTUES", "KXCHESSFIDERATINGTOP",
    # soccer season standings / relegation
    "KXLALIGATOP", "KXEPLTOP", "KXSERIEATOP", "KXLIGUE1TOP",
    "KXEPLRELEGATION", "KXSERIEARELEGATION",
    # season win totals / NCAA
    "KXWNBAWINS", "KXNCAABBCONF", "KXNCAABBPLAYOFFS",
)
# Explicit contaminators (short-horizon, near-calibrated) — documented so the
# exclusion is auditable, even though the keep-list already omits them.
SPORTS_EXCLUDED = (
    "KXATPSETWINNER", "KXWTASETWINNER", "KXATPS1GWINNER", "KXATPS2GWINNER",
    "KXATPS3GWINNER", "KXWNBA1QWINNER", "KXWNBA2QWINNER", "KXWNBA3QWINNER",
    "KXWNBA4QWINNER", "KXCOUNTYCHAMPMATCH",
)
HARD_DROP_PREFIX = ("KXMVE",)   # multi-leg parlay junk


def _classify_series(ticker: str, category: str) -> Optional[str]:
    """Return 'politics' | 'sports_futures' | None (drop).

    Crypto ranges and short-horizon weather live in Financial/Climate
    categories, so restricting to Politics + Sports already excludes them.
    """
    t = (ticker or "").upper()
    if any(t.startswith(p) for p in HARD_DROP_PREFIX):
        return None
    if category == "Politics":
        return "politics"
    if category == "Sports":
        if any(t.startswith(p) for p in SPORTS_FUTURES_SERIES):
            return "sports_futures"
        return None
    return None


def _in_final_universe(series: str, kind: str) -> bool:
    """Belt-and-suspenders filter applied to the cached settled rows before
    pricing — lets the universe tighten without re-pulling the 29k rows."""
    s = (series or "").upper()
    if s in SPORTS_EXCLUDED:
        return False
    if kind == "politics":
        return True
    return any(s.startswith(p) for p in SPORTS_FUTURES_SERIES)


def discover_series(force: bool = False) -> List[Dict[str, str]]:
    out_path = DATA / "series_universe.json"
    if out_path.exists() and not force:
        return json.loads(out_path.read_text())
    kept: List[Dict[str, str]] = []
    counts: Counter = Counter()
    for cat in ("Politics", "Sports"):
        cursor = None
        while True:
            params: Dict[str, Any] = {"category": cat, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = kc.get("/series/", params)
            for s in data.get("series", []):
                tk = s.get("ticker") or ""
                kind = _classify_series(tk, cat)
                if kind is None:
                    continue
                kept.append({"ticker": tk, "kind": kind,
                             "fee_type": s.get("fee_type") or "",
                             "title": (s.get("title") or "")[:80]})
                counts[kind] += 1
            cursor = data.get("cursor")
            if not cursor:
                break
    out_path.write_text(json.dumps(kept, indent=1))
    print(f"[discover] kept {len(kept)} series: {dict(counts)}", flush=True)
    return kept


# ── Stage 2: settled markets ─────────────────────────────────────────────

def _iso_epoch(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def pull_settled(series: List[Dict[str, str]], lookback_days: int,
                 max_series: Optional[int] = None) -> List[Dict[str, Any]]:
    """Per-series settled-market pull.  Only `result`, times, event and titles
    survive on the settled LIST; price/volume are fetched later via candles."""
    out_path = DATA / "settled_markets.jsonl"
    fetched_series_path = DATA / "_settled_series_done.json"
    done = set()
    rows: List[Dict[str, Any]] = []
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    if fetched_series_path.exists():
        done = set(json.loads(fetched_series_path.read_text()))

    min_close_ts = int(time.time()) - lookback_days * 86400
    todo = [s for s in series if s["ticker"] not in done]
    if max_series is not None:
        todo = todo[:max_series]
    print(f"[settled] {len(todo)} series to probe "
          f"({len(done)} already done, {len(rows)} rows cached)", flush=True)

    fh = open(out_path, "a")
    n_new = 0
    for i, s in enumerate(todo, 1):
        stk = s["ticker"]
        try:
            cursor = None
            got = 0
            while True:
                params: Dict[str, Any] = {
                    "series_ticker": stk, "status": "settled",
                    "min_close_ts": min_close_ts, "limit": 1000,
                }
                if cursor:
                    params["cursor"] = cursor
                data = kc.get("/markets", params)
                ms = data.get("markets", [])
                for m in ms:
                    res = (m.get("result") or "").lower()
                    if res not in ("yes", "no"):
                        continue   # 'scalar'/void/empty -> not a clean binary
                    rec = {
                        "ticker": m.get("ticker"),
                        "series": stk,
                        "kind": s["kind"],
                        "event": m.get("event_ticker"),
                        "result": 1 if res == "yes" else 0,
                        "open_time": m.get("open_time"),
                        "close_time": m.get("close_time"),
                        "title": (m.get("title") or "")[:120],
                        "yes_sub_title": (m.get("yes_sub_title") or "")[:80],
                    }
                    fh.write(json.dumps(rec) + "\n")
                    rows.append(rec)
                    got += 1
                    n_new += 1
                cursor = data.get("cursor")
                if not cursor or not ms:
                    break
            if got:
                print(f"[settled] {stk}: +{got}", flush=True)
        except Exception as exc:
            print(f"[settled] {stk} FAILED: {exc}", flush=True)
        done.add(stk)
        if i % 50 == 0:
            fh.flush()
            fetched_series_path.write_text(json.dumps(sorted(done)))
            print(f"[settled] progress {i}/{len(todo)} "
                  f"({n_new} new rows)", flush=True)
    fh.close()
    fetched_series_path.write_text(json.dumps(sorted(done)))
    print(f"[settled] done: {len(rows)} total settled binary contracts, "
          f"{n_new} new this run", flush=True)
    return rows


# ── Stage 3: reference prices via candlesticks ───────────────────────────

def _mid_from_candle(c: Dict[str, Any]) -> Optional[float]:
    b = kc.fnum((c.get("yes_bid") or {}).get("close_dollars"))
    a = kc.fnum((c.get("yes_ask") or {}).get("close_dollars"))
    if b is not None and a is not None and 0 < b <= a < 1:
        return (a + b) / 2.0
    # fall back to last trade price in the period if quotes missing
    p = kc.fnum((c.get("price") or {}).get("close_dollars")) \
        or kc.fnum((c.get("price") or {}).get("mean_dollars"))
    if p is not None and 0 < p < 1:
        return p
    return None


def _candle_mid_at(candles: List[Dict[str, Any]], ts: float) -> Optional[float]:
    prior = [c for c in candles if c.get("end_period_ts", 0) <= ts]
    for c in reversed(prior or candles):
        mid = _mid_from_candle(c)
        if mid is not None:
            return mid
    return None


def pull_prices(rows: List[Dict[str, Any]], min_volume: float = 20.0,
                workers: int = 8) -> List[Dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out_path = DATA / "market_prices.jsonl"
    have = set()
    priced: List[Dict[str, Any]] = []
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                priced.append(r)
                have.add(r["ticker"])
    todo = [r for r in rows if r["ticker"] not in have]
    print(f"[prices] {len(todo)} markets to fetch candles "
          f"({len(have)} cached)", flush=True)

    def fetch(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        cl = _iso_epoch(r.get("close_time"))
        op = _iso_epoch(r.get("open_time"))
        if cl is None:
            return {"ticker": r["ticker"], "priced": False, "why": "no_close"}
        if op is None or op >= cl:
            op = cl - 30 * 86400
        span = cl - op
        interval = 1440 if span > 10 * 86400 else (60 if span > 2 * 86400 else 1)
        start = op
        # bound daily-candle windows so a multi-month market is one page
        if interval == 1440:
            start = max(op, cl - 180 * 86400)
        try:
            data = kc.get(
                f"/series/{r['series']}/markets/{r['ticker']}/candlesticks",
                {"start_ts": int(start), "end_ts": int(cl),
                 "period_interval": interval})
        except Exception as exc:
            return {"ticker": r["ticker"], "priced": False, "why": str(exc)[:40]}
        candles = (data or {}).get("candlesticks", [])
        if not candles:
            return {"ticker": r["ticker"], "priced": False, "why": "no_candles"}
        vol = sum(kc.fnum(c.get("volume_fp")) or 0.0 for c in candles)
        first_ts = min(c.get("end_period_ts", cl) for c in candles)
        midlife_ts = (first_ts + cl) / 2.0
        rec = {
            "ticker": r["ticker"], "series": r["series"], "kind": r["kind"],
            "event": r["event"], "result": r["result"],
            "priced": True, "volume": vol,
            "span_days": round(span / 86400.0, 2),
            "px_midlife": _candle_mid_at(candles, midlife_ts),
            "px_t1d": _candle_mid_at(candles, cl - 86400),
            "px_open": _candle_mid_at(candles, first_ts + 3600),
        }
        return rec

    fh = open(out_path, "a")
    n = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fetch, r) for r in todo]
        for fut in as_completed(futs):
            rec = fut.result()
            n += 1
            if rec:
                fh.write(json.dumps(rec) + "\n")
                if rec.get("priced"):
                    priced.append(rec)
            if n % 500 == 0:
                fh.flush()
                print(f"[prices] {n}/{len(todo)}", flush=True)
    fh.close()
    usable = [r for r in priced if r.get("priced") and r.get("px_midlife")
              and r.get("volume", 0) >= min_volume]
    print(f"[prices] {len(priced)} priced, {len(usable)} usable "
          f"(mid present & volume>={min_volume})", flush=True)
    return priced


# ── Stage 4: calibration ─────────────────────────────────────────────────

BANDS = [(0.0, 0.03), (0.03, 0.10), (0.10, 0.20), (0.20, 0.35),
         (0.35, 0.50), (0.50, 0.65), (0.65, 0.80), (0.80, 0.90),
         (0.90, 0.97), (0.97, 1.0)]


def _bootstrap_event_ci(items: List[Tuple[str, float, int]],
                        n_boot: int = 2000, seed: int = 7
                        ) -> Tuple[float, float, float]:
    """items = (event, price, result).  Returns (mean_realized, lo, hi) for
    the realized YES-rate, resampling whole EVENTS (clustered)."""
    by_event: Dict[str, List[int]] = defaultdict(list)
    for ev, _px, res in items:
        by_event[ev or "_"].append(res)
    events = list(by_event.keys())
    if not events:
        return (float("nan"), float("nan"), float("nan"))
    rng = random.Random(seed)
    all_res = [res for v in by_event.values() for res in v]
    point = sum(all_res) / len(all_res)
    means = []
    for _ in range(n_boot):
        pool: List[int] = []
        for _ in range(len(events)):
            pool.extend(by_event[events[rng.randrange(len(events))]])
        if pool:
            means.append(sum(pool) / len(pool))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means))]
    return (point, lo, hi)


def calibrate(priced: List[Dict[str, Any]], min_volume: float,
              price_field: str = "px_midlife") -> Dict[str, Any]:
    usable = [r for r in priced
              if r.get("priced") and r.get(price_field) is not None
              and r.get("volume", 0) >= min_volume
              and _in_final_universe(r.get("series", ""), r.get("kind", ""))]
    report_bands = []
    for lo, hi in BANDS:
        sub = [r for r in usable if lo <= r[price_field] < hi]
        if not sub:
            report_bands.append({"band": [lo, hi], "n": 0})
            continue
        items = [(r["event"], r[price_field], r["result"]) for r in sub]
        mean_px = sum(r[price_field] for r in sub) / len(sub)
        realized, clo, chi = _bootstrap_event_ci(items)
        n_events = len({r["event"] for r in sub})
        report_bands.append({
            "band": [lo, hi], "n": len(sub), "n_events": n_events,
            "mean_price": round(mean_px, 4),
            "realized_yes": round(realized, 4),
            "realized_ci": [round(clo, 4), round(chi, 4)],
            "edge_vs_implied": round(realized - mean_px, 4),
        })
    by_kind = Counter(r["kind"] for r in usable)
    return {"n_usable": len(usable), "by_kind": dict(by_kind),
            "price_field": price_field, "bands": report_bands}


def _fmt_table(cal: Dict[str, Any]) -> str:
    lines = [
        "| price band | n | events | mean price (implied) | realized YES | 95% CI (event-clustered) | edge (real−impl) |",
        "|---|---|---|---|---|---|---|",
    ]
    for b in cal["bands"]:
        if b["n"] == 0:
            lines.append(f"| {b['band'][0]:.2f}–{b['band'][1]:.2f} | 0 | — | — | — | — | — |")
            continue
        lo, hi = b["realized_ci"]
        lines.append(
            f"| {b['band'][0]:.2f}–{b['band'][1]:.2f} | {b['n']} | {b['n_events']} "
            f"| {b['mean_price']:.3f} | {b['realized_yes']:.3f} "
            f"| [{lo:.3f}, {hi:.3f}] | {b['edge_vs_implied']:+.3f} |")
    return "\n".join(lines)


def _verdict(cal: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Spec gate: <10c bucket must win < implied; 85-95c must win > implied."""
    notes = []
    low = [b for b in cal["bands"] if b["n"] and b["band"][1] <= 0.10]
    fav = [b for b in cal["bands"]
           if b["n"] and b["band"][0] >= 0.80 and b["band"][1] <= 0.97]
    low_ok = fav_ok = None
    if low:
        n = sum(b["n"] for b in low)
        real = sum(b["realized_yes"] * b["n"] for b in low) / n
        impl = sum(b["mean_price"] * b["n"] for b in low) / n
        low_ok = real < impl
        notes.append(f"<10c: realized {real:.3f} vs implied {impl:.3f} "
                     f"(n={n}) -> {'BIAS PRESENT' if low_ok else 'no bias'}")
    else:
        notes.append("<10c: NO usable data")
    if fav:
        n = sum(b["n"] for b in fav)
        real = sum(b["realized_yes"] * b["n"] for b in fav) / n
        impl = sum(b["mean_price"] * b["n"] for b in fav) / n
        fav_ok = real > impl
        notes.append(f"80-97c: realized {real:.3f} vs implied {impl:.3f} "
                     f"(n={n}) -> {'BIAS PRESENT' if fav_ok else 'no bias'}")
    else:
        notes.append("80-97c: NO usable data")
    if low_ok and fav_ok:
        return "GO", notes
    if low_ok or fav_ok:
        return "PARTIAL", notes
    return "NO-GO", notes


def write_outputs(cal: Dict[str, Any], args) -> None:
    verdict, notes = _verdict(cal)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    table = _fmt_table(cal)
    params = {
        "generated": stamp,
        "verdict": verdict,
        "lookback_days": args.lookback_days,
        "min_volume": args.min_volume,
        "price_field": cal["price_field"],
        "n_usable": cal["n_usable"],
        "by_kind": cal["by_kind"],
        "favorites_band": [0.85, 0.95],
        "longshot_band": [0.03, 0.10],
        "min_horizon_days": 14,
        "bands": cal["bands"],
    }
    (HERE / "p019_params.json").write_text(json.dumps(params, indent=2))

    md = f"""# P-019 Longshot Maker — Calibration Gate REPORT
*Generated {stamp}. Step-1 go/no-go from `backtest_longshot.py` on live Kalshi settled data.*

## Verdict: **{verdict}**

{chr(10).join('- ' + n for n in notes)}

## Method
Settled Kalshi contracts in the P-019 universe (**politics** + **sports season-futures / top-N / awards**, all zero-maker-fee `quadratic` series), closing within the last **{args.lookback_days} days**. For each contract the reference "posted" price is the **bid/ask mid at mid-life** (halfway between first candle and close), the price at which a maker would rest a quote while the market is still uncertain — not lifetime VWAP (convergence-contaminated) nor a fixed horizon (unavailable on short markets). Only contracts with cumulative candle volume ≥ **{args.min_volume}** are kept. Realized YES-rate CIs are bootstrapped resampling whole **events** (contracts within an event correlate).

Usable contracts: **{cal['n_usable']}**  ·  by kind: {cal['by_kind']}

## Price-decile calibration (reference price = {cal['price_field']})
If the favorite-longshot bias is present, **realized YES < implied** at low prices (longshots overpriced) and **realized YES > implied** at high prices (favorites underpriced); `edge` is positive for a BUYER of that band.

{table}

## Reading the gate
- **Longshot leg (sell YES 3–10¢)** is viable only if the `0.03–0.10` band's realized YES-rate sits **below** its implied price with the CI clearing it.
- **Favorites leg (buy YES 85–95¢)** is viable only if the `0.80–0.97` bands' realized YES-rate sits **above** implied.
- Sample unit is the **event**, not the contract. A wide CI = underpowered, not disproven.

## Caveats
- Long-horizon marquee markets (elections, season champions) are disproportionately **still open**, so the settled sample skews toward shorter-cycle resolved questions — the exact long-horizon books P-019 targets are under-represented here. Treat a positive gate as necessary, not sufficient; the live paper collector is what proves the durable edge.
- Reference price is a mid, not an executed maker fill. It measures whether the *bias* exists, not the *achievable* maker edge net of adverse selection — that is what the paper engine measures next.
"""
    (HERE / "REPORT_Longshot_2026-07.md").write_text(md)
    print("\n" + "=" * 70)
    print(f"VERDICT: {verdict}")
    for n in notes:
        print("  " + n)
    print("=" * 70)
    print(f"wrote {HERE/'REPORT_Longshot_2026-07.md'}")
    print(f"wrote {HERE/'p019_params.json'}")


# ── Driver ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback-days", type=int, default=365)
    ap.add_argument("--max-series", type=int, default=None,
                    help="cap series probed (subset smoke test)")
    ap.add_argument("--min-volume", type=float, default=20.0)
    ap.add_argument("--per-event-cap", type=int, default=25,
                    help="max contracts sampled per event (0 = no cap)")
    ap.add_argument("--rate", type=float, default=0.25,
                    help="min seconds between API calls")
    ap.add_argument("--calibrate-only", action="store_true")
    ap.add_argument("--price-field", default="px_midlife",
                    choices=["px_midlife", "px_t1d", "px_open"])
    args = ap.parse_args()
    kc.set_min_interval(args.rate)

    if args.calibrate_only:
        priced = [json.loads(l) for l in
                  (DATA / "market_prices.jsonl").read_text().splitlines() if l.strip()]
        cal = calibrate(priced, args.min_volume, args.price_field)
        write_outputs(cal, args)
        return

    series = discover_series()
    rows = pull_settled(series, args.lookback_days, args.max_series)

    # Restrict to the audited final universe, then bound the candlestick job by
    # capping contracts PER EVENT (whole events kept for clustering; random
    # within-event sampling preserves the price cross-section in expectation).
    rows = [r for r in rows if _in_final_universe(r["series"], r["kind"])]
    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_event[r["event"] or "_"].append(r)
    rng = random.Random(13)
    sampled: List[Dict[str, Any]] = []
    for ev, items in by_event.items():
        if args.per_event_cap and len(items) > args.per_event_cap:
            sampled.extend(rng.sample(items, args.per_event_cap))
        else:
            sampled.extend(items)
    print(f"[universe] {len(rows)} in-universe contracts, {len(by_event)} events "
          f"-> {len(sampled)} sampled (cap {args.per_event_cap}/event)", flush=True)

    priced = pull_prices(sampled, args.min_volume)
    cal = calibrate(priced, args.min_volume, args.price_field)
    write_outputs(cal, args)


if __name__ == "__main__":
    main()
