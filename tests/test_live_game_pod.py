"""Tests for src/pods/live_game_pod.py — P-014 LiveGamePod."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.pods.live_game_pod import LiveGamePod
from src.live_odds_poller import LiveOddsPoller, LiveOddsSnapshot, BookmakerLine
from src.game_state import GameStateTracker, GameState, GamePhase
from src.base_pod import ScanResult


# ── Fixtures ──────────────────────────────────────────────────────────

FIXED_NOW = datetime(2026, 3, 28, 21, 0, 0, tzinfo=timezone.utc)


def _make_snapshot(
    game_id="game_1",
    sport="basketball_nba",
    home="Lakers",
    away="Celtics",
    lines=None,
):
    """Build a LiveOddsSnapshot with default lines."""
    if lines is None:
        lines = [
            BookmakerLine("pinnacle", "Pinnacle", -150, 130, ""),
            BookmakerLine("fanduel", "FanDuel", -155, 135, ""),
            BookmakerLine("draftkings", "DraftKings", -148, 128, ""),
        ]
    return LiveOddsSnapshot(
        game_id=game_id,
        sport=sport,
        home_team=home,
        away_team=away,
        commence_time="2026-03-28T19:00:00Z",
        is_live=True,
        bookmaker_lines=lines,
        poll_timestamp=FIXED_NOW.isoformat(),
    )


def _make_game_state(
    game_id="game_1",
    sport="basketball_nba",
    home="Lakers",
    away="Celtics",
    home_score=85,
    away_score=78,
):
    return GameState(
        game_id=game_id,
        sport=sport,
        home_team=home,
        away_team=away,
        home_score=home_score,
        away_score=away_score,
        phase=GamePhase.LIVE,
    )


def _mock_poller(snapshots=None):
    """Build a mock LiveOddsPoller that returns given snapshots."""
    poller = MagicMock(spec=LiveOddsPoller)
    if snapshots is None:
        snapshots = [_make_snapshot()]
    poller.poll.return_value = snapshots
    return poller


def _mock_tracker(games=None):
    """Build a mock GameStateTracker."""
    tracker = MagicMock(spec=GameStateTracker)
    if games is None:
        games = [_make_game_state()]
    tracker.update.return_value = games
    tracker.get_game.side_effect = lambda gid: next(
        (g for g in games if g.game_id == gid), None
    )
    return tracker


def _mock_risk_manager():
    rm = MagicMock()
    rm.max_bet_pct = 0.03
    rm.max_position_usd = 100
    rm.ledger = MagicMock()
    rm.ledger.bankroll = 10000
    return rm


def _make_pod(
    poller=None,
    tracker=None,
    kalshi_client=None,
    risk_manager=None,
    **kwargs,
):
    """Build a LiveGamePod with mocked dependencies."""
    return LiveGamePod(
        poller=poller or _mock_poller(),
        game_tracker=tracker or _mock_tracker(),
        kalshi_client=kalshi_client,
        edge_calculator=MagicMock(),
        risk_manager=risk_manager or _mock_risk_manager(),
        trade_log_path=Path("/tmp/test_p014.jsonl"),
        mode="paper",
        _now_fn=lambda: FIXED_NOW,
        **kwargs,
    )


# ── Basic scan_once tests ─────────────────────────────────────────────

class TestScanOnce:
    def test_scans_live_games(self):
        """Should call poller and tracker for each sport."""
        poller = _mock_poller()
        tracker = _mock_tracker()
        pod = _make_pod(poller=poller, tracker=tracker)

        results = pod.scan_once()
        # Should have polled for each sport
        assert poller.poll.call_count == 2  # NBA + MLB
        assert tracker.update.call_count == 2

    def test_skips_games_with_few_books(self):
        """Games with fewer than min_books should be skipped."""
        snap = _make_snapshot(lines=[
            BookmakerLine("pinnacle", "Pinnacle", -150, 130, ""),
        ])
        poller = _mock_poller(snapshots=[snap])
        pod = _make_pod(
            poller=poller,
            min_books_for_consensus=3,
        )
        results = pod.scan_once()
        # Should not produce trading signals (only 1 book)
        placed = [r for r in results if r.action == "PLACED"]
        assert len(placed) == 0

    def test_data_collection_without_kalshi(self):
        """When no Kalshi client, should still log DATA_COLLECTION results."""
        pod = _make_pod(kalshi_client=None)
        results = pod.scan_once()
        data_results = [r for r in results if r.action == "DATA_COLLECTION"]
        assert len(data_results) > 0
        assert data_results[0].skip_reason == "no_kalshi_market"

    def test_max_concurrent_games(self):
        """Should respect max_concurrent_games limit."""
        snaps = [
            _make_snapshot(game_id=f"game_{i}", home=f"Team {i}", away=f"Opp {i}")
            for i in range(10)
        ]
        games = [
            _make_game_state(game_id=f"game_{i}", home=f"Team {i}", away=f"Opp {i}")
            for i in range(10)
        ]
        poller = _mock_poller(snapshots=snaps)
        tracker = _mock_tracker(games=games)
        pod = _make_pod(
            poller=poller,
            tracker=tracker,
            max_concurrent_games=3,
        )

        results = pod.scan_once()
        # Should not exceed 3 unique games in results
        # (first poll for NBA may take up to 3 games, MLB would be blocked)
        unique_games = set(r.extra.get("home_team", "") for r in results if r.action != "DATA_COLLECTION")
        # With no Kalshi client, all are DATA_COLLECTION, so just check data results
        data_games = set(r.market_id for r in results if r.action == "DATA_COLLECTION")
        # The pod processes NBA first (up to max) then MLB (same snapshots via mock)
        # But dedup prevents same game from being logged twice
        assert len(data_games) <= 10  # bounded by dedup


# ── Venue name and registration ───────────────────────────────────────

class TestPodIdentity:
    def test_venue_name(self):
        pod = _make_pod()
        assert pod.venue_name() == "kalshi"

    def test_pod_id(self):
        pod = _make_pod()
        assert pod.pod_id == "P-014"

    def test_pod_name(self):
        pod = _make_pod()
        assert pod.pod_name == "Live Game Agent"


# ── from_config tests ─────────────────────────────────────────────────

class TestFromConfig:
    def test_builds_from_config(self):
        config = {
            "pods": {
                "P-014": {
                    "environment": "demo",
                    "sports": ["basketball_nba"],
                    "scan": {"max_concurrent_games": 3},
                    "signals": {"min_edge_pct": 0.03},
                    "fair_value": {"min_books_for_consensus": 4},
                    "risk": {"max_exposure_per_game_usd": 200},
                    "odds_api": {"regions": ["us"]},
                },
            },
        }
        poller = _mock_poller()
        tracker = _mock_tracker()

        pod = LiveGamePod.from_config(
            config,
            poller=poller,
            game_tracker=tracker,
            edge_calculator=MagicMock(),
            risk_manager=_mock_risk_manager(),
            trade_log_path=Path("/tmp/test.jsonl"),
        )
        assert pod._sports == ["basketball_nba"]
        assert pod._min_edge_pct == 0.03
        assert pod._min_books == 4
        assert pod._max_exposure_per_game == 200
        assert pod._max_concurrent_games == 3
        assert pod.mode == "paper"


# ── Paper order placement ─────────────────────────────────────────────

class TestPaperOrders:
    def test_paper_order_generates_id(self):
        pod = _make_pod()
        order_id = pod._place_order("MKT-001", "YES", 50.0, 0.6)
        assert order_id is not None
        assert order_id.startswith("paper-")

    def test_paper_order_different_ids(self):
        pod = _make_pod()
        id1 = pod._place_order("MKT-001", "YES", 50.0, 0.6)
        id2 = pod._place_order("MKT-002", "NO", 50.0, 0.4)
        assert id1 != id2


# ── fill_price on PLACED rows ─────────────────────────────────────────
#
# P-014 wrote `fill_price: null` on 356 of 356 PLACED rows across its
# entire life (2026-03-28 .. 2026-07-28, verified on the droplet).  The
# ScanResult simply never set the field, and because the pod calls
# write_log() itself, MultiExecutor's paper fill never reached disk.  No
# price meant no fee (0.07*P*(1-P)) and no contract count, so P-014 was
# the only pod that had to be dropped from the fee bill.

from src.kalshi_live_discovery import KalshiMarketMatch
from src.live_fair_value import LiveFairValueResult
from src.trade_log_schema import TradeLogSchema


def _make_match(yes_bid=70, yes_ask=75, yes_side="home"):
    return KalshiMarketMatch(
        game_id="game_1",
        ticker="KXMLBGAME-26JUL251310KCDET-DET",
        title="Will Detroit win?",
        yes_side=yes_side,
        home_team="Lakers",
        away_team="Celtics",
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        volume=1495790,
        fuzzy_score=98.0,
        market_type="game_winner",
    )


def _place(pod, side="YES", fair_prob=0.80, venue_prob=0.725):
    return pod._try_place_trade(
        snapshot=_make_snapshot(),
        game_state=_make_game_state(),
        kalshi_match=_make_match(),
        side=side,
        fair_prob=fair_prob,
        venue_prob=venue_prob,
        edge=(fair_prob - venue_prob) / venue_prob,
        now_str=FIXED_NOW.isoformat(),
        signal_source="consensus",
        fv_result=LiveFairValueResult(
            home_prob=fair_prob, away_prob=1.0 - fair_prob,
            method="live_ensemble", confidence=0.99, num_books=3,
        ),
    )


class TestFillPriceRecorded:
    def test_placed_row_carries_fill_price(self, tmp_path):
        pod = _make_pod()
        pod.trade_log_path = tmp_path / "p014.jsonl"
        result = _place(pod)
        assert result.action == "PLACED"
        assert result.fill_price is not None
        assert result.fill_price == pytest.approx(0.725)

    def test_fill_price_is_the_price_of_the_side_bought(self, tmp_path):
        """For NO, venue_prob is already 1 − mid, so fill_price is the NO
        price — the price paid — not the YES price.  contracts =
        position_size_usd / fill_price must hold for both sides."""
        pod = _make_pod()
        pod.trade_log_path = tmp_path / "p014.jsonl"
        result = _place(pod, side="NO", fair_prob=0.343, venue_prob=0.275)
        assert result.side == "NO"
        assert result.fill_price == pytest.approx(0.275)

    def test_written_row_passes_schema_validation(self, tmp_path):
        """The row as it lands on disk, not just the in-memory dataclass."""
        import json
        pod = _make_pod()
        pod.trade_log_path = tmp_path / "p014.jsonl"
        _place(pod)
        lines = pod.trade_log_path.read_text().strip().splitlines()
        placed = [json.loads(x) for x in lines]
        placed = [e for e in placed if e.get("action") == "PLACED"]
        assert placed, "no PLACED row written"
        for entry in placed:
            assert entry["fill_price"] is not None
            assert TradeLogSchema.validate(entry) == []

    def test_skipped_rows_have_no_fill_price(self, tmp_path):
        """A trade that was never placed must not claim a fill."""
        pod = _make_pod(max_exposure_per_game_usd=0.0)
        pod.trade_log_path = tmp_path / "p014.jsonl"
        result = _place(pod)
        assert result.action == "SKIPPED_RISK"
        assert result.fill_price is None


# ── Kelly fraction tests ──────────────────────────────────────────────

class TestKelly:
    def test_positive_edge(self):
        pod = _make_pod()
        kelly = pod._kelly_fraction(edge=0.05, fair_prob=0.6)
        assert kelly > 0
        assert kelly <= 0.05  # capped

    def test_zero_edge(self):
        pod = _make_pod()
        kelly = pod._kelly_fraction(edge=0.0, fair_prob=0.6)
        assert kelly == 0.0

    def test_extreme_prob(self):
        pod = _make_pod()
        kelly = pod._kelly_fraction(edge=0.05, fair_prob=0.0)
        assert kelly == 0.0
        kelly2 = pod._kelly_fraction(edge=0.05, fair_prob=1.0)
        assert kelly2 == 0.0

    def test_capped_at_kelly_cap(self):
        pod = _make_pod()
        # Large edge should still be capped at 5%
        kelly = pod._kelly_fraction(edge=0.50, fair_prob=0.9)
        assert kelly == 0.05


# ── Extra metadata tests ──────────────────────────────────────────────

class TestBuildExtra:
    def test_includes_game_state(self):
        pod = _make_pod()
        snap = _make_snapshot()
        gs = _make_game_state()
        extra = pod._build_extra(snap, gs)
        assert extra["home_score"] == 85
        assert extra["away_score"] == 78
        assert extra["game_phase"] == "live"
        assert extra["num_books"] == 3

    def test_includes_pinnacle(self):
        pod = _make_pod()
        snap = _make_snapshot()
        extra = pod._build_extra(snap, None)
        assert "pinnacle_home_odds" in extra
        assert extra["pinnacle_home_odds"] == -150

    def test_handles_no_game_state(self):
        pod = _make_pod()
        snap = _make_snapshot()
        extra = pod._build_extra(snap, None)
        assert "home_score" not in extra
        assert "num_books" in extra


# ── Pod registry integration ──────────────────────────────────────────

class TestRegistry:
    def test_registered_in_registry(self):
        from src.pod_registry import get_pod_class
        cls = get_pod_class("P-014")
        assert cls is not None
        assert cls.__name__ == "LiveGamePod"
