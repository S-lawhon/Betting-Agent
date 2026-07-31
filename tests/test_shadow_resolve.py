"""
tests/test_shadow_resolve.py
────────────────────────────
Tests for the P-029 shadow logger's resolution selection.

This SQL decides which combos ever enter the Gate 0 sample, so an error here
is silent and biases the gate rather than breaking anything. It has already
failed once in production: discovery outran resolution ~3:1, the backlog hit
546,370, and every traded row in the gate sample came from a single 6-hour
window while the log cheerfully reported "resolved 400 combos" each cycle.

  ✓ only combos past RESOLVE_MIN_AGE_H are due
  ✓ in-zone rows are taken before out-of-zone ones
  ✓ out-of-zone rows still get the leftover capacity
  ✓ the batch is capped
  ✓ already-resolved rows are never re-picked
  ✓ the priority band is a SUPERSET of the gate zone on BOTH pricing bases
  ✓ a NULL copula_price falls back to indep_price, never vanishes
"""

import sqlite3
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from combo_research.shadow_public import (  # noqa: E402
    PRIORITY_SQL, RESOLVE_MIN_AGE_H, _due, iso_at,
)

H = 3600.0


class ResolveSelectionTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute(
            "CREATE TABLE combo (market_ticker TEXT PRIMARY KEY, seen_ts TEXT,"
            " n_legs INTEGER, indep_price REAL, copula_price REAL,"
            " resolved INTEGER DEFAULT 0)")
        self.now = time.time()

    def add(self, tkr, age_h, n_legs=3, indep=0.20, copula=0.23, resolved=0):
        self.con.execute(
            "INSERT INTO combo (market_ticker, seen_ts, n_legs, indep_price,"
            " copula_price, resolved) VALUES (?,?,?,?,?,?)",
            (tkr, iso_at(self.now - age_h * H), n_legs, indep, copula, resolved))

    def due(self, limit=600, priority=True):
        before = iso_at(self.now - RESOLVE_MIN_AGE_H * H)
        return [r[0] for r in _due(self.con, before, limit, priority)]

    # --- age gate ---------------------------------------------------------
    def test_young_combos_are_not_due(self):
        self.add("YOUNG", age_h=RESOLVE_MIN_AGE_H - 1)
        self.add("OLD", age_h=RESOLVE_MIN_AGE_H + 1)
        self.assertEqual(self.due(), ["OLD"])

    def test_oldest_first(self):
        self.add("A", age_h=50)
        self.add("B", age_h=80)
        self.add("C", age_h=60)
        self.assertEqual(self.due(), ["B", "C", "A"])

    def test_resolved_rows_are_never_repicked(self):
        self.add("DONE", age_h=99, resolved=1)
        self.add("TODO", age_h=99)
        self.assertEqual(self.due(), ["TODO"])

    # --- priority ---------------------------------------------------------
    def test_in_zone_taken_before_out_of_zone(self):
        # OUT is older, but priority must still win.
        self.add("OUT", age_h=99, copula=0.70)
        self.add("IN", age_h=50, copula=0.20)
        self.assertEqual(self.due(priority=True), ["IN"])
        self.assertEqual(self.due(priority=False), ["OUT"])

    def test_out_of_zone_gets_leftover_capacity(self):
        self.add("IN", age_h=50, copula=0.20)
        self.add("OUT", age_h=99, copula=0.70)
        batch = 600
        rows = self.due(batch, True)
        rows += self.due(batch - len(rows), False)
        self.assertEqual(rows, ["IN", "OUT"])

    def test_batch_is_capped(self):
        for i in range(10):
            self.add(f"T{i:02d}", age_h=50 + i)
        self.assertEqual(len(self.due(limit=4)), 4)

    def test_zero_limit_returns_nothing_and_does_not_query(self):
        self.add("IN", age_h=99)
        self.assertEqual(self.due(limit=0), [])

    # --- the band is a superset of BOTH zone bases ------------------------
    def test_band_covers_gate_zone_on_both_bases(self):
        """The gate zone is 0.10–0.35 on the model price, and the stored
        in_zone column mixes an independence-based rule with a copula-based
        one. Copula runs ~+3c above independence, so a combo sitting at either
        edge on EITHER basis must still be picked up."""
        cases = [
            ("indep_lo", 0.10, 0.13),   # at the zone floor on independence
            ("indep_hi", 0.35, 0.38),   # at the ceiling on independence
            ("cop_lo", 0.07, 0.10),     # at the floor on copula
            ("cop_hi", 0.32, 0.35),     # at the ceiling on copula
        ]
        for tkr, indep, copula in cases:
            self.add(tkr, age_h=50, indep=indep, copula=copula)
        got = set(self.due())
        self.assertEqual(got, {t for t, _, _ in cases},
                         "a combo inside the gate zone on either pricing basis "
                         "was excluded from priority resolution")

    def test_clearly_out_of_band_is_deprioritised(self):
        self.add("CHEAP", age_h=50, indep=0.01, copula=0.02)
        self.add("RICH", age_h=50, indep=0.80, copula=0.85)
        self.assertEqual(self.due(priority=True), [])
        self.assertEqual(sorted(self.due(priority=False)), ["CHEAP", "RICH"])

    def test_leg_count_bounds(self):
        self.add("L1", age_h=50, n_legs=1)
        self.add("L2", age_h=50, n_legs=2)
        self.add("L4", age_h=50, n_legs=4)
        self.add("L5", age_h=50, n_legs=5)
        self.assertEqual(sorted(self.due(priority=True)), ["L2", "L4"])

    # --- null handling ----------------------------------------------------
    def test_null_copula_falls_back_to_indep(self):
        """Rows logged before the copula landed carry a NULL copula_price if the
        backfill has not reached them. They must fall back to indep_price, not
        drop out of BOTH the priority and non-priority queries."""
        self.add("NULLCOP", age_h=50, indep=0.20, copula=None)
        self.assertEqual(self.due(priority=True), ["NULLCOP"])

    def test_row_with_no_price_at_all_still_resolvable(self):
        self.add("NOPX", age_h=50, indep=None, copula=None)
        picked = set(self.due(priority=True)) | set(self.due(priority=False))
        self.assertIn("NOPX", picked,
                      "a row with no stored price fell out of both queries and "
                      "would never resolve")

    def test_priority_partition_is_exhaustive(self):
        """Every due row lands in exactly one of the two queries."""
        for i, (nl, ip, cp) in enumerate([
                (2, 0.20, 0.23), (3, 0.90, 0.92), (5, 0.20, 0.23),
                (4, 0.05, 0.06), (2, None, None), (3, 0.20, None)]):
            self.add(f"R{i}", age_h=50 + i, n_legs=nl, indep=ip, copula=cp)
        a, b = set(self.due(priority=True)), set(self.due(priority=False))
        self.assertEqual(a & b, set(), "a row matched both queries")
        self.assertEqual(len(a | b), 6, "a row matched neither query")


if __name__ == "__main__":
    unittest.main()
