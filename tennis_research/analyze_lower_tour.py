"""Test the attention thesis on Challenger/ITF tours.

The P-015 finding was: tennis match-line mispricing concentrates in
qualifying rounds — the least-watched markets on the board. If the
mechanism is *attention* rather than something specific to quals, then
Challenger and ITF main draws (structurally lower-attention than tour
main draws) should show the same or a stronger effect.

This is an out-of-sample test of the MECHANISM, not a re-slice of the
same data: different series, different players, different events.

Usage: python3 analyze_lower_tour.py <scratch_dir>
"""
import sys

from analyze_flb import load, calibration, taker_ev, wilson

SC = sys.argv[1] if len(sys.argv) > 1 else "."

# tour-level (already analyzed) for side-by-side comparison
tour_rows = load(2.0, "hist_candles.jsonl")
low_rows = load(2.0, "lower_tour_candles.jsonl")

print(f"tour-level observations : {len(tour_rows)}")
print(f"lower-tour observations : {len(low_rows)}")
for tier in ("challenger", "itf"):
    n = sum(1 for r in low_rows if r["tier"] == tier)
    print(f"   {tier:<11}: {n}")

# ── calibration by tier ──────────────────────────────────────────────
calibration([r for r in low_rows if r["tier"] == "challenger"], "CHALLENGER T-2h")
calibration([r for r in low_rows if r["tier"] == "itf"], "ITF T-2h")
calibration([r for r in low_rows if r["qual"]], "lower-tour QUALIFIERS T-2h")
calibration([r for r in low_rows if not r["qual"]], "lower-tour MAIN DRAW T-2h")

# ── the headline comparison ──────────────────────────────────────────
print("\n" + "=" * 74)
print("ATTENTION THESIS TEST — favorite taker EV at ask>=0.85, net of fees")
print("=" * 74)
print(f"{'cohort':<34}{'n':>6}{'hit':>8}{'EV/ct':>9}{'95% CI':>22}")


def row(label, rows_, floor=0.85):
    r = taker_ev(rows_, floor)
    if not r:
        print(f"{label:<34}{'—':>6}")
        return None
    print(f"{label:<34}{r['n']:>6}{r['hit']:>8.3f}{r['mean']*100:>+8.2f}c"
          f"   [{r['lo']*100:+.2f}c,{r['hi']*100:+.2f}c]")
    return r


print("\n-- reference: tour level (the original finding) --")
row("tour quals (P-015 basis)", [r for r in tour_rows if r["qual"]])
row("tour main draw", [r for r in tour_rows if not r["qual"]])

print("\n-- out-of-sample: lower tours --")
row("challenger, all", [r for r in low_rows if r["tier"] == "challenger"])
row("challenger quals", [r for r in low_rows if r["tier"] == "challenger" and r["qual"]])
row("challenger main", [r for r in low_rows if r["tier"] == "challenger" and not r["qual"]])
row("ITF, all", [r for r in low_rows if r["tier"] == "itf"])
row("ITF quals", [r for r in low_rows if r["tier"] == "itf" and r["qual"]])
row("ITF main", [r for r in low_rows if r["tier"] == "itf" and not r["qual"]])

print("\n-- pooled candidates for P-015b --")
row("ALL quals (tour+challenger+ITF)",
    [r for r in tour_rows if r["qual"]] + [r for r in low_rows if r["qual"]])
row("ALL lower-tour (any round)", low_rows)

# ── volume implication ───────────────────────────────────────────────
print("\n" + "=" * 74)
print("VOLUME IMPLICATION (trades/month at ask>=0.85)")
print("=" * 74)
import datetime
from collections import Counter


def per_month(rows_, label):
    tr = [r for r in rows_ if 0.85 <= r["fav_ask"] <= 0.985]
    if not tr:
        print(f"  {label:<34} 0/mo")
        return
    months = Counter(
        datetime.datetime.utcfromtimestamp(r["occ"]).strftime("%Y-%m") for r in tr
    )
    # only count months with meaningful coverage
    active = {k: v for k, v in months.items() if v >= 3}
    rate = sum(active.values()) / max(len(active), 1)
    print(f"  {label:<34}{rate:>6.0f}/mo  (n={len(tr)} over {len(active)} months)")


per_month([r for r in tour_rows if r["qual"]], "tour quals (current P-015)")
per_month([r for r in low_rows if r["tier"] == "challenger"], "+ challenger (all rounds)")
per_month([r for r in low_rows if r["tier"] == "itf"], "+ ITF (all rounds)")
per_month([r for r in tour_rows if r["qual"]] + low_rows, "P-015b pooled candidate")

print("\nNOTE: lower-tour data is a seeded random sample of the event pool,")
print("so per-month rates are LOWER BOUNDS on live opportunity count.")
