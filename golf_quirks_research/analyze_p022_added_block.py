"""
golf_quirks_research/analyze_p022_added_block.py
────────────────────────────────────────────────
Reproduces §2.2 of `REPORT_Golf_Quirks_Phase2_P022_2026-07.md`.

§2.1 recorded that every one of the seven published P-022 cells moved DOWN
0.5–1.5¢ when the cache grew 364 → 404 markets, and correctly declined to act
on it. This script answers the question that leaves open — **is that decay?** —
because "armed live and the edge just dropped 0.8¢" is the kind of thing the
next reader acts on if nobody decomposes it.

Answer: no. The added block is 5.3% of filled contracts and its entire loss is
TWO fills where the faded longshot actually won. Against the strategy's own
base rate (faded names win 3.5% of filled markets at a 7.5¢ mean quote), two
winners in twelve fills is a P=0.064 event, observed post-hoc.

It also demonstrates a methodological trap worth keeping: a tournament-
clustered permutation test reports P=0.000 here, which is an ARTIFACT. `KIN26`
holds three filled markets; one −$23.50 fill drags its tournament average to
−38.49¢/ct, a value the published tournament distribution cannot reproduce by
construction. Clustering by tournament is right for a CI on a large sample and
actively misleading on a 3-tournament block. The market-level binomial is the
correct instrument and points the other way.

Run:  python3 golf_quirks_research/analyze_p022_added_block.py
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

import quirks_common as qc          # noqa: E402
import backtest_fade_fills as fade  # noqa: E402

PUBLISHED = os.path.join(_HERE, "published_universe_364.json")
# The published headline cell (Phase-2 §2, and the row §2.1 tracks in bold).
H, OFFSET, QUOTE_SIZE = 12.0, 0.02, 25.0
N_BOOT = 20000


def load_split() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]],
                          List[Dict[str, Any]]]:
    """(published 364, added 40, the full cache IN ITS NATIVE ORDER).

    The full cache is returned separately rather than reconstructed as
    `old + new`: `bootstrap_weighted` seeds a fixed RNG and then indexes a
    tournament dict whose key order follows insertion order, so re-ordering
    the same records shifts the resampling draws and moves the CI by ~0.1¢
    (all-404 reads [+0.08,+4.51] in cache order and [+0.18,+4.53] as
    old+new). The point estimate is unaffected. Cache order is what §2.1
    reports, so cache order is what this script reproduces.
    """
    with open(PUBLISHED) as fh:
        pub = set(json.load(fh)["tickers"])
    recs = fade.load()
    old = [r for r in recs if r["ticker"] in pub]
    new = [r for r in recs if r["ticker"] not in pub]
    missing = pub - {r["ticker"] for r in recs}
    if missing:
        print(f"WARNING: {len(missing)} published tickers absent from the "
              f"cache — that is DATA LOSS, not cache growth: "
              f"{sorted(missing)[:5]}")
    return old, new, recs


def date_span(recs: Sequence[Dict[str, Any]]) -> str:
    eps = [qc.iso_epoch(r.get("close_time")) for r in recs]
    eps = [e for e in eps if e]
    if not eps:
        return "—"
    f = datetime.fromtimestamp(min(eps), timezone.utc).date()
    t = datetime.fromtimestamp(max(eps), timezone.utc).date()
    return f"{f} → {t}"


def cell(recs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return qc.replay(recs, "sell_yes", H, OFFSET, quote_size=QUOTE_SIZE,
                     collect_per_market=True)


def fmt_ci(r: Dict[str, Any]) -> str:
    if r.get("ci95_lo") is None:
        return "[      n/a       ]"
    return f"[{r['ci95_lo'] * 100:+.2f}, {r['ci95_hi'] * 100:+.2f}]"


def main() -> int:
    old, new, allrecs = load_split()
    print(f"=== P-022 added block, headline cell H={H:g} offset +{OFFSET:.2f} "
          f"===\n")

    # 1. Is the widening TIME or BAND?
    def tourn(t: str) -> str:
        p = t.split("-")
        return p[1] if len(p) >= 2 else "?"
    t_old = Counter(tourn(r["ticker"]) for r in old)
    t_new = Counter(tourn(r["ticker"]) for r in new)
    brand_new = {k: v for k, v in t_new.items() if k not in t_old}
    print(f"published : {len(old):4} markets, {len(t_old)} tournaments, "
          f"closes {date_span(old)}")
    print(f"added     : {len(new):4} markets, {len(t_new)} tournaments, "
          f"closes {date_span(new)}")
    print(f"tournaments in the added block ABSENT from the published set: "
          f"{len(brand_new)} -> {dict(sorted(brand_new.items()))}")
    print("  => the widening is a TIME extension, not a band widening: a "
          "genuine (small) out-of-sample window.\n")

    # 2. The three levels.
    r_old, r_new = cell(old), cell(new)
    r_all = qc.replay(allrecs, "sell_yes", H, OFFSET, quote_size=QUOTE_SIZE)
    print(f"{'block':<14} {'posted':>7} {'filled':>7} {'contracts':>10} "
          f"{'net c/ct':>9} {'95% CI':>20}")
    for label, r in (("published 364", r_old), ("added 40", r_new),
                     ("all 404", r_all)):
        print(f"{label:<14} {r['posted_markets']:>7} {r['filled_markets']:>7} "
              f"{r['contracts_filled']:>10.0f} "
              f"{r['net_per_contract'] * 100:>+9.2f} {fmt_ci(r):>20}")
    share = (r_new["contracts_filled"]
             / (r_new["contracts_filled"] + r_old["contracts_filled"]))
    print(f"\nadded block is {share * 100:.1f}% of all filled contracts.")
    contains = (r_new["ci95_lo"] is not None
                and r_new["ci95_lo"] <= r_old["net_per_contract"]
                <= r_new["ci95_hi"])
    print(f"added block CI contains the published "
          f"{r_old['net_per_contract'] * 100:+.2f}c ? {contains}"
          f"   <- 'same edge' cannot be rejected\n")

    # 3. Decompose the added block to individual fills.
    fills = [m for m in r_new["per_market"] if m["filled"] > 0]
    losers = [m for m in fills if m["settlement_value"] >= 0.5]
    winners = [m for m in fills if m["settlement_value"] < 0.5]
    print("every fill in the added block:")
    print(f"  {'ticker':<34} {'sold@':>6} {'settled':>8} {'cts':>6} "
          f"{'P&L $':>9}")
    for m in sorted(fills, key=lambda x: x["pnl_usd"]):
        flag = "  <- faded name WON" if m["settlement_value"] >= 0.5 else ""
        print(f"  {m['ticker']:<34} {m['quote_px']:>6.2f} "
              f"{m['settlement_value']:>8.4f} {m['filled']:>6.1f} "
              f"{m['pnl_usd']:>+9.2f}{flag}")
    lose = sum(m["pnl_usd"] for m in losers)
    win = sum(m["pnl_usd"] for m in winners)
    print(f"\n  {len(losers)} losing fills: {lose:+.2f} USD")
    print(f"  {len(winners)} winning fills: {win:+.2f} USD")
    print(f"  => removing the {len(losers)} losses leaves the block "
          f"{'PROFITABLE' if win > 0 else 'negative'} at "
          f"{win / sum(m['filled'] for m in winners) * 100:+.2f}c/ct\n")

    # 4. The right test: market-level base rate.
    fo = [m for m in r_old["per_market"] if m["filled"] > 0]
    wo = [m for m in fo if m["settlement_value"] >= 0.5]
    p_hat = len(wo) / len(fo)
    q_old = (sum(m["quote_px"] * m["filled"] for m in fo)
             / sum(m["filled"] for m in fo))
    q_new = (sum(m["quote_px"] * m["filled"] for m in fills)
             / sum(m["filled"] for m in fills))
    n, k = len(fills), len(losers)
    pv = sum(math.comb(n, i) * p_hat ** i * (1 - p_hat) ** (n - i)
             for i in range(k, n + 1))
    print("THE RIGHT TEST — market-level base rate of 'the faded name won':")
    print(f"  published : {len(wo):3}/{len(fo):3} = {100 * p_hat:5.2f}%  "
          f"at a {q_old:.3f} mean quote   <- the edge IS this gap")
    print(f"  added     : {k:3}/{n:3} = {100 * k / n:5.2f}%  "
          f"at a {q_new:.3f} mean quote")
    print(f"  P(>= {k} winners in {n} fills | published rate) = {pv:.3f}  "
          f"(expected {p_hat * n:.1f})")
    print("  => uncommon, NOT extraordinary — and observed POST-HOC.\n")

    # 5. The wrong test, kept to show why it is wrong.
    ents = [(t, v["net_per_contract"], v["contracts"])
            for t, v in r_old["per_tournament"].items()]
    obs = r_new["net_per_contract"]
    rng = random.Random(qc.BOOT_SEED)
    hit = 0
    for _ in range(N_BOOT):
        s = [ents[rng.randrange(len(ents))] for _ in range(3)]
        w = sum(c for _, _, c in s)
        if w > 0 and sum(p * c for _, p, c in s) / w <= obs:
            hit += 1
    worst = min(ents, key=lambda e: e[1])
    print("THE WRONG TEST — tournament-clustered permutation (kept as a "
          "cautionary artifact):")
    print(f"  P(random 3-tournament draw <= {obs * 100:+.2f}c) = "
          f"{hit / N_BOOT:.3f}   <- reads decisive, IS an artifact")
    print(f"  worst published tournament: {worst[0]} at "
          f"{worst[1] * 100:+.1f}c/ct — the added block's KIN26 aggregates to "
          f"{r_new['per_tournament'].get('KIN26', {}).get('net_per_contract', float('nan')) * 100:+.1f}c")
    print("  KIN26 holds 3 filled markets; ONE -$23.50 fill sets its "
          "tournament average, so the published")
    print("  tournament distribution cannot reproduce it BY CONSTRUCTION. "
          "Clustering by tournament is correct")
    print("  for a CI on a large sample and misleading on a 3-tournament "
          "block holding 3-5 markets each.\n")

    print("VERDICT: NOT DECAY. The added block is an underpowered, post-hoc "
          "adverse draw that lowers")
    print("confidence, not sign. What it IS evidence for is the TAIL (§5): at "
          "a 6c quote a winner costs")
    print("15.7x the credit, so 2 of 183 filled markets moved the headline "
          "0.84c. That is a sizing and")
    print("per-name-cap finding, and the response was already taken "
          "(T = 14 -> 24, Amendment 1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
