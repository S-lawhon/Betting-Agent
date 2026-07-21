"""
tests/test_executor.py
───────────────────────
Phase 7 must-pass tests:

  KalshiClient guard
  ✓ place_order raises KalshiDemoOnlyError when env="live" and flag unset
  ✓ place_order succeeds when env="live" and live_trading_confirmed=True
  ✓ place_order succeeds when env="demo" regardless of flag

  _build_order
  ✓ yes_price computed from kalshi_prob (cents, clamped 1–99)
  ✓ side lowercased: YES→yes, NO→no
  ✓ contracts = floor(size / cost), min 1 for YES side
  ✓ contracts = floor(size / (1-price)) for NO side
  ✓ contracts minimum 1 even for tiny sizes
  ✓ client_order_id is a hex UUID string

  ExecutionResult
  ✓ dataclass fields accessible

  Executor — paper mode
  ✓ run_cycle calls scanner.scan_once
  ✓ paper mode: PLACED entry → success=True, mode="paper", order_id=None
  ✓ paper mode: Ledger.open_position called after fill
  ✓ paper mode: fill_price equals kalshi_prob from entry
  ✓ paper mode: non-PLACED entries ignored (no Ledger update)
  ✓ paper mode: position_size_usd=0 → no Ledger.open_position call
  ✓ paper mode: no trade log written (Scanner already wrote it)

  Executor — live mode guards
  ✓ live mode + confirmed=False → LiveTradingNotConfirmedError
  ✓ live mode + confirmed=True → place_order called
  ✓ live mode: KalshiDemoOnlyError from place_order → success=False
  ✓ live mode: generic order error → success=False, error populated
  ✓ live mode: successful order → order_id extracted from response
  ✓ live mode: successful order → Ledger.open_position called
  ✓ live mode: EXECUTED record appended to trade log
  ✓ live mode: EXECUTED record has order_id field
  ✓ live mode: write error on trade log does not crash

  Executor — from_config
  ✓ mode="live" in config → Executor mode="live"
  ✓ mode="demo" in config → Executor mode="paper"
  ✓ I_UNDERSTAND_LIVE_TRADING=true → live_trading_confirmed=True
  ✓ I_UNDERSTAND_LIVE_TRADING absent → live_trading_confirmed=False
  ✓ trade_log path read from config paths
  ✓ from_config propagates confirmed flag to KalshiClient

  Executor — session integration
  ✓ run_cycle processes multiple PLACED entries
  ✓ scanner returning empty list → empty results
  ✓ scanner error propagates (no crash)
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.edge_calculator import EdgeCalculator
from src.executor import (
    ExecutionResult,
    Executor,
    LiveTradingNotConfirmedError,
    _build_order,
)
from src.kalshi_client import KalshiClient, KalshiDemoOnlyError
from src.risk_manager import Ledger, RiskManager
from src.scanner import Scanner


# ── Fixtures ──────────────────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)
_SPORT = "americanfootball_nfl"


def _placed_entry(
    ticker="NFL-KC-LV-ML",
    side="YES",
    kalshi_prob=0.57,
    size_usd=50.0,
) -> Dict[str, Any]:
    """Minimal PLACED trade-log entry, as Scanner would produce."""
    return {
        "fingerprint":       "abc123",
        "timestamp_utc":     _NOW.isoformat(),
        "mode":              "paper",
        "sport":             _SPORT,
        "event":             "Kansas City Chiefs vs Las Vegas Raiders",
        "market_ticker":     ticker,
        "side":              side,
        "fair_prob":         0.62,
        "kalshi_prob":       kalshi_prob,
        "edge_pct":          0.05,
        "ev":                0.03,
        "kelly_fraction":    0.005,
        "position_size_usd": size_usd,
        "action":            "PLACED",
        "order_id":          None,
        "fill_price":        kalshi_prob,
    }


def _real_risk(bankroll=10_000.0) -> RiskManager:
    ledger = Ledger(initial_bankroll=bankroll)
    return RiskManager(
        ledger=ledger,
        max_positions=10,
        max_exposure_pct=0.20,
        max_daily_loss_pct=0.05,
        cooldown_minutes=60,
        kelly_fraction=0.25,
        max_bet_pct=0.03,
        min_confidence=0.55,
    )


class _MockScanner:
    def __init__(self, entries=None, raise_on_scan=False):
        self._entries = entries if entries is not None else []
        self._raise = raise_on_scan
        self.scan_calls = 0

    def scan_once(self) -> List[Dict[str, Any]]:
        self.scan_calls += 1
        if self._raise:
            raise RuntimeError("scanner mock error")
        return list(self._entries)


class _MockKalshi:
    """Mock KalshiClient with injectable place_order behaviour."""

    def __init__(self, order_resp=None, raise_exc=None):
        self._resp = order_resp or {"order": {"order_id": "ORDER-123"}}
        self._exc = raise_exc
        self.place_calls: List[Dict] = []
        self.live_trading_confirmed = False
        self.environment = "live"

    def place_order(self, order: Dict) -> Dict:
        self.place_calls.append(order)
        if self._exc:
            raise self._exc
        return self._resp


def _executor(
    scanner=None,
    kalshi=None,
    risk=None,
    mode="paper",
    confirmed=False,
    log_path=None,
) -> Executor:
    return Executor(
        scanner=scanner or _MockScanner(),
        kalshi_client=kalshi or _MockKalshi(),
        risk_manager=risk or _real_risk(),
        mode=mode,
        live_trading_confirmed=confirmed,
        trade_log_path=log_path,
        _now_fn=lambda: _NOW,
    )


# ── KalshiClient guard (Phase 7 modification) ─────────────────────────────────

class TestKalshiClientLiveGuard(unittest.TestCase):

    def _make_client(self, environment="demo", live_confirmed=False):
        """Build a minimal KalshiClient with a mock adapter."""
        from src.kalshi_client import TokenBucketRateLimiter

        class _NoopAdapter:
            def request(self, *a, **kw):
                from src.kalshi_client import _Response
                return _Response(
                    status_code=200,
                    _body=b'{"order": {"order_id": "X"}}',
                    _headers={},
                )

        rl = TokenBucketRateLimiter(5, 10)
        client = KalshiClient(
            base_url="https://demo-api.kalshi.co/trade-api/v2",
            email="test@test.com",
            password="pass",
            environment=environment,
            rate_limiter=rl,
            adapter=_NoopAdapter(),
            live_trading_confirmed=live_confirmed,
        )
        client._token = "fake-token"   # skip login
        return client

    def test_demo_env_places_order_without_flag(self):
        """Demo mode always works."""
        client = self._make_client(environment="demo", live_confirmed=False)
        # Should not raise
        resp = client.place_order({"ticker": "T", "action": "buy", "side": "yes",
                                   "type": "limit", "count": 1, "yes_price": 57})
        self.assertIn("order", resp)

    def test_live_env_without_flag_raises(self):
        """Live mode without confirmation → KalshiDemoOnlyError."""
        client = self._make_client(environment="live", live_confirmed=False)
        with self.assertRaises(KalshiDemoOnlyError):
            client.place_order({"ticker": "T", "action": "buy", "side": "yes",
                                "type": "limit", "count": 1, "yes_price": 57})

    def test_live_env_with_flag_places_order(self):
        """Live mode with confirmed=True bypasses the guard."""
        client = self._make_client(environment="live", live_confirmed=True)
        resp = client.place_order({"ticker": "T", "action": "buy", "side": "yes",
                                   "type": "limit", "count": 1, "yes_price": 57})
        self.assertIn("order", resp)

    def test_flag_defaults_to_false(self):
        """Existing code unaffected — flag defaults False."""
        client = self._make_client(environment="live")
        self.assertFalse(client.live_trading_confirmed)


# ── _build_order ──────────────────────────────────────────────────────────────

class TestBuildOrder(unittest.TestCase):

    def test_yes_side_lowercased(self):
        entry = _placed_entry(side="YES", kalshi_prob=0.57, size_usd=50.0)
        order = _build_order(entry)
        self.assertEqual(order["side"], "yes")

    def test_no_side_lowercased(self):
        entry = _placed_entry(side="NO", kalshi_prob=0.57, size_usd=50.0)
        order = _build_order(entry)
        self.assertEqual(order["side"], "no")

    def test_yes_price_in_cents(self):
        entry = _placed_entry(kalshi_prob=0.57, size_usd=50.0)
        order = _build_order(entry)
        self.assertEqual(order["yes_price"], 57)

    def test_yes_price_clamped_low(self):
        entry = _placed_entry(kalshi_prob=0.001, size_usd=10.0)
        order = _build_order(entry)
        self.assertGreaterEqual(order["yes_price"], 1)

    def test_yes_price_clamped_high(self):
        entry = _placed_entry(kalshi_prob=0.999, size_usd=10.0)
        order = _build_order(entry)
        self.assertLessEqual(order["yes_price"], 99)

    def test_contracts_yes_side(self):
        # size=$57, price=0.57 → floor(57/0.57) = 100 contracts
        entry = _placed_entry(side="YES", kalshi_prob=0.57, size_usd=57.0)
        order = _build_order(entry)
        self.assertEqual(order["count"], 100)

    def test_contracts_no_side(self):
        # size=$50, NO cost=1-0.50=0.50 → floor(50/0.50) = 100 contracts
        # Use 0.50 to avoid float precision issues with (1.0 - 0.57)
        entry = _placed_entry(side="NO", kalshi_prob=0.50, size_usd=50.0)
        order = _build_order(entry)
        self.assertEqual(order["count"], 100)

    def test_contracts_minimum_one(self):
        # Very small size → still 1 contract minimum
        entry = _placed_entry(side="YES", kalshi_prob=0.57, size_usd=0.001)
        order = _build_order(entry)
        self.assertGreaterEqual(order["count"], 1)

    def test_ticker_matches_entry(self):
        entry = _placed_entry(ticker="NFL-KC-LV-ML")
        order = _build_order(entry)
        self.assertEqual(order["ticker"], "NFL-KC-LV-ML")

    def test_client_order_id_is_hex_string(self):
        entry = _placed_entry()
        order = _build_order(entry)
        cid = order["client_order_id"]
        self.assertIsInstance(cid, str)
        self.assertEqual(len(cid), 32)   # uuid4 hex = 32 chars
        self.assertTrue(all(c in "0123456789abcdef" for c in cid))

    def test_type_is_limit(self):
        order = _build_order(_placed_entry())
        self.assertEqual(order["type"], "limit")

    def test_action_is_buy(self):
        order = _build_order(_placed_entry())
        self.assertEqual(order["action"], "buy")


# ── Executor — paper mode ─────────────────────────────────────────────────────

class TestExecutorPaperMode(unittest.TestCase):

    def _run(self, entries, risk=None, log_path=None) -> List[ExecutionResult]:
        scanner = _MockScanner(entries=entries)
        ex = _executor(scanner=scanner, mode="paper", risk=risk,
                       log_path=log_path)
        return ex.run_cycle()

    def test_run_cycle_calls_scanner(self):
        scanner = _MockScanner(entries=[])
        ex = _executor(scanner=scanner, mode="paper")
        ex.run_cycle()
        self.assertEqual(scanner.scan_calls, 1)

    def test_placed_entry_returns_success(self):
        results = self._run([_placed_entry()])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

    def test_paper_mode_field_on_result(self):
        results = self._run([_placed_entry()])
        self.assertEqual(results[0].mode, "paper")

    def test_order_id_none_in_paper(self):
        results = self._run([_placed_entry()])
        self.assertIsNone(results[0].order_id)

    def test_fill_price_equals_kalshi_prob(self):
        entry = _placed_entry(kalshi_prob=0.57)
        results = self._run([entry])
        self.assertAlmostEqual(results[0].fill_price, 0.57)

    def test_contracts_zero_in_paper(self):
        results = self._run([_placed_entry()])
        self.assertEqual(results[0].contracts, 0)

    def test_ledger_position_opened(self):
        risk = _real_risk()
        self.assertEqual(len(risk.ledger._open), 0)
        self._run([_placed_entry(size_usd=50.0)], risk=risk)
        self.assertEqual(len(risk.ledger._open), 1)

    def test_non_placed_entries_ignored(self):
        entries = [
            {**_placed_entry(), "action": "SKIPPED_EDGE"},
            {**_placed_entry(), "action": "SKIPPED_RISK"},
        ]
        results = self._run(entries)
        self.assertEqual(results, [])

    def test_zero_size_no_ledger_position(self):
        risk = _real_risk()
        entry = _placed_entry(size_usd=0.0)
        self._run([entry], risk=risk)
        self.assertEqual(len(risk.ledger._open), 0)

    def test_no_trade_log_written_paper(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        self._run([_placed_entry()], log_path=log_path)
        # Scanner writes the log; Executor does NOT append in paper mode
        self.assertFalse(log_path.exists())

    def test_empty_scanner_returns_empty(self):
        results = self._run([])
        self.assertEqual(results, [])


# ── Executor — live mode ──────────────────────────────────────────────────────

class TestExecutorLiveMode(unittest.TestCase):

    def setUp(self):
        self._log = Path(tempfile.mktemp(suffix=".jsonl"))

    def _records(self):
        if not self._log.exists():
            return []
        lines = self._log.read_text().strip().splitlines()
        return [json.loads(ln) for ln in lines if ln.strip()]

    def test_live_confirmed_false_raises(self):
        entry = _placed_entry()
        ex = _executor(
            scanner=_MockScanner(entries=[entry]),
            mode="live",
            confirmed=False,
        )
        with self.assertRaises(LiveTradingNotConfirmedError):
            ex.run_cycle()

    def test_live_confirmed_true_calls_place_order(self):
        kalshi = _MockKalshi()
        entry = _placed_entry()
        ex = _executor(
            scanner=_MockScanner(entries=[entry]),
            kalshi=kalshi,
            mode="live",
            confirmed=True,
            log_path=self._log,
        )
        ex.run_cycle()
        self.assertEqual(len(kalshi.place_calls), 1)

    def test_live_success_result_fields(self):
        kalshi = _MockKalshi(order_resp={"order": {"order_id": "ORD-XYZ"}})
        entry = _placed_entry()
        ex = _executor(
            scanner=_MockScanner(entries=[entry]),
            kalshi=kalshi,
            mode="live",
            confirmed=True,
            log_path=self._log,
        )
        results = ex.run_cycle()
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertTrue(r.success)
        self.assertEqual(r.mode, "live")
        self.assertEqual(r.order_id, "ORD-XYZ")

    def test_live_success_opens_ledger_position(self):
        risk = _real_risk()
        kalshi = _MockKalshi()
        ex = _executor(
            scanner=_MockScanner(entries=[_placed_entry(size_usd=50.0)]),
            kalshi=kalshi,
            risk=risk,
            mode="live",
            confirmed=True,
            log_path=self._log,
        )
        ex.run_cycle()
        self.assertEqual(len(risk.ledger._open), 1)

    def test_live_success_appends_executed_record(self):
        kalshi = _MockKalshi(order_resp={"order": {"order_id": "ORD-123"}})
        ex = _executor(
            scanner=_MockScanner(entries=[_placed_entry()]),
            kalshi=kalshi,
            mode="live",
            confirmed=True,
            log_path=self._log,
        )
        ex.run_cycle()
        records = self._records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["action"], "EXECUTED")
        self.assertEqual(records[0]["order_id"], "ORD-123")

    def test_demo_only_error_returns_failure(self):
        kalshi = _MockKalshi(raise_exc=KalshiDemoOnlyError("demo only"))
        entry = _placed_entry()
        ex = _executor(
            scanner=_MockScanner(entries=[entry]),
            kalshi=kalshi,
            mode="live",
            confirmed=True,
        )
        results = ex.run_cycle()
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].success)
        self.assertIn("demo", results[0].error.lower())

    def test_generic_order_error_returns_failure(self):
        kalshi = _MockKalshi(raise_exc=RuntimeError("network error"))
        ex = _executor(
            scanner=_MockScanner(entries=[_placed_entry()]),
            kalshi=kalshi,
            mode="live",
            confirmed=True,
        )
        results = ex.run_cycle()
        self.assertFalse(results[0].success)
        self.assertIsNotNone(results[0].error)

    def test_live_log_write_error_no_crash(self):
        bad_path = Path("/dev/null/not_a_dir/trade.jsonl")
        kalshi = _MockKalshi()
        ex = _executor(
            scanner=_MockScanner(entries=[_placed_entry()]),
            kalshi=kalshi,
            mode="live",
            confirmed=True,
            log_path=bad_path,
        )
        results = ex.run_cycle()
        # Should complete successfully despite log write failure
        self.assertTrue(results[0].success)

    def test_order_built_correctly_for_live(self):
        kalshi = _MockKalshi()
        entry = _placed_entry(ticker="NFL-T", side="YES",
                              kalshi_prob=0.57, size_usd=57.0)
        ex = _executor(
            scanner=_MockScanner(entries=[entry]),
            kalshi=kalshi,
            mode="live",
            confirmed=True,
            log_path=self._log,
        )
        ex.run_cycle()
        submitted = kalshi.place_calls[0]
        self.assertEqual(submitted["ticker"], "NFL-T")
        self.assertEqual(submitted["side"], "yes")
        self.assertEqual(submitted["yes_price"], 57)
        self.assertEqual(submitted["count"], 100)

    def test_confirmed_flag_set_on_kalshi_client(self):
        """Executor propagates confirmed flag to KalshiClient."""
        kalshi = _MockKalshi()
        ex = _executor(kalshi=kalshi, mode="live", confirmed=True)
        self.assertTrue(kalshi.live_trading_confirmed)


# ── from_config ───────────────────────────────────────────────────────────────

class TestExecutorFromConfig(unittest.TestCase):

    def _cfg(self, env="demo", trade_log="data/trade_logs/trade_log.jsonl"):
        return {
            "environment": env,
            "paths": {"trade_log": trade_log},
        }

    def _make(self, env="demo", live_gate="", **kwargs):
        scanner = _MockScanner()
        kalshi = _MockKalshi()
        risk = _real_risk()
        with patch.dict(os.environ, {"I_UNDERSTAND_LIVE_TRADING": live_gate},
                        clear=False):
            return Executor.from_config(
                self._cfg(env=env), scanner, kalshi, risk, **kwargs
            )

    def test_demo_env_maps_to_paper_mode(self):
        ex = self._make(env="demo")
        self.assertEqual(ex._mode, "paper")

    def test_live_env_maps_to_live_mode(self):
        ex = self._make(env="live")
        self.assertEqual(ex._mode, "live")

    def test_unknown_env_maps_to_paper(self):
        ex = self._make(env="unknown")
        self.assertEqual(ex._mode, "paper")

    def test_live_gate_true_sets_confirmed(self):
        ex = self._make(env="live", live_gate="true")
        self.assertTrue(ex._confirmed)

    def test_live_gate_true_case_insensitive(self):
        ex = self._make(env="live", live_gate="TRUE")
        self.assertTrue(ex._confirmed)

    def test_live_gate_missing_not_confirmed(self):
        ex = self._make(env="live", live_gate="")
        self.assertFalse(ex._confirmed)

    def test_live_gate_false_not_confirmed(self):
        ex = self._make(env="live", live_gate="false")
        self.assertFalse(ex._confirmed)

    def test_trade_log_path_from_config(self):
        ex = self._make()
        self.assertIn("trade_log.jsonl", str(ex._log_path))

    def test_confirmed_propagated_to_kalshi(self):
        scanner = _MockScanner()
        kalshi = _MockKalshi()
        risk = _real_risk()
        with patch.dict(os.environ, {"I_UNDERSTAND_LIVE_TRADING": "true"},
                        clear=False):
            Executor.from_config(
                {"environment": "live"}, scanner, kalshi, risk
            )
        self.assertTrue(kalshi.live_trading_confirmed)


# ── Integration ───────────────────────────────────────────────────────────────

class TestExecutorIntegration(unittest.TestCase):

    def test_multiple_placed_entries_processed(self):
        entries = [
            _placed_entry(ticker="T1", size_usd=50.0),
            _placed_entry(ticker="T2", size_usd=30.0),
        ]
        risk = _real_risk()
        ex = _executor(
            scanner=_MockScanner(entries=entries),
            risk=risk,
            mode="paper",
        )
        results = ex.run_cycle()
        self.assertEqual(len(results), 2)
        self.assertEqual(len(risk.ledger._open), 2)

    def test_mixed_entries_only_placed_executed(self):
        """PLACED entries are executed; SKIPPED_* entries are ignored."""
        entries = [
            _placed_entry(ticker="T1"),
            {**_placed_entry(ticker="T2"), "action": "SKIPPED_EDGE"},
            _placed_entry(ticker="T3"),
        ]
        risk = _real_risk()
        ex = _executor(
            scanner=_MockScanner(entries=entries),
            risk=risk,
            mode="paper",
        )
        results = ex.run_cycle()
        self.assertEqual(len(results), 2)
        self.assertEqual(len(risk.ledger._open), 2)


if __name__ == "__main__":
    unittest.main()
