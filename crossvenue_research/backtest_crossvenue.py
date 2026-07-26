"""
crossvenue_research/backtest_crossvenue.py
──────────────────────────────────────────
P-020 Phase 1 backtest — "Polymarket mid is a sharper fair value than Kalshi on
politics/world; fade the divergence on Kalshi as a taker."

Settled data only.  Read-only.  Everything cached under data/.

Methodology guardrails honoured:
  * anchors are fixed horizons BEFORE the Kalshi close (T-168h ... T-6h); the
    settlement-contaminated tail (<6h) and `last_price` are never used;
  * Kalshi observations require a TWO-SIDED quote with a bounded spread —
    never a bare ask on an empty book;
  * trades execute at the Kalshi ask (YES) / 1-bid (NO), never at the mid;
  * PnL and CLV are net of the series-aware Kalshi taker fee AND half-spread;
  * every statistic is bootstrapped clustering on the real-world EVENT, never
    per contract.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import random
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi_fees import fee_per_contract  # noqa: E402
from crossvenue_research.harvest import (  # noqa: E402
    DATA, cache, kalshi_candles, poly_history,
)

# ── knobs ────────────────────────────────────────────────────────────
ANCHORS_H = [168, 120, 96, 72, 48, 36, 24, 18, 12, 8, 6]
MAX_SPREAD = 0.06          # Kalshi two-sided quote must be at least this tight
MIN_MID, MAX_MID = 0.03, 0.97
MATCH_TOL_MIN = 90         # observation alignment tolerance, minutes
K_STALE_S = 72 * 3600      # max age of a forward-filled Kalshi quote
P_STALE_S = 12 * 3600      # max age of a forward-filled Poly price
POLY_MIN_VOL = 1_000.0     # floor: below this the Poly print is noise
DEEP_RATIO = 1.0           # poly $vol / kalshi $vol above which Poly is "deeper"
MARKOUT_H = 24             # CLV proxy horizon
MARGINS = [0.00, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12]
BOOT = 5000
SEED = 20260726


def parse_iso(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def f(x) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ── data assembly ────────────────────────────────────────────────────

def kalshi_series_points(pair: dict) -> List[dict]:
    close = parse_iso(pair["kalshi_close"])
    open_ = parse_iso(pair.get("kalshi_open"))
    end = int(close.timestamp())
    start = int((open_ or (close - dt.timedelta(days=30))).timestamp())
    start = max(start, end - 3600 * 24 * 45)
    cs = kalshi_candles(pair["kalshi_series"], pair["kalshi_ticker"], start, end)
    out = []
    for c in cs:
        ts = c.get("end_period_ts")
        bid = f((c.get("yes_bid") or {}).get("close_dollars"))
        ask = f((c.get("yes_ask") or {}).get("close_dollars"))
        vol = f(c.get("volume_fp")) or 0.0
        if ts is None or bid is None or ask is None:
            continue
        # Kalshi volume is in CONTRACTS; Polymarket volumeNum is in USD.  Convert
        # to a comparable dollar notional using the period mid so the
        # "which venue is deeper" split is dollar-vs-dollar.
        out.append({"ts": ts, "bid": bid, "ask": ask, "vol": vol,
                    "usd": vol * (bid + ask) / 2.0})
    out.sort(key=lambda r: r["ts"])
    return out


def poly_series_points(pair: dict) -> List[dict]:
    h = poly_history(pair["poly_token_yes"])
    return sorted(({"ts": int(p["t"]), "p": float(p["p"])} for p in h
                   if p.get("t") and p.get("p") is not None),
                  key=lambda r: r["ts"])


def as_of(points: List[dict], ts: int, max_stale_s: int) -> Optional[dict]:
    """Last point at or before `ts`.

    Kalshi candlesticks are emitted only for periods with activity — a quiet
    book produces no row — so forward-filling the previous close is the correct
    reconstruction of the resting quote, not interpolation.  `max_stale_s`
    bounds how old a quote we are willing to treat as live.
    """
    if not points:
        return None
    lo, hi, best = 0, len(points) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if points[mid]["ts"] <= ts:
            best = points[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None or ts - best["ts"] > max_stale_s:
        return None
    return best


def build_observations(pairs: List[dict]) -> List[dict]:
    obs: List[dict] = []
    tol = MATCH_TOL_MIN * 60
    n = len(pairs)
    for i, p in enumerate(pairs):
        sys.stderr.write(f"\r  obs {i+1}/{n} rows={len(obs)}   ")
        sys.stderr.flush()
        close = parse_iso(p["kalshi_close"])
        if close is None:
            continue
        kpts = kalshi_series_points(p)
        ppts = poly_series_points(p)
        if not kpts or not ppts:
            continue
        kvol = sum(r["usd"] for r in kpts)
        y = 1.0 if p["kalshi_result"] == "yes" else 0.0
        for H in ANCHORS_H:
            ts = int((close - dt.timedelta(hours=H)).timestamp())
            k = as_of(kpts, ts, K_STALE_S)
            pol = as_of(ppts, ts, P_STALE_S)
            if not k or not pol:
                continue
            bid, ask = k["bid"], k["ask"]
            if not (0.0 < bid < ask < 1.0):
                continue
            spread = ask - bid
            if spread > MAX_SPREAD:
                continue
            kmid = (bid + ask) / 2.0
            if not (MIN_MID <= kmid <= MAX_MID):
                continue
            pmid = pol["p"]
            if not (0.01 <= pmid <= 0.99):
                continue
            # markout reference: Kalshi mid MARKOUT_H later, still >=6h pre-close
            mts = min(ts + MARKOUT_H * 3600,
                      int((close - dt.timedelta(hours=6)).timestamp()))
            kf = as_of(kpts, mts, K_STALE_S) if mts > ts + 1800 else None
            fut_mid = None
            if kf and 0.0 < kf["bid"] < kf["ask"] < 1.0:
                fut_mid = (kf["bid"] + kf["ask"]) / 2.0
            obs.append({
                "cluster": p["poly_event"],
                "kalshi_ticker": p["kalshi_ticker"],
                "series": p["kalshi_series"],
                "tier": p["tier"],
                "score": p["score"],
                "H": H,
                "ts": k["ts"],
                "bid": bid, "ask": ask, "kmid": kmid, "spread": spread,
                "pmid": pmid,
                "poly_vol": p["poly_volume"],
                "kalshi_vol": kvol,
                "y": y,
                "fut_mid": fut_mid,
                "fut_h": (mts - ts) / 3600.0 if fut_mid is not None else None,
            })
    sys.stderr.write("\n")
    return obs


# ── statistics ───────────────────────────────────────────────────────

def cluster_bootstrap(rows: List[dict], key, n_boot: int = BOOT,
                      seed: int = SEED) -> Tuple[float, float, float, int]:
    """Mean of key(row), with a cluster (event) bootstrap CI."""
    vals = [(r["cluster"], key(r)) for r in rows]
    vals = [(c, v) for c, v in vals if v is not None and not math.isnan(v)]
    if not vals:
        return float("nan"), float("nan"), float("nan"), 0
    byc: Dict[str, List[float]] = defaultdict(list)
    for c, v in vals:
        byc[c].append(v)
    clusters = list(byc)
    point = sum(v for _, v in vals) / len(vals)
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        tot, cnt = 0.0, 0
        for _ in range(len(clusters)):
            c = clusters[rng.randrange(len(clusters))]
            tot += sum(byc[c])
            cnt += len(byc[c])
        if cnt:
            means.append(tot / cnt)
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means)) - 1]
    return point, lo, hi, len(clusters)


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def logloss(p: float, y: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


# ── strategy ─────────────────────────────────────────────────────────

def trade(row: dict, margin: float) -> Optional[dict]:
    """Taker on Kalshi toward the Polymarket mid, if the gap clears the
    corridor.  Executes at the ask (YES) / 1-bid (NO) — never at the mid."""
    gap = row["pmid"] - row["kmid"]
    half = row["spread"] / 2.0
    corridor = fee_per_contract(row["kmid"], maker=False) + half + margin
    if abs(gap) <= corridor:
        return None
    if gap > 0:
        side, price = "yes", row["ask"]
        won = row["y"] == 1.0
        fut = row["fut_mid"]
    else:
        side, price = "no", 1.0 - row["bid"]
        won = row["y"] == 0.0
        fut = (1.0 - row["fut_mid"]) if row["fut_mid"] is not None else None
    if not (0.01 <= price <= 0.99):
        return None
    fee = fee_per_contract(price, maker=False)
    pnl = (1.0 if won else 0.0) - price - fee
    clv = (fut - price - fee) if fut is not None else None
    return {**row, "side": side, "price": price, "gap": gap,
            "corridor": corridor, "pnl": pnl, "clv": clv, "won": won}


# ── report ───────────────────────────────────────────────────────────

def fmt(p, lo, hi, n, scale=100.0, unit="c"):
    if n == 0 or (isinstance(p, float) and math.isnan(p)):
        return "n/a"
    return f"{p*scale:+.2f}{unit} [{lo*scale:+.2f}, {hi*scale:+.2f}]"


def main() -> None:
    pairs = json.load(open(os.path.join(DATA, "pairs.json")))
    pairs = [p for p in pairs if p["poly_volume"] >= POLY_MIN_VOL]
    print(f"pairs after Poly-depth filter (vol>=${POLY_MIN_VOL:,.0f}): {len(pairs)}")

    obs = cache("observations.json", lambda: build_observations(pairs))
    print(f"observations: {len(obs)} over "
          f"{len({o['cluster'] for o in obs})} event clusters, "
          f"{len({o['kalshi_ticker'] for o in obs})} kalshi markets")

    high = [o for o in obs if o["tier"] == "HIGH"]
    print(f"HIGH-confidence observations: {len(high)}")

    out: Dict[str, object] = {}

    # 1. Is Polymarket a sharper reference at all?
    for label, rows in (("ALL", obs), ("HIGH", high)):
        if not rows:
            continue
        bk = cluster_bootstrap(rows, lambda r: brier(r["kmid"], r["y"]))
        bp = cluster_bootstrap(rows, lambda r: brier(r["pmid"], r["y"]))
        bb = cluster_bootstrap(rows, lambda r: brier(0.5 * (r["kmid"] + r["pmid"]), r["y"]))
        dd = cluster_bootstrap(rows, lambda r: brier(r["kmid"], r["y"]) - brier(r["pmid"], r["y"]))
        lk = cluster_bootstrap(rows, lambda r: logloss(r["kmid"], r["y"]))
        lp = cluster_bootstrap(rows, lambda r: logloss(r["pmid"], r["y"]))
        print(f"\n[{label}] n={len(rows)} clusters={bk[3]}")
        print(f"  Brier Kalshi  {bk[0]:.4f}")
        print(f"  Brier Poly    {bp[0]:.4f}")
        print(f"  Brier 50/50   {bb[0]:.4f}")
        print(f"  Brier(K)-Brier(P) {dd[0]:+.5f}  CI[{dd[1]:+.5f},{dd[2]:+.5f}]"
              f"   (>0 means Poly sharper)")
        print(f"  LogLoss K {lk[0]:.4f}  P {lp[0]:.4f}")
        out[f"brier_{label}"] = {
            "n": len(rows), "clusters": bk[3],
            "kalshi": bk[0], "poly": bp[0], "blend": bb[0],
            "diff": dd[0], "diff_lo": dd[1], "diff_hi": dd[2],
            "logloss_kalshi": lk[0], "logloss_poly": lp[0],
        }

    # 2. Corridor-sensitivity table
    print("\nCORRIDOR SENSITIVITY (HIGH-confidence pairs, taker on Kalshi)")
    print(f"{'margin':>7} {'n':>5} {'clus':>5} {'trades/pair':>11} "
          f"{'net PnL c/ct':>26} {'net CLV c/ct':>26} {'hit%':>6}")
    table = []
    for margin in MARGINS:
        rows = [t for t in (trade(o, margin) for o in high) if t]
        if not rows:
            table.append({"margin": margin, "n": 0})
            print(f"{margin*100:>6.0f}c {0:>5}")
            continue
        pn = cluster_bootstrap(rows, lambda r: r["pnl"])
        cv = cluster_bootstrap(rows, lambda r: r["clv"])
        hit = sum(1 for r in rows if r["won"]) / len(rows)
        table.append({
            "margin": margin, "n": len(rows), "clusters": pn[3],
            "pnl": pn[0], "pnl_lo": pn[1], "pnl_hi": pn[2],
            "clv": cv[0], "clv_lo": cv[1], "clv_hi": cv[2],
            "clv_n": cv[3], "hit": hit,
        })
        print(f"{margin*100:>6.0f}c {len(rows):>5} {pn[3]:>5} "
              f"{len(rows)/max(1,len({r['kalshi_ticker'] for r in rows})):>11.2f} "
              f"{fmt(*pn):>26} {fmt(*cv):>26} {hit*100:>5.1f}%")
    out["corridor_table"] = table

    # 3. PnL / Brier by gap bucket at margin 0
    print("\nBY GAP BUCKET (margin=0, HIGH)")
    buckets = [(0.0, 0.03), (0.03, 0.06), (0.06, 0.10), (0.10, 0.20), (0.20, 1.0)]
    gb = []
    for lo_, hi_ in buckets:
        rows = [t for t in (trade(o, 0.0) for o in high) if t
                and lo_ <= abs(t["gap"]) < hi_]
        if not rows:
            continue
        pn = cluster_bootstrap(rows, lambda r: r["pnl"])
        cv = cluster_bootstrap(rows, lambda r: r["clv"])
        bd = cluster_bootstrap(rows, lambda r: brier(r["kmid"], r["y"]) - brier(r["pmid"], r["y"]))
        gb.append({"lo": lo_, "hi": hi_, "n": len(rows), "clusters": pn[3],
                   "pnl": pn[0], "pnl_lo": pn[1], "pnl_hi": pn[2],
                   "clv": cv[0], "clv_lo": cv[1], "clv_hi": cv[2],
                   "brier_diff": bd[0], "bd_lo": bd[1], "bd_hi": bd[2]})
        print(f"  |gap| {lo_*100:>3.0f}-{hi_*100:>3.0f}c  n={len(rows):>4} "
              f"clus={pn[3]:>3}  PnL {fmt(*pn)}  CLV {fmt(*cv)}  "
              f"dBrier {bd[0]:+.4f} [{bd[1]:+.4f},{bd[2]:+.4f}]")
    out["gap_buckets"] = gb

    # 4. By anchor horizon (margin 0.02)
    print("\nBY ANCHOR HORIZON (margin=2c, HIGH)")
    ah = []
    for H in ANCHORS_H:
        rows = [t for t in (trade(o, 0.02) for o in high if o["H"] == H) if t]
        if not rows:
            continue
        pn = cluster_bootstrap(rows, lambda r: r["pnl"])
        ah.append({"H": H, "n": len(rows), "pnl": pn[0],
                   "lo": pn[1], "hi": pn[2], "clusters": pn[3]})
        print(f"  T-{H:>4}h  n={len(rows):>4} clus={pn[3]:>3}  PnL {fmt(*pn)}")
    out["anchors"] = ah

    # 5. Leave-one-cluster-out robustness at margin 2c
    rows = [t for t in (trade(o, 0.02) for o in high) if t]
    if rows:
        byc = defaultdict(list)
        for r in rows:
            byc[r["cluster"]].append(r["pnl"])
        loo = []
        allv = [v for vs in byc.values() for v in vs]
        for c in byc:
            rest = [v for k, vs in byc.items() if k != c for v in vs]
            if rest:
                loo.append(sum(rest) / len(rest))
        pos = sum(1 for c, vs in byc.items() if sum(vs) / len(vs) > 0)
        print(f"\nLOO (margin=2c): min {min(loo)*100:+.2f}c  max {max(loo)*100:+.2f}c"
              f"   positive clusters {pos}/{len(byc)}")
        out["loo"] = {"min": min(loo), "max": max(loo),
                      "pos_clusters": pos, "n_clusters": len(byc),
                      "overall": sum(allv) / len(allv)}

    # 6. Lead-lag: does the gap predict the SUBSEQUENT Kalshi move?
    #    (higher-powered than PnL, and the test that killed P-021/P-024)
    print("\nLEAD-LAG  d(kalshi mid) over next 24h  vs  gap")
    for label, rows in (("HIGH", high), ("HIGH+MED",
                                         [o for o in obs if o["tier"] in ("HIGH", "MED")])):
        rr = [r for r in rows if r["fut_mid"] is not None]
        if len(rr) < 20:
            continue
        num = cluster_bootstrap(rr, lambda r: (r["fut_mid"] - r["kmid"]) * (r["pmid"] - r["kmid"]))
        den = sum((r["pmid"] - r["kmid"]) ** 2 for r in rr) / len(rr)
        beta = num[0] / den if den else float("nan")
        blo, bhi = (num[1] / den, num[2] / den) if den else (float("nan"),) * 2
        print(f"  [{label}] n={len(rr)} clus={num[3]}  beta={beta:+.3f} "
              f"CI[{blo:+.3f},{bhi:+.3f}]  (1.0 = Kalshi fully converges to Poly)")
        out[f"leadlag_{label}"] = {"n": len(rr), "clusters": num[3],
                                   "beta": beta, "lo": blo, "hi": bhi}

    # 7. Optimal blend weight on Poly (Brier-minimising), event-clustered
    print("\nBLEND WEIGHT on Poly (Brier), HIGH")
    for w in (0.0, 0.1, 0.25, 0.5, 0.75, 1.0):
        b = cluster_bootstrap(high, lambda r, w=w: brier((1 - w) * r["kmid"] + w * r["pmid"], r["y"]))
        print(f"  w={w:<4} Brier {b[0]:.4f}  CI[{b[1]:.4f},{b[2]:.4f}]")
        out.setdefault("blend", []).append({"w": w, "brier": b[0],
                                            "lo": b[1], "hi": b[2]})

    # 8. Basis distribution — direct confrontation with the ev-map H2 prior
    #    ("the Kalshi<->Poly basis is fee-bounded inside a ~+/-2c corridor")
    print("\nBASIS DISTRIBUTION (|poly_mid - kalshi_mid|)")
    import statistics as st
    for label, rows in (("HIGH", high), ("HIGH+MED",
                                         [o for o in obs if o["tier"] in ("HIGH", "MED")])):
        g = sorted(abs(r["pmid"] - r["kmid"]) for r in rows)
        if not g:
            continue
        corr = [fee_per_contract(r["kmid"]) + r["spread"] / 2.0 for r in rows]
        over = sum(1 for r in rows
                   if abs(r["pmid"] - r["kmid"]) >
                   fee_per_contract(r["kmid"]) + r["spread"] / 2.0)
        d = {"n": len(g), "median": st.median(g),
             "p75": g[int(.75 * len(g))], "p90": g[int(.90 * len(g))],
             "pct_over_2c": sum(1 for x in g if x > 0.02) / len(g),
             "pct_over_corridor": over / len(rows),
             "median_corridor": st.median(corr)}
        print(f"  [{label}] n={d['n']}  median |gap| {d['median']*100:.2f}c  "
              f"p75 {d['p75']*100:.2f}c  p90 {d['p90']*100:.2f}c  "
              f"| median corridor {d['median_corridor']*100:.2f}c  "
              f"| >2c: {d['pct_over_2c']:.0%}  >corridor: {d['pct_over_corridor']:.0%}")
        out[f"basis_{label}"] = d

    # 9. Same corridor sweep on the wider HIGH+MED universe (more clusters)
    print("\nCORRIDOR SENSITIVITY (HIGH+MED — 3x the event clusters)")
    hm = [o for o in obs if o["tier"] in ("HIGH", "MED")]
    tab2 = []
    for margin in MARGINS:
        rows = [t for t in (trade(o, margin) for o in hm) if t]
        if not rows:
            continue
        pn = cluster_bootstrap(rows, lambda r: r["pnl"])
        cv = cluster_bootstrap(rows, lambda r: r["clv"])
        tab2.append({"margin": margin, "n": len(rows), "clusters": pn[3],
                     "pnl": pn[0], "pnl_lo": pn[1], "pnl_hi": pn[2],
                     "clv": cv[0], "clv_lo": cv[1], "clv_hi": cv[2]})
        print(f"{margin*100:>6.0f}c {len(rows):>5} {pn[3]:>5}  "
              f"PnL {fmt(*pn):>26}  CLV {fmt(*cv):>26}")
    out["corridor_table_highmed"] = tab2

    # 10. Split on which venue is actually deeper
    print("\nSPLIT BY WHICH VENUE IS DEEPER (HIGH+MED, margin=0)")
    for label, sel in (("Poly deeper", lambda r: r["poly_vol"] > r["kalshi_vol"]),
                       ("Kalshi deeper", lambda r: r["poly_vol"] <= r["kalshi_vol"])):
        rows = [t for t in (trade(o, 0.0) for o in hm if sel(o)) if t]
        if len(rows) < 5:
            print(f"  {label}: n={len(rows)} (too few)")
            continue
        pn = cluster_bootstrap(rows, lambda r: r["pnl"])
        bd = cluster_bootstrap(rows, lambda r: brier(r["kmid"], r["y"]) - brier(r["pmid"], r["y"]))
        print(f"  {label:<14} n={len(rows):>4} clus={pn[3]:>3}  PnL {fmt(*pn)}  "
              f"dBrier {bd[0]:+.4f} [{bd[1]:+.4f},{bd[2]:+.4f}]")
        out[f"depth_{label.replace(' ', '_')}"] = {
            "n": len(rows), "clusters": pn[3], "pnl": pn[0],
            "lo": pn[1], "hi": pn[2], "brier_diff": bd[0],
            "bd_lo": bd[1], "bd_hi": bd[2]}

    json.dump(out, open(os.path.join(os.path.dirname(DATA), "results.json"), "w"),
              indent=1, default=float)
    print("\nwrote crossvenue_research/results.json")


if __name__ == "__main__":
    main()
