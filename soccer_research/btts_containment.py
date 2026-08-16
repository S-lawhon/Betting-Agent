#!/usr/bin/env python3
"""Soccer BTTS containment scanner — Phase 1 (READ-ONLY, offline).

Governing rule: soccer_research/BTTS_CONTAINMENT_RULE.md, committed in 3cffb2e
BEFORE this file existed and before any pair was matched.

Tests the entailment P(BTTS_1H) <= P(BTTS_full) on the same fixture, via the
attrition funnel inherited verbatim from satellites_research/wins_scanner.py.
Runs entirely off the Phase 0 snapshots; makes no network call.

    python3 soccer_research/btts_containment.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.kalshi_fees import fee_per_contract  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

MIN_DEPTH_CT = 20        # F3, inherited from wins_scanner MIN_SIZE
MIN_FIXTURES = 20        # rule section 4, NO DECISION - SEASONAL below this

_SEG = re.compile(r"^(?P<series>KX[A-Z0-9]+?)(?P<half>1H|2H|)BTTS-(?P<fixture>[^-]+)-")


def parse(ticker: str):
    """(league, half, fixture) or None.

    KXMLS1HBTTS-26AUG16SEAVAN-BTTS -> ("KXMLS", "1H", "26AUG16SEAVAN")
    KXMLSBTTS-26AUG16SEAVAN-BTTS   -> ("KXMLS", "",   "26AUG16SEAVAN")
    """
    m = _SEG.match(ticker or "")
    if not m:
        return None
    return m.group("series"), m.group("half"), m.group("fixture")


def load_snapshots() -> list[dict]:
    return [json.loads(p.read_text())
            for p in sorted(DATA.glob("btts_1h_snapshot_*.json"))]


def index(snap: dict) -> dict:
    """(league, fixture) -> {half: row}, over BOTH market groups."""
    out: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for group in ("first_half", "full_match"):
        for row in snap["markets"][group]:
            p = parse(row.get("ticker", ""))
            if not p:
                continue
            league, half, fixture = p
            out[(league, fixture)][half] = row
    return out


def scan(snap: dict) -> dict:
    """One snapshot through F0..F4. F5 needs two snapshots."""
    idx = index(snap)
    att = {k: 0 for k in ("F0_fixtures", "F0_pairs", "F0p_mid_inversions",
                          "F1_two_sided", "F2_executable", "F3_depth",
                          "F4_net_of_fees")}
    findings, inversions = [], []

    for (league, fixture), halves in sorted(idx.items()):
        full = halves.get("")
        if not full:
            continue
        att["F0_fixtures"] += 1
        for hkey in ("1H", "2H"):
            part = halves.get(hkey)
            if not part:
                continue
            att["F0_pairs"] += 1

            # F0' — the naive mid violation. NOT a finding (rule section 6.4).
            if (part["mid"] is not None and full["mid"] is not None
                    and part["mid"] > full["mid"]):
                att["F0p_mid_inversions"] += 1
                inversions.append((part["ticker"], full["ticker"]))

            # F1 — both legs genuinely two-sided.
            if not (part["two_sided"] and full["two_sided"]):
                continue
            att["F1_two_sided"] += 1

            # F2 — executable: SELL the half at its bid, BUY full at its ask.
            # Payoff is 1{full} - 1{half} >= 0 in every settled state, so a
            # positive net credit is the arbitrage.
            credit = part["yes_bid"] - full["yes_ask"]
            if credit <= 0:
                continue
            att["F2_executable"] += 1

            # F3 — depth on every leg actually used.
            size = min(part["bid_qty"], full["ask_qty"])
            if size < MIN_DEPTH_CT:
                continue
            att["F3_depth"] += 1

            # F4 — net of the TAKER fee on both legs, at the traded prices.
            fees = (fee_per_contract(part["yes_bid"], maker=False,
                                     series_ticker=part["series"])
                    + fee_per_contract(full["yes_ask"], maker=False,
                                       series_ticker=full["series"]))
            net = credit - fees
            if net <= 0:
                continue
            att["F4_net_of_fees"] += 1
            findings.append({
                "legs": [part["ticker"], full["ticker"]],
                "credit_c": round(credit * 100, 2),
                "fees_c": round(fees * 100, 2),
                "net_c": round(net * 100, 2),
                "size_ct": size,
            })

    return {"attrition": att, "findings": findings, "inversions": inversions,
            "snapshot": snap["snapshot"], "captured_at_utc": snap["captured_at_utc"]}


def verdict(results: list[dict]) -> tuple[str, str]:
    """Rule section 4, applied mechanically."""
    last = results[-1]
    a = last["attrition"]
    if a["F0_fixtures"] < MIN_FIXTURES:
        return ("NO DECISION — SEASONAL",
                f"only {a['F0_fixtures']} matched fixtures (< {MIN_FIXTURES})")
    if all(r["attrition"]["F2_executable"] == 0 for r in results):
        return ("KILL",
                "F2 = 0 across all matched fixtures in every snapshot: no "
                "violation survives crossing both spreads")
    if a["F4_net_of_fees"] == 0:
        return ("NO DECISION",
                "violations exist but none clears the taker fee on both legs")
    if len(results) < 2:
        return ("NO DECISION",
                "F4 > 0 but persistence (F5) is unmeasured; one snapshot is "
                "not a result (rule section 6.3)")
    first, lastf = results[0], results[-1]
    a0 = {tuple(sorted(f["legs"])) for f in first["findings"]}
    a1 = {tuple(sorted(f["legs"])) for f in lastf["findings"]}
    if not (a0 & a1):
        return ("NO DECISION", "F4 > 0 but nothing persisted across snapshots")
    return ("ESCALATE",
            f"{len(a0 & a1)} fee-clearing violations persisted across snapshots")


def main() -> int:
    snaps = load_snapshots()
    if not snaps:
        print("no snapshots found; run btts_1h_census.py --snapshot 1 first")
        return 1

    results = []
    for snap in snaps:
        r = scan(snap)
        results.append(r)
        print(f"\n=== snapshot {r['snapshot']} @ {r['captured_at_utc']}")
        for k, v in r["attrition"].items():
            print(f"    {k:24s} {v}")
        for f in r["findings"]:
            print("    FINDING", json.dumps(f))
        if r["inversions"]:
            print(f"    (F0' inversions are NOT findings — rule section 6.4)")

    if len(results) >= 2:
        i0 = {tuple(x) for x in results[0]["inversions"]}
        i1 = {tuple(x) for x in results[-1]["inversions"]}
        print(f"\nF5 — mid inversions: {len(i0)} first, {len(i1)} last, "
              f"{len(i0 & i1)} in both")
        f0 = {tuple(sorted(f['legs'])) for f in results[0]["findings"]}
        f1 = {tuple(sorted(f['legs'])) for f in results[-1]["findings"]}
        print(f"F5 — fee-clearing violations persisting: {len(f0 & f1)}")
    else:
        print("\nF5 — NOT MEASURED (one snapshot only)")

    v, why = verdict(results)
    print(f"\n===== VERDICT (rule section 4) =====\n  {v}\n  {why}")

    (DATA / "containment_results.json").write_text(
        json.dumps({"results": results, "verdict": v, "rationale": why},
                   indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
