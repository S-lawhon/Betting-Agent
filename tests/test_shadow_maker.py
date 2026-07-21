"""
tests/test_shadow_maker.py
──────────────────────────
Tests for the shadow-maker fill model and settlement scoring.

The fill classification is the crux of the measurement: if it is wrong,
every downstream conclusion about maker viability is wrong, and the
error is silent.  These tests pin the semantics.

  ✓ taker_side="no" below our bid  → aggressive buy fill
  ✓ taker_side="no" at our bid     → at-touch only (queue unknown)
  ✓ taker_side="no" above our bid  → no fill
  ✓ taker_side="yes" above our ask → aggressive sell fill
  ✓ taker_side="yes" at our ask    → at-touch only
  ✓ unfilled quote ages out to NOFILL after 6h
  ✓ settlement P&L: YES bought wins/loses; YES sold wins/loses
  ✓ report runs on an empty log
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts import shadow_maker as sm

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def _trade(price, side, qty=10.0, minutes_after=5):
    ts = _NOW + timedelta(minutes=minutes_after)
    return {
        "yes_price_dollars": f"{price:.4f}",
        "taker_side": side,
        "count_fp": f"{qty:.2f}",
        "created_time": ts.isoformat().replace("+00:00", "Z"),
    }


class _FakeClient:
    """Stands in for KalshiTennisClient; serves a canned tape."""

    def __init__(self, trades=None, market=None):
        self._trades = trades or []
        self._market = market

    def _get(self, path):
        if "trades" in path:
            return {"trades": self._trades, "cursor": ""}
        return None

    def get_market(self, ticker):
        return self._market


def _write_quote(path: Path, our_bid=0.30, our_ask=0.35, status="QUOTED",
                 ts=None, **extra):
    rec = {
        "quote_id": "Q1", "ts": (ts or _NOW).isoformat(),
        "ticker": "KXATPSETWINNER-X-1-AAA", "series": "KXATPSETWINNER",
        "prop": "set1", "tour": "ATP", "p_fav": 0.7, "fair": 0.325,
        "our_bid": our_bid, "our_ask": our_ask,
        "book_bid": 0.25, "book_ask": 0.40, "spread": 0.15,
        "status": status,
    }
    rec.update(extra)
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


class TestFillModel(unittest.TestCase):
    def _run_fill(self, trades, now=None, **quote_kw):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "q.jsonl"
            _write_quote(log, **quote_kw)
            orig = sm.now_utc
            sm.now_utc = lambda: (now or _NOW + timedelta(minutes=30))
            try:
                sm.phase_fill(_FakeClient(trades), log)
                return sm._read(log)[0]
            finally:
                sm.now_utc = orig

    def test_aggressive_buy_fill(self):
        # someone sold YES at 0.28, below our 0.30 bid → we fill first
        r = self._run_fill([_trade(0.28, "no")])
        self.assertEqual(r["status"], "FILLED")
        self.assertEqual(r["fill"]["agg_buy"], 10.0)
        self.assertEqual(r["fill"]["touch_buy"], 0.0)

    def test_at_touch_buy_is_not_a_fill(self):
        # trade exactly at our bid → queue position unknown, not counted
        r = self._run_fill([_trade(0.30, "no")])
        self.assertEqual(r["status"], "QUOTED")  # still pending, not filled
        self.assertEqual(r.get("fill"), None)

    def test_no_fill_above_bid(self):
        r = self._run_fill([_trade(0.33, "no")])
        self.assertEqual(r["status"], "QUOTED")

    def test_aggressive_sell_fill(self):
        # someone bought YES at 0.38, above our 0.35 offer → we fill
        r = self._run_fill([_trade(0.38, "yes")])
        self.assertEqual(r["status"], "FILLED")
        self.assertEqual(r["fill"]["agg_sell"], 10.0)

    def test_at_touch_sell_recorded_when_aged_out(self):
        # at-touch only, and old enough to age out → NOFILL but touch logged
        r = self._run_fill([_trade(0.35, "yes")],
                           now=_NOW + timedelta(hours=7))
        self.assertEqual(r["status"], "NOFILL")
        self.assertEqual(r["fill"]["touch_sell"], 10.0)
        self.assertEqual(r["fill"]["agg_sell"], 0.0)

    def test_ages_out_to_nofill(self):
        r = self._run_fill([], now=_NOW + timedelta(hours=7))
        self.assertEqual(r["status"], "NOFILL")

    def test_stays_pending_before_age_limit(self):
        r = self._run_fill([], now=_NOW + timedelta(hours=1))
        self.assertEqual(r["status"], "QUOTED")


class TestSettle(unittest.TestCase):
    def _settle(self, fill, result, our_bid=0.30, our_ask=0.35):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "q.jsonl"
            _write_quote(log, our_bid=our_bid, our_ask=our_ask,
                         status="FILLED", fill=fill)
            client = _FakeClient(market={"result": result, "status": "settled"})
            sm.phase_settle(client, log)
            return sm._read(log)[0]

    def _fill(self, agg_buy=0.0, agg_sell=0.0):
        return {"agg_buy": agg_buy, "agg_sell": agg_sell,
                "touch_buy": 0.0, "touch_sell": 0.0,
                "checked_at": _NOW.isoformat(), "age_hours": 1.0}

    def test_bought_yes_wins(self):
        r = self._settle(self._fill(agg_buy=10), "yes")
        self.assertEqual(r["status"], "SETTLED")
        self.assertAlmostEqual(r["pnl"], 10 * (1 - 0.30), places=2)

    def test_bought_yes_loses(self):
        r = self._settle(self._fill(agg_buy=10), "no")
        self.assertAlmostEqual(r["pnl"], -10 * 0.30, places=2)

    def test_sold_yes_wins(self):
        # sold YES at 0.35 == bought NO at 0.65; market resolves NO
        r = self._settle(self._fill(agg_sell=10), "no")
        self.assertAlmostEqual(r["pnl"], 10 * (1 - 0.65), places=2)

    def test_sold_yes_loses(self):
        r = self._settle(self._fill(agg_sell=10), "yes")
        self.assertAlmostEqual(r["pnl"], -10 * 0.65, places=2)

    def test_unresolved_stays_filled(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "q.jsonl"
            _write_quote(log, status="FILLED", fill=self._fill(agg_buy=5))
            client = _FakeClient(market={"result": "", "status": "active"})
            sm.phase_settle(client, log)
            self.assertEqual(sm._read(log)[0]["status"], "FILLED")


class TestReport(unittest.TestCase):
    def test_empty_log(self):
        with tempfile.TemporaryDirectory() as td:
            sm.report(Path(td) / "none.jsonl")  # must not raise


if __name__ == "__main__":
    unittest.main()
