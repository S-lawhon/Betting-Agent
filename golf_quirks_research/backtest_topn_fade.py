"""
golf_quirks_research/backtest_topn_fade.py
──────────────────────────────────────────
P-023c — the over-priced top-N FLIP SIDE.

P-023a killed the top-N *buy* basket: the tie/boundary inflation written into
GOLFFINISH.pdf is real, but the pre-event anchor already embeds it. The kill
numbers were not zero, though —

    PGA top-10/20 (the P-017 control)  -3.2c/ct
    round-based top-N                  -2.3c to -11.4c/ct

— i.e. the market looks systematically *over*-priced on these boundary names.
Nobody has ever asked whether the SHORT side of that is tradeable. This script
asks, in the order the prompt pre-registers:

  1. REPLICATION  — reproduce the published P-023a buy-side cells exactly.
                    (If the base number cannot be reproduced the flip side is
                    moot. Anchors: H=48h for full-tournament top-N, H=12h for
                    round-based top-N; band 0.08-0.45; scalar rows treated as
                    voids, per REPORT_Golf_Quirks_2026-07.md 1b/4.2.)
  2. DECOMPOSITION— split the raw fade gross into: what a fade actually pays
                    to get out (spread), what Kalshi charges (fee), what the
                    settlement rulebook contributes (mechanic), and the
                    unexplained residual. The prompt's gate: the fade only has
                    a THESIS if a mechanical component survives.
  3. SEGMENTATION — by anchor horizon and price band, tournament-clustered,
                    so a single-cell survivor can be recognised as noise.
  4. DIAGNOSTICS  — anchor staleness (the Task-7 lesson: a stale anchor
                    manufactured a +9.5c false ADVANCE on make-cut), and
                    anchor COMPOSITION (traded-bar anchors are print-side
                    biased; a fade edge that lives only there is an artifact).

Everything is settled-data Phase 1. Fill realism (Phase 2) is a separate
script and is only warranted if this clears.

House methodology (quirks_common.py): tournament-clustered bootstrap, never
per-contract; executed prints or tight two-sided quotes only, never bare asks;
settlement read from `settlement_value_dollars`.

Usage
  python3 backtest_topn_fade.py                 # validates the harness first
  python3 backtest_topn_fade.py --skip-validate
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import quirks_common as qc  # noqa: E402
import backtest_fade_fills as fade  # noqa: E402
from src.kalshi_fees import fee_per_contract  # noqa: E402

BAND = (0.08, 0.45)

# cohort -> (series, published anchor H, published (n, tourn, px, rv, edge))
COHORTS: Dict[str, Dict[str, Any]] = {
    "pga_top1020": dict(series=("KXPGATOP10", "KXPGATOP20"), h=48.0,
                        label="CONTROL PGA top-10/20",
                        pub=(1627, 12, 0.199, 0.167, -0.032)),
    "pga_top5":    dict(series=("KXPGATOP5",), h=48.0, label="PGA top-5",
                        pub=(351, 12, 0.159, 0.122, -0.037)),
    "pga_top40":   dict(series=("KXPGATOP40",), h=48.0, label="PGA top-40",
                        pub=(190, 3, 0.290, 0.305, +0.015)),
    "r1top5":      dict(series=("KXPGAR1TOP5",), h=12.0, label="R1 top-5",
                        pub=(224, 10, 0.133, 0.085, -0.048)),
    "r1top10":     dict(series=("KXPGAR1TOP10",), h=12.0, label="R1 top-10",
                        pub=(375, 10, 0.205, 0.139, -0.066)),
    "r2top10":     dict(series=("KXPGAR2TOP10",), h=12.0, label="R2 top-10",
                        pub=(125, 9, 0.255, 0.232, -0.023)),
    "r3top10":     dict(series=("KXPGAR3TOP10",), h=12.0, label="R3 top-10",
                        pub=(82, 9, 0.272, 0.159, -0.114)),
}

# Horizons swept per cohort family.
H_GRID_FULL = (72.0, 48.0, 24.0, 12.0)
H_GRID_ROUND = (24.0, 12.0, 6.0, 3.0)

PRICE_BANDS = ((0.08, 0.15), (0.15, 0.25), (0.25, 0.35), (0.35, 0.45))


# ── anchor with its quote attached ───────────────────────────────────────

def anchor_row(rec: Dict[str, Any], hours: float) -> Optional[Dict[str, Any]]:
    """The P-023a anchor candle, plus the bid/ask that stood on it.

    `qc.anchor_at` returns only (t, price) because the published gate only
    ever needed the reference price. A fade has to SELL, so we need the whole
    quote: the executable sell price is the BID, not the anchor.
    """
    close = qc.iso_epoch(rec.get("close_time"))
    if close is None:
        return None
    target = close - hours * 3600.0
    best = None
    for c in rec.get("candles") or []:
        end = c.get("end_period_ts")
        if end is None or end > target:
            continue
        px = qc.candle_price(c)
        if px is None:
            continue
        if best is None or end > best[0]:
            best = (float(end), px, c)
    if best is None:
        return None
    end, px, c = best
    bid = qc.fnum((c.get("yes_bid") or {}).get("close_dollars"))
    ask = qc.fnum((c.get("yes_ask") or {}).get("close_dollars"))
    two_sided = (bid is not None and ask is not None and bid > 0 and ask < 1
                 and ask >= bid and (ask - bid) <= qc.ANCHOR_MAX_SPREAD)
    traded = ((c.get("price") or {}).get("close_dollars") is not None
              and (qc.fnum(c.get("volume_fp")) or 0.0) > 0)
    return {
        "anchor_epoch": end, "anchor_price": px,
        "bid": bid if two_sided else None,
        "ask": ask if two_sided else None,
        "two_sided": two_sided,
        "from_trade": traded,
        "candle_volume": qc.fnum(c.get("volume_fp")) or 0.0,
        "lag_h": (close - end) / 3600.0,
        "staleness_h": max(0.0, target - end) / 3600.0,
    }


def build(series: Sequence[str], hours: float,
          band: Tuple[float, float] = BAND,
          include_scalar: bool = False) -> List[Dict[str, Any]]:
    lo, hi = band
    out: List[Dict[str, Any]] = []
    for rec in qc.iter_candle_recs(series):
        a = anchor_row(rec, hours)
        if a is None or not (lo <= a["anchor_price"] <= hi):
            continue
        if rec.get("result") == "scalar" and not include_scalar:
            continue
        sv = qc.settlement_value(rec)
        if sv is None:
            continue
        row = dict(a)
        row.update({
            "ticker": rec["ticker"], "event": rec.get("event", ""),
            "tournament": qc.tournament_of(rec.get("event", "")),
            "series": qc.series_of(rec["ticker"]),
            "result": rec.get("result"), "sv": sv,
        })
        out.append(row)
    out.sort(key=lambda r: r["ticker"])
    return out


# ── statistics ───────────────────────────────────────────────────────────

def stat(rows: Sequence[Dict[str, Any]], value_fn) -> Dict[str, Any]:
    ent = [(r["tournament"], value_fn(r)) for r in rows
           if value_fn(r) is not None]
    if not ent:
        return {"n": 0}
    m, lo, hi = qc.bootstrap_unweighted(ent)
    by: Dict[str, List[float]] = defaultdict(list)
    for t, v in ent:
        by[t].append(v)
    per = {t: sum(v) / len(v) for t, v in by.items()}
    return {
        "n": len(ent), "tournaments": len(by),
        "mean": qc.r4(m), "ci_lo": qc.r4(lo), "ci_hi": qc.r4(hi),
        "tourn_positive": sum(1 for v in per.values() if v > 0),
        "per_tournament": {t: round(v, 4) for t, v in sorted(per.items())},
    }


def loo(rows: Sequence[Dict[str, Any]], value_fn) -> Dict[str, float]:
    ent = [(r["tournament"], value_fn(r)) for r in rows
           if value_fn(r) is not None]
    keys = sorted({t for t, _ in ent})
    out = {}
    for k in keys:
        sub = [v for t, v in ent if t != k]
        out[k] = round(sum(sub) / len(sub), 4) if sub else float("nan")
    return out


# ── the four fade prices ─────────────────────────────────────────────────
# Every one is "sell YES at X, receive X, pay out the settlement value".

def f_gross(r):        # sell at the anchor reference (the mirror of P-023a)
    return r["anchor_price"] - r["sv"]


def f_bid(r):          # TAKER fade: hit the bid, pay the taker fee
    if r["bid"] is None:
        return None
    return r["bid"] - r["sv"] - fee_per_contract(r["bid"], maker=False)


def f_bid_nofee(r):    # hit the bid, fee excluded (isolates the spread term)
    if r["bid"] is None:
        return None
    return r["bid"] - r["sv"]


def f_ask(r):          # MAKER fade: rest an offer AT the ask, fee 0
    if r["ask"] is None:
        return None
    return r["ask"] - r["sv"]


def yes_count_inflation() -> Dict[str, Dict[str, Any]]:
    """Mean YES settlements per event vs the nominal N, per series.

    Reproduces REPORT_Golf_Quirks_2026-07.md 4.1. `result="scalar"` on a top-N
    market is a WITHDRAWAL REFUND (GOLFFINISH: "Withdrawal Before Tee-Off...
    resolves to the last fair price"), not a tie payout, so scalars are not
    counted as YES.
    """
    nominal = {"KXPGATOP5": 5, "KXPGATOP10": 10, "KXPGATOP20": 20,
               "KXPGATOP40": 40, "KXPGAR1TOP5": 5, "KXPGAR1TOP10": 10,
               "KXPGAR2TOP10": 10, "KXPGAR3TOP10": 10}
    per_event: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(
        lambda: defaultdict(int))
    with open(qc.SETTLED_META) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            s = r.get("series")
            if s not in nominal:
                continue
            per_event[(s, r.get("event_ticker"))][r.get("result")] += 1
    agg: Dict[str, List[int]] = defaultdict(list)
    for (s, _), c in per_event.items():
        if sum(c.values()) < nominal[s]:
            continue                       # partial event in the cache
        agg[s].append(c.get("yes", 0))
    return {s: {"nominal": nominal[s], "events": len(v),
                "mean_yes": sum(v) / len(v)}
            for s, v in agg.items() if v}


def round_end_reference() -> Dict[str, Dict[str, float]]:
    """Modal close epoch of each round-leader series, per tournament.

    Kalshi sets many per-market `close_time`s to a conservative event-level
    default (CLAUDE.md), so neither min nor max is the real round end — the
    MODE is. Used only to answer "is the so-called pre-tournament anchor
    actually pre-tournament?".
    """
    raw: Dict[str, Dict[str, List[float]]] = defaultdict(
        lambda: defaultdict(list))
    with open(qc.SETTLED_META) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            s = r.get("series")
            if s not in ("KXPGAR1LEAD", "KXPGAR2LEAD", "KXPGAR3LEAD"):
                continue
            e = qc.iso_epoch(r.get("close_time"))
            if e:
                raw[qc.tournament_of(r.get("event_ticker", ""))][s].append(e)
    out: Dict[str, Dict[str, float]] = {}
    for t, d in raw.items():
        out[t] = {}
        for s, v in d.items():
            hist: Dict[int, int] = defaultdict(int)
            for x in v:
                hist[round(x / 3600)] += 1
            out[t][s] = max(hist.items(), key=lambda kv: kv[1])[0] * 3600.0
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="topn_fade_results.json")
    ap.add_argument("--skip-validate", action="store_true")
    args = ap.parse_args(argv)

    if not args.skip_validate:
        print("=== harness validation vs published P-022 Phase 2 ===")
        if not fade.validate(fade.load()):
            print("\nABORT: harness does not reproduce P-022. Trust nothing.")
            return 1
        print()

    payload: Dict[str, Any] = {"band": list(BAND), "cohorts": {}}

    # ── 1. replication ────────────────────────────────────────────────
    print("=== 1. REPLICATION of the published P-023a buy-side gate ===")
    print(f"{'cohort':<22} {'H':>4} {'n (pub)':>13} {'trn':>7} "
          f"{'px (pub)':>15} {'realized (pub)':>17} {'buy edge (pub)':>17}  ok")
    all_ok = True
    for key, cfg in COHORTS.items():
        rows = build(cfg["series"], cfg["h"])
        pub = cfg["pub"]
        s = stat(rows, lambda r: r["sv"] - r["anchor_price"])
        px = sum(r["anchor_price"] for r in rows) / len(rows)
        rv = sum(r["sv"] for r in rows) / len(rows)
        ok = (s["n"] == pub[0] and s["tournaments"] == pub[1]
              and abs(px - pub[2]) <= 0.002 and abs(rv - pub[3]) <= 0.002
              and abs(s["mean"] - pub[4]) <= 0.002)
        all_ok &= ok
        print(f"{cfg['label']:<22} {cfg['h']:>4g} {s['n']:>6d}/{pub[0]:<6d} "
              f"{s['tournaments']:>3d}/{pub[1]:<3d} "
              f"{px:>7.3f}/{pub[2]:<7.3f} {rv:>8.3f}/{pub[3]:<8.3f} "
              f"{s['mean']:>+8.3f}/{pub[4]:<+8.3f}  "
              f"{'PASS' if ok else 'FAIL'}")
        payload["cohorts"][key] = {"label": cfg["label"], "anchor_h": cfg["h"],
                                   "replication": {"got": s, "published": pub,
                                                   "pass": ok}}
    print(f"\nREPLICATION: {'PASS' if all_ok else 'FAIL'}"
          f"  (all 7 published P-023a cells)\n")
    payload["replication_pass"] = all_ok
    if not all_ok:
        print("Base number not reproduced — the flip-side question is moot.")
        return 1

    # ── 2. decomposition ──────────────────────────────────────────────
    print("=== 2. DECOMPOSITION of the fade gross ===")
    print("gross = anchor - realized. Then: what it costs to actually sell.")
    hdr = (f"{'cohort':<22} {'n':>5} {'trn':>4} {'anchor':>7} {'bid':>6} "
           f"{'ask':>6} {'spr':>6} {'realized':>9} | {'gross c':>8} "
           f"{'@bid c':>8} {'fee c':>7} {'@bid-fee c':>11} {'@ask c':>8}")
    print(hdr)
    print("-" * len(hdr))
    for key, cfg in COHORTS.items():
        rows = build(cfg["series"], cfg["h"])
        q = [r for r in rows if r["two_sided"]]
        g = stat(rows, f_gross)
        b = stat(q, f_bid)
        bn = stat(q, f_bid_nofee)
        a = stat(q, f_ask)
        if not q:
            print(f"{cfg['label']:<22} {len(rows):>5d} — no two-sided anchors")
            continue
        mbid = sum(r["bid"] for r in q) / len(q)
        mask = sum(r["ask"] for r in q) / len(q)
        manc = sum(r["anchor_price"] for r in q) / len(q)
        mrv = sum(r["sv"] for r in q) / len(q)
        mfee = sum(fee_per_contract(r["bid"], maker=False) for r in q) / len(q)
        print(f"{cfg['label']:<22} {len(q):>5d} {b['tournaments']:>4d} "
              f"{manc:>7.3f} {mbid:>6.3f} {mask:>6.3f} {mask - mbid:>6.3f} "
              f"{mrv:>9.3f} | {g['mean'] * 100:>+8.1f} "
              f"{bn['mean'] * 100:>+8.1f} {mfee * 100:>7.2f} "
              f"{b['mean'] * 100:>+11.1f} {a['mean'] * 100:>+8.1f}")
        payload["cohorts"][key]["decomposition"] = {
            "n_two_sided": len(q), "n_total": len(rows),
            "mean_anchor": qc.r4(manc), "mean_bid": qc.r4(mbid),
            "mean_ask": qc.r4(mask), "mean_spread": qc.r4(mask - mbid),
            "mean_realized": qc.r4(mrv), "mean_taker_fee_at_bid": qc.r4(mfee),
            "fade_gross_all": g, "fade_at_bid_nofee": bn,
            "fade_at_bid_net": b, "fade_at_ask_makerfee0": a,
        }

    # ── 2b. anchor composition ────────────────────────────────────────
    print("\n=== 2b. ANCHOR COMPOSITION — does the over-pricing live only in "
          "traded-bar anchors? ===")
    print("A last-trade anchor is print-side biased (retail lifts offers on "
          "boundary names).")
    hdr2 = (f"{'cohort':<22} {'from trade n':>13} {'fade c':>8} {'CI':>18} | "
            f"{'from mid n':>11} {'fade c':>8} {'CI':>18}")
    print(hdr2)
    print("-" * len(hdr2))
    for key, cfg in COHORTS.items():
        rows = build(cfg["series"], cfg["h"])
        tr = [r for r in rows if r["from_trade"]]
        md = [r for r in rows if not r["from_trade"]]
        st = stat(tr, f_gross)
        sm = stat(md, f_gross)
        def f(s):
            if not s.get("n"):
                return f"{0:>13d} {'—':>8} {'—':>18}"
            ci = ("n/a" if s["ci_lo"] is None
                  else f"[{s['ci_lo'] * 100:+.1f},{s['ci_hi'] * 100:+.1f}]")
            return f"{s['n']:>13d} {s['mean'] * 100:>+8.1f} {ci:>18}"
        print(f"{cfg['label']:<22} {f(st)} | {f(sm).replace('           ', '', 1)}")
        payload["cohorts"][key]["anchor_composition"] = {
            "from_trade": st, "from_two_sided_mid": sm}

    # ── 2c. print-side bias in the anchor ─────────────────────────────
    print("\n=== 2c. IS THE ANCHOR ITSELF BIASED HIGH? "
          "(anchor vs the contemporaneous mid) ===")
    print("The P-023a anchor prefers the candle's EXECUTED close. If prints on "
          "boundary top-N\nnames are predominantly buys lifting the offer, the "
          "anchor is an ask, not a fair value —\nand a 'fade edge' measured "
          "against it is partly a measurement artifact.")
    hdr2c = (f"{'cohort':<22} {'n traded+quoted':>16} {'anchor':>8} "
             f"{'mid':>8} {'anchor-mid':>11} {'% anchor==ask':>14} "
             f"{'% anchor<=bid':>14}")
    print(hdr2c)
    for key, cfg in COHORTS.items():
        rows = [r for r in build(cfg["series"], cfg["h"])
                if r["two_sided"] and r["from_trade"]]
        if len(rows) < 5:
            print(f"{cfg['label']:<22} {len(rows):>16d}  (too thin)")
            continue
        a = sum(r["anchor_price"] for r in rows) / len(rows)
        m = sum((r["bid"] + r["ask"]) / 2 for r in rows) / len(rows)
        eq = 100 * sum(1 for r in rows
                       if abs(r["anchor_price"] - r["ask"]) < 1e-9) / len(rows)
        le = 100 * sum(1 for r in rows
                       if r["anchor_price"] <= r["bid"] + 1e-9) / len(rows)
        print(f"{cfg['label']:<22} {len(rows):>16d} {a:>8.4f} {m:>8.4f} "
              f"{a - m:>+11.4f} {eq:>13.0f}% {le:>13.0f}%")
        payload["cohorts"][key]["anchor_vs_mid"] = {
            "n": len(rows), "mean_anchor": qc.r4(a), "mean_mid": qc.r4(m),
            "anchor_minus_mid": qc.r4(a - m),
            "pct_anchor_at_ask": round(eq, 1),
            "pct_anchor_at_or_below_bid": round(le, 1)}

    print("\n  same rows, fade priced three ways:")
    for key, cfg in COHORTS.items():
        rows = [r for r in build(cfg["series"], cfg["h"]) if r["two_sided"]]
        if len(rows) < 10:
            continue
        sa, sm, sb = (stat(rows, f_gross),
                      stat(rows, lambda r: (r["bid"] + r["ask"]) / 2 - r["sv"]),
                      stat(rows, f_bid_nofee))
        print(f"    {cfg['label']:<22} n={len(rows):>4d}  at-anchor "
              f"{sa['mean'] * 100:>+5.1f}  at-mid {sm['mean'] * 100:>+5.1f} "
              f"[{sm['ci_lo'] * 100:+.1f},{sm['ci_hi'] * 100:+.1f}]  "
              f"at-bid {sb['mean'] * 100:>+5.1f}")
        payload["cohorts"][key]["fade_at_mid"] = sm

    # ── 2d. the mechanical term ───────────────────────────────────────
    print("\n=== 2d. THE MECHANICAL TERM — which way does the rulebook "
          "point? ===")
    print("GOLFFINISH ties pay YES in FULL, so more markets settle YES than "
          "the nominal N.\nThat INFLATES the realized value a fade must pay "
          "out: it is a headwind, not a thesis.")
    counts = yes_count_inflation()
    hdr2d = (f"{'cohort':<22} {'nominal N':>10} {'events':>7} {'mean YES':>9} "
             f"{'YES/N':>7} {'excess':>8} {'realized':>9} "
             f"{'no-tie realized':>16} {'headwind c/ct':>14}")
    print(hdr2d)
    for key, cfg in COHORTS.items():
        rows = build(cfg["series"], cfg["h"])
        rv = sum(r["sv"] for r in rows) / len(rows)
        tot_y = tot_n = 0.0
        nev = 0
        for s in cfg["series"]:
            c = counts.get(s)
            if not c:
                continue
            tot_y += c["mean_yes"] * c["events"]
            tot_n += c["nominal"] * c["events"]
            nev += c["events"]
        if not nev:
            continue
        ratio = tot_y / tot_n
        excess = (ratio - 1.0) / ratio          # share of YES that are excess
        rv_no_tie = rv * (1.0 - excess)
        print(f"{cfg['label']:<22} {tot_n / nev:>10.0f} {nev:>7d} "
              f"{tot_y / nev:>9.2f} {ratio:>7.3f} {excess * 100:>7.1f}% "
              f"{rv:>9.3f} {rv_no_tie:>16.3f} {-(rv - rv_no_tie) * 100:>+13.1f}")
        payload["cohorts"][key]["mechanical_term"] = {
            "nominal_N": round(tot_n / nev, 1), "events": nev,
            "mean_yes_settlements": round(tot_y / nev, 2),
            "yes_over_n": round(ratio, 4),
            "excess_share_of_yes": round(excess, 4),
            "realized": qc.r4(rv), "counterfactual_no_tie_realized":
                qc.r4(rv_no_tie),
            "fade_headwind_per_contract": qc.r4(-(rv - rv_no_tie)),
        }

    # ── 3. segmentation ───────────────────────────────────────────────
    print("\n=== 3a. SEGMENTATION by anchor horizon (fade gross, "
          "tournament-clustered) ===")
    for key, cfg in COHORTS.items():
        grid = H_GRID_FULL if cfg["h"] == 48.0 else H_GRID_ROUND
        seg = {}
        parts = []
        for h in grid:
            rows = build(cfg["series"], h)
            s = stat(rows, f_gross)
            q = [r for r in rows if r["two_sided"]]
            sb = stat(q, f_bid)
            seg[f"H{h:g}"] = {"gross": s, "at_bid_net": sb}
            if s.get("n"):
                ci = ("n/a" if s["ci_lo"] is None
                      else f"[{s['ci_lo'] * 100:+.1f},{s['ci_hi'] * 100:+.1f}]")
                nb = (f"{sb['mean'] * 100:+.1f}" if sb.get("n") else "—")
                parts.append(f"H{h:g}: n={s['n']} {s['mean'] * 100:+.1f} "
                             f"{ci} @bid {nb}")
        print(f"  {cfg['label']:<22} " + " | ".join(parts))
        payload["cohorts"][key]["by_horizon"] = seg

    print("\n=== 3b. SEGMENTATION by price band (fade gross at the "
          "published anchor) ===")
    hdr3 = (f"{'cohort':<22} " + " ".join(f"{f'{lo:.2f}-{hi:.2f}':>17}"
                                          for lo, hi in PRICE_BANDS))
    print(hdr3)
    for key, cfg in COHORTS.items():
        cells = []
        seg = {}
        for lo, hi in PRICE_BANDS:
            rows = build(cfg["series"], cfg["h"], band=(lo, hi))
            s = stat(rows, f_gross)
            seg[f"{lo:.2f}-{hi:.2f}"] = s
            cells.append(f"{s['n']:>5d} {s['mean'] * 100:>+6.1f}     "
                         if s.get("n") else f"{'—':>17} ")
        print(f"{cfg['label']:<22} " + " ".join(cells))
        payload["cohorts"][key]["by_price_band"] = seg

    # ── 4. diagnostics ────────────────────────────────────────────────
    print("\n=== 4a. ANCHOR STALENESS (the Task-7 make-cut lesson) ===")
    print("staleness = how old the anchor price already is at T = close - H. "
          "A quote posted\nat T off a stale anchor is not an executable "
          "model; Phase 2 must post contemporaneously.")
    hdr4 = (f"{'cohort':<22} {'n':>5} {'median lag_h':>13} {'H':>5} "
            f"{'median stale_h':>15} {'% stale>2h':>11} {'% stale>12h':>12}")
    print(hdr4)
    for key, cfg in COHORTS.items():
        rows = build(cfg["series"], cfg["h"])
        st = sorted(r["staleness_h"] for r in rows)
        lg = sorted(r["lag_h"] for r in rows)
        med = st[len(st) // 2]
        print(f"{cfg['label']:<22} {len(rows):>5d} {lg[len(lg) // 2]:>13.1f} "
              f"{cfg['h']:>5g} {med:>15.1f} "
              f"{100 * sum(1 for x in st if x > 2) / len(st):>10.0f}% "
              f"{100 * sum(1 for x in st if x > 12) / len(st):>11.0f}%")
        payload["cohorts"][key]["anchor_staleness"] = {
            "median_lag_h": round(lg[len(lg) // 2], 2),
            "median_staleness_h": round(med, 2),
            "pct_stale_gt_2h": round(100 * sum(1 for x in st if x > 2) / len(st), 1),
            "pct_stale_gt_12h": round(100 * sum(1 for x in st if x > 12) / len(st), 1),
        }

    print("\n=== 4b. IS THE 'PRE-TOURNAMENT' ANCHOR ACTUALLY "
          "PRE-TOURNAMENT? ===")
    print("REPORT_Golf_Quirks_2026-07.md 4.2 describes the full-tournament "
          "anchor as '48h, before R1'.\nMeasured against the modal "
          "round-leader close for the same tournament:")
    ref = round_end_reference()
    hdr4b = (f"{'cohort':<22} {'n':>5} {'covered':>8} {'after R1 end':>13} "
             f"{'after R2 end':>13} {'after R3 end':>13}")
    print(hdr4b)
    for key, cfg in COHORTS.items():
        rows = build(cfg["series"], cfg["h"])
        have = 0
        after = {"R1": 0, "R2": 0, "R3": 0}
        for r in rows:
            d = ref.get(r["tournament"])
            if not d:
                continue
            have += 1
            for lab, ser in (("R1", "KXPGAR1LEAD"), ("R2", "KXPGAR2LEAD"),
                             ("R3", "KXPGAR3LEAD")):
                if ser in d and r["anchor_epoch"] > d[ser]:
                    after[lab] += 1
        if not have:
            continue
        print(f"{cfg['label']:<22} {len(rows):>5d} {have:>8d} "
              + " ".join(f"{100 * after[l] / have:>12.0f}%"
                         for l in ("R1", "R2", "R3")))
        payload["cohorts"][key]["anchor_vs_round_ends"] = {
            "covered": have,
            "pct_after_R1": round(100 * after["R1"] / have, 1),
            "pct_after_R2": round(100 * after["R2"] / have, 1),
            "pct_after_R3": round(100 * after["R3"] / have, 1)}

    print("\n=== 4c. SCALAR ROWS (withdrawal refunds) — the void assumption ===")
    for key, cfg in COHORTS.items():
        wo = build(cfg["series"], cfg["h"], include_scalar=False)
        wi = build(cfg["series"], cfg["h"], include_scalar=True)
        n_sc = len(wi) - len(wo)
        s_wo = stat(wo, f_gross)
        s_wi = stat(wi, f_gross)
        print(f"  {cfg['label']:<22} scalar rows in band: {n_sc:>3d}  "
              f"fade excl {s_wo['mean'] * 100:+.1f}c  "
              f"incl-at-refund {s_wi['mean'] * 100:+.1f}c")
        payload["cohorts"][key]["scalar_sensitivity"] = {
            "n_scalar_in_band": n_sc, "fade_excl": s_wo["mean"],
            "fade_incl_at_refund": s_wi["mean"]}

    # ── 5. leave-one-out on the fade gross and at the bid ─────────────
    print("\n=== 5. LEAVE-ONE-OUT (tournament jackknife) ===")
    for key, cfg in COHORTS.items():
        rows = build(cfg["series"], cfg["h"])
        l = loo(rows, f_gross)
        q = [r for r in rows if r["two_sided"]]
        lb = loo(q, f_bid)
        if not l:
            continue
        pos = sum(1 for v in l.values() if v > 0)
        posb = sum(1 for v in lb.values() if v > 0) if lb else 0
        print(f"  {cfg['label']:<22} gross [{min(l.values()) * 100:+.1f},"
              f"{max(l.values()) * 100:+.1f}] {pos}/{len(l)} positive | "
              f"@bid-net [{min(lb.values()) * 100:+.1f},"
              f"{max(lb.values()) * 100:+.1f}] {posb}/{len(lb)} positive"
              if lb else "")
        payload["cohorts"][key]["leave_one_out"] = {
            "gross": l, "at_bid_net": lb}

    out = (args.out if os.path.isabs(args.out)
           else os.path.join(qc.HERE, args.out))
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
