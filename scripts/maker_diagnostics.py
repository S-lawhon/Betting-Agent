#!/usr/bin/env python3
"""
scripts/maker_diagnostics.py
────────────────────────────
Tier-1 diagnostics for the maker pilots — P-016 (Live MLB maker) and
P-017M (Golf fade maker), selectable with ``--pod``.

MEASURES ONLY — never changes parameters.  Per the recursion design
(live_agent_research/RECURSIVE_REVIEW_DESIGN.md), adaptation and
judgment are kept separate from measurement so that tuning cannot
contaminate the pre-registered gate.

Report:
  0. Promote/kill scoreboard  — markout-adjusted net P&L per fill, net of
                                the series-aware maker fee, with an
                                event-clustered CI, the value after
                                dropping the single best day, and the
                                VPIN interaction (below- vs above-median).
  1. Coverage / health        — games/books, quote uptime, feed gaps
  2. Fill distance from mid    — where fills actually happen (P-016)
  3. Adverse selection         — markouts by seconds-since-state-change
  4. Leverage calibration      — markouts by |dWP/drun| (P-016)
  5. Directional bias          — markouts by side and price decile
  6. Model calibration         — fair-value buckets vs realized (P-016)
  7. Guardrail cost            — shadow (counterfactual) fills (P-016)
  8. Markout curve             — mean net markout at each horizon + CI
  9. Order-flow toxicity       — net markout by VPIN tercile
 10. Event-clocked markout     — next-event vs wall-clock (P-016)
 11. Proposals                 — deterministic, threshold-based

Milestone logic: emits a FULL review when champion fills cross the next
multiple of --milestone (default 150); otherwise a short health line.
State kept in data/trade_logs/.<pod>_diag_state.json.

Usage:
    python3 -m scripts.maker_diagnostics [--pod P-016|P-017M]
        [--fills PATH] [--quotes PATH] [--milestone 150] [--force-full]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_fees import fee_per_contract  # noqa: E402


# ── Per-pod configuration ────────────────────────────────────────────

POD_CONFIG: Dict[str, dict] = {
    "P-016": {
        "label": "P-016 LIVE MAKER (Kalshi MLB)",
        "fills": "data/trade_logs/maker_fills.jsonl",
        "quotes": "data/trade_logs/maker_quotes.jsonl",
        "state": "data/trade_logs/.maker_diag_state.json",
        "horizons": [60.0, 300.0, 900.0],
        "headline_horizon": 300.0,     # the "+5m" gate number
        "cluster_key": "game_pk",      # sample unit = game
        "has_shadow": True,
        "has_next_event": True,
        "has_state_change": True,
        "has_leverage": True,
    },
    "P-017M": {
        "label": "P-017M GOLF FADE MAKER (Kalshi top-N)",
        "fills": "data/trade_logs/golf_maker_fills.jsonl",
        "quotes": "data/trade_logs/golf_maker_quotes.jsonl",
        "state": "data/trade_logs/.golf_maker_diag_state.json",
        "horizons": [300.0, 900.0, 3600.0],
        "headline_horizon": 300.0,
        "cluster_key": "event",        # sample unit = tournament
        "has_shadow": False,
        "has_next_event": False,
        "has_state_change": False,
        "has_leverage": False,
    },
}


# ── Loading ──────────────────────────────────────────────────────────

def load_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def index_fills(records: List[dict]):
    """Returns (fills, markouts, settles, next_event).

    markouts:   fill_id → {horizon_s → record}   (wall-clock only)
    next_event: fill_id → record                 (one per fill, P-016)
    A MARKOUT record is routed by ``horizon_type``; legacy records with
    no horizon_type predate the split and are treated as wall-clock.
    """
    fills, markouts, settles, next_event = {}, defaultdict(dict), {}, {}
    for rec in records:
        t, fid = rec.get("type"), rec.get("fill_id")
        if t == "FILL":
            fills[fid] = rec
        elif t == "MARKOUT":
            if rec.get("horizon_type") == "next_event":
                next_event[fid] = rec
            else:
                markouts[fid][rec.get("horizon_s")] = rec
        elif t == "SETTLE":
            settles[fid] = rec
    return fills, markouts, settles, next_event


# ── Stats helpers ────────────────────────────────────────────────────

def wavg(pairs) -> Optional[float]:
    """Quantity-weighted mean of (value, weight)."""
    pairs = [(v, w) for v, w in pairs if v is not None and w]
    if not pairs:
        return None
    tw = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / tw if tw else None


def tstat(vals: List[float]) -> Optional[float]:
    n = len(vals)
    if n < 2:
        return None
    m = sum(vals) / n
    var = sum((v - m) ** 2 for v in vals) / (n - 1)
    if var <= 0:
        return None
    return m / math.sqrt(var / n)


def c(x) -> str:
    return f"{100.0 * x:+6.2f}c" if x is not None else "    n/a"


def bucket_label(edges, i) -> str:
    lo = edges[i]
    hi = edges[i + 1] if i + 1 < len(edges) else None
    return f"{lo:g}-{hi:g}" if hi is not None else f"{lo:g}+"


def bucketize(value, edges) -> Optional[int]:
    if value is None:
        return None
    idx = None
    for i, e in enumerate(edges):
        if value >= e:
            idx = i
    return idx


def series_of(fill: dict) -> str:
    """Series ticker prefix for series-aware fee lookup (e.g. KXMLBGAME,
    KXPGATOP10)."""
    tk = fill.get("ticker", "") or ""
    return tk.split("-")[0] if tk else ""


# ── Markout accessors (series-aware net) ─────────────────────────────

def markout_of(markouts, fid, horizon) -> Optional[float]:
    m = markouts.get(fid, {}).get(horizon)
    return m.get("markout_per_contract") if m else None


def net_markout(fills, markouts, fid, horizon) -> Optional[float]:
    """Wall-clock markout net of the SERIES-AWARE maker fee — the honest
    per-contract number.  Golf prop series charge zero maker fee; the
    MLB game-winner series charges the general rate."""
    mo = markout_of(markouts, fid, horizon)
    if mo is None:
        return None
    f = fills[fid]
    return mo - fee_per_contract(f.get("price", 0.5), maker=True,
                                 series_ticker=series_of(f))


def next_event_net(fills, next_event, fid) -> Optional[float]:
    rec = next_event.get(fid)
    if rec is None:
        return None
    mo = rec.get("markout_per_contract")
    if mo is None:
        return None
    f = fills[fid]
    return mo - fee_per_contract(f.get("price", 0.5), maker=True,
                                 series_ticker=series_of(f))


def cluster_of(fill: dict, cfg: dict):
    """Event-cluster key (sample unit): game for P-016, tournament for
    P-017M.  Falls back gracefully if the primary key is absent."""
    return (fill.get(cfg["cluster_key"]) or fill.get("event")
            or fill.get("ticker") or "?")


# ── Clustered bootstrap CI ───────────────────────────────────────────

def clustered_ci(items: List[dict], value_fn: Callable[[dict], Optional[float]],
                 weight_fn: Callable[[dict], float],
                 cluster_fn: Callable[[dict], object],
                 n_boot: int = 2000, seed: int = 17):
    """Event-clustered bootstrap of a quantity-weighted mean.

    Resamples CLUSTERS (games / tournaments) with replacement — within-
    event outcomes correlate, so the event, not the fill, is the unit of
    observation (see CLAUDE.md).  Returns (point, lo95, hi95); the CI is
    None when there are too few clusters to bootstrap.
    """
    clusters: Dict[object, List[dict]] = defaultdict(list)
    for it in items:
        clusters[cluster_fn(it)].append(it)
    keys = list(clusters)
    if not keys:
        return (None, None, None)

    def wmean(sample_keys) -> Optional[float]:
        num = den = 0.0
        for k in sample_keys:
            for it in clusters[k]:
                v = value_fn(it)
                w = weight_fn(it)
                if v is None or not w:
                    continue
                num += v * w
                den += w
        return num / den if den else None

    point = wmean(keys)
    if len(keys) < 3:
        return (point, None, None)
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        sample = [keys[rng.randrange(len(keys))] for _ in keys]
        m = wmean(sample)
        if m is not None:
            boots.append(m)
    if len(boots) < 50:
        return (point, None, None)
    boots.sort()
    lo = boots[int(0.025 * len(boots))]
    hi = boots[int(0.975 * len(boots))]
    return (point, lo, hi)


# ── Bucketed table ───────────────────────────────────────────────────

def section_grouped(title, groups, fills, markouts, horizon=300.0,
                    order=None):
    """Print a bucketed markout table.  groups: label → [fill_id]."""
    print(f"\n{title}")
    print(f"  {'bucket':<18} {'fills':>6} {'qty':>7} "
          f"{'net markout/ct':>17} {'t':>6}")
    labels = [l for l in order if l in groups] if order else sorted(groups)
    for label in labels:
        fids = groups[label]
        vals = [(net_markout(fills, markouts, f, horizon),
                 fills[f].get("qty", 1.0)) for f in fids]
        per_contract = [v for v, _ in vals if v is not None]
        w = wavg(vals)
        qty = sum(fills[f].get("qty", 0) for f in fids)
        t = tstat(per_contract)
        print(f"  {label:<18} {len(fids):>6} {qty:>7.0f} "
              f"{c(w):>17} {(f'{t:+.2f}' if t is not None else '   n/a'):>6}")


# ── Scoreboard (section 0) ───────────────────────────────────────────

def scoreboard(real, fills, markouts, next_event, cfg) -> None:
    horizon = cfg["headline_horizon"]
    items = list(real.values())

    def vfn(f):
        return net_markout(fills, markouts, f["fill_id"], horizon)

    def wfn(f):
        return f.get("qty", 1.0)

    def cfn(f):
        return cluster_of(f, cfg)

    print("\n0. PROMOTE/KILL SCOREBOARD")
    print(f"   (markout-adjusted net P&L per fill at +{int(horizon)}s, "
          f"net of series-aware maker fee)")

    point, lo, hi = clustered_ci(items, vfn, wfn, cfn)
    n_marked = sum(1 for f in items if vfn(f) is not None)
    total = sum(vfn(f) * wfn(f) for f in items if vfn(f) is not None)
    n_clusters = len({cfn(f) for f in items})

    ci_txt = (f"[{c(lo)}, {c(hi)}]" if lo is not None else "[CI n/a]")
    print(f"   per-contract mean: {c(point)}  95% event-clustered CI "
          f"{ci_txt}")
    print(f"   marked fills: {n_marked}/{len(items)}   clusters "
          f"(sample units): {n_clusters}   total P&L: ${total:+.2f}")

    # ── Drop the single best DAY (robustness) ────────────────────────
    by_day: Dict[str, float] = defaultdict(float)
    for f in items:
        v = vfn(f)
        if v is None:
            continue
        day = (f.get("iso") or "")[:10] or "?"
        by_day[day] += v * wfn(f)
    if by_day:
        best_day = max(by_day, key=by_day.get)
        total_ex_best = total - by_day[best_day]
        print(f"   drop best day ({best_day}, ${by_day[best_day]:+.2f}): "
              f"total ${total_ex_best:+.2f} over {len(by_day)} day(s)")

    # ── VPIN interaction (below- vs above-median toxicity) ───────────
    vpins = sorted(f.get("vpin_at_fill") for f in items
                   if f.get("vpin_at_fill") is not None)
    if len(vpins) >= 6:
        med = vpins[len(vpins) // 2]
        below = [f for f in items
                 if f.get("vpin_at_fill") is not None
                 and f["vpin_at_fill"] <= med]
        above = [f for f in items
                 if f.get("vpin_at_fill") is not None
                 and f["vpin_at_fill"] > med]
        wb = wavg([(vfn(f), wfn(f)) for f in below])
        wa = wavg([(vfn(f), wfn(f)) for f in above])
        print(f"   VPIN split @ median {med:.2f}:  "
              f"below {c(wb)} (n={len(below)})   "
              f"above {c(wa)} (n={len(above)})")
        print("   (edge concentrated below median ⇒ toxic flow is the cost; "
              "a Phase-2 pull rule writes itself.)")
    else:
        print("   VPIN interaction: insufficient vpin-tagged fills yet")

    # ── Gate reminder ────────────────────────────────────────────────
    print(f"   GATE: {len(real)}/500 real fills; promote only on "
          f"markout-adjusted P&L > 0")
    print("   robust to dropping the best day. Sample unit = "
          f"{cfg['cluster_key']}, not fill.")


# ── VPIN tercile section (section 9) ─────────────────────────────────

def section_vpin(real, fills, markouts, cfg) -> None:
    horizon = cfg["headline_horizon"]
    print("\n9. ORDER-FLOW TOXICITY (net markout by VPIN tercile)")
    print("   High-VPIN fills sharply negative ⇒ one-sidedness is toxic")
    print("   and VPIN should become a quote-pull trigger (Phase 2).")
    tagged = [(f.get("vpin_at_fill"), fid)
              for fid, f in real.items()
              if f.get("vpin_at_fill") is not None]
    if len(tagged) < 6:
        print(f"   only {len(tagged)} vpin-tagged fills — need more.")
        # also show one-sidedness coverage as a fallback
        os_n = sum(1 for f in real.values()
                   if f.get("one_sidedness_at_fill") is not None)
        print(f"   (one_sidedness-tagged fills: {os_n})")
        return
    vals = sorted(v for v, _ in tagged)
    t1 = vals[len(vals) // 3]
    t2 = vals[2 * len(vals) // 3]
    g = defaultdict(list)
    for v, fid in tagged:
        if v <= t1:
            g[f"low  (<= {t1:.2f})"].append(fid)
        elif v <= t2:
            g[f"mid  (<= {t2:.2f})"].append(fid)
        else:
            g[f"high (>  {t2:.2f})"].append(fid)
    order = [f"low  (<= {t1:.2f})", f"mid  (<= {t2:.2f})",
             f"high (>  {t2:.2f})"]
    section_grouped("   markouts by VPIN tercile:", g, fills, markouts,
                    horizon=horizon, order=order)


# ── Markout curve section (section 8) ────────────────────────────────

def section_curve(real, fills, markouts, next_event, cfg) -> None:
    print("\n8. MARKOUT CURVE (mean net markout vs horizon)")
    print("   Positive early / negative late ⇒ run over on continuation;")
    print("   flat-positive ⇒ clean spread capture.")
    print(f"  {'horizon':<12} {'fills':>6} {'net/ct':>12} "
          f"{'95% clustered CI':>26}")
    items = list(real.values())

    def wfn(f):
        return f.get("qty", 1.0)

    def cfn(f):
        return cluster_of(f, cfg)

    for h in cfg["horizons"]:
        def vfn(f, _h=h):
            return net_markout(fills, markouts, f["fill_id"], _h)
        n = sum(1 for f in items if vfn(f) is not None)
        point, lo, hi = clustered_ci(items, vfn, wfn, cfn)
        ci = (f"[{c(lo)}, {c(hi)}]" if lo is not None else "[n/a]")
        print(f"  +{int(h):<10}s {n:>6} {c(point):>12} {ci:>26}")

    if cfg["has_next_event"] and next_event:
        def vfn_ne(f):
            return next_event_net(fills, next_event, f["fill_id"])
        n = sum(1 for f in items if vfn_ne(f) is not None)
        point, lo, hi = clustered_ci(items, vfn_ne, wfn, cfn)
        ci = (f"[{c(lo)}, {c(hi)}]" if lo is not None else "[n/a]")
        print(f"  {'next_event':<11} {n:>6} {c(point):>12} {ci:>26}")


# ── Event-clocked section (section 10, P-016) ────────────────────────

def section_next_event(real, fills, markouts, next_event, cfg) -> None:
    if not cfg["has_next_event"]:
        return
    print("\n10. EVENT-CLOCKED MARKOUT (next state change vs wall-clock)")
    if not next_event:
        print("   no event-clocked markouts yet.")
        return
    horizon = cfg["headline_horizon"]
    # by side
    print(f"  {'side':<10} {'fills':>6} {'net next_event':>15} "
          f"{f'net +{int(horizon)}s':>12}")
    for side in ("buy", "sell"):
        fids = [fid for fid, f in real.items() if f.get("side") == side]
        ne = wavg([(next_event_net(fills, next_event, fid),
                    fills[fid].get("qty", 1.0)) for fid in fids])
        wc = wavg([(net_markout(fills, markouts, fid, horizon),
                    fills[fid].get("qty", 1.0)) for fid in fids])
        print(f"  {side:<10} {len(fids):>6} {c(ne):>15} {c(wc):>12}")
    # secs_elapsed distribution
    elapsed = [next_event[fid].get("secs_elapsed") for fid in real
               if fid in next_event
               and next_event[fid].get("secs_elapsed") is not None]
    if elapsed:
        elapsed.sort()
        med = elapsed[len(elapsed) // 2]
        print(f"   secs to next event: median {med:.0f}s  "
              f"min {elapsed[0]:.0f}s  max {elapsed[-1]:.0f}s  "
              f"(n={len(elapsed)})")
    print("   (next_event ≪ wall-clock and negative ⇒ fills cluster right "
          "before\n    the resolving event — the event-timing pick-off.)")


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pod", default="P-016", choices=sorted(POD_CONFIG))
    ap.add_argument("--fills", default=None)
    ap.add_argument("--quotes", default=None)
    ap.add_argument("--state", default=None)
    ap.add_argument("--milestone", type=int, default=150)
    ap.add_argument("--force-full", action="store_true")
    args = ap.parse_args()

    cfg = POD_CONFIG[args.pod]
    fills_path = Path(args.fills or cfg["fills"])
    quotes_path = Path(args.quotes or cfg["quotes"])
    state_path = Path(args.state or cfg["state"])

    recs = load_jsonl(fills_path)
    fills, markouts, settles, next_event = index_fills(recs)
    qrecs = load_jsonl(quotes_path)

    real = {k: v for k, v in fills.items() if not v.get("shadow")}
    shadow = {k: v for k, v in fills.items() if v.get("shadow")}

    # ── Milestone gate ───────────────────────────────────────────────
    prev = {}
    if state_path.exists():
        try:
            prev = json.loads(state_path.read_text())
        except json.JSONDecodeError:
            prev = {}
    last_reported = int(prev.get("last_milestone", 0))
    milestone = (len(real) // args.milestone) * args.milestone
    full = args.force_full or milestone > last_reported

    if not full:
        vtag = sum(1 for f in real.values()
                   if f.get("vpin_at_fill") is not None)
        print(f"[{args.pod} health] real fills={len(real)} "
              f"shadow={len(shadow)} settled={len(settles)} "
              f"quotes_logged={len(qrecs)} vpin_tagged={vtag} "
              f"— next full review at {last_reported + args.milestone} fills")
        return

    print("=" * 72)
    print(f"{cfg['label']} — TIER-1 DIAGNOSTICS  (real fills: {len(real)})")
    print("=" * 72)

    # ── 0. Scoreboard (top-of-report) ────────────────────────────────
    if real:
        scoreboard(real, fills, markouts, next_event, cfg)

    # ── 1. Coverage / health ─────────────────────────────────────────
    anchors = [q for q in qrecs if q.get("type") == "ANCHOR"]
    quotes = [q for q in qrecs if q.get("type") == "QUOTE"]
    pulls = [q for q in qrecs if q.get("type") == "PULL"]
    games = {q.get("game_pk") or q.get("ticker") for q in quotes}
    print("\n1. COVERAGE")
    print(f"  books anchored: {len(anchors)}   markets quoted: {len(games)}   "
          f"quote updates: {len(quotes)}   pulls: {len(pulls)}")
    if quotes and cfg["has_shadow"]:
        two_sided = sum(1 for q in quotes
                        if q.get("bid") is not None
                        and q.get("ask") is not None)
        print(f"  two-sided quote updates: {two_sided}/{len(quotes)} "
              f"({100.0*two_sided/len(quotes):.0f}%)")
        widths = [q["ask"] - q["bid"] for q in quotes
                  if q.get("bid") is not None and q.get("ask") is not None]
        if widths:
            print(f"  avg quoted width: {100*sum(widths)/len(widths):.2f}c")

    if not real:
        print("\n  No real fills yet — deeper sections need fills.")
        _save_state(state_path, prev, milestone)
        return

    # ── 2. Fill rate by quote distance from mid ──────────────────────
    if any(f.get("quote_dist_from_mid") is not None for f in real.values()):
        print("\n2. FILL DISTANCE FROM MID (where fills actually happen)")
        dist_edges = [0.0, 0.02, 0.04, 0.06, 0.10]
        dist_order = [bucket_label(dist_edges, i)
                      for i in range(len(dist_edges))]
        g = defaultdict(list)
        for fid, f in real.items():
            i = bucketize(f.get("quote_dist_from_mid"), dist_edges)
            if i is not None:
                g[bucket_label(dist_edges, i)].append(fid)
        section_grouped("   markouts by distance:", g, fills, markouts,
                        horizon=cfg["headline_horizon"], order=dist_order)

    # ── 3. Adverse selection: time since observed state change ───────
    if cfg["has_state_change"]:
        print("\n3. ADVERSE SELECTION (pick-off signature of feed lag)")
        print("   Negative markouts concentrated at LOW seconds-since-change")
        print("   ⇒ we are being picked off on stale quotes after events.")
        t_edges = [0.0, 15.0, 30.0, 60.0, 120.0, 300.0]
        t_order = [f"{bucket_label(t_edges, i)}s"
                   for i in range(len(t_edges))]
        g = defaultdict(list)
        for fid, f in real.items():
            i = bucketize(f.get("secs_since_state_change"), t_edges)
            if i is not None:
                g[f"{bucket_label(t_edges, i)}s"].append(fid)
        section_grouped("   markouts by staleness:", g, fills, markouts,
                        horizon=cfg["headline_horizon"], order=t_order)

    # ── 4. Leverage calibration ──────────────────────────────────────
    if cfg["has_leverage"]:
        print("\n4. LEVERAGE (is sens_mult widening calibrated?)")
        s_edges = [0.0, 0.05, 0.10, 0.15, 0.25]
        s_order = [bucket_label(s_edges, i) for i in range(len(s_edges))]
        g = defaultdict(list)
        for fid, f in real.items():
            i = bucketize(f.get("sens_at_quote"), s_edges)
            if i is not None:
                g[bucket_label(s_edges, i)].append(fid)
        section_grouped("   markouts by |dWP/drun|:", g, fills, markouts,
                        horizon=cfg["headline_horizon"], order=s_order)

    # ── 5. Directional bias: side and price ──────────────────────────
    print("\n5. DIRECTION (side and price)")
    g = defaultdict(list)
    for fid, f in real.items():
        g[f.get("side", "?")].append(fid)
    section_grouped("   markouts by side:", g, fills, markouts,
                    horizon=cfg["headline_horizon"])

    p_edges = [0.0, 0.20, 0.40, 0.60, 0.80]
    p_order = [bucket_label(p_edges, i) for i in range(len(p_edges))]
    g = defaultdict(list)
    for fid, f in real.items():
        i = bucketize(f.get("price"), p_edges)
        if i is not None:
            g[bucket_label(p_edges, i)].append(fid)
    section_grouped("   markouts by fill price:", g, fills, markouts,
                    horizon=cfg["headline_horizon"], order=p_order)

    # ── 6. Model calibration vs realized outcomes (P-016) ────────────
    cal = defaultdict(list)
    if cfg["has_leverage"]:  # P-016 records fair_at_fill
        print("\n6. MODEL CALIBRATION (fair value vs realized settlement)")
        print(f"  {'fair bucket':<18} {'n':>6} {'mean fair':>10} "
              f"{'realized':>10} {'gap':>8}")
        for fid, s in settles.items():
            f = fills.get(fid)
            if not f or f.get("shadow"):
                continue
            fair = f.get("fair_at_fill")
            if fair is None:
                continue
            i = bucketize(fair, [0.0, 0.2, 0.4, 0.6, 0.8])
            if i is None:
                continue
            cal[bucket_label([0.0, 0.2, 0.4, 0.6, 0.8], i)].append(
                (fair, s.get("result", 0.0)))
        if cal:
            for label in sorted(cal):
                pts = cal[label]
                mf = sum(p[0] for p in pts) / len(pts)
                mr = sum(p[1] for p in pts) / len(pts)
                print(f"  {label:<18} {len(pts):>6} {mf:>10.3f} "
                      f"{mr:>10.3f} {mr - mf:>+8.3f}")
            print("  (gap ≫ 0 ⇒ model under-forecasts; a persistent "
                  "one-sided")
            print("   gap is a recalibration signal, not a strategy change.)")
        else:
            print("  no settled real fills yet")

    # ── 7. Guardrail cost (shadow vs real) ───────────────────────────
    if cfg["has_shadow"]:
        print("\n7. GUARDRAIL COST (counterfactual fills where we did NOT "
              "quote)")
        if shadow:
            by_reason = defaultdict(list)
            for fid, f in shadow.items():
                by_reason[f.get("reason", "?")].append(fid)
            for reason in sorted(by_reason):
                fids = by_reason[reason]
                vals = [(net_markout(fills, markouts, f,
                                     cfg["headline_horizon"]),
                         fills[f].get("qty", 1.0)) for f in fids]
                w = wavg(vals)
                pnl = sum(settles[f].get("pnl_usd", 0.0)
                          for f in fids if f in settles)
                print(f"  {reason:<16} fills={len(fids):>4}  "
                      f"net +5m {c(w)}  settled P&L ${pnl:+.2f}")
            print("  (Positive numbers ⇒ the guardrail is costing us edge "
                  "and")
            print("   is a candidate to relax — as a PROPOSAL, not an "
                  "auto-change.)")
        else:
            print("  no shadow fills yet")

    # ── 8. Markout curve ─────────────────────────────────────────────
    section_curve(real, fills, markouts, next_event, cfg)

    # ── 9. Order-flow toxicity ───────────────────────────────────────
    section_vpin(real, fills, markouts, cfg)

    # ── 10. Event-clocked markout ────────────────────────────────────
    section_next_event(real, fills, markouts, next_event, cfg)

    # ── 11. Proposals ────────────────────────────────────────────────
    emit_proposals(real, shadow, fills, markouts, settles, cal, cfg)

    # ── Gate snapshot ────────────────────────────────────────────────
    print("\n" + "-" * 72)
    horizon = cfg["headline_horizon"]
    m5 = [(net_markout(fills, markouts, fid, horizon), f.get("qty", 1.0))
          for fid, f in real.items()]
    total = sum(v * w for v, w in m5 if v is not None)
    print(f"GATE: {len(real)}/500 real fills   "
          f"fee-adjusted +{int(horizon)}s markout P&L: ${total:+.2f}")
    print("-" * 72)

    _save_state(state_path, prev, milestone)


def _bucket_stat(real, fills, markouts, pred, horizon=300.0):
    """(n, weighted net markout, t-stat) over fills matching `pred`."""
    fids = [fid for fid, f in real.items() if pred(f)]
    vals = [(net_markout(fills, markouts, f, horizon),
             fills[f].get("qty", 1.0)) for f in fids]
    per = [v for v, _ in vals if v is not None]
    return len(fids), wavg(vals), tstat(per)


def emit_proposals(real, shadow, fills, markouts, settles, cal, cfg) -> None:
    """Deterministic, threshold-based change proposals.

    Per RECURSIVE_REVIEW_DESIGN.md, proposals are tagged by direction:
      [CONSERVATIVE] — reduces exposure; auto-eligible within bounds
      [AGGRESSIVE]   — increases exposure; ALWAYS requires human approval
    Nothing here edits config.  This function only prints.
    """
    print("\n11. PROPOSALS (nothing is auto-applied)")
    props: List[str] = []
    horizon = cfg["headline_horizon"]

    MIN_N, MIN_T = 30, 2.0

    # (a) Pick-off on stale quotes → post-event suppression window (P-016)
    if cfg["has_state_change"]:
        n, w, t = _bucket_stat(
            real, fills, markouts,
            lambda f: (f.get("secs_since_state_change") is not None
                       and f["secs_since_state_change"] < 30.0),
            horizon)
        if (n >= MIN_N and w is not None and w < -0.005
                and t is not None and t < -MIN_T):
            props.append(
                f"[CONSERVATIVE] Post-event suppression window.\n"
                f"    Fills within 30s of an observed state change: n={n}, "
                f"net {c(w)} (t={t:+.2f}).\n"
                f"    This is the feed-lag pick-off signature. Proposal: "
                f"suppress quoting for ~30s\n    after each observed state "
                f"change, or widen materially in that window.\n"
                f"    Theory-driven (Glosten-Milgrom): widen where informed "
                f"flow concentrates.")

    # (a2) High-VPIN toxicity → VPIN pull trigger (both pods)
    tagged = [(f.get("vpin_at_fill"), fid) for fid, f in real.items()
              if f.get("vpin_at_fill") is not None]
    if len(tagged) >= 2 * MIN_N:
        vals = sorted(v for v, _ in tagged)
        med = vals[len(vals) // 2]
        hi_fids = [fid for v, fid in tagged if v > med]
        vv = [(net_markout(fills, markouts, f, horizon),
               fills[f].get("qty", 1.0)) for f in hi_fids]
        per = [v for v, _ in vv if v is not None]
        w, t = wavg(vv), tstat(per)
        if (w is not None and w < -0.005 and t is not None and t < -MIN_T):
            props.append(
                f"[CONSERVATIVE] VPIN pull/widen trigger.\n"
                f"    Above-median-VPIN fills (>{med:.2f}): n={len(hi_fids)}, "
                f"net {c(w)} (t={t:+.2f}).\n"
                f"    One-sided flow is adversely selecting us. Proposal: pull "
                f"or widen quotes\n    when VPIN exceeds ~{med:.2f} "
                f"(mirrors the divergence_guard pull). Phase-2 change,\n    "
                f"separate sample — do NOT alter quoting mid-gate.")

    # (b) Fill starvation → narrower base (AGGRESSIVE: needs approval)
    if len(real) < 40:
        n_near, _, _ = _bucket_stat(
            real, fills, markouts,
            lambda f: (f.get("quote_dist_from_mid") or 0) < 0.03, horizon)
        props.append(
            f"[AGGRESSIVE — APPROVAL REQUIRED] Fill starvation.\n"
            f"    Only {len(real)} real fills so far ({n_near} within 3c of "
            f"mid).\n    If this persists, the pilot cannot reach the "
            f"500-fill gate in reasonable time.\n"
            f"    Options: narrow the quote, quote more markets, or accept a "
            f"longer pilot.\n"
            f"    NOTE: narrowing raises adverse-selection exposure — do not "
            f"auto-apply.")

    # (c) Model calibration drift → recalibration (P-016)
    if cal:
        gaps = []
        for label, pts in cal.items():
            if len(pts) >= 20:
                mf = sum(p[0] for p in pts) / len(pts)
                mr = sum(p[1] for p in pts) / len(pts)
                gaps.append((label, mr - mf, len(pts)))
        one_sided = [g for _, g, _ in gaps]
        if len(gaps) >= 2 and (all(g > 0.05 for g in one_sided)
                               or all(g < -0.05 for g in one_sided)):
            direction = "under" if one_sided[0] > 0 else "over"
            props.append(
                f"[CONSERVATIVE] Model recalibration.\n"
                f"    Fair value {direction}-forecasts across ALL buckets: "
                f"{[(l, round(g, 3)) for l, g, _ in gaps]}.\n"
                f"    A persistent one-sided gap is a calibration fix "
                f"(intercept/slope), not a\n    strategy change. Re-fit the "
                f"pregame anchor or RUNS_PER_HALF_INNING.")

    # (d) Guardrail cost → relax gate (AGGRESSIVE: needs approval) (P-016)
    if cfg["has_shadow"]:
        for reason in ("inning_gate", "inventory_cap"):
            fids = [fid for fid, f in shadow.items()
                    if f.get("reason") == reason]
            if len(fids) < MIN_N:
                continue
            vals = [(net_markout(fills, markouts, f, horizon),
                     fills[f].get("qty", 1.0)) for f in fids]
            per = [v for v, _ in vals if v is not None]
            w, t = wavg(vals), tstat(per)
            if w is not None and w > 0.005 and t is not None and t > MIN_T:
                props.append(
                    f"[AGGRESSIVE — APPROVAL REQUIRED] Relax {reason}.\n"
                    f"    Counterfactual fills there: n={len(fids)}, net "
                    f"{c(w)} (t={t:+.2f}).\n"
                    f"    The guardrail appears to be costing edge. But it "
                    f"exists because the WP model\n    is weakest in those "
                    f"states — verify calibration there BEFORE relaxing.")

    # (e) Directional asymmetry → skew review
    nb, wb, _ = _bucket_stat(real, fills, markouts,
                             lambda f: f.get("side") in ("buy",), horizon)
    ns, ws, _ = _bucket_stat(real, fills, markouts,
                             lambda f: f.get("side") in ("sell", "sell_yes"),
                             horizon)
    if (nb >= MIN_N and ns >= MIN_N and wb is not None and ws is not None
            and abs(wb - ws) > 0.01):
        worse = "buy" if wb < ws else "sell"
        props.append(
            f"[CONSERVATIVE] Directional asymmetry.\n"
            f"    buy {c(wb)} (n={nb}) vs sell {c(ws)} (n={ns}) — {worse} "
            f"side is materially worse.\n"
            f"    Proposal: shade the skew against the {worse} side, or widen "
            f"that side only.")

    if not props:
        print("  No threshold-crossing patterns. Continue collecting fills.")
    else:
        for i, p in enumerate(props, 1):
            print(f"\n  ({i}) {p}")
    print("\n  → Review against RECURSIVE_REVIEW_DESIGN.md. AGGRESSIVE items "
          "require\n    explicit approval; champion config stays frozen for "
          "the gate.")


def _save_state(path: Path, prev: dict, milestone: int) -> None:
    prev["last_milestone"] = milestone
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prev))
    except OSError:
        pass


if __name__ == "__main__":
    main()
