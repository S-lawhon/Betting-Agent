#!/usr/bin/env python3
"""
scripts/maker_diagnostics.py
────────────────────────────
Tier-1 diagnostics for the P-016 Live Maker pilot.

MEASURES ONLY — never changes parameters.  Per the recursion design
(live_agent_research/RECURSIVE_REVIEW_DESIGN.md), adaptation and
judgment are kept separate from measurement so that tuning cannot
contaminate the pre-registered gate.

Decomposition:
  1. Coverage / health      — games, books, quote uptime, feed gaps
  2. Fill rate              — by quote distance from mid
  3. Adverse selection      — markouts by seconds-since-observed-state-change
                              (the pick-off signature of our feed lag)
  4. Leverage calibration   — markouts by |dWP/drun| bucket
  5. Directional bias       — markouts by side and price decile (FLB check)
  6. Model calibration      — fair-value buckets vs realized settlement
  7. Guardrail cost         — shadow (counterfactual) fills vs real

Milestone logic: emits a FULL review when champion fills cross the next
multiple of --milestone (default 150); otherwise a short health line.
State kept in data/trade_logs/.maker_diag_state.json.

Usage:
    python3 -m scripts.maker_diagnostics [--fills PATH] [--quotes PATH]
                                         [--milestone 150] [--force-full]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_fees import fee_per_contract  # noqa: E402

DEFAULT_FILLS = "data/trade_logs/maker_fills.jsonl"
DEFAULT_QUOTES = "data/trade_logs/maker_quotes.jsonl"
STATE_FILE = "data/trade_logs/.maker_diag_state.json"


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
    fills, markouts, settles = {}, defaultdict(dict), {}
    for rec in records:
        t, fid = rec.get("type"), rec.get("fill_id")
        if t == "FILL":
            fills[fid] = rec
        elif t == "MARKOUT":
            markouts[fid][rec.get("horizon_s")] = rec
        elif t == "SETTLE":
            settles[fid] = rec
    return fills, markouts, settles


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


# ── Report sections ──────────────────────────────────────────────────

def markout_of(fills, markouts, fid, horizon) -> Optional[float]:
    m = markouts.get(fid, {}).get(horizon)
    return m.get("markout_per_contract") if m else None


def net_markout(fills, markouts, fid, horizon) -> Optional[float]:
    """Markout net of the maker fee — the honest per-contract number."""
    mo = markout_of(fills, markouts, fid, horizon)
    if mo is None:
        return None
    f = fills[fid]
    return mo - fee_per_contract(f.get("price", 0.5), maker=True)


def section_grouped(title, groups, fills, markouts, horizon=300.0,
                    order=None):
    """Print a bucketed markout table.  groups: label → [fill_id].

    `order` preserves numeric bucket ordering (plain sort() would put
    "120-300s" between "0-15s" and "15-30s").
    """
    print(f"\n{title}")
    print(f"  {'bucket':<18} {'fills':>6} {'qty':>7} "
          f"{'net +5m/contract':>17} {'t':>6}")
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fills", default=DEFAULT_FILLS)
    ap.add_argument("--quotes", default=DEFAULT_QUOTES)
    ap.add_argument("--state", default=STATE_FILE)
    ap.add_argument("--milestone", type=int, default=150)
    ap.add_argument("--force-full", action="store_true")
    args = ap.parse_args()

    recs = load_jsonl(Path(args.fills))
    fills, markouts, settles = index_fills(recs)
    qrecs = load_jsonl(Path(args.quotes))

    real = {k: v for k, v in fills.items() if not v.get("shadow")}
    shadow = {k: v for k, v in fills.items() if v.get("shadow")}

    # ── Milestone gate ───────────────────────────────────────────────
    state_path = Path(args.state)
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
        print(f"[P-016 health] real fills={len(real)} shadow={len(shadow)} "
              f"settled={len(settles)} quotes_logged={len(qrecs)} "
              f"— next full review at {last_reported + args.milestone} fills")
        return

    print("=" * 72)
    print(f"P-016 LIVE MAKER — TIER-1 DIAGNOSTICS  (real fills: {len(real)})")
    print("=" * 72)

    # ── 1. Coverage / health ─────────────────────────────────────────
    anchors = [q for q in qrecs if q.get("type") == "ANCHOR"]
    quotes = [q for q in qrecs if q.get("type") == "QUOTE"]
    pulls = [q for q in qrecs if q.get("type") == "PULL"]
    games = {q.get("game_pk") for q in quotes}
    print("\n1. COVERAGE")
    print(f"  books anchored: {len(anchors)}   games quoted: {len(games)}   "
          f"quote updates: {len(quotes)}   pulls: {len(pulls)}")
    if quotes:
        two_sided = sum(1 for q in quotes
                        if q.get("bid") is not None and q.get("ask") is not None)
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
    print("\n2. FILL DISTANCE FROM MID (where fills actually happen)")
    dist_edges = [0.0, 0.02, 0.04, 0.06, 0.10]
    dist_order = [bucket_label(dist_edges, i) for i in range(len(dist_edges))]
    g = defaultdict(list)
    for fid, f in real.items():
        i = bucketize(f.get("quote_dist_from_mid"), dist_edges)
        if i is not None:
            g[bucket_label(dist_edges, i)].append(fid)
    section_grouped("   markouts by distance:", g, fills, markouts,
                    order=dist_order)

    # ── 3. Adverse selection: time since observed state change ───────
    print("\n3. ADVERSE SELECTION (pick-off signature of feed lag)")
    print("   Negative markouts concentrated at LOW seconds-since-change")
    print("   ⇒ we are being picked off on stale quotes after events.")
    t_edges = [0.0, 15.0, 30.0, 60.0, 120.0, 300.0]
    t_order = [f"{bucket_label(t_edges, i)}s" for i in range(len(t_edges))]
    g = defaultdict(list)
    for fid, f in real.items():
        i = bucketize(f.get("secs_since_state_change"), t_edges)
        if i is not None:
            g[f"{bucket_label(t_edges, i)}s"].append(fid)
    section_grouped("   markouts by staleness:", g, fills, markouts,
                    order=t_order)

    # ── 4. Leverage calibration ──────────────────────────────────────
    print("\n4. LEVERAGE (is sens_mult widening calibrated?)")
    s_edges = [0.0, 0.05, 0.10, 0.15, 0.25]
    s_order = [bucket_label(s_edges, i) for i in range(len(s_edges))]
    g = defaultdict(list)
    for fid, f in real.items():
        i = bucketize(f.get("sens_at_quote"), s_edges)
        if i is not None:
            g[bucket_label(s_edges, i)].append(fid)
    section_grouped("   markouts by |dWP/drun|:", g, fills, markouts,
                    order=s_order)

    # ── 5. Directional bias: side and price ──────────────────────────
    print("\n5. DIRECTION (FLB skew check)")
    g = defaultdict(list)
    for fid, f in real.items():
        g[f.get("side", "?")].append(fid)
    section_grouped("   markouts by side:", g, fills, markouts)

    p_edges = [0.0, 0.20, 0.40, 0.60, 0.80]
    p_order = [bucket_label(p_edges, i) for i in range(len(p_edges))]
    g = defaultdict(list)
    for fid, f in real.items():
        i = bucketize(f.get("price"), p_edges)
        if i is not None:
            g[bucket_label(p_edges, i)].append(fid)
    section_grouped("   markouts by fill price:", g, fills, markouts,
                    order=p_order)

    # ── 6. Model calibration vs realized outcomes ────────────────────
    print("\n6. MODEL CALIBRATION (fair value vs realized settlement)")
    print(f"  {'fair bucket':<18} {'n':>6} {'mean fair':>10} "
          f"{'realized':>10} {'gap':>8}")
    cal = defaultdict(list)
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
        print("  (gap ≫ 0 ⇒ model under-forecasts; a persistent one-sided")
        print("   gap is a recalibration signal, not a strategy change.)")
    else:
        print("  no settled real fills yet")

    # ── 7. Guardrail cost (shadow vs real) ───────────────────────────
    print("\n7. GUARDRAIL COST (counterfactual fills where we did NOT quote)")
    if shadow:
        by_reason = defaultdict(list)
        for fid, f in shadow.items():
            by_reason[f.get("reason", "?")].append(fid)
        for reason in sorted(by_reason):
            fids = by_reason[reason]
            vals = [(net_markout(fills, markouts, f, 300.0),
                     fills[f].get("qty", 1.0)) for f in fids]
            w = wavg(vals)
            pnl = sum(settles[f].get("pnl_usd", 0.0)
                      for f in fids if f in settles)
            print(f"  {reason:<16} fills={len(fids):>4}  "
                  f"net +5m {c(w)}  settled P&L ${pnl:+.2f}")
        print("  (Positive numbers ⇒ the guardrail is costing us edge and")
        print("   is a candidate to relax — as a PROPOSAL, not an auto-change.)")
    else:
        print("  no shadow fills yet")

    # ── 8. Proposals ─────────────────────────────────────────────────
    emit_proposals(real, shadow, fills, markouts, settles, cal)

    # ── Gate snapshot ────────────────────────────────────────────────
    print("\n" + "-" * 72)
    m5 = [(net_markout(fills, markouts, fid, 300.0), f.get("qty", 1.0))
          for fid, f in real.items()]
    total = sum(v * w for v, w in m5 if v is not None)
    print(f"GATE: {len(real)}/500 real fills   "
          f"fee-adjusted +5m markout P&L: ${total:+.2f}")
    print("-" * 72)

    _save_state(state_path, prev, milestone)


def _bucket_stat(real, fills, markouts, pred, horizon=300.0):
    """(n, weighted net markout, t-stat) over fills matching `pred`."""
    fids = [fid for fid, f in real.items() if pred(f)]
    vals = [(net_markout(fills, markouts, f, horizon),
             fills[f].get("qty", 1.0)) for f in fids]
    per = [v for v, _ in vals if v is not None]
    return len(fids), wavg(vals), tstat(per)


def emit_proposals(real, shadow, fills, markouts, settles, cal) -> None:
    """Deterministic, threshold-based change proposals.

    Per RECURSIVE_REVIEW_DESIGN.md, proposals are tagged by direction:
      [CONSERVATIVE] — reduces exposure; auto-eligible within bounds
      [AGGRESSIVE]   — increases exposure; ALWAYS requires human approval
    Nothing here edits config.  This function only prints.
    """
    print("\n8. PROPOSALS (nothing is auto-applied)")
    props: List[str] = []

    MIN_N, MIN_T = 30, 2.0

    # (a) Pick-off on stale quotes → post-event suppression window
    n, w, t = _bucket_stat(
        real, fills, markouts,
        lambda f: (f.get("secs_since_state_change") is not None
                   and f["secs_since_state_change"] < 30.0))
    if n >= MIN_N and w is not None and w < -0.005 and t is not None and t < -MIN_T:
        props.append(
            f"[CONSERVATIVE] Post-event suppression window.\n"
            f"    Fills within 30s of an observed state change: n={n}, "
            f"net +5m {c(w)} (t={t:+.2f}).\n"
            f"    This is the feed-lag pick-off signature. Proposal: suppress "
            f"quoting for ~30s\n    after each observed state change, or widen "
            f"materially in that window.\n"
            f"    Theory-driven (Glosten-Milgrom): widen where informed flow "
            f"concentrates.")

    # (b) Fill starvation → narrower base (AGGRESSIVE: needs approval)
    if len(real) < 40:
        n_near, w_near, _ = _bucket_stat(
            real, fills, markouts,
            lambda f: (f.get("quote_dist_from_mid") or 0) < 0.03)
        props.append(
            f"[AGGRESSIVE — APPROVAL REQUIRED] Fill starvation.\n"
            f"    Only {len(real)} real fills so far ({n_near} within 3c of "
            f"mid).\n    If this persists, the pilot cannot reach the 500-fill "
            f"gate in reasonable time.\n"
            f"    Options: narrow base_half_width, quote more games, or accept "
            f"a longer pilot.\n"
            f"    NOTE: narrowing raises adverse-selection exposure — do not "
            f"auto-apply.")

    # (c) Model calibration drift → recalibration
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

    # (d) Guardrail cost → relax gate (AGGRESSIVE: needs approval)
    for reason in ("inning_gate", "inventory_cap"):
        fids = [fid for fid, f in shadow.items() if f.get("reason") == reason]
        if len(fids) < MIN_N:
            continue
        vals = [(net_markout(fills, markouts, f, 300.0),
                 fills[f].get("qty", 1.0)) for f in fids]
        per = [v for v, _ in vals if v is not None]
        w, t = wavg(vals), tstat(per)
        if w is not None and w > 0.005 and t is not None and t > MIN_T:
            props.append(
                f"[AGGRESSIVE — APPROVAL REQUIRED] Relax {reason}.\n"
                f"    Counterfactual fills there: n={len(fids)}, net +5m "
                f"{c(w)} (t={t:+.2f}).\n"
                f"    The guardrail appears to be costing edge. But it exists "
                f"because the WP model\n    is weakest in those states — "
                f"verify calibration there BEFORE relaxing.")

    # (e) Directional asymmetry → FLB skew review
    nb, wb, _ = _bucket_stat(real, fills, markouts,
                             lambda f: f.get("side") == "buy")
    ns, ws, _ = _bucket_stat(real, fills, markouts,
                             lambda f: f.get("side") == "sell")
    if (nb >= MIN_N and ns >= MIN_N and wb is not None and ws is not None
            and abs(wb - ws) > 0.01):
        worse = "buy" if wb < ws else "sell"
        props.append(
            f"[CONSERVATIVE] Directional asymmetry.\n"
            f"    buy {c(wb)} (n={nb}) vs sell {c(ws)} (n={ns}) — {worse} side "
            f"is materially worse.\n"
            f"    Proposal: shade flb_skew_coef against the {worse} side, or "
            f"widen that side only.")

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
