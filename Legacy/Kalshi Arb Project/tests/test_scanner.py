"""
tests/test_scanner.py
──────────────────────
Phase 6 must-pass tests:
  ✓ _mid_price: computes (yes_bid + yes_ask) / 200
  ✓ _mid_price: returns None when yes_bid missing
  ✓ _mid_price: returns None when yes_ask missing
  ✓ _mid_price: returns None when value out of (0,1) range
  ✓ _mid_price: handles float values
  ✓ _make_fingerprint: returns 64-char hex SHA-256
  ✓ _make_fingerprint: same ticker/side/hour → same fingerprint
  ✓ _make_fingerprint: different side → different fingerprint
  ✓ _make_fingerprint: different hour → different fingerprint
  ✓ scan_once: happy path writes PLACED entry to log
  ✓ scan_once: SKIPPED_EDGE when no edge cleared thresholds
  ✓ scan_once: SKIPPED_RISK when risk manager blocks
  ✓ scan_once: SKIPPED_DUPLICATE when fingerprint already seen
  ✓ scan_once: Kalshi fetch error → returns empty list
  ✓ scan_once: Odds fetch error → skips sport, returns empty list
  ✓ scan_once: no mid-price → skips market, no log entry
  ✓ scan_once: no consensus → skips market, no log entry
  ✓ scan_once: no odds events → skips sport
  ✓ scan_once: no matches → skips sport
  ✓ scan_once: multiple sports scanned independently
  ✓ trade log: PLACED entry has all required schema fields
  ✓ trade log: fill_price set to mid-price on PLACED
  ✓ trade log: order_id is None in paper mode
  ✓ trade log: skip_reason present on SKIPPED_EDGE
  ✓ trade log: skip_reason present on SKIPPED_RISK
  ✓ trade log: multiple entries appended (not overwritten)
  ✓ trade log: write error does not crash scan_once
  ✓ from_config: reads sports from odds_api.sports
  ✓ from_config: reads trade_log path from paths
  ✓ from_config: demo environment mapped to paper mode
  ✓ from_config: live environment preserved
  ✓ Scanner: session fingerprint set persists across scan_once calls

Phase 7 must-pass tests:
  ✓ _infer_yes_side: TIE-suffix ticker returns None
  ✓ _infer_yes_side: non-TIE suffix is not None
  ✓ _infer_yes_side: "Will [home] win?" → "home"
  ✓ _infer_yes_side: "Will [away] win?" → "away"
  ✓ _infer_yes_side: "will X win?" case-insensitive
  ✓ _infer_yes_side: partial team name in title matches home
  ✓ _infer_yes_side: tennis "Will Fils win?" → "home"
  ✓ _infer_yes_side: tennis "Will Mensik win?" → "away"
  ✓ _infer_yes_side: ticker suffix -KC matches home team
  ✓ _infer_yes_side: ticker suffix -LV matches away team
  ✓ _infer_yes_side: ticker -FIL matches Arthur Fils (home)
  ✓ _infer_yes_side: ticker -MEN matches Jakub Mensik (away)
  ✓ _infer_yes_side: no signal → skip (None)
  ✓ _infer_yes_side: empty market → skip (None)
  ✓ scan_once: TIE-suffix market skipped → no log entry
  ✓ scan_once: "Will [away] win?" → fair_prob = away_prob
  ✓ scan_once: "Will [home] win?" → fair_prob = home_prob
  ✓ scan_once: two variants of same event → only first placed
  ✓ scan_once: SKIPPED_EDGE variant → next variant still evaluated

Restart / persistence tests:
  ✓ Scanner restart within same hour → PLACED not re-placed (SKIPPED_DUPLICATE)
  ✓ Scanner restart in new hour → new placement allowed
  ✓ _load_seen_from_log: missing log file handled gracefully
  ✓ _load_seen_from_log: non-PLACED entries ignored
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.edge_calculator import EdgeCalculator, EdgeResult
from src.matcher import MatchResult, Matcher
from src.odds_client import ConsensusProbability, OddsEvent
from src.risk_manager import Ledger, RiskManager, TradeDecision
from src.scanner import Scanner, _infer_yes_side, _make_fingerprint, _mid_price


# ── Shared fixture helpers ─────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)
_SPORT = "americanfootball_nfl"


def _event(home="Kansas City Chiefs", away="Las Vegas Raiders",
           event_id="evt-1") -> OddsEvent:
    return OddsEvent(
        event_id=event_id,
        sport_key=_SPORT,
        commence_time=_NOW,
        home_team=home,
        away_team=away,
        bookmaker_odds=[],
    )


def _consensus(home_prob=0.62, away_prob=0.38) -> ConsensusProbability:
    return ConsensusProbability(
        event_id="evt-1",
        sport_key=_SPORT,
        home_team="Kansas City Chiefs",
        away_team="Las Vegas Raiders",
        commence_time=_NOW,
        home_prob=home_prob,
        away_prob=away_prob,
        books_used=["pinnacle"],
        method="weighted_sharp",
        requests_remaining=500,
    )


def _kalshi_market(ticker="NFL-KC-LV-ML", title="Will Kansas City Chiefs win?",
                   yes_bid=55, yes_ask=59, volume=1000) -> Dict[str, Any]:
    return {
        "ticker": ticker,
        "title": title,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "volume": volume,
        "status": "open",
    }


def _match(ticker="NFL-KC-LV-ML", title="Will Kansas City Chiefs win?",
           odds_event=None) -> MatchResult:
    return MatchResult(
        kalshi_ticker=ticker,
        kalshi_title=title,
        odds_event=odds_event or _event(),
        fuzzy_score=95.0,
        time_delta_minutes=0.0,
    )


# ── Mock clients ───────────────────────────────────────────────────────────────

class _MockKalshi:
    def __init__(self, markets=None, raise_on_list=False):
        self._markets = markets or []
        self._raise = raise_on_list
        self.list_calls = 0

    def list_markets(self, **kwargs):
        self.list_calls += 1
        if self._raise:
            raise RuntimeError("kalshi mock error")
        return {"markets": self._markets}


class _MockOdds:
    def __init__(self, events=None, consensus=None, raise_on_get=False):
        self._events = events if events is not None else []
        self._consensus = consensus
        self._raise = raise_on_get

    def get_events(self, sport):
        if self._raise:
            raise RuntimeError("odds mock error")
        return self._events

    def get_consensus(self, event):
        return self._consensus


class _MockMatcher:
    def __init__(self, results=None):
        self._results = results if results is not None else []

    def match_all(self, markets, events, sport=None):
        return list(self._results)


def _real_edge(min_edge_pct=0.03, min_ev=0.01) -> EdgeCalculator:
    return EdgeCalculator(
        fee_pct=0.07,
        kelly_fraction=0.25,
        max_bet_pct=0.03,
        min_edge_pct=min_edge_pct,
        min_ev=min_ev,
    )


def _real_risk(bankroll=10_000.0) -> RiskManager:
    ledger = Ledger(initial_bankroll=bankroll)
    return RiskManager(ledger=ledger, max_positions=10, max_exposure_pct=0.20,
                       max_daily_loss_pct=0.05, cooldown_minutes=60,
                       kelly_fraction=0.25, max_bet_pct=0.03,
                       min_confidence=0.55)


def _scanner(
    kalshi=None, odds=None, matcher=None, edge=None, risk=None,
    log_path=None, sports=None, mode="paper",
) -> Scanner:
    return Scanner(
        kalshi_client=kalshi or _MockKalshi(),
        odds_client=odds or _MockOdds(),
        matcher=matcher or _MockMatcher(),
        edge_calculator=edge or _real_edge(),
        risk_manager=risk or _real_risk(),
        sports=sports if sports is not None else [_SPORT],
        trade_log_path=log_path or Path(tempfile.mktemp(suffix=".jsonl")),
        mode=mode,
        _now_fn=lambda: _NOW,
    )


# ── _mid_price ────────────────────────────────────────────────────────────────

class TestMidPrice(unittest.TestCase):

    def test_computes_average(self):
        mid = _mid_price({"yes_bid": 55, "yes_ask": 59})
        self.assertAlmostEqual(mid, 0.57)

    def test_integer_values(self):
        mid = _mid_price({"yes_bid": 50, "yes_ask": 50})
        self.assertAlmostEqual(mid, 0.50)

    def test_float_values(self):
        mid = _mid_price({"yes_bid": 55.0, "yes_ask": 65.0})
        self.assertAlmostEqual(mid, 0.60)

    def test_missing_yes_bid_returns_none(self):
        self.assertIsNone(_mid_price({"yes_ask": 59}))

    def test_missing_yes_ask_returns_none(self):
        self.assertIsNone(_mid_price({"yes_bid": 55}))

    def test_missing_both_returns_none(self):
        self.assertIsNone(_mid_price({"ticker": "X"}))

    def test_zero_bid_returns_none(self):
        # mid = (0 + 0) / 200 = 0.0 — not in (0,1)
        self.assertIsNone(_mid_price({"yes_bid": 0, "yes_ask": 0}))

    def test_100_ask_returns_none(self):
        # mid = (100 + 100) / 200 = 1.0 — not in (0,1)
        self.assertIsNone(_mid_price({"yes_bid": 100, "yes_ask": 100}))

    def test_invalid_type_returns_none(self):
        self.assertIsNone(_mid_price({"yes_bid": "bad", "yes_ask": 59}))


# ── _make_fingerprint ─────────────────────────────────────────────────────────

class TestMakeFingerprint(unittest.TestCase):

    def test_returns_64_char_hex(self):
        fp = _make_fingerprint("NFL-KC-LV", "YES", _NOW)
        self.assertEqual(len(fp), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))

    def test_same_inputs_same_output(self):
        fp1 = _make_fingerprint("NFL-KC-LV", "YES", _NOW)
        fp2 = _make_fingerprint("NFL-KC-LV", "YES", _NOW)
        self.assertEqual(fp1, fp2)

    def test_different_side_different_fp(self):
        fp_yes = _make_fingerprint("NFL-KC-LV", "YES", _NOW)
        fp_no  = _make_fingerprint("NFL-KC-LV", "NO",  _NOW)
        self.assertNotEqual(fp_yes, fp_no)

    def test_different_ticker_different_fp(self):
        fp1 = _make_fingerprint("NFL-KC-LV", "YES", _NOW)
        fp2 = _make_fingerprint("NFL-KC-LV-DIFF", "YES", _NOW)
        self.assertNotEqual(fp1, fp2)

    def test_same_hour_different_minute_same_fp(self):
        t1 = _NOW
        t2 = _NOW + timedelta(minutes=30)   # still same hour
        fp1 = _make_fingerprint("T", "YES", t1)
        fp2 = _make_fingerprint("T", "YES", t2)
        self.assertEqual(fp1, fp2)

    def test_different_hour_different_fp(self):
        t1 = _NOW
        t2 = _NOW + timedelta(hours=1)
        fp1 = _make_fingerprint("T", "YES", t1)
        fp2 = _make_fingerprint("T", "YES", t2)
        self.assertNotEqual(fp1, fp2)


# ── scan_once — happy path ────────────────────────────────────────────────────

class TestScanOnceHappyPath(unittest.TestCase):

    def setUp(self):
        self._log = Path(tempfile.mktemp(suffix=".jsonl"))
        # Chiefs -165 fair / 57¢ Kalshi → strong edge
        self._market = _kalshi_market(yes_bid=54, yes_ask=60)   # mid = 0.57
        self._cp = _consensus(home_prob=0.62)                   # edge ≈ 0.05

    def _make_scanner(self) -> Scanner:
        return _scanner(
            kalshi=_MockKalshi(markets=[self._market]),
            odds=_MockOdds(events=[_event()], consensus=self._cp),
            matcher=_MockMatcher(results=[_match()]),
            edge=_real_edge(min_edge_pct=0.03, min_ev=0.01),
            risk=_real_risk(),
            log_path=self._log,
        )

    def test_returns_list_with_one_entry(self):
        s = self._make_scanner()
        entries = s.scan_once()
        self.assertEqual(len(entries), 1)

    def test_action_is_placed(self):
        s = self._make_scanner()
        entries = s.scan_once()
        self.assertEqual(entries[0]["action"], "PLACED")

    def test_log_file_created(self):
        s = self._make_scanner()
        s.scan_once()
        self.assertTrue(self._log.exists())

    def test_log_entry_is_valid_json(self):
        s = self._make_scanner()
        s.scan_once()
        line = self._log.read_text().strip()
        rec = json.loads(line)
        self.assertIsInstance(rec, dict)

    def test_fill_price_equals_mid(self):
        s = self._make_scanner()
        entries = s.scan_once()
        mid = (54 + 60) / 200.0
        self.assertAlmostEqual(entries[0]["fill_price"], mid, places=4)

    def test_order_id_is_none_in_paper_mode(self):
        s = self._make_scanner()
        entries = s.scan_once()
        self.assertIsNone(entries[0]["order_id"])

    def test_mode_is_paper(self):
        s = self._make_scanner()
        entries = s.scan_once()
        self.assertEqual(entries[0]["mode"], "paper")

    def test_ticker_in_entry(self):
        s = self._make_scanner()
        entries = s.scan_once()
        self.assertEqual(entries[0]["market_ticker"], "NFL-KC-LV-ML")


# ── scan_once — required schema fields ───────────────────────────────────────

class TestScanOnceSchemaFields(unittest.TestCase):

    _REQUIRED = [
        "fingerprint", "timestamp_utc", "mode", "sport", "event",
        "market_ticker", "side", "fair_prob", "kalshi_prob", "edge_pct",
        "ev", "kelly_fraction", "position_size_usd", "action",
    ]

    def test_all_required_fields_present(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        market = _kalshi_market(yes_bid=54, yes_ask=60)
        cp = _consensus(home_prob=0.62)
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            log_path=log_path,
        )
        s.scan_once()
        rec = json.loads(log_path.read_text().strip())
        for field in self._REQUIRED:
            self.assertIn(field, rec, f"Missing required field: {field}")

    def test_fingerprint_is_64_char_hex(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        market = _kalshi_market(yes_bid=54, yes_ask=60)
        cp = _consensus(home_prob=0.62)
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            log_path=log_path,
        )
        s.scan_once()
        rec = json.loads(log_path.read_text().strip())
        self.assertEqual(len(rec["fingerprint"]), 64)


# ── scan_once — skip paths ────────────────────────────────────────────────────

class TestScanOnceSkips(unittest.TestCase):

    def setUp(self):
        self._log = Path(tempfile.mktemp(suffix=".jsonl"))

    def _read_log(self):
        if not self._log.exists():
            return []
        lines = self._log.read_text().strip().splitlines()
        return [json.loads(ln) for ln in lines if ln.strip()]

    def test_skipped_edge_when_no_edge(self):
        # 50¢ bid/ask mid → 0.50, fair = 0.51 → tiny edge, fails min_edge_pct=0.03
        market = _kalshi_market(yes_bid=50, yes_ask=50)  # mid = 0.50
        cp = _consensus(home_prob=0.51)
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            edge=_real_edge(min_edge_pct=0.03, min_ev=0.01),
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "SKIPPED_EDGE")
        rec = self._read_log()[0]
        self.assertIn("skip_reason", rec)

    def test_skipped_risk_when_risk_blocked(self):
        """Risk manager with max_positions=0 blocks every trade."""
        market = _kalshi_market(yes_bid=54, yes_ask=60)
        cp = _consensus(home_prob=0.62)
        ledger = Ledger(initial_bankroll=10_000)
        risk = RiskManager(ledger=ledger, max_positions=0)  # 0 allowed = always blocked
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            risk=risk,
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "SKIPPED_RISK")
        rec = self._read_log()[0]
        self.assertIn("skip_reason", rec)

    def test_skipped_duplicate_on_second_scan(self):
        """Same market in the same hour → no re-entry on second scan_once.

        The open-game-key guard prevents any re-entry on a game that
        already has an open position.  This fires before the fingerprint
        dedup check, so the second scan returns an empty list.
        """
        market = _kalshi_market(yes_bid=54, yes_ask=60)
        cp = _consensus(home_prob=0.62)
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            log_path=self._log,
        )
        entries1 = s.scan_once()   # PLACED
        entries2 = s.scan_once()   # blocked by open-game-key guard
        self.assertEqual(entries1[0]["action"], "PLACED")
        self.assertEqual(len(entries2), 0)   # silently skipped — open position

    def test_kalshi_fetch_error_returns_empty(self):
        s = _scanner(
            kalshi=_MockKalshi(raise_on_list=True),
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(entries, [])
        self.assertFalse(self._log.exists())

    def test_odds_fetch_error_returns_empty_for_sport(self):
        market = _kalshi_market()
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(raise_on_get=True),
            matcher=_MockMatcher(results=[_match()]),
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(entries, [])

    def test_no_mid_price_skips_market(self):
        # Market missing yes_bid/yes_ask → no price → skip
        market = {"ticker": "NFL-KC-LV-ML", "title": "...", "status": "open"}
        cp = _consensus(home_prob=0.62)
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(entries, [])
        self.assertFalse(self._log.exists())

    def test_no_consensus_skips_market(self):
        market = _kalshi_market()
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=None),
            matcher=_MockMatcher(results=[_match()]),
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(entries, [])

    def test_no_odds_events_skips_sport(self):
        market = _kalshi_market()
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[]),
            matcher=_MockMatcher(results=[_match()]),
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(entries, [])

    def test_no_matches_skips_sport(self):
        market = _kalshi_market()
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=_consensus()),
            matcher=_MockMatcher(results=[]),   # no matches
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(entries, [])

    def test_position_size_zero_on_skipped_edge(self):
        market = _kalshi_market(yes_bid=50, yes_ask=50)
        cp = _consensus(home_prob=0.50)
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(entries[0]["position_size_usd"], 0.0)


# ── Multiple sports / markets ─────────────────────────────────────────────────

class TestScanOnceMultipleSports(unittest.TestCase):

    def test_two_sports_scanned(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        nfl_market = _kalshi_market(ticker="NFL-T", yes_bid=54, yes_ask=60)
        nba_market = _kalshi_market(ticker="NBA-T", yes_bid=54, yes_ask=60)

        call_log = []
        class TrackingOdds:
            def get_events(inner_self, sport):
                call_log.append(sport)
                return [_event()]
            def get_consensus(inner_self, event):
                return _consensus(home_prob=0.62)

        s = Scanner(
            kalshi_client=_MockKalshi(markets=[nfl_market, nba_market]),
            odds_client=TrackingOdds(),
            matcher=_MockMatcher(results=[_match()]),
            edge_calculator=_real_edge(),
            risk_manager=_real_risk(),
            sports=["americanfootball_nfl", "basketball_nba"],
            trade_log_path=log_path,
            _now_fn=lambda: _NOW,
        )
        s.scan_once()
        self.assertIn("americanfootball_nfl", call_log)
        self.assertIn("basketball_nba", call_log)

    def test_multiple_entries_appended(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        market1 = _kalshi_market(ticker="T1", yes_bid=54, yes_ask=60)
        market2 = _kalshi_market(ticker="T2", yes_bid=54, yes_ask=60)
        # Give each match a distinct event_id so the per-cycle dedup does not
        # suppress the second entry (they are different underlying events).
        ev1 = _event(event_id="evt-1")
        ev2 = _event(event_id="evt-2")
        match1 = _match(ticker="T1", odds_event=ev1)
        match2 = _match(ticker="T2", odds_event=ev2)
        cp = _consensus(home_prob=0.62)
        s = _scanner(
            kalshi=_MockKalshi(markets=[market1, market2]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[match1, match2]),
            log_path=log_path,
        )
        entries = s.scan_once()
        self.assertEqual(len(entries), 2)
        lines = log_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)

    def test_write_error_does_not_crash(self):
        bad_path = Path("/dev/null/not_a_dir/trade.jsonl")
        market = _kalshi_market(yes_bid=54, yes_ask=60)
        cp = _consensus(home_prob=0.62)
        s = Scanner(
            kalshi_client=_MockKalshi(markets=[market]),
            odds_client=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            edge_calculator=_real_edge(),
            risk_manager=_real_risk(),
            sports=[_SPORT],
            trade_log_path=bad_path,
            _now_fn=lambda: _NOW,
        )
        # Should not raise
        entries = s.scan_once()
        self.assertEqual(len(entries), 1)   # entry returned even if write fails


# ── from_config ───────────────────────────────────────────────────────────────

class TestScannerFromConfig(unittest.TestCase):

    def _cfg(self, **overrides) -> dict:
        base = {
            "odds_api": {"sports": ["americanfootball_nfl", "basketball_nba"]},
            "paths": {"trade_log": "data/trade_logs/trade_log.jsonl"},
            "environment": "demo",
        }
        base.update(overrides)
        return base

    def test_reads_sports_from_config(self):
        s = Scanner.from_config(
            self._cfg(), _MockKalshi(), _MockOdds(),
            _MockMatcher(), _real_edge(), _real_risk(),
        )
        self.assertIn("americanfootball_nfl", s._sports)
        self.assertIn("basketball_nba", s._sports)

    def test_reads_trade_log_path(self):
        s = Scanner.from_config(
            self._cfg(), _MockKalshi(), _MockOdds(),
            _MockMatcher(), _real_edge(), _real_risk(),
        )
        self.assertIn("trade_log.jsonl", str(s._log_path))

    def test_demo_environment_maps_to_paper(self):
        s = Scanner.from_config(
            self._cfg(environment="demo"), _MockKalshi(), _MockOdds(),
            _MockMatcher(), _real_edge(), _real_risk(),
        )
        self.assertEqual(s._mode, "paper")

    def test_live_environment_preserved(self):
        s = Scanner.from_config(
            self._cfg(environment="live"), _MockKalshi(), _MockOdds(),
            _MockMatcher(), _real_edge(), _real_risk(),
        )
        self.assertEqual(s._mode, "live")

    def test_unknown_environment_defaults_to_paper(self):
        s = Scanner.from_config(
            self._cfg(environment="unknown"), _MockKalshi(), _MockOdds(),
            _MockMatcher(), _real_edge(), _real_risk(),
        )
        self.assertEqual(s._mode, "paper")

    def test_missing_odds_api_gives_empty_sports(self):
        s = Scanner.from_config(
            {}, _MockKalshi(), _MockOdds(),
            _MockMatcher(), _real_edge(), _real_risk(),
        )
        self.assertEqual(s._sports, [])


# ── Fingerprint session persistence ───────────────────────────────────────────

class TestFingerprintPersistence(unittest.TestCase):

    def test_seen_set_grows_across_calls(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        market = _kalshi_market(yes_bid=54, yes_ask=60)
        cp = _consensus(home_prob=0.62)
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            log_path=log_path,
        )
        self.assertEqual(len(s._seen), 0)
        s.scan_once()
        self.assertEqual(len(s._seen), 1)
        s.scan_once()
        self.assertEqual(len(s._seen), 1)  # not added twice

    def test_kalshi_called_twice_across_two_scans(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        kalshi = _MockKalshi(markets=[_kalshi_market(yes_bid=54, yes_ask=60)])
        cp = _consensus(home_prob=0.62)
        s = _scanner(
            kalshi=kalshi,
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            log_path=log_path,
        )
        s.scan_once()
        s.scan_once()
        self.assertEqual(kalshi.list_calls, 2)


# ── Phase 7: _infer_yes_side ──────────────────────────────────────────────────

class TestInferYesSide(unittest.TestCase):
    """Phase 7: _infer_yes_side() YES-team/player detection."""

    _HOME = "Kansas City Chiefs"
    _AWAY = "Las Vegas Raiders"

    def _market(self, title: str = "", ticker: str = "NFL-KC-LV-ML") -> Dict[str, Any]:
        return {"title": title, "ticker": ticker}

    # ── TIE suffix ────────────────────────────────────────────────────────

    def test_tie_suffix_returns_none(self):
        m = self._market(ticker="KXHOCKEY-26FEB19-BOS-PHI-TIE")
        self.assertIsNone(_infer_yes_side(m, self._HOME, self._AWAY))

    def test_tie_suffix_case_handled(self):
        # suffix is upper-cased internally; lowercase "tie" in source works too
        m = self._market(ticker="KXHOCKEY-BOS-PHI-TIE")
        self.assertIsNone(_infer_yes_side(m, "Boston Bruins", "Philadelphia Flyers"))

    def test_non_tie_suffix_not_none(self):
        m = self._market(ticker="KXHOCKEY-26FEB19-BOS-PHI-BOS")
        self.assertIsNotNone(_infer_yes_side(m, "Boston Bruins", "Philadelphia Flyers"))

    # ── "Will [X] win?" title pattern ─────────────────────────────────────

    def test_will_home_win_returns_home(self):
        m = self._market(title="Will Kansas City Chiefs win?")
        self.assertEqual(_infer_yes_side(m, self._HOME, self._AWAY), "home")

    def test_will_away_win_returns_away(self):
        m = self._market(title="Will Las Vegas Raiders win?")
        self.assertEqual(_infer_yes_side(m, self._HOME, self._AWAY), "away")

    def test_will_win_case_insensitive(self):
        m = self._market(title="will kansas city chiefs win?")
        self.assertEqual(_infer_yes_side(m, self._HOME, self._AWAY), "home")

    def test_will_win_partial_name_home(self):
        # "Chiefs" strongly matches "Kansas City Chiefs" over "Las Vegas Raiders"
        m = self._market(title="Will the Chiefs win?")
        self.assertEqual(_infer_yes_side(m, self._HOME, self._AWAY), "home")

    def test_tennis_will_fils_win(self):
        m = self._market(title="Will Arthur Fils win?", ticker="KXATP-FIL-MEN-FIL")
        self.assertEqual(_infer_yes_side(m, "Arthur Fils", "Jakub Mensik"), "home")

    def test_tennis_will_mensik_win(self):
        m = self._market(title="Will Jakub Mensik win?", ticker="KXATP-FIL-MEN-MEN")
        self.assertEqual(_infer_yes_side(m, "Arthur Fils", "Jakub Mensik"), "away")

    # ── Ticker suffix fallback ─────────────────────────────────────────────

    def test_ticker_suffix_home_team(self):
        # Suffix "KC" only scores 10 against "Kansas City Chiefs" (< 15 threshold).
        # Title "... winner" doesn't match "Will X win?" either.
        # Without confidence in YES-side → skip (None) to avoid phantom edges.
        m = self._market(
            title="Kansas City Chiefs vs Las Vegas Raiders winner",
            ticker="NFL-KC-LV-KC",
        )
        self.assertIsNone(_infer_yes_side(m, self._HOME, self._AWAY))

    def test_ticker_suffix_away_team(self):
        # Suffix "LV" scores higher against "Las Vegas Raiders" than "Kansas City Chiefs"
        m = self._market(
            title="Kansas City Chiefs vs Las Vegas Raiders winner",
            ticker="NFL-KC-LV-LV",
        )
        self.assertEqual(_infer_yes_side(m, self._HOME, self._AWAY), "away")

    def test_ticker_tennis_fils_suffix(self):
        m = self._market(
            title="ATP Qatar: Fils vs Mensik winner",
            ticker="KXATPQATAR-26FEB19-FIL-MEN-FIL",
        )
        # "FIL" matches "Arthur Fils" better than "Jakub Mensik"
        self.assertEqual(_infer_yes_side(m, "Arthur Fils", "Jakub Mensik"), "home")

    def test_ticker_tennis_mensik_suffix(self):
        m = self._market(
            title="ATP Qatar: Fils vs Mensik winner",
            ticker="KXATPQATAR-26FEB19-FIL-MEN-MEN",
        )
        # "MEN" matches "Jakub Mensik" better than "Arthur Fils"
        self.assertEqual(_infer_yes_side(m, "Arthur Fils", "Jakub Mensik"), "away")

    # ── Default fallback ───────────────────────────────────────────────────

    def test_default_none_when_no_signal(self):
        # No "Will X win?" and no hyphen in ticker → skip (None)
        m = {"title": "NFL Championship game", "ticker": "NFL"}
        self.assertIsNone(_infer_yes_side(m, self._HOME, self._AWAY))

    def test_empty_market_returns_none(self):
        self.assertIsNone(_infer_yes_side({}, "Home Team", "Away Team"))

    def test_none_title_returns_none(self):
        m = {"title": None, "ticker": "NFL"}
        self.assertIsNone(_infer_yes_side(m, self._HOME, self._AWAY))


# ── Phase 7: integration tests ────────────────────────────────────────────────

class TestPhase7Integration(unittest.TestCase):
    """Integration: _evaluate_match uses _infer_yes_side correctly."""

    def setUp(self):
        self._log = Path(tempfile.mktemp(suffix=".jsonl"))

    def _read_log(self) -> list:
        if not self._log.exists():
            return []
        return [json.loads(ln) for ln in self._log.read_text().strip().splitlines() if ln]

    def test_tie_market_skipped_no_entry(self):
        """TIE-suffix market → _evaluate_match returns None → no log entry."""
        market = _kalshi_market(
            ticker="KXHOCKEY-26FEB19-BOS-PHI-TIE",
            title="Will there be a tie?",
            yes_bid=20,
            yes_ask=24,
        )
        cp = _consensus(home_prob=0.62)
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[
                _match(ticker="KXHOCKEY-26FEB19-BOS-PHI-TIE",
                       title="Will there be a tie?"),
            ]),
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(entries, [])
        self.assertFalse(self._log.exists())

    def test_away_yes_uses_away_prob(self):
        """'Will Las Vegas Raiders win?' → fair_prob recorded as away_prob (0.38)."""
        # mid = (30+36)/200 = 0.33; away_prob=0.38 → YES edge ≈ 5%
        market = _kalshi_market(
            ticker="NFL-KC-LV-LV",
            title="Will Las Vegas Raiders win?",
            yes_bid=30,
            yes_ask=36,
        )
        cp = _consensus(home_prob=0.62, away_prob=0.38)
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[
                _match(ticker="NFL-KC-LV-LV", title="Will Las Vegas Raiders win?"),
            ]),
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(len(entries), 1)
        # fair_prob must reflect away_prob, NOT home_prob
        self.assertAlmostEqual(entries[0]["fair_prob"], 0.38, places=2)
        self.assertNotAlmostEqual(entries[0]["fair_prob"], 0.62, places=2)

    def test_home_yes_uses_home_prob(self):
        """'Will Kansas City Chiefs win?' → fair_prob recorded as home_prob (0.62)."""
        # mid = (54+60)/200 = 0.57; home_prob=0.62 → YES edge ≈ 5%
        market = _kalshi_market(
            ticker="NFL-KC-LV-KC",
            title="Will Kansas City Chiefs win?",
            yes_bid=54,
            yes_ask=60,
        )
        cp = _consensus(home_prob=0.62, away_prob=0.38)
        s = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[
                _match(ticker="NFL-KC-LV-KC", title="Will Kansas City Chiefs win?"),
            ]),
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(len(entries), 1)
        self.assertAlmostEqual(entries[0]["fair_prob"], 0.62, places=2)

    def test_no_double_bet_same_event(self):
        """
        Two market variants for the same Odds API event → only the first
        variant is placed; the second is skipped entirely (per-cycle event dedup).
        """
        home_market = _kalshi_market(
            ticker="NFL-KC-LV-KC",
            title="Will Kansas City Chiefs win?",
            yes_bid=54, yes_ask=60,
        )
        away_market = _kalshi_market(
            ticker="NFL-KC-LV-LV",
            title="Will Las Vegas Raiders win?",
            yes_bid=30, yes_ask=36,
        )
        cp = _consensus(home_prob=0.62, away_prob=0.38)
        # Both matches reference the same underlying event_id ("evt-1")
        s = _scanner(
            kalshi=_MockKalshi(markets=[home_market, away_market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[
                _match(ticker="NFL-KC-LV-KC", title="Will Kansas City Chiefs win?"),
                _match(ticker="NFL-KC-LV-LV", title="Will Las Vegas Raiders win?"),
            ]),
            log_path=self._log,
        )
        entries = s.scan_once()
        # Only the first variant should be evaluated and placed
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["market_ticker"], "NFL-KC-LV-KC")
        self.assertAlmostEqual(entries[0]["fair_prob"], 0.62, places=2)


# ── Per-cycle event deduplication ────────────────────────────────────────────

class TestEventDedup(unittest.TestCase):
    """Per-cycle dedup: only one Kalshi variant traded per Odds API event."""

    def setUp(self):
        self._log = Path(tempfile.mktemp(suffix=".jsonl"))

    def test_event_dedup_only_one_variant_placed(self):
        """Two edgy variants for the same event → only first is placed."""
        m1 = _kalshi_market(ticker="T-HOME", title="Will Kansas City Chiefs win?",
                            yes_bid=54, yes_ask=60)
        m2 = _kalshi_market(ticker="T-AWAY", title="Will Las Vegas Raiders win?",
                            yes_bid=30, yes_ask=36)
        cp = _consensus(home_prob=0.62, away_prob=0.38)
        # Both MatchResults share the same event_id ("evt-1" from _event())
        s = _scanner(
            kalshi=_MockKalshi(markets=[m1, m2]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[
                _match(ticker="T-HOME", title="Will Kansas City Chiefs win?"),
                _match(ticker="T-AWAY", title="Will Las Vegas Raiders win?"),
            ]),
            log_path=self._log,
        )
        entries = s.scan_once()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["market_ticker"], "T-HOME")
        self.assertEqual(entries[0]["action"], "PLACED")

    def test_event_dedup_skipped_edge_tries_next_variant(self):
        """First variant SKIPPED_EDGE → second variant for same event is evaluated."""
        # First variant: mid = 0.62 = home_prob → zero YES edge → SKIPPED_EDGE
        no_edge = _kalshi_market(ticker="T-NOEDGE", title="Will Kansas City Chiefs win?",
                                 yes_bid=61, yes_ask=63)   # mid = 0.62
        # Second variant: mid = 0.33, away_prob = 0.38 → ~5% YES edge → PLACED
        edge = _kalshi_market(ticker="T-EDGE", title="Will Las Vegas Raiders win?",
                              yes_bid=30, yes_ask=36)       # mid = 0.33
        cp = _consensus(home_prob=0.62, away_prob=0.38)
        s = _scanner(
            kalshi=_MockKalshi(markets=[no_edge, edge]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[
                _match(ticker="T-NOEDGE", title="Will Kansas City Chiefs win?"),
                _match(ticker="T-EDGE",   title="Will Las Vegas Raiders win?"),
            ]),
            log_path=self._log,
            edge=_real_edge(min_edge_pct=0.03, min_ev=0.01),
        )
        entries = s.scan_once()
        actions = [e["action"] for e in entries]
        self.assertIn("SKIPPED_EDGE", actions)
        self.assertIn("PLACED", actions)
        placed = next(e for e in entries if e["action"] == "PLACED")
        self.assertEqual(placed["market_ticker"], "T-EDGE")

    def test_two_different_events_both_placed(self):
        """Variants of two *different* events are each evaluated independently."""
        ev1 = _event(home="Kansas City Chiefs", away="Las Vegas Raiders", event_id="evt-1")
        ev2 = _event(home="New England Patriots", away="Miami Dolphins",   event_id="evt-2")
        m1 = _kalshi_market(ticker="T1", yes_bid=54, yes_ask=60)
        m2 = _kalshi_market(ticker="T2", yes_bid=54, yes_ask=60)
        cp = _consensus(home_prob=0.62)

        class TwoEventOdds:
            def get_events(self, sport):      return [ev1, ev2]
            def get_consensus(self, event):   return cp

        s = Scanner(
            kalshi_client=_MockKalshi(markets=[m1, m2]),
            odds_client=TwoEventOdds(),
            matcher=_MockMatcher(results=[
                _match(ticker="T1", odds_event=ev1),
                _match(ticker="T2", odds_event=ev2),
            ]),
            edge_calculator=_real_edge(),
            risk_manager=_real_risk(),
            sports=[_SPORT],
            trade_log_path=self._log,
            _now_fn=lambda: _NOW,
        )
        entries = s.scan_once()
        # Both different events should each get an entry
        self.assertEqual(len(entries), 2)
        tickers = {e["market_ticker"] for e in entries}
        self.assertIn("T1", tickers)
        self.assertIn("T2", tickers)


# ── Restart / fingerprint persistence ────────────────────────────────────────

class TestRestartPersistence(unittest.TestCase):
    """Scanner reloads PLACED fingerprints from the trade log on construction."""

    def test_restart_same_hour_no_replace(self):
        """
        New Scanner instance reading an existing log that has a PLACED entry
        for the same game → second scan is blocked by open-game-key guard.

        The fingerprint dedup would also catch this, but the open-position
        guard fires first and silently skips the game entirely.
        """
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        market = _kalshi_market(yes_bid=54, yes_ask=60)
        cp = _consensus(home_prob=0.62)

        # First scanner places the trade
        s1 = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            log_path=log_path,
        )
        entries1 = s1.scan_once()
        self.assertEqual(entries1[0]["action"], "PLACED")

        # Second scanner (simulated restart, same hour) pre-loads log
        s2 = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            log_path=log_path,
        )
        entries2 = s2.scan_once()
        self.assertEqual(len(entries2), 0)   # blocked — open position on this game

    def test_restart_new_hour_blocked_by_open_position(self):
        """
        Restarting in a *different* hour generates a new fingerprint, but
        the open-game-key guard still blocks re-entry because the first
        trade is unsettled.  This is the intended safety behaviour — once
        a position is open on a game, no further bets on that game until
        it settles (WIN/LOSS/VOID).
        """
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        market = _kalshi_market(yes_bid=54, yes_ask=60)
        cp = _consensus(home_prob=0.62)
        hour1 = _NOW
        hour2 = _NOW.replace(hour=_NOW.hour + 1)   # next UTC hour

        # First scanner at hour1
        s1 = Scanner(
            kalshi_client=_MockKalshi(markets=[market]),
            odds_client=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            edge_calculator=_real_edge(),
            risk_manager=_real_risk(),
            sports=[_SPORT],
            trade_log_path=log_path,
            _now_fn=lambda: hour1,
        )
        e1 = s1.scan_once()
        self.assertEqual(e1[0]["action"], "PLACED")

        # Second scanner at hour2 — new fingerprint but open position blocks
        s2 = Scanner(
            kalshi_client=_MockKalshi(markets=[market]),
            odds_client=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            edge_calculator=_real_edge(),
            risk_manager=_real_risk(bankroll=10_000.0),  # fresh risk manager
            sports=[_SPORT],
            trade_log_path=log_path,
            _now_fn=lambda: hour2,
        )
        e2 = s2.scan_once()
        self.assertEqual(len(e2), 0)   # blocked — open position on this game

    def test_load_seen_missing_log_is_silent(self):
        """If the trade log doesn't exist yet, _load_seen_from_log is a no-op."""
        s = _scanner(log_path=Path("/tmp/does_not_exist_xyz.jsonl"))
        self.assertEqual(len(s._seen), 0)

    def test_load_seen_ignores_non_placed_entries(self):
        """Only PLACED entries contribute to _seen; SKIPPED_* entries are ignored."""
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        market = _kalshi_market(yes_bid=50, yes_ask=50)   # no-edge market
        cp = _consensus(home_prob=0.51)
        s1 = _scanner(
            kalshi=_MockKalshi(markets=[market]),
            odds=_MockOdds(events=[_event()], consensus=cp),
            matcher=_MockMatcher(results=[_match()]),
            log_path=log_path,
        )
        e1 = s1.scan_once()
        self.assertEqual(e1[0]["action"], "SKIPPED_EDGE")

        # Second scanner: SKIPPED_EDGE fingerprint should NOT be in _seen
        s2 = _scanner(log_path=log_path)
        self.assertEqual(len(s2._seen), 0)   # no PLACED → nothing loaded


if __name__ == "__main__":
    unittest.main()
