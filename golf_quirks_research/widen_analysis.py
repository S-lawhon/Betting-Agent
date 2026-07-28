"""
golf_quirks_research/widen_analysis.py
──────────────────────────────────────
P-022 widening, steps 3-4: re-run the Phase-2 fill replay, UNCHANGED, over the
extended universe — and keep the blocks separable.

Reuses `quirks_common.replay` and the same bootstrap/leave-one-out helpers the
published Phase-2 cells came from. **No third harness**: `backtest_fade_fills.py`
is untouched and must still pass `--validate`.

Three blocks, reported separately before anything is pooled
(`P022_WIDENING_PREDECLARATION.md` §5 and the §9 amendment):

  ORIGINAL   the 19 tournaments the published cells rest on
  GAP        4 tournaments INSIDE the study's span the original pull missed
  FORWARD    4 tournaments that settled after the cache ends

Anchor contemporaneity (§4): staleness is `anchor_lag_h - 12`, i.e. how much
older than the intended H-12h mark the last usable candle actually is. The
declared rule is **drop anchors staler than 6h, never correct them**. Because
the ORIGINAL study applied no such filter, every block is reported BOTH ways —
filtered and unfiltered — so the comparison is like-for-like in at least one of
them.

Run:  python3 -m golf_quirks_research.widen_analysis
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from typing import Any, Dict, List, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import quirks_common as qc          # noqa: E402

OUT = os.path.join(HERE, "widen_results.json")

GAP = {"SOO26", "DOWC26", "MEILCFSG26", "USSOC26"}
FORWARD = {"3MO26", "KIN26", "ISPHWSO26", "ISHSO26"}

H, OFFSET = 12.0, 0.02             # frozen; the published headline cell
STALE_MAX_H = 6.0                  # declared in advance


def block_of(tournament: str) -> str:
    if tournament in GAP:
        return "GAP"
    if tournament in FORWARD:
        return "FORWARD"
    return "ORIGINAL"


def summarise(recs: Sequence[Dict[str, Any]], label: str) -> Dict[str, Any]:
    if not recs:
        print(f"  {label:10} — no markets")
        return {"label": label, "n_markets": 0}
    r = qc.replay(recs, "sell_yes", H, OFFSET)
    per_t = r.get("per_tournament") or {}
    # Use the replay's OWN CI fields rather than recomputing: they are what the
    # published Phase-2 cells were quoted from, and recomputing invites a second
    # number that drifts from the first. `net_per_contract` and the CI are in
    # DOLLARS per contract; the published cells are cents, so scale for display
    # only, once, here.
    entries = [(ev, v["net_per_contract"], v["contracts"])
               for ev, v in per_t.items() if v.get("contracts")]
    pos = sum(1 for _e, c, _n in entries if c > 0)
    net_c = (r.get("net_per_contract") or 0.0) * 100.0
    lo, hi = r.get("ci95_lo"), r.get("ci95_hi")
    loo = qc.leave_one_out(entries) if len(entries) > 1 else {}
    out = {
        "label": label,
        "n_markets": len(recs),
        "posted_markets": r.get("posted_markets"),
        "filled_markets": r.get("filled_markets"),
        "contracts": r.get("contracts_filled"),
        "net_cents_per_contract": net_c,
        "ci_low_cents": (lo * 100.0) if lo is not None else None,
        "ci_high_cents": (hi * 100.0) if hi is not None else None,
        "e_settle_posted": r.get("e_settle_posted"),
        "e_settle_filled": r.get("e_settle_filled"),
        "tournaments": len(entries),
        "tournaments_positive": pos,
        "loo_all_positive": (all(v > 0 for v in loo.values()) if loo else None),
        "loo_min": (min(loo.values()) * 100.0) if loo else None,
        "per_tournament_cents": {e: round(c * 100.0, 2) for e, c, _n in entries},
    }
    ci = (f"[{lo*100:+.1f}, {hi*100:+.1f}]" if lo is not None else "—")
    print(f"  {label:10} mkts={len(recs):4} posted={r.get('posted_markets') or 0:4} "
          f"filled={r.get('filled_markets') or 0:4} "
          f"ct={r.get('contracts_filled') or 0:7.0f} "
          f"net={net_c:+6.2f}c  CI {ci:>17}  "
          f"tourn={len(entries):3}(+{pos})")
    return out


def main() -> None:
    universe = qc.build_universe(qc.LEADER_SERIES, H, (0.03, 0.12))
    recs = qc.load_trade_recs(os.path.join(HERE, "data", "leader_trades"))
    by_ticker = {r["ticker"]: r for r in universe}
    print(f"universe (anchor in band): {len(universe)} markets")
    print(f"tick caches loaded       : {len(recs)} markets\n")

    # attach block + staleness
    for r in recs:
        u = by_ticker.get(r["ticker"])
        t = qc.tournament_of(r.get("event", "")) if not u else u["tournament"]
        r["_block"] = block_of(t)
        lag = (u or {}).get("anchor_lag_h")
        r["_stale_h"] = (lag - H) if lag is not None else None

    blocks = ("ORIGINAL", "GAP", "FORWARD")
    print("── anchor contemporaneity (staleness = anchor_lag_h − 12h) ──")
    stale_stats: Dict[str, Any] = {}
    for b in blocks:
        v = [r["_stale_h"] for r in recs
             if r["_block"] == b and r["_stale_h"] is not None]
        if not v:
            print(f"  {b:10} — none")
            stale_stats[b] = {"n": 0}
            continue
        v.sort()
        over = sum(1 for x in v if x > STALE_MAX_H)
        stale_stats[b] = {"n": len(v), "median": statistics.median(v),
                          "p90": v[int(0.9 * len(v))], "max": v[-1],
                          "n_over_6h": over}
        print(f"  {b:10} n={len(v):4} median={statistics.median(v):6.2f}h "
              f"p90={v[int(0.9*len(v))]:7.2f}h max={v[-1]:8.2f}h "
              f"stale>6h: {over}/{len(v)}")

    results: Dict[str, Any] = {"stale": stale_stats, "H": H, "offset": OFFSET}

    print(f"\n── replay, UNFILTERED (as the ORIGINAL study was run) ──")
    results["unfiltered"] = {
        b: summarise([r for r in recs if r["_block"] == b], b) for b in blocks}
    results["unfiltered"]["ADDED"] = summarise(
        [r for r in recs if r["_block"] in ("GAP", "FORWARD")], "ADDED")
    results["unfiltered"]["POOLED"] = summarise(recs, "POOLED")

    print(f"\n── replay, anchors staler than {STALE_MAX_H:g}h DROPPED (declared rule) ──")
    keep = [r for r in recs
            if r["_stale_h"] is not None and r["_stale_h"] <= STALE_MAX_H]
    print(f"  dropped {len(recs) - len(keep)} of {len(recs)} markets\n")
    results["filtered"] = {
        b: summarise([r for r in keep if r["_block"] == b], b) for b in blocks}
    results["filtered"]["ADDED"] = summarise(
        [r for r in keep if r["_block"] in ("GAP", "FORWARD")], "ADDED")
    results["filtered"]["POOLED"] = summarise(keep, "POOLED")

    json.dump(results, open(OUT, "w"), indent=1, default=str)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
